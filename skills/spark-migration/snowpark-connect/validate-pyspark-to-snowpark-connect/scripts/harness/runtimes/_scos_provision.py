"""SCOS golden-schema provisioning logic.

Extracted so that ScosRuntime.provision() can invoke it directly without
shelling out. The runtime layer calls provision_golden_schemas() for each
entrypoint before running trials.

Creates one golden schema per entrypoint containing:
- Source tables (CREATE OR REPLACE TABLE with declared schema, then
  COPY INTO from staged mock CSVs — honoring reader_options.sep)
- Empty sink tables (CREATE TABLE with declared schema, for DML to land)
- Staged file sources (mock files PUT to stage for file-category reads)

Mock data is uploaded in parallel using ThreadPoolExecutor.

Idempotent (hash-driven): skips tables whose schema_hash has not changed
according to the local provision_hashes.json store AND still exist on
Snowflake.
"""

from __future__ import annotations

import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from helpers import _declared_table_name  # type: ignore[import-not-found]


def _get_connector():
    """Lazily import snowflake.connector so --help works without the dep."""
    try:
        import snowflake.connector  # type: ignore
        return snowflake.connector
    except ImportError as exc:
        raise SystemExit(
            "ERROR: snowflake-connector-python not available. "
            "Install with: pip install snowflake-connector-python"
        ) from exc


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STAGE_NAME = "SCOS_TEST_STAGE"
DATABASE = os.environ.get("SCOS_VALIDATION_DATABASE", "SCOS_VALIDATION")

_SF_TYPE_MAP = {
    "string": "STRING", "varchar": "STRING", "text": "STRING", "char": "STRING",
    "int": "NUMBER(38,0)", "integer": "NUMBER(38,0)", "bigint": "NUMBER(38,0)",
    "long": "NUMBER(38,0)", "short": "NUMBER(38,0)", "smallint": "NUMBER(38,0)",
    "byte": "NUMBER(38,0)", "tinyint": "NUMBER(38,0)",
    "decimal": "NUMBER(38,10)", "numeric": "NUMBER(38,10)",
    "double": "DOUBLE", "float": "FLOAT", "real": "FLOAT",
    "boolean": "BOOLEAN", "bool": "BOOLEAN",
    "date": "DATE", "timestamp": "TIMESTAMP_NTZ",
    "timestamp_ntz": "TIMESTAMP_NTZ", "timestamp_ltz": "TIMESTAMP_LTZ",
    "timestamp_tz": "TIMESTAMP_TZ", "binary": "BINARY",
}

MAX_PUT_WORKERS = int(os.environ.get("SCOS_PUT_WORKERS", "8"))
# Entrypoints provisioned concurrently, each on its own connection. Bounded to
# avoid exhausting the account's concurrent-session limit.
MAX_PROVISION_WORKERS = int(os.environ.get("SCOS_PROVISION_WORKERS", "8"))


# ---------------------------------------------------------------------------
# Snowflake operations
# ---------------------------------------------------------------------------


def _database_exists(cur: Any) -> bool:
    """Check if the configured database already exists (no CREATE DB priv needed)."""
    try:
        cur.execute(f"SHOW DATABASES LIKE '{DATABASE}'")
        return len(cur.fetchall()) > 0
    except Exception:
        return False


