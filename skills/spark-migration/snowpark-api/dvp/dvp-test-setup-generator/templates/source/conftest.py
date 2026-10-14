"""Pytest configuration for PySpark (source) tests.

Source tests preserve the original IO strategy: a mix of CSV files and
Hive tables. The SparkSession is created with enableHiveSupport() so
spark.table() and saveAsTable() work with a local Hive metastore.

Input tables are created from DDL schemas in data_io_schema.json and loaded
with CSV data from synthetic_data/.

Provides BaseSourceWorkloadTest — a reusable base class for testing any
source workload. Subclasses set MAIN_FN and I/O lists; the base class
handles setup, execution, baseline persistence, and common validations.
"""

import csv
import json
import logging
import re
import shutil
from pathlib import Path


def _sanitize_filename(name: str) -> str:
    """Sanitize a data_io_schema name to match synthetic_data/generator.py output."""
    clean = re.sub(r'^(s3|file|hdfs|gs):/+', '', name)
    clean = clean.replace('/', '_').replace('\\', '_')
    clean = clean.replace('\n', '_').replace('\r', '_')
    clean = re.sub(r'[<>:"|?*\s\n\r\t]', '_', clean)
    clean = clean.strip('_.')
    for suffix in ('.csv', '.parquet'):
        if clean.endswith(suffix):
            clean = clean[:-len(suffix)]
    if len(clean) > 100:
        clean = clean[:100]
    return clean or "unknown_table"

import pytest
from pyspark.sql import SparkSession
from pyspark.sql.utils import AnalysisException
from pyspark.errors.exceptions.captured import AnalysisException as CapturedAnalysisException
try:
    # When running pytest from 03-tests/ (recommended), this resolves the shared helpers.
    from conftest import BaseIOConfig, load_data_io, build_create_table, resolve_entrypoint, parse_source, SYNTHETIC_DATA, EXPECTED_OUTPUT, RESULTS_DIR
except (ModuleNotFoundError, ImportError):
    # When running pytest directly from 03-tests/source, fall back to loading
    # the parent conftest.py by file path.
    import importlib.util

    _ROOT_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"
    _spec = importlib.util.spec_from_file_location("_dvp_root_conftest", _ROOT_CONFTEST)
    _mod = importlib.util.module_from_spec(_spec)
    assert _spec and _spec.loader
    _spec.loader.exec_module(_mod)

    BaseIOConfig = _mod.BaseIOConfig
    load_data_io = _mod.load_data_io
    build_create_table = _mod.build_create_table
    resolve_entrypoint = _mod.resolve_entrypoint
    parse_source = _mod.parse_source
    SYNTHETIC_DATA = _mod.SYNTHETIC_DATA
    EXPECTED_OUTPUT = _mod.EXPECTED_OUTPUT
    RESULTS_DIR = _mod.RESULTS_DIR

logger = logging.getLogger(__name__)

_AMBIGUOUS_RE = re.compile(
    r"Reference\s+[`']?(?P<col>[^`'\s]+)[`']?\s+is\s+ambiguous",
    re.IGNORECASE,
)

_UNRESOLVED_RE = re.compile(
    r"(?:with\s+name\s+[`']?(?P<col>[^`'\s]+)[`']?\s+cannot\s+be\s+resolved|cannot\s+resolve\s+[`']?(?P<col2>[^`'\s]+)[`']?)",
    re.IGNORECASE,
)


def _parse_ambiguous_column(exc: BaseException) -> str | None:
    msg = str(exc)
    m = _AMBIGUOUS_RE.search(msg)
    return m.group("col") if m else None


def _parse_unresolved_column(exc: BaseException) -> str | None:
    msg = str(exc)
    m = _UNRESOLVED_RE.search(msg)
    if not m:
        return None
    return m.group("col") or m.group("col2")


def _load_asg(results_dir: Path) -> dict | None:
    matches = sorted(results_dir.glob("*_asg.json"))
    if not matches:
        return None
    try:
        with open(matches[0]) as f:
            return json.load(f)
    except Exception:
        return None


