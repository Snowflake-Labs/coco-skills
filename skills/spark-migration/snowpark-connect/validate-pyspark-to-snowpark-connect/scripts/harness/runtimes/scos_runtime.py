"""SCOS runtime — Phase B validation via Snowpark Connect against a cloned schema."""

from __future__ import annotations

import os
import shutil
import sys
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .base import TrialContext, TrialRequest, TrialResult, ValidationRuntime
from ._executor import run_and_capture

from helpers import (  # type: ignore[import-not-found]
    _SF_CONN_HOLDER,
    clone_golden_schema_for_trial,
    declared_sink_tables,
    file_io_env,
    install_sql_date_pin,
    load_provision_hashes,
    provision_hash_matches,
    schema_hash,
    _io_id_from_name,
    _declared_table_name,
)

# Stage created inside each per-trial clone schema to receive file-sink writes.
SINK_STAGE_NAME = "SCOS_SINKS"
# Parallelism for downloading staged sinks (one GET per sink prefix).
SINK_GET_WORKERS = int(os.environ.get("SCOS_GET_WORKERS", "8"))


def _sink_stage_path(db: str, clone: str, io_id: str) -> str:
    """Unquoted stage path for SCOS file-sink writes (no trailing slash)."""
    return f"@{db}.{clone}.{SINK_STAGE_NAME}/{io_id}"


def _stage_root_for_trial(state_json: Dict[str, Any], ep_id: str, output_schema: str) -> Optional[str]:
    """Compute the stage root path for file reads in the SCOS clone schema."""
    snowflake = state_json.get("snowflake", {}) or {}
    ep_info = (snowflake.get("golden_schemas") or {}).get(ep_id, {}) or {}
    database = snowflake.get("database")
    stage_fqn = ep_info.get("stage", "")
    stage_prefix = ep_info.get("stage_prefix", "")
    if not database or not stage_prefix:
        return None
    stage_name = str(stage_fqn).replace('"', "").split(".")[-1] if stage_fqn else ""
    if not stage_name:
        return None
    # CLONE SCHEMA does not clone internal stages — use golden schema for reads.
    golden_schema = ep_info.get("schema", "")
    stage_schema = golden_schema if golden_schema else output_schema
    return f"@{database}.{stage_schema}.{stage_name}/{stage_prefix}"


def _list_seed_tables(state_json: dict, clone_schema: str, ep_id: str = "") -> list[str]:
    """Seed tables the clone exposes, as ``clone_schema.table`` (lowercased).

    Prefers the list persisted at provision time
    (``state.snowflake.golden_schemas[ep_id].tables``) so we avoid opening a
    fresh connection + SHOW TABLES on every trial (P8). Falls back to a live
    SHOW TABLES when the state predates that field."""
    golden = (
        ((state_json.get("snowflake") or {}).get("golden_schemas") or {}).get(ep_id) or {}
    )
    persisted = golden.get("tables")
    if persisted is not None:
        return [f"{clone_schema}.{t}".lower() for t in persisted]

    database = state_json["snowflake"]["database"]
    connection_name = state_json["config"]["connection_name"]
    import snowflake.connector  # type: ignore

    conn = snowflake.connector.connect(connection_name=connection_name)
    cur = conn.cursor()
    try:
        cur.execute(f'SHOW TABLES IN SCHEMA "{database}"."{clone_schema}"')
        rows = cur.fetchall()
        return [f"{clone_schema}.{row[1]}".lower() for row in rows]
    finally:
        cur.close()
        conn.close()


def _schemas_matching_run_prefix(schema_names: list, run_prefix: str) -> list:
    """Return schema names that match the run prefix (case-insensitive).

    The prefix is ensured to end with '_' so that e.g. 'PROJ_RUN1_' never
    matches 'PROJ_RUN10_FOO'.
    """
    prefix = run_prefix.upper()
    if not prefix.endswith("_"):
        prefix += "_"
    return [s for s in schema_names if s.upper().startswith(prefix)]


