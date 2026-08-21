"""Test helpers for SCOS validation.

The code under test is rewritten by the patch blueprint (see
``scripts/patch_engine.py``) so that non-Spark I/O becomes native
``spark.read``/``spark.write`` against paths held in
``SCOS_INPUT_<id>`` / ``SCOS_TEST_AUX_<name>`` / ``SCOS_SINK_<id>`` env vars.
:func:`file_io_env` builds those env vars from caller-resolved paths; each
runtime resolves its own flavor-appropriate paths before calling it.

Exports:

    capture_results(session, output_schema, output_dir, sink_capture_dir, *,
                    exclude=())
        Call AFTER the workload exits. Captures everything the workload
        produced from two complementary sources:
          1. Catalog tables in *output_schema* (saveAsTable / insertInto
             writes) — dumped as Parquet via spark.table().
          2. Files under *sink_capture_dir* (path-form writes to a SCOS_SINK_<id>
             capture directory) — copied into the manifest as-is when already
             Parquet, or normalized.
        Writes ``<output_dir>/tables/<name>.parquet`` and a manifest at
        ``<output_dir>/_index.json``. Returns the manifest dict.
    intercept_session(session)
        Patches getOrCreate to return the provided session.
    file_io_env(ep_config, *, read_paths, write_paths=None)
        Builds SCOS_INPUT_*/SCOS_SINK_* env vars from caller-resolved paths.
        Naming only — path resolution is the caller's responsibility.
    expected_env_vars(entrypoint_config)
        Maps each declared source/sink name to the env var(s) the harness sets.
    build_script_argv(entrypoint_config, entrypoint_path)
        Builds ``sys.argv`` for script entrypoints that use ``argparse`` in
        ``__main__``.
    seed_entrypoint(session, ep_config, mock_data_dir, *, output_schema)
        Seeds table/connector sources and precreates empty sink tables per
        the new sources/sinks DICT contract; returns pre-created table names.
    declared_sink_tables(entrypoint_config, output_schema)
        Returns the table sinks that should remain visible in snapshots,
        even if they were pre-created before the workload ran.
    install_delta_patches(spark)
        Phase A idempotency patches (saveAsTable overwrite, DELETE/INSERT
        tolerance). No-op in Phase B.
    clone_golden_schema_for_trial(state_json, ep_id)
        Context manager. ``CREATE OR REPLACE SCHEMA <golden>_T<8-hex>
        CLONE <golden>`` (metadata-only); yields the clone name; drops
        on teardown. Phase B per-trial isolation.
    compare_results(phase_a_dir, phase_b_dir, comparator_path)
        Walks tables/ subdirs, pairs by name, diffs each pair via
        comparator.py.
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime as _dt
import hashlib
import importlib.util
import json
import os
import pathlib as _pathlib
import re as _re
import shutil
import sys
from typing import Any, Dict, Iterable, List, Optional
from unittest import mock


class SchemaCollisionError(Exception):
    """Raised when >=2 table-keys collapse to the same _declared_table_name."""
    def __init__(self, ep_id: str, collisions: dict):
        self.ep_id = ep_id
        self.collisions = collisions
        detail = "; ".join(
            "%s <- %s" % (sf_name, ", ".join(sorted(keys)))
            for sf_name, keys in sorted(collisions.items())
        )
        super().__init__(
            "entrypoint %s declares colliding table names (%s) — rename one table "
            "file so the names differ by more than case" % (ep_id, detail)
        )


# ---------------------------------------------------------------------------
# Stable content-hash for table entries (used by datagen and provision)
# ---------------------------------------------------------------------------

def schema_hash(table_entry: dict) -> str:
    """SHA-256 hex digest of the canonical JSON for fields affecting DDL/generation."""
    canonical = _canonical_payload(table_entry)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_payload(table_entry: dict) -> str:
    """Deterministic JSON string of the hash-relevant fields."""
    columns = []
    for col in table_entry.get("columns") or []:
        entry = {
            "name": col.get("name", ""),
            "type": col.get("type", "string"),
            "nullable": col.get("nullable", True),
        }
        # the enum domain feeds generation (categorical / pool), so a change to it
        # must reseed even when the column is not part of a cross-table pool.
        if col.get("values") is not None:
            entry["values"] = col["values"]
        columns.append(entry)
    columns.sort(key=lambda c: c["name"])

    payload = {
        "columns": columns,
        "access": table_entry.get("access", "read"),
        "category": table_entry.get("category", "table"),
        "format": table_entry.get("format", ""),
        "reader_options": table_entry.get("reader_options") or {},
        "pool_sig": table_entry.get("pool_sig") or {},
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Per-entrypoint directory layout helpers (pure; no datagen import)
# ---------------------------------------------------------------------------

def split_entrypoint(ep: dict) -> tuple:
    """Split an entrypoint dict into (meta, tables) for the directory layout.

    meta = shallow copy without ``tables``; tables = ep.get("tables") or {}.
    """
    meta = {k: v for k, v in ep.items() if k != "tables"}
    tables = ep.get("tables") or {}
    return meta, tables


def merge_entrypoint(meta: dict, tables: dict) -> dict:
    """Merge meta + tables back into a full entrypoint dict."""
    result = dict(meta)
    result["tables"] = tables
    return result


_TABLE_UNSAFE_RE = _re.compile(r'[/\\:*?"<>|\s]')


def _table_filename(key: str, used: set) -> str:
    """Return a sanitized, collision-free filename stem (no .json extension).

    Replaces unsafe filesystem chars with ``_``; appends ``_2``, ``_3``... on
    collision.  Mutates *used* to track the chosen name.
    """
    sanitized = _TABLE_UNSAFE_RE.sub("_", key)
    if not sanitized:
        sanitized = "_empty_"
    candidate = sanitized
    n = 2
    while candidate in used:
        candidate = "%s_%d" % (sanitized, n)
        n += 1
    used.add(candidate)
    return candidate


def load_entrypoint(schemas_dir, ep_id: str) -> dict:
    """Load a single entrypoint from the per-entrypoint directory layout.

    Reads ``entrypoints/<id>/_meta.json`` and each
    ``entrypoints/<id>/tables/*.json``, popping ``_table_key`` from each
    table file to reconstruct the tables dict.  Returns the fully merged
    entrypoint dict (``_meta`` fields plus the ``tables`` dict).

    Detects collisions: ≥2 table-keys whose _declared_table_name collapse to
    the same name (e.g., "Table_A" and "table_a" both -> "table_a").

    Args:
        schemas_dir: Path to schemas directory
        ep_id: Entrypoint ID

    Returns:
        Fully merged entrypoint dict (``_meta`` fields plus ``tables``).

    Raises:
        FileNotFoundError if the directory or _meta.json is absent
        SchemaCollisionError if table-name collisions are detected
    """
    schemas_path = _pathlib.Path(schemas_dir)
    ep_dir = schemas_path / "entrypoints" / ep_id
    meta_path = ep_dir / "_meta.json"
    if not ep_dir.is_dir():
        raise FileNotFoundError("Entrypoint directory not found: %s" % ep_dir)
    if not meta_path.is_file():
        raise FileNotFoundError("Entrypoint _meta.json not found: %s" % meta_path)
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    tables: dict = {}
    tables_dir = ep_dir / "tables"
    if tables_dir.is_dir():
        for p in sorted(tables_dir.glob("*.json")):
            with open(p, encoding="utf-8") as f:
                tbl = json.load(f)
            # Fall back to the filename stem when _table_key is absent (e.g. an
            # agent hand-created a table file) so the table is not silently lost.
            table_key = tbl.pop("_table_key", p.stem)
            tables[table_key] = tbl

    # Detect collisions: ≥2 keys that collapse to same _declared_table_name
    name_to_keys: dict[str, list] = {}
    for table_key, tbl in tables.items():
        declared_name = _declared_table_name(table_key, tbl)
        name_to_keys.setdefault(declared_name, []).append(table_key)

    collisions = {name: keys for name, keys in name_to_keys.items() if len(keys) > 1}

    if collisions:
        raise SchemaCollisionError(ep_id, collisions)

    return merge_entrypoint(meta, tables)


# ---------------------------------------------------------------------------
# Assemble analysis from shared/schemas/ (canonical source)
# ---------------------------------------------------------------------------

def assemble_analysis(schemas_dir: str) -> dict:
    """Build the analysis dict from shared/schemas/ without importing datagen.

    datagen is the canonical WRITER of schemas/; this function is the
    kit-resident READER so that conftest/driver need not depend on datagen.

    Returns {"entrypoints": [...], "import_roots": sorted-list}.
    """
    schemas_path = _pathlib.Path(schemas_dir)
    manifest_path = schemas_path / "manifest.json"

    if manifest_path.is_file():
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        eps = []
        for ref in manifest.get("entrypoints") or []:
            ep_id = ref.get("id")
            if not ep_id:
                raise ValueError(
                    f"manifest entrypoint entry missing 'id': {ref!r} "
                    f"(schemas dir: {schemas_path}). The manifest must use the "
                    f"directory layout ({{'id','path','dir'}} refs); re-run "
                    f"schema_mine to regenerate it."
                )
            eps.append(load_entrypoint(schemas_path, ep_id))
        auxiliary_files = manifest.get("auxiliary_files") or []
    else:
        # Fallback: glob entrypoints/*/ DIRECTORIES when manifest is missing.
        ep_dir = schemas_path / "entrypoints"
        eps = []
        if ep_dir.is_dir():
            for d in sorted(p for p in ep_dir.iterdir() if p.is_dir()):
                try:
                    eps.append(load_entrypoint(schemas_path, d.name))
                except (FileNotFoundError, json.JSONDecodeError):
                    pass
        auxiliary_files = []

    import_roots: set = set()
    for ep in eps:
        for root in ep.get("import_roots") or []:
            import_roots.add(root)

    return {
        "entrypoints": eps,
        "import_roots": sorted(import_roots),
        "auxiliary_files": auxiliary_files,
    }