def _privilege_probe(cur: Any) -> bool:
    """Verify the active role can create / use the configured database.

    If the database already exists, only USAGE + CREATE SCHEMA are needed
    (no CREATE DATABASE privilege required). Useful for personal DBs like
    USER$<NAME> where the operator owns the DB but cannot create new ones.

    Returns True once the database is confirmed to exist (pre-existing or just
    created), so callers can skip a redundant SHOW DATABASES. Exits with code 3
    on privilege failure.
    """
    cur.execute("SELECT CURRENT_ROLE()")
    role = cur.fetchone()[0]
    if _database_exists(cur):
        # DB pre-exists; verify CREATE SCHEMA via a probe.
        probe = f"_SCOS_PROBE_{role}".replace("-", "_")[:60]
        try:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {DATABASE}.{probe}")
            cur.execute(f"DROP SCHEMA IF EXISTS {DATABASE}.{probe}")
        except Exception as exc:
            print(
                f"ERROR: role {role} cannot CREATE SCHEMA in existing database {DATABASE}.\n"
                f"  Snowflake said: {exc}\n"
                f"  Grant CREATE SCHEMA ON DATABASE {DATABASE} TO ROLE {role}, or switch roles.",
                file=sys.stderr,
            )
            sys.exit(3)
        return True
    try:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS {DATABASE}")
    except Exception as exc:
        print(
            f"ERROR: role {role} cannot CREATE DATABASE {DATABASE}.\n"
            f"  Snowflake said: {exc}\n"
            f"  Either grant CREATE DATABASE ON ACCOUNT to {role} (e.g. SYSADMIN),\n"
            f"  pre-create {DATABASE}, or set SCOS_VALIDATION_DATABASE to an existing DB you own.",
            file=sys.stderr,
        )
        sys.exit(3)
    return True


def _create_golden_schema(cur: Any, schema_name: str, database_exists: bool = True) -> None:
    """Create a per-entrypoint golden schema and its internal stage.

    ``database_exists`` is threaded from ``_privilege_probe`` so we don't re-run
    SHOW DATABASES once per entrypoint."""
    if not database_exists:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS {DATABASE}")
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {DATABASE}.{schema_name}")
    cur.execute(f"USE SCHEMA {DATABASE}.{schema_name}")
    cur.execute(
        f"CREATE STAGE IF NOT EXISTS {STAGE_NAME} "
        "ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')"
    )


def _quote_col_name(name: str) -> str:
    """Quote a column name for Snowflake DDL.

    Unquoted Snowflake identifiers must match [A-Za-z_][A-Za-z0-9_$]*. Names
    containing hyphens, dots, or other special characters need to be wrapped
    in double quotes (with internal `"` escaped). Standard names are
    uppercased and emitted unquoted to preserve case-insensitive resolution.
    """
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name):
        return name.upper()
    return '"' + name.replace('"', '""') + '"'


def _spark_type_to_sf(t: str) -> str:
    """Map a Spark/StructType type string to a Snowflake type.

    Struct / object-bearing types (``struct<...>``, ``array<struct<...>>``,
    ``map<...,struct<...>>``, bare ``struct`` / ``object``) are mapped to VARCHAR
    — NOT OBJECT/ARRAY(OBJECT(...)).  The mock parquet carries REAL nested types
    (for Phase A PySpark), and the COPY INTO path JSON-stringifies nested
    parquet columns into the VARCHAR target at load time (see
    ``_copy_into_source``).  This avoids the SCOS blocker where
    TRY_PARSE_JSON rejects non-castable OBJECT inputs.

    Scope is deliberately limited to object-bearing types.  Plain
    ``array<scalar>`` / ``map<scalar,scalar>`` / ``variant`` keep their native
    Snowflake mapping (ARRAY/VARIANT do not trigger the SCOS blocker; coercing
    them to VARCHAR would regress otherwise-passing trials).

    Falls back to STRING for unknown atomic types.
    """
    if not t:
        return "STRING"
    tl = t.strip().lower()
    if "struct" in tl or "object" in tl:
        return "VARCHAR"
    if tl.startswith("array<") or tl.startswith("map<"):
        # Plain (non-struct) semi-structured types keep their native mapping.
        return "VARIANT"
    if tl.startswith("decimal(") or tl.startswith("numeric("):
        # Preserve precision/scale.
        return tl.upper()
    return _SF_TYPE_MAP.get(tl, "STRING")


def _columns_to_ddl(columns: list) -> str:
    """Build the column-list portion of a CREATE TABLE statement."""
    parts: list[str] = []
    for col in columns:
        col_name = _quote_col_name(col["name"])
        sf_type = _spark_type_to_sf(col.get("type", "string"))
        nullable = col.get("nullable", True)
        null_clause = "" if nullable else " NOT NULL"
        parts.append(f"  {col_name} {sf_type}{null_clause}")
    return ",\n".join(parts)