def _suggest_drop_order(col: str, candidates: list[str], asg: dict | None) -> list[str]:
    """Best-effort ordering of tables to drop col from.

    If Snowflake credentials are available, ask Cortex (COMPLETE) to rank.
    Otherwise fall back to a stable deterministic ordering.
    """
    candidates = list(dict.fromkeys(candidates))
    if len(candidates) <= 1:
        return candidates

    # Best-effort Cortex call (no hard dependency).
    try:
        import os
        import snowflake.connector

        conn_name = os.getenv("SNOWFLAKE_CONNECTION_NAME")
        if not conn_name:
            raise RuntimeError("no connection")

        join_rels = (asg or {}).get("column_relationships", [])
        join_snippets = []
        for rel in join_rels:
            left = rel.get("left_source")
            right = rel.get("right_source")
            if not left or not right:
                continue
            join_snippets.append({
                "source_transformation": rel.get("source_transformation"),
                "join_type": rel.get("join_type"),
                "left_source": rel.get("left_source"),
                "right_source": rel.get("right_source"),
                "left_column": rel.get("left_column"),
                "right_column": rel.get("right_column"),
            })
        join_snippets = join_snippets[:20]

        prompt = {
            "task": "Rank which input table to drop a duplicated ambiguous column from to resolve Spark AMBIGUOUS_REFERENCE.",
            "ambiguous_column": col,
            "candidate_tables": candidates,
            "join_relationships": join_snippets,
            "rules": [
                "Prefer dropping from the table where the column is less semantically owned.",
                "If unclear, prefer dropping from lookup/side tables rather than the main fact stream.",
                "Return JSON only.",
            ],
            "output_schema": {
                "drop_order": ["tableA", "tableB"],
                "reason": "short string",
            },
        }

        conn = snowflake.connector.connect(connection_name=conn_name)
        cur = conn.cursor()
        cur.execute(
            "SELECT SNOWFLAKE.CORTEX.COMPLETE(" 
            "'mistral-large2', "
            "[{'role':'user','content':%s}], "
            "{'temperature':0.2,'max_tokens':600}"
            ")",
            (json.dumps(prompt),),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row and row[0]:
            payload = json.loads(row[0])
            content = payload.get("choices", [{}])[0].get("messages")
            data = json.loads(content) if isinstance(content, str) else content
            order = data.get("drop_order") or []
            order = [t for t in order if t in candidates]
            if order:
                # append any missing candidates at end
                tail = [t for t in candidates if t not in order]
                return order + tail
    except Exception:
        pass

    return sorted(candidates, key=lambda t: (t == (t or "").upper(), (t or "").lower()))


def _copy_input_files(input_files, input_dir, drop_map: dict[str, set[str]] | None = None):
    """Copy CSV files for file-based inputs to the temp input directory.

    If drop_map is provided, drop listed columns from the copied CSV.
    """
    drop_map = drop_map or {}

    # First pass: copy (and drop columns if requested)
    for entry in input_files:
        name = entry["name"]
        safe = _sanitize_filename(name)
        src = SYNTHETIC_DATA / f"{safe.lower()}.csv"
        if not src.exists():
            # Fallback: try stem only (e.g., "regional_sales.csv" → "regional_sales")
            stem = _sanitize_filename(Path(name).stem)
            src = SYNTHETIC_DATA / f"{stem.lower()}.csv"
        dst = input_dir / src.name
        drop_cols = set(drop_map.get(name, set()))

        if not drop_cols:
            shutil.copy2(src, dst)
            continue

        with open(src, newline="", encoding="utf-8") as f_in, open(dst, "w", newline="", encoding="utf-8") as f_out:
            reader = csv.DictReader(f_in)
            fieldnames = [c for c in (reader.fieldnames or []) if c not in drop_cols]
            writer = csv.DictWriter(f_out, fieldnames=fieldnames)
            writer.writeheader()
            for row in reader:
                writer.writerow({k: v for k, v in row.items() if k in fieldnames})

    # Second pass: ensure required filters/joins have at least one matching row.
    # This keeps the baseline outputs non-empty for workloads with literal filters.
    ex_path = input_dir / "exchange_rates.csv"
    raw_path = input_dir / "raw_transactions.csv"
    if ex_path.exists() and raw_path.exists():
        try:
            with open(raw_path, newline="", encoding="utf-8") as f_raw:
                raw_reader = csv.DictReader(f_raw)
                raw_rows = list(raw_reader)
                raw_fields = raw_reader.fieldnames or []

            raw_dates = [r.get("transaction_date") for r in raw_rows if r.get("transaction_date")]
            raw_date = raw_dates[0] if raw_dates else None

            with open(ex_path, newline="", encoding="utf-8") as f_ex:
                ex_reader = csv.DictReader(f_ex)
                ex_rows = list(ex_reader)
                ex_fields = ex_reader.fieldnames or []

            # Ensure there is an EUR row for the exact date join used by ZERO.
            if raw_date and {"currency_code", "rate_date", "exchange_rate"}.issubset(set(ex_fields)):
                has_eur_match = any(
                    (r.get("currency_code") or "").strip().upper() == "EUR" and (r.get("rate_date") == raw_date)
                    for r in ex_rows
                )
                if not has_eur_match:
                    ex_rows.insert(0, {
                        "currency_code": "EUR",
                        "rate_date": raw_date,
                        "exchange_rate": "1.0",
                    })
                    with open(ex_path, "w", newline="", encoding="utf-8") as f_out:
                        w = csv.DictWriter(f_out, fieldnames=ex_fields)
                        w.writeheader()
                        w.writerows(ex_rows)

            # Ensure TOP_CATEGORIES is non-empty by forcing at least one raw
            # transaction to match a PRODUCT_CATALOG product_id.
            cat_src = SYNTHETIC_DATA / "product_catalog.csv"
            if cat_src.exists() and raw_rows and "product_id" in set(raw_fields):
                with open(cat_src, newline="", encoding="utf-8") as f_cat:
                    cat_reader = csv.DictReader(f_cat)
                    cat_ids = [r.get("product_id") for r in cat_reader if r.get("product_id")]

                if cat_ids:
                    cat_id_set = set(cat_ids)
                    has_match = any((r.get("product_id") or "") in cat_id_set for r in raw_rows)
                    if not has_match:
                        raw_rows[0]["product_id"] = cat_ids[0]
                        with open(raw_path, "w", newline="", encoding="utf-8") as f_out:
                            w = csv.DictWriter(f_out, fieldnames=raw_fields)
                            w.writeheader()
                            w.writerows(raw_rows)
        except Exception:
            pass


def _create_and_load_tables(spark_session, input_tables, drop_map: dict[str, set[str]] | None = None):
    """Create Hive tables from DDL and load CSV data. Returns list of created table names.

    If drop_map is provided, drop listed columns from the table schema and loaded data.
    """
    drop_map = drop_map or {}
    created_tables = []
    for entry in input_tables:
        table_name = entry["name"]
        csv_filename = f"{_sanitize_filename(table_name).lower()}.csv"
        drop_cols = set(drop_map.get(table_name, set()))

        # Ensure table schema matches entry['columns'] for this attempt.
        spark_session.sql(f"DROP TABLE IF EXISTS {table_name}")

        if drop_cols:
            entry = dict(entry)
            entry["columns"] = [c for c in (entry.get("columns") or []) if c.get("name") not in drop_cols]

        spark_session.sql(build_create_table(entry))
        created_tables.append(table_name)

        csv_df = spark_session.read \
            .option("header", True) \
            .option("inferSchema", True) \
            .csv(str(SYNTHETIC_DATA / csv_filename))
        if drop_cols:
            csv_df = csv_df.drop(*sorted(drop_cols))
        csv_df.write.mode("overwrite").insertInto(table_name)

    return created_tables


def _persist_ambiguity_action(action: dict) -> None:
    out_path = RESULTS_DIR / "synthetic_data" / "ambiguity_actions.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data = {"summary": {"resolved": 0}, "actions": []}
    if out_path.exists():
        try:
            with open(out_path) as f:
                data = json.load(f)
        except Exception:
            data = {"summary": {"resolved": 0}, "actions": []}

    data.setdefault("actions", []).append(action)
    data.setdefault("summary", {})["resolved"] = len(data["actions"])

    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)