def _bare_table_name(raw: str) -> str:
    """Bare (last dot-separated, lowercased) table name. Returns "" for file
    URIs (``s3://...``, ``/dbfs/...``) — those are file sources, not tables."""
    if not raw:
        return ""
    if "://" in raw or raw.startswith("/"):
        return ""
    parts = [p for p in raw.replace("`", "").split(".") if p]
    return parts[-1].lower() if parts else ""


def _declared_table_name(tbl_name: str, tbl: Dict[str, Any]) -> str:
    """Catalog table name for a declared source or sink."""
    import re as _re
    def _is_valid_ident(s: str) -> bool:
        return bool(s) and bool(_re.match(r"^[A-Za-z0-9_]+$", s))

    bare = _bare_table_name(tbl.get("original_path", ""))
    if bare and _is_valid_ident(bare):
        return bare
    # original_path-derived name is invalid (e.g. "*/csv/" from glob paths) — fall
    # back to the explicit schema-file key which is always a clean identifier.
    bare = _bare_table_name(tbl_name or "")
    if bare and _is_valid_ident(bare):
        return bare
    return str(tbl_name).strip().lower()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SPARK_TYPE_MAP = {
    "string": "StringType", "varchar": "StringType", "text": "StringType",
    "char": "StringType",
    "int": "IntegerType", "integer": "IntegerType",
    "long": "LongType", "bigint": "LongType",
    "short": "ShortType", "smallint": "ShortType",
    "byte": "ByteType", "tinyint": "ByteType",
    "float": "FloatType", "double": "DoubleType", "real": "DoubleType",
    "decimal": "DecimalType", "numeric": "DecimalType",
    "boolean": "BooleanType", "bool": "BooleanType",
    "date": "DateType", "timestamp": "TimestampType",
    "timestamp_ntz": "TimestampNTZType", "timestamp_ltz": "TimestampType",
    "binary": "BinaryType",
}


# ---------------------------------------------------------------------------
# Events log
# ---------------------------------------------------------------------------

def _append_event(conv_root: str, event: Dict[str, Any]) -> None:
    """Append a structured event to ``Validation/events.jsonl``."""
    event = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"), **event}
    p = _pathlib.Path(conv_root) / "Validation" / "events.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(event, default=str) + "\n")