def _copy_into_source(
    cur: Any,
    table_fqn: str,
    stage_fqn: str,
    run_id: str,
    mock_file: str,
    reader_options: dict,
    source_schema: list | None = None,
    _parquet_local_path: str | None = None,
    _stage_subdir: str = "_seed",
) -> None:
    """COPY INTO a source table from the staged mock data file.

    Dispatches by file extension: CSV (default), Parquet, JSON.
    For CSV, honors ``reader_options.sep`` (or ``delimiter``) so workloads
    with pipe/tab-separated mock data load correctly.

    For Parquet: if the source schema declares struct/object-bearing columns,
    those columns exist as REAL nested types in the parquet but map to VARCHAR
    in Snowflake. We use a SELECT-based COPY that wraps those columns with
    TO_VARCHAR(...) to JSON-stringify the nested value into the VARCHAR target.

    Snowflake COPY INTO restriction: only ONE variant/object/array column is
    allowed per COPY INTO (even in the transform/SELECT form — it is a
    compile-time check on the parquet file, not the target table). When the
    mock has MULTIPLE struct columns we pre-convert to CSV (JSON-serialising
    struct fields to strings) and COPY from CSV instead, which has no such
    restriction.
    """
    stage_path = f"@{stage_fqn}/{run_id}/{_stage_subdir}/{mock_file}"
    ext = os.path.splitext(mock_file)[1].lower()

    if ext == ".parquet":
        # Detect if any columns need JSON-stringification (complex->VARCHAR).
        # Also used to decide whether to use the positional-ref transform form.
        complex_col_names: set[str] = set()
        if source_schema:
            for col_def in source_schema:
                col_type = col_def.get("type", "")
                tl = col_type.strip().lower()
                if "struct" in tl or "object" in tl:
                    complex_col_names.add(col_def["name"].upper())

        # SNOWFLAKE RESTRICTION: only one variant/object/array column allowed per
        # COPY INTO (compile-time check on the parquet source, even in SELECT form).
        # Pre-convert to CSV using pyarrow whenever there are any struct/object columns
        # so the COPY FROM CSV path avoids the restriction entirely. This includes
        # single-column cases (e.g. array<struct<...>>) because the positional
        # SELECT+TO_VARCHAR form also triggers 002019 for nested array/struct columns.
        if len(complex_col_names) >= 1 and _parquet_local_path and source_schema:
            try:
                import json as _json
                import tempfile
                import pyarrow.parquet as _pq
                import pyarrow as _pa
                tbl = _pq.read_table(_parquet_local_path)
                # JSON-stringify struct/list columns; leave primitives as-is.
                rows: list[str] = []
                col_names = [c["name"] for c in source_schema]
                for batch in tbl.to_batches():
                    for row_i in range(batch.num_rows):
                        parts: list[str] = []
                        for col_def in source_schema:
                            col_name = col_def["name"]
                            tl = col_def.get("type", "").strip().lower()
                            try:
                                val = batch.column(col_name)[row_i].as_py()
                            except KeyError:
                                val = None
                            if ("struct" in tl or "object" in tl) and val is not None:
                                parts.append('"' + _json.dumps(val).replace('"', '""') + '"')
                            elif val is None:
                                parts.append("")
                            elif isinstance(val, str):
                                parts.append('"' + val.replace('"', '""') + '"')
                            else:
                                parts.append(str(val))
                        rows.append(",".join(parts))
                csv_header = ",".join(f'"{c}"' for c in col_names)
                csv_content = csv_header + "\n" + "\n".join(rows)
                # Write to a correctly-named temp file so PUT stages it with the right filename
                csv_stage_name = mock_file.replace(".parquet", "_converted.csv")
                csv_stage_path = f"@{stage_fqn}/{run_id}/_seed/{csv_stage_name}"
                import tempfile as _tf
                csv_tmp_dir = _tf.mkdtemp()
                csv_tmp = os.path.join(csv_tmp_dir, csv_stage_name)
                with open(csv_tmp, "w", encoding="utf-8") as tf:
                    tf.write(csv_content)
                cur.execute(
                    f"PUT 'file://{csv_tmp}' '@{stage_fqn}/{run_id}/_seed/' "
                    f"SOURCE_COMPRESSION = NONE OVERWRITE = TRUE AUTO_COMPRESS = FALSE"
                )
                import os as _os
                _os.unlink(csv_tmp)
                _os.rmdir(csv_tmp_dir)
                # COPY INTO from CSV — PARSE_HEADER reads header row, MATCH_BY_COLUMN_NAME
                # maps header names to table columns (case-insensitive). Struct fields were
                # JSON-stringified above so each Snowflake VARCHAR column receives a string.
                cur.execute(
                    f"COPY INTO {table_fqn} FROM '{csv_stage_path}' "
                    "FILE_FORMAT = (TYPE = CSV FIELD_OPTIONALLY_ENCLOSED_BY = '\"' "
                    "PARSE_HEADER = TRUE NULL_IF = ('') TRIM_SPACE = TRUE) "
                    "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE "
                    "ON_ERROR = 'ABORT_STATEMENT'"
                )
                return
            except Exception as _e:
                # Fall through to positional-SELECT parquet path on conversion failure
                import warnings
                warnings.warn(
                    f"Complex-struct parquet->CSV conversion failed ({_e}); "
                    "falling through to parquet COPY (may fail with 002019)"
                )

        if source_schema and complex_col_names:
            # Fallback: pyarrow CSV conversion unavailable or failed.
            # Use positional SELECT + TO_VARCHAR for each complex column.
            # NOTE: this may trigger Snowflake 002019 for array<struct<...>> types
            # even though we wrap with TO_VARCHAR — prefer the CSV path above.
            select_exprs: list[str] = []
            for i, col_def in enumerate(source_schema, start=1):
                col_name_upper = col_def["name"].upper()
                quoted_target = _quote_col_name(col_def["name"])
                if col_name_upper in complex_col_names:
                    select_exprs.append(f"TO_VARCHAR(${i}) AS {quoted_target}")
                else:
                    select_exprs.append(f"${i} AS {quoted_target}")

            select_list = ", ".join(select_exprs)
            cur.execute(
                f"COPY INTO {table_fqn} FROM ("
                f"SELECT {select_list} FROM '{stage_path}'"
                ") "
                "FILE_FORMAT = (TYPE = PARQUET) "
                # MATCH_BY_COLUMN_NAME is NOT supported in transform (subquery) form
                "ON_ERROR = 'ABORT_STATEMENT'"
            )
        else:
            # No complex columns (or no schema info) — use MATCH_BY_COLUMN_NAME.
            # The positional SELECT form triggers Snowflake error 002019 ("only one
            # column of type variant/object/array") on some account versions even
            # for fully-flat parquets; MATCH_BY_COLUMN_NAME avoids that path.
            cur.execute(
                f"COPY INTO {table_fqn} FROM '{stage_path}' "
                "FILE_FORMAT = (TYPE = PARQUET) "
                "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE "
                "ON_ERROR = 'ABORT_STATEMENT'"
            )
    elif ext in (".json", ".jsonl", ".ndjson"):
        cur.execute(
            f"COPY INTO {table_fqn} FROM '{stage_path}' "
            "FILE_FORMAT = (TYPE = JSON STRIP_OUTER_ARRAY = TRUE) "
            "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE "
            "ON_ERROR = 'ABORT_STATEMENT'"
        )
    else:
        # CSV (includes .csv, .tsv, and unknown extensions)
        sep = (
            reader_options.get("sep")
            or reader_options.get("delimiter")
            or ("\t" if ext == ".tsv" else ",")
        )
        if sep == "\t":
            delim_clause = "FIELD_DELIMITER = '\\t'"
        else:
            escaped = sep.replace("\\", "\\\\").replace("'", "\\'")
            delim_clause = f"FIELD_DELIMITER = '{escaped}'"

        cur.execute(
            f"COPY INTO {table_fqn} FROM '{stage_path}' "
            f"FILE_FORMAT = (TYPE = CSV {delim_clause} SKIP_HEADER = 1 "
            "FIELD_OPTIONALLY_ENCLOSED_BY = '\"' NULL_IF = ('')) "
            "ON_ERROR = 'ABORT_STATEMENT'"
        )