class ScosRuntime(ValidationRuntime):
    """Phase B: Snowpark Connect against a trial-isolated clone of the golden schema."""

    flavor = "scos"

    def provision(self, request: TrialRequest, conn: Optional[Any] = None) -> None:
        """Idempotently provision this entrypoint's golden schema (hash-gated).

        Called by the driver before run_trial. provision_golden_schemas skips
        unchanged tables cheaply and reseeds changed ones, so this self-heals
        stale schemas and picks up schema edits between runs. Sets
        state_json[snowflake][golden_schemas][<id>] so the subsequent clone
        can find it.

        ``conn`` is only consulted on the full provision path. The hash-match
        fast-path returns before ``conn`` is examined, so a caller-provided
        conn is silently unused when the fast-path fires. On the full path,
        if ``conn`` is provided it is used as-is and not closed on return;
        if ``conn`` is None, a new connection is opened and closed after use.
        """
        from pathlib import Path
        import snowflake.connector  # lazy (present in the scos venv)
        from . import _scos_provision

        state = request.state_json or {}
        config = state.get("config", {})
        connection_name = config.get("connection_name")
        project_slug = config.get("project_slug")
        run_id = state.get("run_id")
        database = (state.get("snowflake", {}) or {}).get("database") or os.environ.get(
            "SCOS_VALIDATION_DATABASE", "SCOS_VALIDATION"
        )
        if not (connection_name and project_slug and run_id):
            # Not enough config to provision; skip (driver will still call run_trial).
            return

        ep = request.ep_config or {}
        ep_id = ep.get("id")
        if not ep_id:
            return
        mock_data_root = Path(os.path.dirname(request.mock_data_dir))

        # ---------------------------------------------------------------------------
        # Fast-path: state entry present + all table hashes match on disk = golden
        # schema is up-to-date on Snowflake. If the schema was dropped externally,
        # run_trial will fail with 'schema not found' and the operator can re-run.
        # ---------------------------------------------------------------------------
        sf_state = state.get("snowflake", {}) or {}
        if sf_state.get("golden_schemas", {}).get(ep_id) is not None:
            workspace_root = mock_data_root.resolve().parents[1]
            store = load_provision_hashes(workspace_root)
            all_match = True
            has_readable = False
            for tbl_name, tbl in ep.get("tables", {}).items():
                access = tbl.get("access", "read")
                if access == "write":
                    # Write-only tables have no seed data — no hash to compare; skip.
                    continue
                has_readable = True
                table_key = _declared_table_name(tbl_name, tbl)
                if not provision_hash_matches(store, "scos", ep_id, table_key, schema_hash(tbl)):
                    all_match = False
                    break
            if all_match and has_readable:
                return  # Golden schema is current; skip the Snowflake round-trip.

        # ---------------------------------------------------------------------------
        # Full provision path — open a connection unless one was passed in.
        # ---------------------------------------------------------------------------
        _conn_owned = conn is None
        if _conn_owned:
            holder = _SF_CONN_HOLDER.get()
            if holder is not None:
                conn = holder.acquire(connection_name)
                _conn_owned = False
            else:
                conn = snowflake.connector.connect(connection_name=connection_name)
        try:
            golden = _scos_provision.provision_golden_schemas(
                conn,
                {"connection_name": connection_name},
                [ep],
                mock_data_root,
                project_slug,
                run_id,
                database,
            )
        finally:
            if _conn_owned:
                conn.close()

        # Make the clone lookup succeed — clone_golden_schema_for_trial reads
        # state["snowflake"]["golden_schemas"][trial_id]. We set under both
        # trial_id and ep_id to be safe (they may differ).
        ep_info = golden.get(ep_id)
        if ep_info:
            sf = state.setdefault("snowflake", {})
            sf.setdefault("database", database)
            sf.setdefault("golden_schemas", {})[request.trial_id] = ep_info
            sf["golden_schemas"][ep_id] = ep_info

    def cleanup_session(self, *, state: dict, database: str, dry_run: bool = False) -> list:
        """Drop golden + leaked clone schemas for this run via run-prefix sweep.

        Discovers schemas by deterministic prefix match (not state.json), so it
        works even when lazy provisioning never wrote golden_schemas to disk.

        DROPs run in parallel — each worker holds its own Snowflake connection
        (a cursor can't be shared across threads). ``SCOS_CLEANUP_WORKERS`` env
        var caps concurrency (default 16); we always clamp to len(matched) so a
        3-schema sweep never opens 16 connections. Down to a single connection
        for a single-schema sweep to preserve the fast path.
        """
        config = state.get("config", {}) or {}
        connection_name = config.get("connection_name")
        project_slug = config.get("project_slug")
        run_id = state.get("run_id")
        if not (connection_name and project_slug and run_id):
            return []

        run_prefix = f"{project_slug}_{run_id}".upper()

        import snowflake.connector  # type: ignore

        conn = snowflake.connector.connect(connection_name=connection_name)
        try:
            cur = conn.cursor()
            try:
                cur.execute(f"SHOW SCHEMAS IN DATABASE {database}")
                all_names = [row[1] for row in cur.fetchall()]
            finally:
                cur.close()
            matched = _schemas_matching_run_prefix(all_names, run_prefix)
            fqns = [f"{database}.{s}" for s in matched]
            if dry_run or not fqns:
                return fqns

            # Fast path: single schema — reuse the connection we already have.
            if len(fqns) == 1:
                fqn = fqns[0]
                cur = conn.cursor()
                try:
                    cur.execute(f"DROP SCHEMA IF EXISTS {fqn} CASCADE")
                finally:
                    cur.close()
                print(f"  Dropped: {fqn}")
                return fqns

            # Parallel path — one connection per worker (Snowflake cursors are
            # not thread-safe). DROP SCHEMA CASCADE is IO-bound (single network
            # round-trip on the metadata layer), so threading gives near-linear
            # speedup up to Snowflake's schema-DDL concurrency ceiling.
            from concurrent.futures import ThreadPoolExecutor, as_completed

            max_workers = int(os.environ.get("SCOS_CLEANUP_WORKERS", "16"))
            workers = max(1, min(max_workers, len(fqns)))

            def _drop_one(fqn: str) -> tuple[str, Optional[str]]:
                try:
                    c = snowflake.connector.connect(connection_name=connection_name)
                    try:
                        cur = c.cursor()
                        try:
                            cur.execute(f"DROP SCHEMA IF EXISTS {fqn} CASCADE")
                        finally:
                            cur.close()
                    finally:
                        c.close()
                    return fqn, None
                except Exception as exc:  # noqa: BLE001
                    return fqn, repr(exc)

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [pool.submit(_drop_one, fqn) for fqn in fqns]
                for fut in as_completed(futs):
                    fqn, err = fut.result()
                    if err is None:
                        print(f"  Dropped: {fqn}")
                    else:
                        sys.stderr.write(f"  WARNING: DROP {fqn} failed: {err}\n")
            return fqns
        finally:
            conn.close()

    def _download_staged_sinks(
        self,
        state_json: Dict[str, Any],
        db: str,
        clone_schema: str,
        io_ids: List[str],
        sink_capture_dir: str,
    ) -> None:
        """GET staged sink files into local sink_capture_dir before capture_results runs.

        Each sink lives under its own ``SCOS_SINKS/<io_id>/`` prefix. There is no
        batch GET across distinct prefixes (GET flattens filenames into the target
        dir, which would collapse per-sink grouping), so we fetch one prefix per
        sink into its own local dir. Fetches run in a small thread pool, each on
        its own connection (a cursor cannot be shared across threads safely).
        """
        if not io_ids:
            return
        import snowflake.connector  # type: ignore

        connection_name = state_json["config"]["connection_name"]

        def _get_one(io_id: str) -> None:
            local_dir = os.path.join(sink_capture_dir, io_id)
            os.makedirs(local_dir, exist_ok=True)
            get_sql = (
                f"GET '@\"{db}\".\"{clone_schema}\".\"{SINK_STAGE_NAME}\"/{io_id}/'"
                f" 'file://{local_dir}/'"
            )
            conn = snowflake.connector.connect(connection_name=connection_name)
            try:
                conn.cursor().execute(get_sql)
            except Exception as exc:  # noqa: BLE001
                # A sink with no rows produces no staged files — expected.
                sys.stderr.write(
                    f"[scos_runtime] INFO: GET staged sink {io_id!r} yielded nothing"
                    f" (may be empty): {exc}\n"
                )
            finally:
                conn.close()

        if len(io_ids) == 1:
            _get_one(io_ids[0])
            return
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(len(io_ids), SINK_GET_WORKERS)) as pool:
            list(pool.map(_get_one, io_ids))

    def _create_sink_stage(self, state_json: Dict[str, Any], db: str, clone_schema: str) -> None:
        """Create the per-trial sink stage via the Snowflake connector.

        Spark Connect cannot parse Snowflake DDL — ``spark.sql('CREATE STAGE ...')``
        raises ``[PARSE_SYNTAX_ERROR]`` — so the stage must be created through
        ``snowflake.connector`` (the same path that clones the schema). Failing
        here is fatal: without the stage, every file-sink write fails at capture.
        """
        import snowflake.connector  # type: ignore

        connection_name = state_json["config"]["connection_name"]
        conn = snowflake.connector.connect(connection_name=connection_name)
        try:
            conn.cursor().execute(
                f'CREATE STAGE IF NOT EXISTS "{db}"."{clone_schema}"."{SINK_STAGE_NAME}"'
            )
        finally:
            conn.close()

    def run_trial(self, request: TrialRequest) -> TrialResult:
        os.environ["SPARK_CONNECT_MODE_ENABLED"] = "1"

        # Ensure snowpark_connect is importable under bare name
        try:
            import snowflake.snowpark_connect as _spc  # type: ignore
            sys.modules.setdefault("snowpark_connect", _spc)
        except ImportError:
            pass
        import snowpark_connect  # type: ignore

        spark = None
        clone_ctx = None

        saved_env: dict[str, str | None] = {}
        env_keys = [
            "SCOS_TRIAL_CLONE_SCHEMA", "SCOS_DATABASE_NAME",
            "SPARK_CONNECT_MODE_ENABLED", "SCOS_OUTPUT_SCHEMA",
        ]
        for key in env_keys:
            saved_env[key] = os.environ.get(key)

        try:
            # Clone golden schema for isolation
            clone_ctx = clone_golden_schema_for_trial(request.state_json, request.trial_id)
            clone_schema = clone_ctx.__enter__()
            os.environ["SCOS_TRIAL_CLONE_SCHEMA"] = clone_schema

            sink_tables = declared_sink_tables(request.ep_config, clone_schema)

            # Init Spark Connect session
            spark = snowpark_connect.init_spark_session()

            # Pin server-side current_date()/current_timestamp() so execution-date
            # columns (EFFECTIVE_DATE, UPDATED_TS, ...) are deterministic and match
            # the Phase A baseline regardless of when (which UTC day) Phase B runs.
            install_sql_date_pin(spark)

            # Point session at the clone schema
            _scos_db = request.state_json["snowflake"].get("database", "")
            os.environ["SCOS_DATABASE_NAME"] = _scos_db
            os.environ["SCOS_OUTPUT_SCHEMA"] = f"{_scos_db}.{clone_schema}" if _scos_db else clone_schema
            try:
                if _scos_db:
                    spark.sql(f"USE DATABASE {_scos_db}").collect()
                # USE SCHEMA must be fully qualified — bare schema names fail in SCOS
                schema_ref = f"{_scos_db}.{clone_schema}" if _scos_db else clone_schema
                spark.sql(f"USE SCHEMA {schema_ref}").collect()
            except Exception as exc:
                # If the clone schema can't be selected the trial would silently
                # run against the wrong schema — make that visible.
                sys.stderr.write(
                    f"[scos_runtime] WARNING: USE SCHEMA {clone_schema!r} failed: {exc}\n"
                )
            # Also set schema on the underlying Snowpark session for table writes
            try:
                _sf_session = getattr(spark, "_session", None) or getattr(spark, "session", None)
                if _sf_session and hasattr(_sf_session, "_conn"):
                    _sf_session._conn.run_query(f'USE DATABASE "{_scos_db}"')
                    _sf_session._conn.run_query(f'USE SCHEMA "{_scos_db}"."{clone_schema}"')
            except Exception:
                pass

            # Package workload import_roots for SCOS UDF workers
            try:
                _add_import = getattr(spark, "add_import", None)
                if _add_import is None:
                    _sf_session = getattr(spark, "_session", None) or getattr(
                        spark, "session", None
                    )
                    _add_import = (
                        getattr(_sf_session, "add_import", None) if _sf_session else None
                    )
                if callable(_add_import):
                    _output_root = os.path.join(request.project_root, "Output")
                    for _root in (request.analysis or {}).get("import_roots", []) or []:
                        _abs = os.path.join(_output_root, _root)
                        if os.path.isdir(_abs):
                            try:
                                _add_import(_abs)
                            except Exception:
                                pass
            except Exception:
                pass

            # Discover seed tables from the clone (prefers the list persisted at
            # provision time; see _list_seed_tables).
            ep_id = (request.ep_config or {}).get("id", "")
            seed_tables = _list_seed_tables(request.state_json, clone_schema, ep_id)

            # File I/O env setup — resolve stage paths for file reads
            stage_root = _stage_root_for_trial(request.state_json or {}, ep_id, clone_schema)

            _tables = request.ep_config.get("tables") or {}
            file_read_paths: Dict[str, str] = {}
            for name, tbl in _tables.items():
                if tbl.get("category") != "file" or tbl.get("access", "read") == "write":
                    continue
                mock_file = tbl.get("mock_file", "")
                if not mock_file:
                    continue
                rel = mock_file.replace("\\", "/").strip("/")
                path = f"{stage_root}/inputs/{rel}" if stage_root else os.path.join(request.mock_data_dir, mock_file)
                file_read_paths[name] = path

            # File write sinks — stage path in clone; GET into local dir before capture
            sink_capture_dir = os.path.join(request.results_dir, request.trial_id, "_sinks")
            shutil.rmtree(sink_capture_dir, ignore_errors=True)
            os.makedirs(sink_capture_dir, exist_ok=True)
            file_write_paths: Dict[str, str] = {}
            file_sink_io_ids: List[str] = []
            for name, tbl in _tables.items():
                if tbl.get("category") != "file":
                    continue
                if tbl.get("access", "read") not in ("write", "readwrite"):
                    continue
                _io_id = _io_id_from_name(name).lower()
                file_write_paths[name] = _sink_stage_path(_scos_db, clone_schema, _io_id)
                file_sink_io_ids.append(_io_id)

            # Routing contract: file_io_env publishes SCOS_SINK_<UPPER_IO> env vars
            # whose VALUE is the clone-stage path above. The patch-author rewrites
            # each file-sink write to `os.environ["SCOS_SINK_<UPPER_IO>"]`, so the
            # workload writes into @<db>.<clone>.SCOS_SINKS/<lower_io>, and
            # _download_staged_sinks GETs that same prefix back into _sinks/<lower_io>.
            file_env = file_io_env(request.ep_config, read_paths=file_read_paths, write_paths=file_write_paths)
            for key, val in file_env.items():
                saved_env[key] = os.environ.get(key)
                os.environ[key] = val

            # Build context and run
            run_id = os.environ.get("SCOS_RUN_ID", uuid4().hex[:8])
            _pre_capture_hook = None
            if file_sink_io_ids:
                # Create the sink stage in the clone (connector, not spark.sql —
                # Spark Connect cannot parse CREATE STAGE) before the workload runs.
                self._create_sink_stage(request.state_json, _scos_db, clone_schema)

                def _pre_capture_hook(
                    _state=request.state_json,
                    _db=_scos_db,
                    _schema=clone_schema,
                    _ids=list(file_sink_io_ids),
                    _dir=sink_capture_dir,
                ):
                    self._download_staged_sinks(_state, _db, _schema, _ids, _dir)
            ctx = TrialContext(
                trial_id=request.trial_id,
                flavor="scos",
                output_schema=clone_schema,
                results_dir=request.results_dir,
                seed_tables=seed_tables,
                sink_tables=sink_tables,
                run_id=run_id,
                sink_capture_dir=sink_capture_dir,
                pre_capture_hook=_pre_capture_hook,
            )

            manifest = run_and_capture(spark, request, ctx)

            return TrialResult(
                trial_id=request.trial_id,
                flavor="scos",
                results_dir=request.results_dir,
                ok=manifest["ok"],
                manifest=manifest,
                output_schema=clone_schema,
                error=manifest.get("error"),
            )
        finally:
            if clone_ctx is not None:
                clone_ctx.__exit__(None, None, None)
            if spark is not None and hasattr(spark, "stop"):
                spark.stop()
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