def _conv_root_from_output_dir(output_dir: str) -> Optional[str]:
    """Derive conv_root by walking up from output_dir to find Validation/."""
    d = os.path.abspath(output_dir)
    for _ in range(10):
        parent = os.path.dirname(d)
        if os.path.basename(d) == "Validation":
            return parent
        if parent == d:
            break
        d = parent
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _slugify(target: str, max_len: int = 128) -> str:
    """Deterministic, host-qualified slug from a write target path.

    Strip scheme, replace ``/`` and ``.`` with ``__``, collapse runs,
    lowercase, truncate.
    """
    s = _re.sub(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", "", str(target))
    s = _re.sub(r"[/.]", "__", s)
    s = _re.sub(r"[^a-zA-Z0-9_]", "_", s)
    s = _re.sub(r"_{2,}", "__", s)
    s = s.strip("_").lower()
    return s[:max_len] if s else "unknown"


def _dump_pandas(pdf, parquet_path: str) -> bool:
    try:
        pdf.to_parquet(parquet_path, index=False)
        return True
    except Exception:
        return False


def _read_tabular_output(path: str):
    import pandas as pd

    def _visible_files(directory: str) -> List[str]:
        files = []
        for name in sorted(os.listdir(directory)):
            if name.startswith("_") or name.startswith("."):
                continue
            full = os.path.join(directory, name)
            if os.path.isfile(full):
                files.append(full)
        return files

    if os.path.isdir(path):
        files = _visible_files(path)
        if not files:
            return None, None
        ext_hint = os.path.splitext(path)[1].lower()
        if ext_hint == ".csv" or any(f.lower().endswith(".csv") for f in files):
            pdf = pd.concat((pd.read_csv(f) for f in files), ignore_index=True)
            return pdf, "csv"
        if ext_hint == ".json" or any(f.lower().endswith(".json") for f in files):
            pdf = pd.concat((pd.read_json(f, lines=True) for f in files), ignore_index=True)
            return pdf, "json"
        if ext_hint in (".txt", ".text") or any(
            f.lower().endswith((".txt", ".text")) for f in files
        ):
            rows = []
            for file_path in files:
                with open(file_path, encoding="utf-8") as handle:
                    rows.extend({"value": line.rstrip("\n")} for line in handle)
            return pd.DataFrame(rows), "text"
        try:
            return pd.read_parquet(path), "parquet"
        except Exception:
            pass
        try:
            pdf = pd.concat((pd.read_json(f, lines=True) for f in files), ignore_index=True)
            return pdf, "json"
        except Exception:
            pass
        try:
            pdf = pd.concat((pd.read_csv(f) for f in files), ignore_index=True)
            return pdf, "csv"
        except Exception:
            pass
        rows = []
        for file_path in files:
            with open(file_path, encoding="utf-8") as handle:
                rows.extend({"value": line.rstrip("\n")} for line in handle)
        return pd.DataFrame(rows), "text"

    ext = os.path.splitext(path)[1].lower()
    if ext == ".parquet":
        return pd.read_parquet(path), "parquet"
    if ext == ".csv":
        return pd.read_csv(path), "csv"
    if ext == ".json":
        return pd.read_json(path, lines=True), "json"
    if ext in (".txt", ".text", ".log"):
        with open(path, encoding="utf-8") as handle:
            rows = [{"value": line.rstrip("\n")} for line in handle]
        return pd.DataFrame(rows), "text"
    return None, None


def _resolve_spark_type(type_str: str):
    from pyspark.sql import types as T

    base = type_str.lower().split("(")[0].strip()
    # Accept PySpark class-name forms (``"LongType"`` / ``"StringType"``)
    # in addition to SQL keywords. Data-synthesizer-emitted schemas occasionally
    # use the class name directly.
    if base.endswith("type") and base not in _SPARK_TYPE_MAP:
        _stripped = base[:-4]
        if _stripped in _SPARK_TYPE_MAP:
            base = _stripped
    cls_name = _SPARK_TYPE_MAP.get(base)
    if cls_name is None:
        # Last-resort: try to honor an explicit ``XxxType`` class name
        # (e.g. ``MapType``, ``ArrayType`` with no parametrization in the
        # schema record). Falls through to StringType when the class
        # isn't a no-arg constructible PySpark type.
        cand = getattr(T, type_str.split("(")[0].strip(), None)
        if cand is not None:
            try:
                return cand()
            except Exception:
                pass
        return T.StringType()
    cls = getattr(T, cls_name, T.StringType)
    if cls_name == "DecimalType":
        m = _re.search(r"\((\d+)\s*,\s*(\d+)\)", type_str)
        if m:
            return cls(int(m.group(1)), int(m.group(2)))
        return cls(38, 18)
    return cls()


def _build_spark_schema(schema_fields: list):
    from pyspark.sql.types import StructType, StructField

    def _resolve_field(c):
        # If a field carries nested ``fields`` (dict-of-struct shape),
        # recurse to build a StructType. Otherwise resolve scalar.
        nested = c.get("fields")
        if isinstance(nested, list) and nested:
            return _build_spark_schema(nested)
        return _resolve_spark_type(c.get("type", "string"))

    return StructType([
        StructField(
            c["name"],
            _resolve_field(c),
            c.get("nullable", True),
        )
        for c in schema_fields
    ])


# ---------------------------------------------------------------------------
# Delta idempotency patches (Phase A only)
# ---------------------------------------------------------------------------

_DELTA_PATCHES_INSTALLED = False

_UNCONDITIONAL_DELETE_RE = _re.compile(
    r"^\s*DELETE\s+FROM\s+([^\s;]+)\s*;?\s*$", _re.IGNORECASE
)


def install_delta_patches(spark):
    """Phase A idempotency patches for source workloads.

    - ``saveAsTable(..., mode="overwrite")`` → ``DROP TABLE IF EXISTS`` first.
    - ``DELETE FROM t`` with no ``WHERE`` → ``DELETE FROM t WHERE 1=1``.
    - ``DELETE FROM`` a missing table → warning + empty DataFrame.
    - ``INSERT INTO`` a missing table → ``CREATE TABLE ... AS SELECT``.

    Idempotent: safe to call multiple times. Skipped when
    ``SPARK_CONNECT_MODE_ENABLED`` is set (Phase B uses SCOS semantics).
    """
    global _DELTA_PATCHES_INSTALLED
    if _DELTA_PATCHES_INSTALLED:
        return
    if os.environ.get("SPARK_CONNECT_MODE_ENABLED"):
        return
    _DELTA_PATCHES_INSTALLED = True

    from pyspark.sql.readwriter import DataFrameWriter

    # Intercept .mode() so chained writes (df.write.mode("overwrite").saveAsTable)
    # expose the mode to _safe_save_as_table. PySpark stores mode on the Java
    # writer; calling _jwrite.mode() without args returns the builder, not a string.
    _orig_mode = DataFrameWriter.mode

    def _tracking_mode(self, saveMode=None):
        if saveMode is not None:
            self._scos_mode = (
                saveMode.lower() if isinstance(saveMode, str) else saveMode
            )
        return _orig_mode(self, saveMode)

    DataFrameWriter.mode = _tracking_mode

    orig_save_as_table = DataFrameWriter.saveAsTable

    def _safe_save_as_table(
        self, name, format=None, mode=None, partitionBy=None, **options
    ):
        _mode = mode
        if _mode is None:
            _mode = getattr(self, "_scos_mode", None)
        if _mode == "overwrite":
            spark.sql(f"DROP TABLE IF EXISTS {name}")
            mode = "errorifexists"
        return orig_save_as_table(
            self, name, format=format, mode=mode, partitionBy=partitionBy, **options
        )

    DataFrameWriter.saveAsTable = _safe_save_as_table

    _DELETE_RE = _re.compile(r"^\s*DELETE\s+FROM\s+([^\s;]+)", _re.IGNORECASE)
    _INSERT_INTO_RE = _re.compile(
        r"^\s*INSERT\s+INTO\s+([^\s(]+)\s+(SELECT\s+.+)", _re.IGNORECASE | _re.DOTALL
    )

    orig_sql = spark.sql

    def _patched_sql(query, *args, **kwargs):
        if isinstance(query, str):
            match = _UNCONDITIONAL_DELETE_RE.match(query)
            if match:
                query = f"DELETE FROM {match.group(1)} WHERE 1=1"
        try:
            return orig_sql(query, *args, **kwargs)
        except Exception as exc:
            if not isinstance(query, str):
                raise
            err = str(exc).lower()
            if "table_or_view_not_found" not in err and "table or view not found" not in err:
                raise
            m_del = _DELETE_RE.match(query)
            if m_del:
                print(
                    f"warn: delta-patch: DELETE FROM {m_del.group(1)} skipped (table missing)",
                    file=sys.stderr,
                )
                return spark.createDataFrame([], "a: string")
            m_ins = _INSERT_INTO_RE.match(query)
            if m_ins:
                table, select = m_ins.group(1), m_ins.group(2)
                create_sql = f"CREATE TABLE {table} USING DELTA AS {select}"
                print(
                    f"warn: delta-patch: INSERT INTO rewritten to CREATE TABLE for {table}",
                    file=sys.stderr,
                )
                return orig_sql(create_sql, *args, **kwargs)
            raise

    spark.sql = _patched_sql


# ---------------------------------------------------------------------------
# Schema + filesystem readback
# ---------------------------------------------------------------------------

def _catalog_table_ref(output_schema: str, table_name: str) -> str:
    """Fully-qualified table name for ``session.table()``."""
    db = os.environ.get("SCOS_DATABASE_NAME", "").strip()
    if db:
        return f"{db}.{output_schema}.{table_name}"
    return f"{output_schema}.{table_name}"


def _show_tables_sql(output_schema: str) -> str:
    """Spark SQL to list tables in *output_schema*.

    Local Spark metastore is single-level (``SHOW TABLES IN <schema>``).
    Snowflake via SCOS needs ``SHOW TABLES IN <database>.<schema>`` because
    ``USE DATABASE`` / ``USE SCHEMA`` are unreliable on the connect session.
    """
    db = os.environ.get("SCOS_DATABASE_NAME", "").strip()
    if db:
        return f"SHOW TABLES IN {db}.{output_schema}"
    return f"SHOW TABLES IN {output_schema}"


def _table_name_from_show_row(row) -> Optional[str]:
    """Extract a table name from a ``SHOW TABLES`` result row."""
    try:
        d = row.asDict(recursive=True)
    except Exception:
        d = {}
    name = d.get("tableName") or d.get("name")
    if name:
        return str(name)
    if len(row) > 1 and row[1]:
        return str(row[1])
    if row[0]:
        return str(row[0])
    return None


def _list_catalog_tables(session, output_schema: str) -> list[str]:
    """Table names in *output_schema* via ``session.sql`` (both phases)."""
    try:
        rows = session.sql(_show_tables_sql(output_schema)).collect()
    except Exception as exc:
        sys.stderr.write(
            f"warn: capture_results: list tables failed: {exc}\n"
        )
        return []
    names: list[str] = []
    for row in rows:
        # Skip temp views (createOrReplaceTempView): SHOW TABLES lists them with
        # isTemporary=True, but they are not catalog tables — trying to snapshot
        # one via spark.table(schema.view) then fails. Common in notebooks.
        try:
            if row.asDict(recursive=True).get("isTemporary"):
                continue
        except Exception:
            pass
        name = _table_name_from_show_row(row)
        if name:
            names.append(name.lower())
    return names


# Concurrency for catalog-table capture. Each table read (count + dump) is
# independent, and the Spark/SCOS session tolerates concurrent action
# submission, so a `saveAsTable`-heavy trial dumps its output tables in parallel
# instead of one-at-a-time. Set to 1 to force serial. Mirrors SCOS_GET_WORKERS
# (which parallelizes staged-sink file GETs) but is independently tunable.
_TABLE_CAPTURE_WORKERS = int(os.environ.get("SCOS_TABLE_CAPTURE_WORKERS", "8"))


def _run_table_captures(names, fn):
    """Run per-table capture ``fn`` over ``names`` — parallel when it pays.

    ``fn`` must catch its own per-table exceptions and return a
    ``(status, payload)`` tuple where status is ``'ok'`` / ``'fail'`` / ``'skip'``,
    so worker threads never raise.

    Concurrency safety is handled two ways rather than trusting the session
    blindly:
      - a **pool-level** error (executor/submit failure) falls back to a full
        serial run;
      - any per-table ``'fail'`` from the parallel pass is **re-run serially**.
        A genuine error stays failed; a failure that was only a concurrency
        artifact clears on the serial retry. This keeps the recorded baseline
        from silently absorbing a concurrency-induced miss.
    """
    if len(names) <= 1 or _TABLE_CAPTURE_WORKERS <= 1:
        return [fn(n) for n in names]
    from concurrent.futures import ThreadPoolExecutor, as_completed
    try:
        by_name: dict = {}
        with ThreadPoolExecutor(max_workers=min(_TABLE_CAPTURE_WORKERS, len(names))) as pool:
            futures = {pool.submit(fn, n): n for n in names}
            for fut in as_completed(futures):
                by_name[futures[fut]] = fut.result()
    except Exception as exc:  # noqa: BLE001 — pool-level failure → full serial retry
        sys.stderr.write(
            f"warn: parallel table capture failed ({exc}); retrying serially\n"
        )
        return [fn(n) for n in names]
    # Re-run tables that failed under concurrency, serially.
    for name in names:
        if by_name.get(name, ("fail", None))[0] == "fail":
            by_name[name] = fn(name)
    return [by_name[n] for n in names]


def capture_results(
    session,
    output_schema: str,
    output_dir: str,
    sink_capture_dir: Optional[str] = None,
    *,
    exclude: Iterable[str] = (),
    exclude_if_empty: Iterable[str] = (),
) -> Dict[str, Any]:
    """Capture everything the workload wrote, from both:

    1. Catalog tables in *output_schema* (the workload's ``saveAsTable``
       / ``insertInto`` writes). Dumped as Parquet via
       ``spark.table().write.parquet(...)``.
    2. Files under *sink_capture_dir* (path-form writes the workload sent to a
       ``SCOS_SINK_<id>`` capture directory). For Phase A, these are real
       Parquet/CSV/JSON files written by the workload; for Phase B, writes go
       to the Snowflake clone schema instead.

    Output layout::

        <output_dir>/tables/<name>.parquet  (one Parquet per logical table —
                                             from catalog AND from sink-capture
                                             path-form writes, slugified)
        <output_dir>/_index.json            (manifest)

    `exclude`: lowercased table names to skip (typically the seeds).
    `exclude_if_empty`: declared sink names explicitly allowed to be empty.

    Returns the manifest dict. Skips transient ``snowpark_temp_*`` tables.
    """
    excluded = {t.lower() for t in exclude}
    excluded |= {t.split(".")[-1].lower() for t in exclude}
    exclude_if_empty_set = {t.lower() for t in exclude_if_empty}
    exclude_if_empty_set |= {t.split(".")[-1].lower() for t in exclude_if_empty}

    tables_dir = os.path.join(output_dir, "tables")
    artifacts_dir = os.path.join(output_dir, "artifacts")
    os.makedirs(tables_dir, exist_ok=True)
    os.makedirs(artifacts_dir, exist_ok=True)

    captured_tables: List[Dict[str, Any]] = []
    captured_artifacts: List[Dict[str, Any]] = []
    failures: List[Dict[str, str]] = []

    def _write_index() -> Dict[str, Any]:
        """Build and persist _index.json; always called, even on partial capture."""
        trial_id = os.path.basename(os.path.normpath(output_dir))
        phase_dir_name = os.path.basename(os.path.dirname(os.path.normpath(output_dir)))
        phase = phase_dir_name if phase_dir_name in ("phase_a", "phase_b") else "unknown"

        for entry in captured_tables:
            entry.setdefault("absolute_path", os.path.abspath(
                os.path.join(output_dir, entry["path"])
            ))

        manifest = {
            "trial_id": trial_id,
            "phase": phase,
            "output_schema": output_schema,
            "captured_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "tables": captured_tables,
            "artifacts": captured_artifacts,
            "failures": failures,
        }

        try:
            final_path = os.path.join(output_dir, "_index.json")
            tmp_path = final_path + ".tmp"
            payload = json.dumps(manifest, indent=2)
            with open(tmp_path, "w") as f:
                f.write(payload)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp_path, final_path)
        except Exception:
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except Exception:
                pass

        conv_root = _conv_root_from_output_dir(output_dir)
        if conv_root:
            _append_event(conv_root, {
                "kind": "capture_completed",
                "trial_id": trial_id,
                "phase": phase,
                "tables_captured": len(captured_tables),
                "failures": len(failures),
            })

        return manifest

    try:
        # 1. Catalog tables — captured concurrently (independent reads).
        catalog_names: List[str] = []
        for table_name in _list_catalog_tables(session, output_schema):
            qualified = f"{output_schema}.{table_name}".lower()
            if table_name in excluded or qualified in excluded:
                continue
            if _re.match(r"^snowpark_temp_", table_name, _re.IGNORECASE):
                continue
            catalog_names.append(table_name)

        def _capture_one_table(table_name: str):
            """Capture one catalog table. Returns a (status, payload) tuple.

            Catches its own exceptions so it is safe to run in a worker thread:
            ('ok', entry) | ('fail', failure) | ('skip', None).
            """
            qualified = f"{output_schema}.{table_name}".lower()
            out_path = os.path.join(tables_dir, f"{table_name}.parquet")
            try:
                df = session.table(_catalog_table_ref(output_schema, table_name))
                # Single data round-trip: toPandas() also gives us the row count
                # (len(pdf)), so we don't issue a separate df.count() query.
                pdf = df.toPandas()
                pdf.attrs = {}  # strip Databricks Connect PlanMetrics before serialization
                row_count = len(pdf)
                if (
                    row_count == 0
                    and (table_name in exclude_if_empty_set or qualified in exclude_if_empty_set)
                ):
                    sys.stderr.write(
                        f"warn: capture_results: skipped allow_empty sink "
                        f"{table_name} in {output_schema}\n"
                    )
                    return ("skip", None)
                if not _dump_pandas(pdf, out_path):
                    return ("fail", {"source": "catalog", "name": table_name,
                                     "reason": "dump_failed"})
                return ("ok", {
                    "name": table_name,
                    "path": os.path.relpath(out_path, output_dir),
                    "schema_json": df.schema.json(),
                    "row_count": row_count,
                })
            except Exception as exc:
                return ("fail", {"source": "catalog", "name": table_name,
                                 "reason": str(exc)[:200]})

        for status, payload in _run_table_captures(catalog_names, _capture_one_table):
            if status == "ok":
                captured_tables.append(payload)
            elif status == "fail":
                failures.append(payload)
        # Deterministic order regardless of thread completion order (catalog is
        # the first section, so these lists hold only catalog entries here).
        captured_tables.sort(key=lambda e: e["name"])
        failures.sort(key=lambda f: f.get("name", ""))

        # 2. Filesystem outputs under the per-trial sink-capture dir (path-form
        #    writes the workload sent to SCOS_SINK_<id>).
        try:
            start_ts = float(os.environ.get("SCOS_TRIAL_START_TS", "0") or 0)
        except ValueError:
            start_ts = 0

        # Filesystem sinks are keyed by their io_id only (the first path
        # component of *rel*, since the sink env var maps to
        # sink_capture_dir/<io_id>). This matches how the Databricks runtime
        # names file sinks (bare io_id) so Phase A and Phase B captures of the
        # same sink land under an identical table name and compare cleanly.
        candidate_dirs = [sink_capture_dir]
        seen_roots: set[str] = set()
        seen_files: set[str] = set()
        for base_dir in candidate_dirs:
            if not base_dir or not os.path.isdir(base_dir):
                continue
            for root, _dirs, fnames in os.walk(base_dir):
                part_files = [
                    os.path.join(root, name)
                    for name in fnames
                    if name.startswith("part-")
                ]
                fresh_parts = [
                    path for path in part_files
                    if not start_ts or os.path.getmtime(path) >= start_ts
                ]
                if fresh_parts and root not in seen_roots:
                    seen_roots.add(root)
                    rel = os.path.relpath(root, base_dir)
                    slug = _slugify(rel)
                    root_capture_name = _slugify(rel.split(os.sep, 1)[0])
                    out_path = os.path.join(tables_dir, f"{slug}.parquet")
                    try:
                        pdf, fmt = _read_tabular_output(root)
                        if (
                            pdf is not None
                            and len(pdf.index) == 0
                            and (
                                slug.lower() in exclude_if_empty_set
                                or root_capture_name.lower() in exclude_if_empty_set
                            )
                        ):
                            sys.stderr.write(
                                f"warn: capture_results: skipped allow_empty sink "
                                f"{slug} in {output_schema}\n"
                            )
                            continue
                        if pdf is not None and _dump_pandas(pdf, out_path):
                            captured_tables.append({
                                "name": slug,
                                "path": os.path.relpath(out_path, output_dir),
                                "schema_json": json.dumps(
                                    [{"name": str(col), "type": str(dtype)} for col, dtype in pdf.dtypes.items()]
                                ),
                                "row_count": len(pdf.index),
                                "source": "filesystem",
                                "rel_path": rel,
                                "format": fmt,
                            })
                        else:
                            failures.append({"source": "filesystem", "name": slug, "reason": "dump_failed"})
                    except Exception as exc:
                        failures.append({"source": "filesystem", "name": slug, "reason": str(exc)[:200]})
                    continue

                for name in fnames:
                    if name.startswith("_") or name.startswith(".") or name.startswith("part-"):
                        continue
                    src_path = os.path.join(root, name)
                    if src_path in seen_files:
                        continue
                    try:
                        if start_ts and os.path.getmtime(src_path) < start_ts:
                            continue
                    except OSError:
                        continue
                    seen_files.add(src_path)
                    rel = os.path.relpath(src_path, base_dir)
                    slug = _slugify(rel)
                    out_path = os.path.join(tables_dir, f"{slug}.parquet")
                    try:
                        pdf, fmt = _read_tabular_output(src_path)
                        if pdf is not None and _dump_pandas(pdf, out_path):
                            captured_tables.append({
                                "name": slug,
                                "path": os.path.relpath(out_path, output_dir),
                                "schema_json": json.dumps(
                                    [{"name": str(col), "type": str(dtype)} for col, dtype in pdf.dtypes.items()]
                                ),
                                "row_count": len(pdf.index),
                                "source": "filesystem",
                                "rel_path": rel,
                                "format": fmt,
                            })
                            continue
                    except Exception as exc:
                        failures.append({"source": "filesystem", "name": slug, "reason": str(exc)[:200]})
                        continue

                    artifact_copy = os.path.join(artifacts_dir, f"{slug}{os.path.splitext(name)[1]}")
                    try:
                        shutil.copy2(src_path, artifact_copy)
                        captured_artifacts.append({
                            "name": slug,
                            "path": os.path.relpath(artifact_copy, output_dir),
                            "source": "filesystem",
                            "rel_path": rel,
                        })
                    except Exception as exc:
                        failures.append({"source": "filesystem", "name": slug, "reason": str(exc)[:200]})
    except Exception:
        pass
    return _write_index()