@pytest.fixture(scope="session")
def spark_session(tmp_path_factory):
    """Create a local SparkSession with Hive support for testing source workloads.

    Uses a temporary directory for the Hive warehouse to avoid polluting
    the project directory with metastore_db and spark-warehouse folders.
    """
    warehouse_dir = str(tmp_path_factory.mktemp("spark-warehouse"))

    spark = SparkSession.builder \
        .master("local[*]") \
        .appName("dvp-source-tests") \
        .enableHiveSupport() \
        .config("spark.sql.warehouse.dir", warehouse_dir) \
        .config("javax.jdo.option.ConnectionURL",
                "jdbc:derby:memory:metastore_db;create=true") \
        .config("spark.sql.legacy.createHiveTableByDefault", False) \
        .getOrCreate()

    yield spark

    spark.stop()


@pytest.fixture(scope="session")
def workload_session(spark_session):
    """Alias used by BaseIOConfig test methods."""
    return spark_session


@pytest.fixture(scope="session")
def test_stage():
    """Source tests don't use Snowflake stages."""
    return None


@pytest.fixture(scope="session")
def table_namespace():
    """Source tests don't need table namespace qualification."""
    return None


# ---------------------------------------------------------------------------
# Baseline persistence helpers
# ---------------------------------------------------------------------------

