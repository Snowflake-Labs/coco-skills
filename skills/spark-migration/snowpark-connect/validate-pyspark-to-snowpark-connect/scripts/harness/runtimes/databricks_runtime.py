"""Databricks Connect runtime for Phase A baseline generation.

Uses databricks-connect (Spark Connect) to run the original source in-process
against a remote Databricks cluster. Golden schemas are seeded once at
provision time, then SHALLOW CLONEd per trial.

Architecture:
1. provision() - cluster prewarm + seed golden schema for this entrypoint
2. run_trial()  - SHALLOW CLONE pre-provisioned golden + run_and_capture
3. cleanup_session() - discovery-driven teardown invoked by cleanup.py
   (drops this run's golden + orphaned trial schemas via prefix sweep)
4. atexit - only stops the Spark session (releases gRPC pool); goldens persist
   and are dropped exclusively by cleanup.py via cleanup_session().
"""

from __future__ import annotations

import atexit
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .base import TrialContext, TrialRequest, TrialResult, ValidationRuntime
from ._executor import run_and_capture

from helpers import (  # type: ignore[import-not-found]
    declared_sink_tables,
    file_io_env,
    _io_id_from_name,
    _declared_table_name,
)


# Module-level golden schema cache (process-scoped, one entry per ep_id)
_golden_schemas: Dict[str, str] = {}   # ep_id -> qualified schema name
_golden_tables: Dict[str, List[str]] = {}  # ep_id -> list of seeded table names
# Databricks-connect supports concurrent SQL; bound the fan-out for clone/seed.
_MAX_DBX_WORKERS = int(os.environ.get("SCOS_DBX_WORKERS", "8"))
_golden_files: Dict[str, Dict[str, str]] = {}  # ep_id -> {source_name -> dbfs_path}

_atexit_registered = False


def resolve_databricks_catalog(spark) -> str:
    """Catalog that holds the golden/trial schemas.

    Defaults to ``hive_metastore`` — writable on standard *and* UC-enabled
    (USER_ISOLATION/"shared") clusters with no special grants, and accessible to
    databricks-connect regardless of Unity Catalog. Unity Catalog is a governance
    layer, not a databricks-connect requirement; UC catalogs typically need
    explicit ``CREATE SCHEMA`` grants the token may lack.

    Override with the ``DATABRICKS_CATALOG`` env var to target a governed catalog
    (e.g. on UC-only clusters where hive_metastore is disabled). The chosen
    catalog is write-probed so a bad target fails fast with a clear message
    instead of a cryptic error mid-run.
    """
    cat = os.environ.get("DATABRICKS_CATALOG", "").strip() or "hive_metastore"
    # Unique probe schema per process: under xdist, every worker probes the same
    # catalog concurrently. A shared name races (Hive metastore IF EXISTS is not
    # atomic) and spuriously fails one worker. The CREATE is the real permission
    # gate; the DROP is best-effort cleanup.
    probe = f"{cat}.scos_perm_probe_{uuid4().hex[:8]}"
    try:
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {probe}")
    except Exception as exc:
        raise RuntimeError(
            f"Cannot CREATE SCHEMA in Databricks catalog {cat!r}: {exc}\n"
            f"  Grant CREATE SCHEMA on {cat} to your token, or set DATABRICKS_CATALOG "
            f"to a catalog you can write to (hive_metastore works on most clusters)."
        ) from exc
    try:
        spark.sql(f"DROP SCHEMA IF EXISTS {probe}")
    except Exception:
        pass  # best-effort; cleanup_session/cleanup.py sweeps stragglers
    return cat