# ---------------------------------------------------------------------------
# intercept_session
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def intercept_session(session):
    """Patch ``SparkSession.builder.getOrCreate`` to return *session*.

    Also neutralizes ``stop()`` on the injected session's own class for the
    duration of the workload. The harness owns the session lifecycle: the session
    must still be alive when the workload returns so ``capture_results`` can read
    the declared sinks. Standalone scripts very often end with
    ``finally: spark.stop()``, which otherwise leaves capture with a dead session —
    surfacing unhelpfully as "'NoneType' object has no attribute 'setCallSite'"
    and then "Declared sink '<name>' produced no captured rows". The runtime's own
    teardown runs after this context exits, so real cleanup still happens.

    ``type(session)`` is used rather than a fixed import because Phase A gets a
    classic ``pyspark.sql.SparkSession`` while Phase B gets a Spark Connect
    session class; patching one would silently miss the other.
    """
    with mock.patch(
        "pyspark.sql.SparkSession.builder.getOrCreate",
        side_effect=lambda: session,
    ), mock.patch.object(type(session), "stop", lambda self: None):
        yield session


def _write_diff_payload(diffs_dir: str, name: str, result: Dict[str, Any]) -> None:
    os.makedirs(diffs_dir, exist_ok=True)
    with open(os.path.join(diffs_dir, f"{name}.json"), "w") as f:
        json.dump(result, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# compare_results
# ---------------------------------------------------------------------------

def compare_results(
    phase_a_dir: str,
    phase_b_dir: str,
    comparator_path: str,
    *,
    key_columns: Optional[List[str]] = None,
    ignore_columns: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Compare ``tables/`` subdirs between Phase A and Phase B results.

    Walks ``{phase_a_dir}/tables/`` and ``{phase_b_dir}/tables/`` for
    ``*.parquet`` files, pairs by table name, and runs
    ``comparator.compare()`` on each pair. Order-independent.

    Diffs are written to ``{phase_b_dir}/diffs/<table>.json`` (co-located
    with the Phase B trial output).

    Raises ``AssertionError`` on any mismatch with a structured summary.
    Returns the list of per-table comparison results.
    """
    spec = importlib.util.spec_from_file_location("_comparator", comparator_path)
    comparator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(comparator)
    schemas_dir = os.environ.get("SCOS_SCHEMAS_DIR")
    trial_id = os.path.basename(os.path.normpath(phase_b_dir))

    # Diffs are co-located inside the Phase B trial dir.
    diffs_dir = os.path.join(phase_b_dir, "diffs")

    a_tables = _find_tables(os.path.join(phase_a_dir, "tables"))
    b_tables = _find_tables(os.path.join(phase_b_dir, "tables"))
    all_names = sorted(set(a_tables) | set(b_tables))

    results: List[Dict[str, Any]] = []
    failures: List[str] = []

    conv_root = _conv_root_from_output_dir(phase_b_dir)

    for name in all_names:
        a_path = a_tables.get(name)
        b_path = b_tables.get(name)

        if a_path and not b_path:
            failures.append(f"{name}: present in Phase A but missing in Phase B")
            results.append({"table": name, "result": "missing_in_phase_b"})
            continue

        if b_path and not a_path:
            failures.append(f"{name}: present in Phase B but missing in Phase A")
            results.append({"table": name, "result": "missing_in_phase_a"})
            continue

        result = comparator.compare(
            a_path, b_path,
            key_columns=key_columns,
            ignore_columns=ignore_columns,
            key_columns_from_schemas=schemas_dir,
            expected_divergences_from_schemas=schemas_dir,
            expected_divergences_trial=trial_id,
        )
        result["table"] = name
        results.append(result)
        _write_diff_payload(diffs_dir, name, result)

        # Emit diff event.
        verdict = result.get("result", "unknown")
        if conv_root:
            _append_event(conv_root, {
                "kind": "diff_written",
                "trial_id": trial_id,
                "table": name,
                "verdict": verdict,
            })

        if result["result"] not in ("match", "match_with_skips"):
            failures.append(f"{name}: {result.get('summary', 'diverge')}")

    if failures:
        raise AssertionError(
            f"Baseline mismatch — {len(failures)} table(s) diverge:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )

    return results


def _find_tables(directory: str) -> Dict[str, str]:
    """Return {table_name: parquet_path} for ``*.parquet`` files in *directory*."""
    tables: Dict[str, str] = {}
    if not os.path.isdir(directory):
        return tables
    for entry in sorted(os.listdir(directory)):
        if entry.endswith(".parquet"):
            tables[entry[:-8]] = os.path.join(directory, entry)
    return tables


def declared_sink_tables(
    entrypoint_config: Dict[str, Any],
    output_schema: str,
) -> List[str]:
    """Return declared write/readwrite table names for ``entrypoint_config``.

    These tables are valid snapshot targets and must not be excluded just
    because the harness pre-created them before execution.

    Uses the unified ``tables`` dict with ``access`` field.
    """
    result: List[str] = []
    seen: set[str] = set()
    for tbl_name, tbl in (entrypoint_config.get("tables") or {}).items():
        access = tbl.get("access", "read")
        if access not in ("write", "readwrite"):
            continue
        category = tbl.get("category", "table")
        if category != "table":
            continue
        bare = _declared_table_name(tbl_name, tbl)
        if not bare:
            continue
        target = f"{output_schema}.{bare}".lower()
        if target in seen:
            continue
        seen.add(target)
        result.append(target)
    return result


def declared_allow_empty_sink_tables(
    entrypoint_config: Dict[str, Any],
    output_schema: str,
) -> List[str]:
    """Return declared sinks explicitly allowed to be empty.

    Table sinks use fully-qualified ``schema.table`` names so catalog capture can
    skip empty seeded tables. File sinks use their normalized io_id so
    filesystem capture can skip empty outputs too.
    """
    result: List[str] = []
    seen: set[str] = set()
    for tbl_name, tbl in (entrypoint_config.get("tables") or {}).items():
        if not str(tbl.get("allow_empty") or "").strip():
            continue
        access = tbl.get("access", "read")
        if access not in ("write", "readwrite"):
            continue
        category = tbl.get("category", "table")
        if category == "table":
            bare = _declared_table_name(tbl_name, tbl)
            if not bare:
                continue
            target = f"{output_schema}.{bare}".lower()
        else:
            target = _io_id_from_name(str(tbl_name)).lower()
            if not target:
                continue
        if target in seen:
            continue
        seen.add(target)
        result.append(target)
    return result


def declared_sink_capture_specs(entrypoint_config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Return normalized capture expectations for every declared sink."""
    result: Dict[str, Dict[str, Any]] = {}
    for tbl_name, tbl in (entrypoint_config.get("tables") or {}).items():
        access = tbl.get("access", "read")
        if access not in ("write", "readwrite"):
            continue
        category = tbl.get("category", "table")
        if category == "table":
            capture_name = _declared_table_name(tbl_name, tbl)
        else:
            capture_name = _io_id_from_name(tbl_name).lower()
        capture_name = str(capture_name or "").strip().lower()
        if not capture_name:
            continue
        result[capture_name] = {
            "capture_name": capture_name,
            "declared_name": str(tbl_name).strip() or capture_name,
            "category": category,
            "allow_empty": str(tbl.get("allow_empty") or "").strip(),
        }
    return result


def requires_nonempty_sink_capture(entrypoint_config: Dict[str, Any]) -> bool:
    """True when the entrypoint declares any sink that must capture rows."""
    return any(
        not spec.get("allow_empty")
        for spec in declared_sink_capture_specs(entrypoint_config).values()
    )


def validate_declared_sink_outputs(
    entrypoint_config: Dict[str, Any],
    manifest: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Validate declared sink outputs against the capture manifest."""
    sink_specs = declared_sink_capture_specs(entrypoint_config)
    if not sink_specs:
        return []

    def _capture_aliases(item: Dict[str, Any]) -> set[str]:
        aliases: set[str] = set()
        name = str(item.get("name") or "").strip().lower()
        if name:
            aliases.add(name)
        rel_path = str(item.get("rel_path") or "").strip().replace("\\", "/")
        if rel_path and rel_path != ".":
            aliases.add(_slugify(rel_path))
            first_component = rel_path.split("/", 1)[0].strip()
            if first_component:
                aliases.add(first_component.lower())
                aliases.add(_slugify(first_component))
        return {alias for alias in aliases if alias}

    def _matches_declared_sink(item: Dict[str, Any], capture_name: str) -> bool:
        aliases = _capture_aliases(item)
        if capture_name in aliases:
            return True
        prefix = f"{capture_name}__"
        return any(alias.startswith(prefix) for alias in aliases)

    failures: List[Dict[str, Any]] = []
    guidance = (
        "Fix the mock/schema data so the sink becomes non-empty, or set "
        "allow_empty to a short reason string if empty output is intentional."
    )
    for sink_name, spec in sink_specs.items():
        allow_empty = spec.get("allow_empty") or ""
        table_matches = [
            item for item in (manifest.get("tables") or [])
            if _matches_declared_sink(item, sink_name)
        ]
        artifact_matches = [
            item for item in (manifest.get("artifacts") or [])
            if _matches_declared_sink(item, sink_name)
        ]
        if not table_matches and not artifact_matches:
            if allow_empty:
                continue
            failures.append({
                "source": "declared_sink",
                "name": sink_name,
                "reason": "empty_declared_sink",
                "message": (
                    f"Declared sink '{sink_name}' produced no captured rows. "
                    f"{guidance}"
                ),
                "critical": True,
            })
            continue
        if artifact_matches or allow_empty:
            continue
        total_rows = sum(int(item.get("row_count") or 0) for item in table_matches)
        if total_rows == 0:
            failures.append({
                "source": "declared_sink",
                "name": sink_name,
                "reason": "empty_declared_sink",
                "message": (
                    f"Declared sink '{sink_name}' captured 0 rows. {guidance}"
                ),
                "critical": True,
            })
    return failures


def declares_any_sink(entrypoint_config: Dict[str, Any]) -> bool:
    """True when the entrypoint declares at least one write/display sink.

    Covers every sink kind the harness captures — table sinks and file/display
    sinks are all recorded in ``tables`` with ``access`` in write/readwrite. When
    this is False the entrypoint is a pure DDL/config notebook with no data to
    capture, so a clean run (no error) is itself a valid baseline.
    """
    return any(
        (tbl.get("access", "read") in ("write", "readwrite"))
        for tbl in (entrypoint_config.get("tables") or {}).values()
    )


# ---------------------------------------------------------------------------
# file_io_env — SCOS_INPUT_<id> / SCOS_SINK_<id> resolution
# ---------------------------------------------------------------------------

def _io_id_from_name(name: str) -> str:
    """Canonical env-var suffix from a source/sink name: upper-snake."""
    if name.strip() == "*":
        return "STAR"
    return _re.sub(r"[^A-Za-z0-9]+", "_", name.strip()).strip("_").upper()


def _aux_key_from_name(name: str) -> str:
    """Uppercase aux env suffix: last dot segment of the source dict key."""
    return name.split(".")[-1].strip().lower().upper()


def _kwarg_dest_token(dest: str) -> str:
    """Normalize an entrypoint_kwargs key for fuzzy source-name matching."""
    token = dest.lower().replace("_", "")
    return _re.sub(r"(filepath|path|file)$", "", token)


def resolve_kwarg_path(dest: str, ep_config: Dict[str, Any]) -> Optional[str]:
    """Resolve a kwarg dest to the harness env path for its matching table."""
    token = _kwarg_dest_token(dest)
    for name, tbl in (ep_config.get("tables") or {}).items():
        access = tbl.get("access", "read")
        if access == "write":
            continue
        name_token = name.lower().replace("_", "")
        if token and token not in name_token and name_token not in token:
            continue
        if not tbl.get("relational", True):
            key = _aux_key_from_name(name)
            return (
                os.environ.get(f"SCOS_TEST_AUX_{key}")
                or os.environ.get(f"SCOS_INPUT_{key}")
            )
        if tbl.get("category") == "file":
            sid = _io_id_from_name(name)
            return (
                os.environ.get(f"SCOS_INPUT_{sid}")
                or os.environ.get(f"SCOS_INPUT_{name}")
            )
    return None


def build_script_argv(ep_config: Dict[str, Any], entrypoint_path: str) -> Optional[List[str]]:
    """Build ``sys.argv`` for script entrypoints that call ``argparse`` in ``__main__``.

    Requires ``cli_args`` in the entrypoint schema: maps each
    ``entrypoint_kwargs`` key to a CLI flag. Values prefer resolved harness env
    paths over static schema placeholders.
    """
    kwargs = ep_config.get("entrypoint_kwargs") or {}
    cli_args = ep_config.get("cli_args") or {}
    if not kwargs or not cli_args:
        return None
    argv = [entrypoint_path]
    for dest, flag in cli_args.items():
        if dest not in kwargs or not flag:
            continue
        val = resolve_kwarg_path(dest, ep_config) or kwargs.get(dest)
        if val:
            argv.extend([flag, str(val)])
    return argv if len(argv) > 1 else None


def expected_env_vars(ep_config: Dict[str, Any]) -> Dict[str, str]:
    """Human-readable env-var names the harness sets for each declared table."""
    out: Dict[str, str] = {}
    for name, tbl in (ep_config.get("tables") or {}).items():
        category = tbl.get("category", "table")
        access = tbl.get("access", "read")
        if category != "file":
            # File-sink (write-only file) gets SCOS_SINK_*
            if access in ("write", "readwrite") and category != "table":
                out[name] = f"SCOS_SINK_{_io_id_from_name(name)}"
            continue
        if not tbl.get("relational", True):
            key = _aux_key_from_name(name)
            out[name] = f"SCOS_TEST_AUX_{key}"
        else:
            sid = _io_id_from_name(name)
            out[name] = f"SCOS_INPUT_{sid}"
    return out


def file_io_env(
    ep_config: Dict[str, Any],
    *,
    read_paths: Dict[str, str],
    write_paths: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Build SCOS_INPUT_*/SCOS_SINK_* env vars from caller-resolved paths.

    Path resolution is the caller's responsibility — this only handles naming.
    Each runtime passes its own flavor-appropriate paths.

      relational file source "core_attr"  → SCOS_INPUT_CORE_ATTR
      non-relational source "ref_file"    → SCOS_TEST_AUX_REF_FILE + SCOS_INPUT_REF_FILE
      file sink "output"                  → SCOS_SINK_OUTPUT
    """
    env: Dict[str, str] = {}
    wp = write_paths or {}
    for name, tbl in (ep_config.get("tables") or {}).items():
        if tbl.get("category") != "file":
            continue
        access = tbl.get("access", "read")
        # Read sources
        if access != "write" and name in read_paths:
            if tbl.get("relational", True):
                sid = _io_id_from_name(name)
                env[f"SCOS_INPUT_{sid}"] = read_paths[name]
                if name.upper().replace(" ", "_") != sid:
                    env[f"SCOS_INPUT_{name.upper()}"] = read_paths[name]
            else:
                key = _aux_key_from_name(name)
                env[f"SCOS_TEST_AUX_{key}"] = read_paths[name]
                env[f"SCOS_INPUT_{key}"] = read_paths[name]
        # Write sinks
        if access in ("write", "readwrite") and name in wp:
            env[f"SCOS_SINK_{_io_id_from_name(name)}"] = wp[name]
    return env


# ---------------------------------------------------------------------------
# seed_entrypoint
# ---------------------------------------------------------------------------

def seed_entrypoint(
    session,
    entrypoint_config: Dict[str, Any],
    mock_data_dir: str,
    *,
    output_schema: str,
) -> List[str]:
    """Seed readable tables and pre-create empty write tables into ``output_schema``.

    Uses the unified ``tables`` dict with ``access`` field:
      - access read/readwrite + category table/connector → seed from mock
      - access read/readwrite + category=file → path-redirect via SCOS_INPUT_*
      - access write + category=table → precreate empty
      - relational=false → aux input via SCOS_TEST_AUX_*

    Returns the list of pre-existing table names created by the harness.
    """
    seeded: List[str] = []
    seeded_set: set = set()

    for tbl_name, tbl in (entrypoint_config.get("tables") or {}).items():
        access = tbl.get("access", "read")
        category = tbl.get("category", "table")
        relational = tbl.get("relational", True)

        # file-category and non-relational handled via env vars, not catalog seed
        if category == "file" or not relational:
            continue

        bare = _declared_table_name(tbl_name, tbl)
        if not bare:
            continue
        target = f"{output_schema}.{bare}"

        if access in ("read", "readwrite"):
            # Seed from mock data
            mock_file = tbl.get("mock_file", "")
            if not mock_file:
                # No mock: create empty if write access
                if access == "readwrite":
                    schema_fields = tbl.get("columns", [])
                    if schema_fields and isinstance(schema_fields, list):
                        empty_df = session.createDataFrame([], _build_spark_schema(schema_fields))
                        empty_df.write.mode("overwrite").saveAsTable(target)
                        seeded.append(target.lower())
                        seeded_set.add(target.lower())
                continue

            csv_path = os.path.join(mock_data_dir, mock_file)
            if not os.path.isfile(csv_path):
                continue

            schema_fields = tbl.get("columns", [])
            reader = session.read
            for opt_name, opt_val in (tbl.get("reader_options") or {}).items():
                reader = reader.option(opt_name, str(opt_val))

            ext = os.path.splitext(mock_file)[1].lower()
            if schema_fields and isinstance(schema_fields, list):
                spark_schema = _build_spark_schema(schema_fields)
                if ext == ".parquet":
                    df = reader.schema(spark_schema).parquet(csv_path)
                elif ext == ".avro":
                    df = reader.format("avro").schema(spark_schema).load(csv_path)
                elif ext in (".json", ".jsonl", ".ndjson"):
                    df = reader.schema(spark_schema).json(csv_path)
                elif ext == ".tsv":
                    df = reader.option("header", "true").option("sep", "\t").option("nullValue", "").schema(spark_schema).csv(csv_path)
                else:
                    df = reader.option("header", "true").option("nullValue", "").schema(spark_schema).csv(csv_path)
            else:
                if ext == ".parquet":
                    df = reader.parquet(csv_path)
                elif ext == ".avro":
                    df = reader.format("avro").load(csv_path)
                elif ext in (".json", ".jsonl", ".ndjson"):
                    df = reader.json(csv_path)
                elif ext == ".tsv":
                    df = reader.option("header", "true").option("sep", "\t").option("inferSchema", "true").option("nullValue", "").csv(csv_path)
                else:
                    df = reader.option("header", "true").option("inferSchema", "true").option("nullValue", "").csv(csv_path)

            df.write.mode("overwrite").saveAsTable(target)
            seeded.append(target.lower())
            seeded_set.add(target.lower())

        elif access == "write":
            # Pre-create empty write table
            if target.lower() in seeded_set:
                continue
            schema_fields = tbl.get("columns", [])
            if not schema_fields or not isinstance(schema_fields, list):
                continue
            empty_df = session.createDataFrame([], _build_spark_schema(schema_fields))
            empty_df.write.mode("overwrite").saveAsTable(target)
            seeded.append(target.lower())
            seeded_set.add(target.lower())

    return seeded


# ---------------------------------------------------------------------------
# Shared Snowflake connection (Phase B, ContextVar-based)
# ---------------------------------------------------------------------------

_SF_CONN_HOLDER: contextvars.ContextVar = contextvars.ContextVar("_SF_CONN_HOLDER", default=None)


class SharedSnowflakeConn:
    """Lazily opens ONE snowflake.connector.Connection on first `.acquire(name)`.

    Subsequent `.acquire()` calls with the same connection_name return the
    cached connection. Different names raise. Owner closes via `.close()`.
    """

    def __init__(self):
        self._conn = None
        self._name = None

    def acquire(self, connection_name):
        if self._conn is None:
            import snowflake.connector
            self._conn = snowflake.connector.connect(connection_name=connection_name)
            self._name = connection_name
        elif self._name != connection_name:
            raise RuntimeError(
                f"SharedSnowflakeConn already opened for {self._name!r}; "
                f"refusing to acquire for {connection_name!r}"
            )
        return self._conn

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


# ---------------------------------------------------------------------------
# Snowflake per-trial schema CLONE (Phase B)
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def clone_golden_schema_for_trial(state_json: dict, ep_id: str):
    """Yield a per-trial Snowflake schema name cloned (metadata-only)
    from the per-ep golden schema, drop the clone on teardown.

    `state_json` is the loaded ``Validation/state.json``. The function
    looks up:

      - ``state["config"]["connection_name"]`` — Snowflake CLI conn name
      - ``state["snowflake"]["database"]`` — DB containing the goldens
      - ``state["snowflake"]["golden_schemas"][ep_id]["schema"]`` —
        the per-ep golden created by the harness provisioning

    The clone is named ``<GOLDEN>_T<8-hex>`` and lives under the same
    DB. Yields the bare schema name (so callers can pass it as
    ``output_schema`` and write fully-qualified ``DB.<clone>.TABLE``).

    On teardown, ``DROP SCHEMA IF EXISTS DB.<clone> CASCADE`` runs.

    Snowflake's CLONE is metadata-only and constant-time regardless of
    table count — much cheaper than re-creating + re-seeding for every
    trial.
    """
    import uuid as _uuid

    sf_state = state_json.get("snowflake", {}) or {}
    config = state_json.get("config", {}) or {}
    connection_name = config.get("connection_name")
    database = sf_state.get("database")
    golden_schemas = sf_state.get("golden_schemas", {}) or {}

    if not connection_name or not database:
        # Phase B is supposed to have this set. If not, fail loudly so
        # the orchestrator surfaces it as a hard-stuck setup error.
        raise RuntimeError(
            f"clone_golden_schema_for_trial: state.json missing "
            f"config.connection_name or snowflake.database "
            f"(connection_name={connection_name!r}, database={database!r})"
        )

    ep_info = golden_schemas.get(ep_id)
    if not ep_info or not ep_info.get("schema"):
        raise RuntimeError(
            f"clone_golden_schema_for_trial: no golden schema for "
            f"ep_id={ep_id!r} in state.snowflake.golden_schemas"
        )

    golden = ep_info["schema"]
    clone = f"{golden}_T{_uuid.uuid4().hex[:8]}".upper()

    try:
        import snowflake.connector  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "snowflake-connector-python is required for Phase B trials"
        ) from exc

    holder = _SF_CONN_HOLDER.get()
    if holder is not None:
        conn = holder.acquire(connection_name)
        _conn_owned = False
    else:
        conn = snowflake.connector.connect(connection_name=connection_name)
        _conn_owned = True

    cur = conn.cursor()
    try:
        cur.execute(f'USE DATABASE "{database}"')
        cur.execute(
            f'CREATE OR REPLACE SCHEMA "{database}"."{clone}" '
            f'CLONE "{database}"."{golden}"'
        )
        cur.execute(f'USE SCHEMA "{database}"."{clone}"')
        try:
            yield clone
        finally:
            try:
                cur.execute(f'DROP SCHEMA IF EXISTS "{database}"."{clone}" CASCADE')
            except Exception:
                # Don't mask the original failure if teardown fails.
                pass
    finally:
        cur.close()
        if _conn_owned:
            conn.close()


# ---------------------------------------------------------------------------
# Runtime-abstraction helpers (used by runtimes/_executor.py and runtimes/)
# ---------------------------------------------------------------------------


def _load_schemas_json(shared_dir: str) -> dict:
    """Load shared/schemas/schemas.json for $ref resolution. Empty dict if absent."""
    import os, json
    path = os.path.join(shared_dir, "schemas", "schemas.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _resolve_schema(schema_field: Any, schemas_cache: dict) -> list:
    """Resolve a schema field: list, $ref dict, or {columns: [...]}.

    Handles all forms encountered in the unified tables dict:
    - A plain list of column dicts (pass-through).
    - A dict with ``$ref`` pointing into schemas.json.
    - A dict with ``columns`` key containing a list.
    """
    if isinstance(schema_field, list):
        return schema_field
    if isinstance(schema_field, dict):
        if "$ref" in schema_field:
            ref = schema_field["$ref"]
            if ref.startswith("schemas.json#/"):
                pointer = ref[len("schemas.json#/"):]
                obj = schemas_cache
                for segment in pointer.split("/"):
                    if segment and isinstance(obj, dict):
                        obj = obj.get(segment, {})
                if isinstance(obj, list):
                    return obj
                if isinstance(obj, dict) and isinstance(obj.get("columns"), list):
                    return obj["columns"]
        if isinstance(schema_field.get("columns"), list):
            return schema_field["columns"]
    return []


# ---------------------------------------------------------------------------
# Events log
# ---------------------------------------------------------------------------


def install_sql_date_pin(spark):
    """Pin server-side ``current_date()`` / ``current_timestamp()`` in spark.sql.

    The conftest date pin only patches the Python ``pyspark.sql.functions``
    helpers; it does NOT affect ``current_date()`` evaluated SERVER-SIDE inside
    ``spark.sql("... current_date() ...")``. On SCOS that runs in UTC, so a
    Phase B run that crosses the UTC day boundary relative to the Phase A
    baseline capture date produces off-by-one-day values in execution-date
    columns (e.g. EFFECTIVE_DATE, UPDATED_TS = current_date) and spurious
    cell divergences vs the baseline.

    When ``SCOS_PINNED_DATE`` is set, this wraps ``spark.sql`` to textually
    replace ``current_date``/``current_timestamp`` (with or without parens)
    with literals fixed to that date, making the workload's SQL deterministic
    and reproducible regardless of wall-clock run time. Date-only columns then
    match the baseline exactly; time-of-day columns (CREATED_AT/INGESTION_TIME)
    that the unpinned baseline captured with a real time still differ and remain
    covered by expected_divergences.

    No-op unless ``SCOS_PINNED_DATE`` is set (Phase B only).
    """
    pinned = os.environ.get("SCOS_PINNED_DATE")
    if not pinned:
        return

    import re as _date_re

    date_lit = f"DATE'{pinned}'"
    ts_lit = f"TIMESTAMP'{pinned} 00:00:00'"
    # Order matters: timestamp before date so 'current_timestamp' isn't partially
    # matched. Match an optional empty arg list; require a word boundary so we do
    # not touch identifiers like my_current_date.
    _TS_RE = _date_re.compile(r"\bcurrent_timestamp\b\s*(?:\(\s*\))?", _date_re.IGNORECASE)
    _DATE_RE = _date_re.compile(r"\bcurrent_date\b\s*(?:\(\s*\))?", _date_re.IGNORECASE)

    orig_sql = spark.sql

    def _pinned_sql(query, *args, **kwargs):
        if isinstance(query, str):
            query = _TS_RE.sub(ts_lit, query)
            query = _DATE_RE.sub(date_lit, query)
        return orig_sql(query, *args, **kwargs)

    spark.sql = _pinned_sql


# ---------------------------------------------------------------------------
# Arrow type pre-coercion for SCOS Connect
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Provision hash store (shared/provision_hashes.json)
# ---------------------------------------------------------------------------


def _provision_hashes_path(workspace_root):
    import os
    return os.path.join(str(workspace_root), "shared", "provision_hashes.json")


def load_provision_hashes(workspace_root) -> dict:
    """Load shared/provision_hashes.json -> {flavor: {ep_id: {table: sha256}}}. {} if absent/corrupt."""
    import json, os
    p = _provision_hashes_path(workspace_root)
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def provision_hash_matches(store: dict, flavor: str, ep_id: str, table: str, current_hash: str) -> bool:
    return ((store.get(flavor) or {}).get(ep_id) or {}).get(table) == current_hash


def record_provision_hash(store: dict, flavor: str, ep_id: str, table: str, current_hash: str) -> None:
    store.setdefault(flavor, {}).setdefault(ep_id, {})[table] = current_hash


def save_provision_hashes(workspace_root, store: dict) -> None:
    """Merge `store` into the on-disk file (per-flavor) and write back, so other flavors' entries survive."""
    import json, os, tempfile
    p = _provision_hashes_path(workspace_root)
    on_disk = load_provision_hashes(workspace_root)
    for flavor, eps in (store or {}).items():
        dest = on_disk.setdefault(flavor, {})
        for ep_id, tbls in eps.items():
            dest.setdefault(ep_id, {}).update(tbls)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    # Unique per-process temp file: a shared "<p>.tmp" name races under pytest-xdist
    # (multiple workers provisioning concurrently clobber each other's temp write).
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=os.path.dirname(p), prefix=".provision_", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(on_disk, fh, indent=2)
        os.replace(tmp_name, p)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