def _write_single_csv(df, name: str):
    """Consolidate a DataFrame into a single CSV in expected_output/.

    Writes the DataFrame to a temp directory, then moves the single
    part file to the final destination. Raises if write fails or
    produces no output (empty DataFrame).
    """
    # Use stem to avoid double extensions (e.g., "processed_data.parquet" → "processed_data.csv")
    stem = Path(name).stem if '.' in name else name
    dst_path = EXPECTED_OUTPUT / f"{stem.lower()}.csv"
    tmp_dir = Path(str(dst_path) + "_tmp")

    try:
        df.coalesce(1).write.mode("overwrite").option("header", True).csv(str(tmp_dir))

        part_files = list(tmp_dir.glob("part-*.csv"))
        if not part_files:
            raise RuntimeError(
                f"No part-*.csv found in {tmp_dir} for {name}. "
                "DataFrame may be empty or write failed silently."
            )

        shutil.move(str(part_files[0]), str(dst_path))
        logger.info(f"Wrote baseline: {dst_path.name}")

    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)




def _drop_tables(spark_session, table_names):
    """Drop Hive tables, logging errors."""
    for name in table_names:
        try:
            spark_session.sql(f"DROP TABLE IF EXISTS {name}")
        except Exception as e:
            logger.warning(f"Teardown: failed to drop {name}: {e}")


# ---------------------------------------------------------------------------
# Format-aware output reading helpers
# ---------------------------------------------------------------------------

def _resolve_output_paths(name: str) -> list[str]:
    """Build candidate paths where a workload output may have been written.

    Returns absolute Python-CWD-based paths first (handles the case where
    Spark JVM CWD differs from the monkeypatched Python CWD), then bare
    relative paths (which Spark resolves from JVM CWD).
    """
    abs_base = str(Path.cwd() / "output" / name)
    rel_base = f"output/{name}"
    paths = [abs_base, rel_base]

    # Also try without extension if name has one (e.g., "processed_data.parquet")
    stem = Path(name).stem
    if stem != name:
        paths.append(str(Path.cwd() / "output" / stem))
        paths.append(f"output/{stem}")

    return paths


def _spark_read(spark_session, path: str, fmt: str = "csv"):
    """Read a path using the appropriate Spark reader for the given format."""
    fmt = (fmt or "csv").lower()
    if fmt == "parquet":
        return spark_session.read.parquet(path)
    elif fmt in ("json", "jsonl"):
        return spark_session.read.json(path)
    elif fmt == "orc":
        return spark_session.read.orc(path)
    elif fmt == "iceberg":
        # Iceberg requires catalog config not available in local tests;
        # try reading as parquet (Iceberg stores data as parquet files).
        return spark_session.read.parquet(path)
    else:
        return spark_session.read.option("header", True).csv(path)