def _put_file(conn_params: dict, sql: str) -> str:
    """Execute a single PUT in its own connection (for thread safety).

    Returns the SQL executed (for logging).
    """
    sf = _get_connector()
    conn = sf.connect(**conn_params)
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql)
        finally:
            cur.close()
    finally:
        conn.close()
    return sql


def _build_put_sql(local_path: Path, stage_dir: str) -> str:
    real_path = local_path.resolve()
    # A mock may be a single file (pandas/CSV) or a directory of part files
    # (a Spark-written parquet dataset, used to preserve nested array<struct>).
    # PUT a directory's contents with a wildcard; a single file as-is.
    src = f"{real_path}/*" if real_path.is_dir() else str(real_path)
    # Snowflake PUT requires the source path in single quotes so spaces in
    # directory names are handled correctly (unquoted paths break SQL parsing).
    return (
        f"PUT 'file://{src}' '{stage_dir}/' "
        "OVERWRITE = TRUE AUTO_COMPRESS = FALSE"
    )


# ---------------------------------------------------------------------------
# Per-entrypoint provisioning
# ---------------------------------------------------------------------------


def _provision_entrypoint(
    cur,
    conn_params: dict,
    ep: dict,
    mock_data_root: Path,
    project_slug: str,
    run_id: str,
    store: dict,
    database_exists: bool = True,
) -> dict:
    """Provision one entrypoint's golden schema.

    Uses the unified ``tables`` dict with ``access`` field:
    1. CREATE SCHEMA + internal stage.
    2. For each table with access read/readwrite:
       - category in {table, connector}: CREATE OR REPLACE TABLE, PUT mock_file,
         COPY INTO.
       - category=file: PUT mock_file to stage (no table).
       - relational=false (document): PUT mock_file to stage (no table).
    3. PUT mock files in parallel.
    4. COPY INTO each table-category source from staged data.
    5. For each table with access write/readwrite that is category=table:
       - CREATE empty TABLE (skip if already created above for readwrite).

    Hash-driven: skips CREATE+load when the local provision_hashes.json
    records a matching hash AND the table still exists on Snowflake.

    Returns a dict suitable for ``state.snowflake.golden_schemas[ep_id]``.
    """
    from helpers import schema_hash as _schema_hash  # type: ignore[import-not-found]
    from helpers import provision_hash_matches, record_provision_hash  # type: ignore[import-not-found]

    ep_id = ep["id"]
    ep_id_sane = re.sub(r"[^A-Za-z0-9_]", "_", ep_id)
    schema_name = f"{project_slug}_{run_id}_{ep_id_sane}_GOLDEN".upper()
    schema_fqn = f"{DATABASE}.{schema_name}"
    stage_fqn = f"{schema_fqn}.{STAGE_NAME}"

    _create_golden_schema(cur, schema_name, database_exists)
    print(f"  [{ep_id}] Schema: {schema_fqn}")
    print(f"  [{ep_id}] Stage:  {stage_fqn}")

    # Set the session database and schema so DDL statements can use short names.
    # Some Snowflake accounts reject three-part identifiers in CREATE TABLE.
    try:
        cur.execute(f"USE DATABASE {DATABASE}")
        cur.execute(f"USE SCHEMA {schema_name}")
    except Exception as exc:
        import warnings; warnings.warn(f"USE DATABASE/SCHEMA failed: {exc}")

    # Build set of existing tables in this schema (one SHOW TABLES call)
    existing_tables: set[str] = set()
    try:
        cur.execute(f"SHOW TABLES IN SCHEMA {schema_fqn}")
        for row in cur.fetchall():
            # Column 1 is table name in SHOW TABLES output
            existing_tables.add(row[1].lower())
    except Exception:
        pass

    ep_mock_dir = mock_data_root / ep_id
    tables: dict = ep.get("tables") or {}

    # -- Step 1: Process readable tables (access read/readwrite) --
    table_sources: list[tuple[str, str, dict, list, str]] = []
    # file_table_sources: file-category sources that should ALSO be provisioned as
    # Snowflake tables so SCOS Phase B can read them via spark.read.table() instead
    # of spark.read.parquet("@stage/path") which fails on some SCOS client versions.
    file_table_sources: list[tuple[str, str, dict, list, str]] = []
    put_sqls: list[str] = []
    created_tables: set[str] = set()

    for tbl_name, tbl in tables.items():
        access = tbl.get("access", "read")
        category = tbl.get("category", "table")
        relational = tbl.get("relational", True)
        mock_file = tbl.get("mock_file")
        columns = tbl.get("columns") or []
        reader_options = tbl.get("reader_options") or {}

        table_name = _declared_table_name(tbl_name, tbl)

        # Compute hash for this table
        current_hash = _schema_hash(tbl)

        if access == "write":
            # Write-only: just create empty table (handled in Step 5 below)
            continue

        if not relational:
            # Document / non-tabular: stage the mock file only
            if mock_file:
                local_path = (ep_mock_dir / mock_file).resolve()
                if not (local_path.is_file() or local_path.is_dir()):
                    raise RuntimeError(f"Mock data file not found for document source: {local_path}")
                stage_subdir = os.path.dirname(mock_file).replace("\\", "/").strip("/")
                if stage_subdir:
                    parts = []
                    for seg in stage_subdir.split("/"):
                        if seg == "" or seg == ".":
                            continue
                        if seg == "..":
                            if parts:
                                parts.pop()
                            continue
                        parts.append(seg)
                    stage_subdir = "/".join(parts)
                stage_dir = f"@{stage_fqn}/{run_id}/inputs"
                if stage_subdir:
                    stage_dir = f"{stage_dir}/{stage_subdir}"
                put_sqls.append(_build_put_sql(local_path, stage_dir))
            continue

        if category in ("table", "connector"):
            if not mock_file:
                if not columns:
                    print(f"  [{ep_id}] WARN: table '{tbl_name}' has no mock_file and no columns; skipping")
                    continue
                # Create empty stub
                table_fqn = f"{schema_name}.{table_name}"
                ddl = (
                    f"CREATE OR REPLACE TABLE {table_fqn} (\n"
                    + _columns_to_ddl(columns)
                    + "\n)"
                )
                cur.execute(ddl)
                created_tables.add(table_name)
                record_provision_hash(store, "scos", ep_id, table_name, current_hash)
                print(f"  [{ep_id}] Empty stub: {table_fqn} (no mock_file)")
                continue

            csv_path = (ep_mock_dir / mock_file).resolve()
            if not (csv_path.is_file() or csv_path.is_dir()):
                raise RuntimeError(
                    f"Mock data file not found: {csv_path}\n"
                    "  The datagen must write mock data before provisioning."
                )
            if not columns:
                raise RuntimeError(
                    f"Table '{tbl_name}' has mock_file but no columns declared"
                )

            # Check if table already exists with same hash (skip if so)
            table_fqn = f"{schema_name}.{table_name}"
            skip_table = (
                table_name.lower() in existing_tables
                and provision_hash_matches(store, "scos", ep_id, table_name, current_hash)
            )

            if skip_table:
                print(f"  [{ep_id}] Skipped (hash match): {table_fqn}")
                created_tables.add(table_name)
                continue

            # CREATE OR REPLACE TABLE
            ddl = (
                f"CREATE OR REPLACE TABLE {table_fqn} (\n"
                + _columns_to_ddl(columns)
                + "\n)"
            )
            cur.execute(ddl)
            created_tables.add(table_name)
            table_sources.append((table_fqn, mock_file, reader_options, columns, str(csv_path)))
            record_provision_hash(store, "scos", ep_id, table_name, current_hash)

            # PUT
            stage_dir = f"@{stage_fqn}/{run_id}/_seed"
            put_sqls.append(_build_put_sql(csv_path, stage_dir))

        elif category == "file":
            if not mock_file:
                print(
                    f"  [{ep_id}] WARN: file table '{tbl_name}' has no mock_file — skipping",
                    file=sys.stderr,
                )
                continue
            local_path = (ep_mock_dir / mock_file).resolve()
            if not (local_path.is_file() or local_path.is_dir()):
                raise RuntimeError(f"Local mock missing for upload: {local_path}")

            stage_subdir = os.path.dirname(mock_file).replace("\\", "/").strip("/")
            if stage_subdir:
                parts = []
                for seg in stage_subdir.split("/"):
                    if seg == "" or seg == ".":
                        continue
                    if seg == "..":
                        if parts:
                            parts.pop()
                        continue
                    parts.append(seg)
                stage_subdir = "/".join(parts)
            stage_dir = f"@{stage_fqn}/{run_id}/inputs"
            if stage_subdir:
                stage_dir = f"{stage_dir}/{stage_subdir}"
            put_sqls.append(_build_put_sql(local_path, stage_dir))

            # Also provision as a Snowflake table so SCOS Phase B can read via
            # spark.read.table() without needing stage-URL parquet reads.
            if columns and access == "read":
                table_fqn = f"{schema_name}.{table_name}"
                current_hash = _schema_hash(tbl)
                skip_table = (
                    table_name.lower() in existing_tables
                    and provision_hash_matches(store, "scos", ep_id, table_name, current_hash)
                )
                if not skip_table:
                    ddl = (
                        f"CREATE OR REPLACE TABLE {table_fqn} (\n"
                        + _columns_to_ddl(columns)
                        + "\n)"
                    )
                    cur.execute(ddl)
                    created_tables.add(table_name)
                    # staged file path = same as inputs/ staging
                    staged_mock_file = os.path.basename(mock_file)
                    # Hash recorded in Step 3b, AFTER COPY INTO succeeds (not here)
                    file_table_sources.append((table_fqn, staged_mock_file, reader_options, columns, str(local_path), current_hash))
                else:
                    created_tables.add(table_name)
                    print(f"  [{ep_id}] Skipped (hash match): {table_fqn}")
        else:
            print(f"  [{ep_id}] WARN: unknown category '{category}' for '{tbl_name}'")

    # -- Step 2: Execute PUTs in parallel --
    csvs_staged = 0
    files_staged = 0

    if put_sqls:
        with ThreadPoolExecutor(max_workers=MAX_PUT_WORKERS) as pool:
            futures = {
                pool.submit(_put_file, conn_params, sql): sql
                for sql in put_sqls
            }
            for fut in as_completed(futures):
                fut.result()
                sql = futures[fut]
                if "/_seed/" in sql:
                    csvs_staged += 1
                else:
                    files_staged += 1

    print(f"  [{ep_id}] CSVs staged: {csvs_staged}, files staged: {files_staged}")

    # -- Step 3: COPY INTO for each table source --
    for table_fqn, mock_file, reader_options, columns, local_path in table_sources:
        _copy_into_source(
            cur, table_fqn, stage_fqn, run_id,
            mock_file,
            reader_options,
            source_schema=columns,
            _parquet_local_path=local_path,
        )
    if table_sources:
        print(f"  [{ep_id}] Source tables loaded: {len(table_sources)}")

    # -- Step 3b: COPY INTO for file-category sources that were also provisioned as tables --
    # These tables are loaded from the inputs/ stage prefix so SCOS Phase B can read
    # via spark.read.table() instead of spark.read.parquet("@stage/path").
    for table_fqn, mock_file, reader_options, columns, local_path, table_hash in file_table_sources:
        _copy_into_source(
            cur, table_fqn, stage_fqn, run_id,
            mock_file,
            reader_options,
            source_schema=columns,
            _parquet_local_path=local_path,
            _stage_subdir="inputs",
        )
        # Record hash AFTER COPY INTO succeeds
        tbl_name_key = table_fqn.split(".")[-1].lower()
        record_provision_hash(store, "scos", ep_id, tbl_name_key.upper(), table_hash)
    if file_table_sources:
        print(f"  [{ep_id}] File-source tables loaded: {len(file_table_sources)}")

    # -- Step 4: Pre-create empty write/readwrite tables not yet created --
    write_created = 0
    for tbl_name, tbl in tables.items():
        access = tbl.get("access", "read")
        if access not in ("write", "readwrite"):
            continue
        category = tbl.get("category", "table")
        if category != "table":
            continue
        columns = tbl.get("columns") or []
        if not columns:
            print(f"  [{ep_id}] WARN: table '{tbl_name}' (write) has no columns; skipping")
            continue
        table_name = _declared_table_name(tbl_name, tbl)
        if table_name in created_tables:
            continue  # Already created above (readwrite case)
        current_hash = _schema_hash(tbl)
        table_fqn = f"{schema_name}.{table_name}"
        ddl = (
            f"CREATE TABLE IF NOT EXISTS {table_fqn} (\n"
            + _columns_to_ddl(columns)
            + "\n)"
        )
        cur.execute(ddl)
        created_tables.add(table_name)
        record_provision_hash(store, "scos", ep_id, table_name, current_hash)
        write_created += 1

    if write_created:
        print(f"  [{ep_id}] Write tables created: {write_created}")

    # Every table the clone will expose = pre-existing + everything created this
    # run. Persisted so scos_runtime can skip a per-trial SHOW TABLES (P8).
    schema_tables = sorted(set(existing_tables) | {t.lower() for t in created_tables})

    return {
        "schema": schema_name,
        "stage": stage_fqn,
        # snake_case matches both the PySpark runtime (scos_runtime.py reads stage_prefix)
        # and the Scala kit (GoldenSchema.stagePrefix field, read via SNAKE_CASE mapper).
        "stage_prefix": run_id,
        "tables": schema_tables,
    }


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def provision_golden_schemas(conn, conn_params, entrypoints, mock_data_root, project_slug, run_id, database):
    """Provision golden schemas for all entrypoints. Returns {ep_id: ep_info dict}.

    Entrypoints are provisioned in parallel (P12): each is an independent chain
    of Snowflake round-trips (CREATE SCHEMA/TABLE, PUT, COPY INTO). Each worker
    uses its OWN connection because a Snowflake cursor/connection is not
    thread-safe. The shared ``store`` (provision hashes) is made thread-safe by
    pre-creating each entrypoint's leaf sub-dict up front, so workers only mutate
    their own ``store["scos"][ep_id]`` node.

    Raises on privilege/SQL errors so the caller can map exit codes.
    """
    from helpers import load_provision_hashes, save_provision_hashes  # type: ignore[import-not-found]

    global DATABASE
    DATABASE = database  # threaded from caller; helpers read this module global

    workspace_root = Path(mock_data_root).resolve().parents[1]
    store = load_provision_hashes(workspace_root)
    # Pre-create per-ep store nodes single-threaded so parallel workers each
    # touch only their own leaf dict (safe under CPython without a lock).
    scos_store = store.setdefault("scos", {})
    for ep in entrypoints:
        scos_store.setdefault(ep["id"], {})

    golden_schemas: dict = {}
    cur = conn.cursor()
    try:
        db_exists = _privilege_probe(cur)
    finally:
        cur.close()

    max_workers = max(1, min(len(entrypoints), MAX_PROVISION_WORKERS))

    def _provision_one(ep: dict) -> tuple:
        sf = _get_connector()
        ep_conn = sf.connect(**conn_params)
        try:
            ep_cur = ep_conn.cursor()
            try:
                info = _provision_entrypoint(
                    ep_cur, conn_params, ep, mock_data_root, project_slug, run_id,
                    store, db_exists,
                )
            finally:
                ep_cur.close()
        finally:
            ep_conn.close()
        return ep["id"], info

    try:
        if max_workers == 1 or len(entrypoints) <= 1:
            for ep in entrypoints:
                ep_id, info = _provision_one(ep)
                golden_schemas[ep_id] = info
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_provision_one, ep): ep["id"] for ep in entrypoints}
                # Collect ALL results; don't let the first raised future abort the
                # loop and silently swallow other concurrent failures.
                errors: list = []
                for fut in as_completed(futures):
                    try:
                        ep_id, info = fut.result()
                        golden_schemas[ep_id] = info
                    except Exception as exc:  # noqa: BLE001 - aggregate + re-raise
                        errors.append(f"{futures[fut]}: {type(exc).__name__}: {exc}")
                if errors:
                    raise RuntimeError(
                        "provisioning failed for "
                        f"{len(errors)} entrypoint(s):\n  " + "\n  ".join(errors)
                    )
    finally:
        save_provision_hashes(workspace_root, store)

    return golden_schemas