class DatabricksRuntime(ValidationRuntime):
    """Phase A runtime: run original source on a Databricks cluster via databricks-connect."""

    flavor = "databricks"

    def __init__(self, project_root: Optional[str] = None):
        from . import detect_databricks_env
        env = detect_databricks_env()
        if env is None:
            raise RuntimeError(
                "Databricks environment not resolved. Set DATABRICKS_HOST, "
                "DATABRICKS_TOKEN, DATABRICKS_CLUSTER_ID (or SCOS_DATABRICKS_ENV_FILE)."
            )
        self._host = env["host"]
        self._token = env["token"]
        self._cluster_id = env["cluster_id"]
        self._spark = None
        self._catalog: Optional[str] = None

        global _atexit_registered
        if not _atexit_registered:
            _atexit_registered = True
            atexit.register(self._atexit_cleanup)

    @property
    def spark(self):
        if self._spark is None:
            from databricks.connect import DatabricksSession
            self._spark = (
                DatabricksSession.builder.remote(
                    host=self._host,
                    token=self._token,
                    cluster_id=self._cluster_id,
                ).getOrCreate()
            )
            self._catalog = resolve_databricks_catalog(self._spark)
        return self._spark

    def provision(self, request: TrialRequest) -> None:
        """Idempotent pre-trial provisioning: prewarm cluster + seed golden schema for this entrypoint.

        Called by the driver before run_trial. Hash-gated: skips if the golden
        schema already exists and hashes match. Populates module caches and
        request.state_json["databricks"]["golden_schemas"][ep_id] so run_trial
        can find the golden FQN.
        """
        from helpers import load_provision_hashes, save_provision_hashes  # type: ignore[import-not-found]

        ep_id = (request.ep_config or {}).get("id") or request.trial_id

        # Already provisioned in this process? Skip.
        if ep_id in _golden_schemas:
            # Ensure state_json has the mapping for run_trial
            golden_fqn = _golden_schemas[ep_id]
            state = request.state_json or {}
            db_state = state.setdefault("databricks", {})
            db_state.setdefault("golden_schemas", {})[ep_id] = golden_fqn
            return

        # Prewarm cluster (best-effort)
        try:
            from databricks.sdk import WorkspaceClient
            client = WorkspaceClient(host=self._host, token=self._token)
            info = client.clusters.get(self._cluster_id)
            state_val = (info.state.value if info.state else "").upper()
            if state_val not in ("RUNNING", "RESIZING"):
                client.clusters.start(self._cluster_id)
        except Exception as exc:
            print(f"[databricks] WARNING: cluster prewarm failed (non-fatal): {exc}", file=sys.stderr)

        # Resolve catalog
        cat = self._catalog or resolve_databricks_catalog(self.spark)
        self._catalog = cat

        # Resolve golden schema name
        run_id = (request.state_json or {}).get("run_id") or os.environ.get("SCOS_RUN_ID") or uuid4().hex[:8]
        golden_bare = f"scos_golden_{run_id}_{ep_id[:16]}".lower()
        golden_fqn = f"{cat}.{golden_bare}"

        # Seed golden schema (hash-gated)
        workspace_root = Path(request.project_root) / "Validation"
        store = load_provision_hashes(workspace_root)
        mock_ep_dir = Path(request.mock_data_dir)
        tables = self._seed_golden_schema(golden_fqn, request.ep_config or {}, mock_ep_dir, store)
        save_provision_hashes(workspace_root, store)

        # Update module caches (seed returns the table list, so no second SHOW).
        _golden_schemas[ep_id] = golden_fqn
        _golden_tables[ep_id] = tables
        print(f"[databricks] golden schema ready: {golden_fqn} ({len(tables)} tables)")

        # Write into request.state_json so run_trial can find it
        state = request.state_json or {}
        db_state = state.setdefault("databricks", {})
        db_state.setdefault("golden_schemas", {})[ep_id] = golden_fqn
        if ep_id in _golden_files:
            db_state.setdefault("file_paths", {})[ep_id] = _golden_files[ep_id]

    def _seed_golden_schema(self, golden_fqn: str, ep_config: dict, mock_ep_dir: Path, store: dict) -> List[str]:
        """Create golden schema and seed tables from the unified tables dict via pandas.

        Returns the full list of table names in the schema (existing + seeded) so
        the caller can skip a second SHOW TABLES (P15)."""
        import pandas as pd
        from helpers import (  # type: ignore[import-not-found]
            schema_hash as _schema_hash,
            _build_spark_schema, _resolve_schema, _bare_table_name,
            _load_schemas_json, provision_hash_matches, record_provision_hash,
        )

        spark = self.spark
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {golden_fqn}")

        shared_dir = str(mock_ep_dir.parent.parent)
        schemas_cache = _load_schemas_json(shared_dir)
        ep_schemas = ep_config.get("schemas", {})
        if isinstance(ep_schemas, dict):
            for k, v in ep_schemas.items():
                schemas_cache.setdefault(k, v)

        # Build set of existing tables
        existing: set = set()
        try:
            for r in spark.sql(f"SHOW TABLES IN {golden_fqn}").collect():
                existing.add(r.tableName.lower())
        except Exception:
            pass
        all_tables: set = set(existing)

        ep_id = ep_config.get("id", "")
        golden_bare_name = golden_fqn.split(".")[-1]
        tables = ep_config.get("tables") or {}

        for name, tbl in tables.items():
            access = tbl.get("access", "read")
            category = tbl.get("category", "table")
            relational = tbl.get("relational", True)
            mock_file = tbl.get("mock_file", "")

            bare = _declared_table_name(name, tbl)
            h = _schema_hash(tbl)

            # Relational read/readwrite tables (table, connector, snowflake, jdbc)
            if access in ("read", "readwrite") and category in ("table", "connector", "snowflake", "jdbc") and relational:
                if bare in existing and provision_hash_matches(store, "databricks", ep_id, bare, h):
                    print(f"  [databricks] [{ep_id}] Skipped (hash match): {bare}")
                    continue
                if not mock_file:
                    continue
                csv_path = mock_ep_dir / mock_file
                if not csv_path.is_file():
                    continue
                try:
                    ext = csv_path.suffix.lower()
                    if ext == ".parquet":
                        pdf = pd.read_parquet(csv_path)
                    elif ext in (".json", ".jsonl"):
                        pdf = pd.read_json(csv_path, lines=True)
                    else:
                        pdf = pd.read_csv(csv_path, dtype=str)
                    schema_fields = _resolve_schema(tbl.get("columns") or [], schemas_cache)
                    if schema_fields and _build_spark_schema:
                        spark_schema = _build_spark_schema(schema_fields)
                        df = spark.createDataFrame(pdf, schema=spark_schema)
                    else:
                        df = spark.createDataFrame(pdf)
                    df.write.mode("overwrite").saveAsTable(f"{golden_fqn}.{bare}")
                    record_provision_hash(store, "databricks", ep_id, bare, h)
                    all_tables.add(bare.lower())
                except Exception as exc:
                    print(f"[databricks] WARNING: could not seed {bare}: {exc}", file=sys.stderr)

            # Write-only tables: create empty
            elif access in ("write", "readwrite") and category == "table" and relational:
                if bare in existing:
                    continue
                columns = tbl.get("columns") or []
                if not columns:
                    continue
                schema_fields = _resolve_schema(columns, schemas_cache)
                if schema_fields and _build_spark_schema:
                    spark_schema = _build_spark_schema(schema_fields)
                    try:
                        spark.createDataFrame([], spark_schema).write.mode("overwrite").saveAsTable(f"{golden_fqn}.{bare}")
                        record_provision_hash(store, "databricks", ep_id, bare, h)
                        all_tables.add(bare.lower())
                    except Exception:
                        pass

            # File-category: upload to DBFS
            elif category == "file" and access in ("read", "readwrite"):
                if not mock_file:
                    continue
                local_path = mock_ep_dir / mock_file
                if not local_path.exists():
                    continue
                dbfs_path = f"dbfs:/tmp/scos-golden/{golden_bare_name}/{mock_file}"
                try:
                    from databricks.sdk import WorkspaceClient
                    client = WorkspaceClient(host=self._host, token=self._token)
                    client.dbfs.upload(dbfs_path, io.BytesIO(local_path.read_bytes()), overwrite=True)
                    _golden_files.setdefault(ep_id, {})[name] = dbfs_path
                except Exception as exc:
                    print(f"[databricks] WARNING: could not upload {mock_file} to DBFS: {exc}", file=sys.stderr)

        return sorted(all_tables)

    def run_trial(self, request: TrialRequest) -> TrialResult:
        """SHALLOW CLONE pre-provisioned golden schema + run_and_capture."""
        spark = self.spark
        ep_id = (request.ep_config or {}).get("id") or request.trial_id
        run_id = (request.state_json or {}).get("run_id") or os.environ.get("SCOS_RUN_ID") or uuid4().hex[:8]
        cat = self._catalog or "hive_metastore"

        golden_fqn = (
            _golden_schemas.get(ep_id)
            or (request.state_json or {}).get("databricks", {}).get("golden_schemas", {}).get(ep_id)
        )
        if golden_fqn is None:
            raise RuntimeError(
                f"No golden schema found for ep_id={ep_id!r}. "
                f"provision(request) must be called before run_trial(request)."
            )

        golden_tables = _golden_tables.get(ep_id, [
            r.tableName for r in spark.sql(f"SHOW TABLES IN {golden_fqn}").collect()
        ])

        trial_schema = f"scos_trial_{request.trial_id[:20]}_{uuid4().hex[:8]}".lower()
        trial_fqn = f"{cat}.{trial_schema}"
        trial_id = request.trial_id

        saved_env: Dict[str, Optional[str]] = {"SCOS_OUTPUT_SCHEMA": os.environ.get("SCOS_OUTPUT_SCHEMA")}
        dbfs_trial_root = f"dbfs:/tmp/scos-trial/{trial_id}"
        try:
            spark.sql(f"CREATE SCHEMA IF NOT EXISTS {trial_fqn}")

            # SHALLOW CLONE each golden table into the trial schema. Independent
            # per table and run every trial, so fan out (P13).
            def _clone_one(t):
                spark.sql(
                    f"CREATE TABLE IF NOT EXISTS {trial_fqn}.{t} "
                    f"SHALLOW CLONE {golden_fqn}.{t}"
                )

            if len(golden_tables) <= 1:
                for t in golden_tables:
                    _clone_one(t)
            else:
                with ThreadPoolExecutor(
                    max_workers=min(len(golden_tables), _MAX_DBX_WORKERS)
                ) as pool:
                    for fut in as_completed([pool.submit(_clone_one, t) for t in golden_tables]):
                        fut.result()

            spark.sql(f"USE {trial_fqn}")
            os.environ["SCOS_OUTPUT_SCHEMA"] = trial_schema

            sink_tables = declared_sink_tables(request.ep_config or {}, trial_schema)

            # File I/O env setup
            file_read_paths = (
                _golden_files.get(ep_id)
                or (request.state_json or {}).get("databricks", {}).get("file_paths", {}).get(ep_id, {})
            ) or {}

            _tables = request.ep_config.get("tables") or {}
            file_write_paths = {
                name: f"{dbfs_trial_root}/{_io_id_from_name(name).lower()}"
                for name, tbl in _tables.items()
                if tbl.get("category") == "file"
                and tbl.get("access", "read") in ("write", "readwrite")
            }

            file_env = file_io_env(request.ep_config, read_paths=file_read_paths, write_paths=file_write_paths)
            for key in file_env:
                saved_env[key] = os.environ.get(key)
            os.environ.update(file_env)

            ctx = TrialContext(
                trial_id=request.trial_id,
                flavor="databricks",
                output_schema=trial_schema,
                results_dir=request.results_dir,
                seed_tables=golden_tables,
                sink_tables=sink_tables,
                run_id=run_id,
            )
            manifest = run_and_capture(spark, request, ctx)

            # Capture DBFS file sinks
            _sink_index_entries = []
            for name, dbfs_path in file_write_paths.items():
                sid = _io_id_from_name(name).lower()
                tbl = _tables.get(name, {})
                fmt = tbl.get("format", "parquet")
                try:
                    df = spark.read.format(fmt).load(dbfs_path)
                    tables_dir = os.path.join(request.results_dir, trial_id, "tables")
                    os.makedirs(tables_dir, exist_ok=True)
                    out_path = os.path.join(tables_dir, f"{sid}.parquet")
                    pdf = df.toPandas()
                    pdf.attrs = {}  # strip Databricks Connect PlanMetrics before serialization
                    pdf.to_parquet(out_path)
                    entry = {"name": sid, "path": f"tables/{sid}.parquet", "source": "filesystem", "row_count": len(pdf)}
                    manifest.setdefault("tables", []).append(entry)
                    _sink_index_entries.append(entry)
                except Exception as exc:
                    sys.stderr.write(f"[databricks] WARNING: could not capture file sink {name}: {exc}\n")

            # run_and_capture's capture_results already wrote _index.json BEFORE the
            # DBFS file-sink loop above ran (databricks captures sinks via read-back,
            # not the local sink_capture_dir). Re-persist _index.json so the on-disk
            # manifest reflects these file sinks — otherwise automated status parsing
            # sees tables: [] even though tables/<sid>.parquet exist.
            if _sink_index_entries:
                index_path = os.path.join(request.results_dir, trial_id, "_index.json")
                try:
                    with open(index_path, encoding="utf-8") as _f:
                        _disk = json.load(_f)
                    _disk.setdefault("tables", []).extend(_sink_index_entries)
                    _tmp = index_path + ".tmp"
                    with open(_tmp, "w", encoding="utf-8") as _f:
                        json.dump(_disk, _f, indent=2)
                    os.replace(_tmp, index_path)
                except Exception as exc:
                    sys.stderr.write(f"[databricks] WARNING: could not update _index.json with file sinks: {exc}\n")

            return TrialResult(
                trial_id=request.trial_id,
                flavor="databricks",
                results_dir=request.results_dir,
                ok=manifest["ok"],
                manifest=manifest,
                output_schema=trial_schema,
                error=manifest.get("error"),
            )
        finally:
            try:
                spark.sql(f"DROP SCHEMA IF EXISTS {trial_fqn} CASCADE")
            except Exception:
                pass
            try:
                from databricks.sdk import WorkspaceClient
                client = WorkspaceClient(host=self._host, token=self._token)
                client.dbfs.delete(dbfs_trial_root, recursive=True)
            except Exception:
                pass
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def cleanup_session(self, *, state: dict, database: str, dry_run: bool = False) -> list:
        """Drop this run's golden + orphaned trial schemas via prefix sweep.

        Discovers schemas by querying the Databricks catalog (SHOW SCHEMAS LIKE
        'scos_*') and matching this run's golden prefix plus any scos_trial_*
        orphans. Does NOT depend on state.json golden_schemas being written.
        """
        run_id = state.get("run_id")
        if not run_id:
            return []

        cat = self._catalog or os.environ.get("DATABRICKS_CATALOG", "").strip() or "hive_metastore"
        golden_prefix = f"scos_golden_{run_id}_".lower()

        try:
            spark = self.spark
        except Exception:
            return []

        try:
            rows = spark.sql(f"SHOW SCHEMAS IN {cat} LIKE 'scos_*'").collect()
            schemas = [
                r.namespace if hasattr(r, "namespace") else r[0]
                for r in rows
            ]
        except Exception:
            return []

        matched = [
            s for s in schemas
            if s.lower().startswith(golden_prefix) or s.lower().startswith("scos_trial_")
        ]
        fqns = [f"{cat}.{s}" for s in matched]

        if dry_run:
            return fqns

        for fqn in fqns:
            try:
                spark.sql(f"DROP SCHEMA IF EXISTS {fqn} CASCADE")
                print(f"  Dropped: {fqn}")
            except Exception:
                pass

        _golden_schemas.clear()
        _golden_tables.clear()
        _golden_files.clear()
        return fqns

    def _atexit_cleanup(self) -> None:
        """Release the gRPC pool. Does NOT drop schemas — cleanup.py handles that."""
        try:
            if self._spark is not None:
                self._spark.stop()
        except Exception:
            pass
        self._spark = None