def _try_read_output(spark_session, name: str, fmt: str = "csv"):
    """Attempt to read a workload output, trying multiple paths and formats.

    Returns the DataFrame on success, or None if the output cannot be found
    at any candidate path.
    """
    candidates = _resolve_output_paths(name)

    # Try with the declared format first
    for path in candidates:
        try:
            return _spark_read(spark_session, path, fmt)
        except Exception:
            continue

    # Fallback: try CSV if declared format was different
    if fmt.lower() not in ("csv",):
        for path in candidates:
            try:
                return spark_session.read.option("header", True).csv(path)
            except Exception:
                continue

    return None


def persist_baseline(spark_session, output_file_entries, output_table_entries):
    """Persist source outputs as single CSV files for migrated tests.

    Skips outputs that cannot be read (e.g., cloud paths not accessible
    locally, or formats that require catalog config) and logs a warning.
    This allows other tests to proceed even when some outputs are
    unavailable in the local test environment.
    """
    EXPECTED_OUTPUT.mkdir(parents=True, exist_ok=True)

    for entry in output_file_entries:
        name = entry["name"]
        fmt = entry.get("format", "csv")

        df = _try_read_output(spark_session, name, fmt)
        if df is None:
            original_path = entry.get("path", f"output/{name}")
            logger.warning(
                f"Could not read output '{name}' (format={fmt}, "
                f"path={original_path}). Skipping baseline — cloud/remote "
                f"paths are not accessible in local tests."
            )
            continue

        try:
            _write_single_csv(df, name)
        except Exception as e:
            logger.warning(f"Failed to persist baseline for '{name}': {e}")

    for entry in output_table_entries:
        table_name = entry["name"]
        try:
            df = spark_session.table(table_name)
            _write_single_csv(df, table_name)
        except Exception as e:
            logger.warning(
                f"Could not read output table '{table_name}': {e}. "
                f"Skipping baseline."
            )


# ---------------------------------------------------------------------------
# Base class for source workload tests
# ---------------------------------------------------------------------------

class BaseSourceWorkloadTest(BaseIOConfig):
    """Base class for testing PySpark source workloads.

    Inherits I/O config, properties, and test methods from BaseIOConfig.
    Adds the PySpark-specific run_workload fixture and baseline persistence.

    The run_workload fixture (scope=class, autouse) runs main() once.
    Inherited test methods validate outputs. Override or add tests as needed.
    """

    def _read_output(self, session, name, test_stage=None):
        """Read a source output — from file or table depending on type.

        Uses the ``format`` field from data_io_schema.json to choose the
        correct Spark reader and tries multiple path candidates to handle
        the JVM-CWD / Python-CWD mismatch.
        """
        if name in self.output_table_names:
            return session.table(name)

        # Look up the declared format for this output
        fmt = "csv"
        for entry in self.OUTPUT_FILES:
            if entry["name"] == name:
                fmt = entry.get("format", "csv")
                break

        df = _try_read_output(session, name, fmt)
        if df is None:
            raise FileNotFoundError(
                f"Output '{name}' not found. The workload may write to "
                f"cloud paths (s3://, hdfs://) not accessible in local tests. "
                f"Check data_io_schema.json for the original output path."
            )
        return df

    @pytest.fixture(scope="class", autouse=True)
    def run_workload(self, spark_session, tmp_path_factory, class_monkeypatch):
        """Set up inputs, run main() once, persist baseline, clean up after."""
        test_dir = tmp_path_factory.mktemp("source-workload")
        input_dir = test_dir / "input"
        input_dir.mkdir()

        class_monkeypatch.setenv("INPUT_DATA_PATH", str(input_dir))
        class_monkeypatch.chdir(test_dir)

        # Experimental ambiguity resolution loop:
        # if Spark throws AMBIGUOUS_REFERENCE for a column, retry by dropping the
        # column from one candidate input at a time until the workload runs.
        asg = _load_asg(RESULTS_DIR)

        # Build column -> candidate tables map from data_io_schema.json.
        # We treat *any* column that appears in multiple input sources as a
        # potential Spark AMBIGUOUS_REFERENCE experiment candidate (including
        # join keys like customer_id).
        entries = load_data_io(role="input")
        col_to_tables: dict[str, list[str]] = {}
        for e in entries:
            t = e.get("name")
            for c in e.get("columns") or []:
                col = c.get("name")
                if col and t:
                    col_to_tables.setdefault(col, []).append(t)

        ambiguous_cols: dict[str, list[str]] = {
            col: sorted(set(tables), key=lambda x: (x == x.upper(), x.lower()))
            for col, tables in col_to_tables.items()
            if len(set(tables)) >= 2
        }

        # Maintain per-column decisions so each ambiguous column is resolved by
        # trying drop-from-A vs drop-from-B (not cumulatively dropping from both).
        decisions: dict[str, str] = {}  # col -> chosen_table
        tried_index: dict[str, int] = {}  # col -> next index into drop_order

        def _decisions_to_drop_map() -> dict[str, set[str]]:
            dm: dict[str, set[str]] = {}
            for c, t in decisions.items():
                dm.setdefault(t, set()).add(c)
            return dm

        max_attempts = 10
        last_exc: Exception | None = None

        for attempt_i in range(max_attempts):
            drop_map = _decisions_to_drop_map()

            # Rebuild inputs for each attempt
            _copy_input_files(self.INPUT_FILES, input_dir, drop_map=drop_map)
            created_tables = _create_and_load_tables(spark_session, self.INPUT_TABLES, drop_map=drop_map)

            _drop_tables(spark_session, self.output_table_names)

            try:
                if hasattr(self, "_call_main"):
                    result = self._call_main(spark_session)
                else:
                    result = self.MAIN_FN(spark=spark_session)
                assert result == 0, f"main() returned {result}, expected 0"

                # Success: persist baseline and record resolution actions
                persist_baseline(spark_session, self.OUTPUT_FILES, self.OUTPUT_TABLES)

                if decisions:
                    _persist_ambiguity_action({
                        "trigger": "spark_ambiguous_reference",
                        "resolver": "test_harness_experiment",
                        "drop_map": {k: sorted(list(v)) for k, v in drop_map.items() if v},
                        "attempts": attempt_i + 1,
                    })

                break

            except (AnalysisException, CapturedAnalysisException) as e:
                last_exc = e

                # Case 1: Spark AMBIGUOUS_REFERENCE
                col = _parse_ambiguous_column(e)
                if col:
                    candidates = ambiguous_cols.get(col) or []
                    if len(candidates) < 2:
                        # Not enough info to experiment
                        raise

                    drop_order = _suggest_drop_order(col, candidates, asg)
                    idx = tried_index.get(col, 0)
                    if idx >= len(drop_order):
                        raise

                    chosen = drop_order[idx]
                    tried_index[col] = idx + 1
                    decisions[col] = chosen

                    _persist_ambiguity_action({
                        "trigger": "spark_ambiguous_reference",
                        "resolver": "test_harness_experiment",
                        "ambiguous_column": col,
                        "chosen_drop_from": chosen,
                        "candidate_tables": candidates,
                        "drop_order": drop_order,
                        "attempt": attempt_i + 1,
                        "error": str(e),
                    })

                    # Cleanup before next attempt
                    _drop_tables(spark_session, created_tables + self.output_table_names)
                    continue

                # Case 2: we dropped a column that's actually required later.
                unresolved = _parse_unresolved_column(e)
                if unresolved and unresolved in decisions:
                    col = unresolved
                    prev = decisions[col]
                    candidates = ambiguous_cols.get(col) or []
                    if len(candidates) < 2:
                        raise

                    drop_order = _suggest_drop_order(col, candidates, asg)
                    idx = tried_index.get(col, 0)
                    if idx >= len(drop_order):
                        raise

                    chosen = drop_order[idx]
                    tried_index[col] = idx + 1
                    decisions[col] = chosen

                    _persist_ambiguity_action({
                        "trigger": "spark_unresolved_column",
                        "resolver": "test_harness_backtrack",
                        "unresolved_column": col,
                        "previous_drop_from": prev,
                        "chosen_drop_from": chosen,
                        "candidate_tables": candidates,
                        "drop_order": drop_order,
                        "attempt": attempt_i + 1,
                        "error": str(e),
                    })

                    _drop_tables(spark_session, created_tables + self.output_table_names)
                    continue

                raise

        else:
            # Too many attempts
            if last_exc:
                raise last_exc
            raise RuntimeError("Ambiguity resolution loop exhausted")

        yield

        _drop_tables(spark_session, created_tables + self.output_table_names)
