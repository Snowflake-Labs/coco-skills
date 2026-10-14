"""Shared pytest configuration for DVP tests (source and migrated)."""

import csv
import functools
import importlib.util
import json
import logging
import re
import sys
import types
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

# Configure logging format for all DVP tests
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

TESTS_ROOT = Path(__file__).resolve().parent

# Make this conftest importable by test modules under --import-mode=importlib.
# Without this, `from conftest import SOURCE_DIR` fails because importlib mode
# does not add the rootdir to sys.path automatically.
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))
DVP_ROOT = TESTS_ROOT.parent
SOURCE_DIR = DVP_ROOT / '01-source'
MIGRATED_DIR = DVP_ROOT / '02-migrated'
MIGRATED_SCOS_DIR = DVP_ROOT / '02-migrated_scos'
RESULTS_DIR = DVP_ROOT / '04-results'
SYNTHETIC_DATA = RESULTS_DIR / 'synthetic_data'
EXPECTED_OUTPUT = TESTS_ROOT / 'data' / 'expected_output'
DATA_IO_SCHEMA_JSON = RESULTS_DIR / 'data_io_schema.json'


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


@functools.lru_cache(maxsize=None)
def _load_data_io_raw(path: str = None) -> tuple:
    """Read a data_io_schema.json file once and cache per path."""
    p = Path(path) if path else DATA_IO_SCHEMA_JSON
    with open(p) as f:
        return tuple(json.load(f))


def load_data_io(
    role: str = None,
    io_type: str = None,
    path: str | Path = None,
) -> list[dict]:
    """Load and optionally filter entries from a data_io_schema.json file.

    Args:
        role: Filter by "input" or "output".
        io_type: Filter by "file" or "table".
        path: Path to data_io_schema.json. Defaults to 04-results/data_io_schema.json.

    Usage::

        all_entries = load_data_io()
        output_files = load_data_io(role="output", io_type="file")
        input_tables = load_data_io(role="input", io_type="table")

        # Multi-workload: point to a specific data_io_schema.json
        entries = load_data_io(path=RESULTS_DIR / "workload2/data_io_schema.json")
    """
    entries = list(_load_data_io_raw(str(path) if path else None))
    if role:
        entries = [e for e in entries if e["role"] == role]
    if io_type:
        entries = [e for e in entries if e["type"] == io_type]
    return entries


def derive_key_columns(
    path: str | Path = None,
) -> dict[str, list[str]]:
    """Build a {output_name: key_columns} map from data_io_schema.json.

    Uses the ``key_columns`` field if present on an output entry,
    otherwise falls back to the first column name.

    Usage::

        KEY_COLUMNS = derive_key_columns()
        # → {"DAILY_SALES_SUMMARY": ["sale_date"], "CUSTOMER_CLV": ["customer_id"], ...}
    """
    outputs = load_data_io(role="output", path=path)
    result = {}
    for entry in outputs:
        if "key_columns" in entry:
            result[entry["name"]] = entry["key_columns"]
        elif entry.get("columns"):
            result[entry["name"]] = [entry["columns"][0]["name"]]
    return result


def build_create_table(entry: dict) -> str:
    """Generate CREATE TABLE statement from columns definition.

    Usage::

        ddl = build_create_table(entry)
        session.sql(ddl).collect()
    """
    cols = ", ".join(f"{c['name']} {c['type']}" for c in entry["columns"])
    return f"CREATE TABLE IF NOT EXISTS {entry['name']} ({cols});"


def import_from(file_path: Path) -> types.ModuleType:
    """Import a module from an explicit file path (no sys.path pollution).

    Avoids name collisions when source and migrated have files with the
    same name. Each test file declares exactly which module it imports.

    The file's parent directory is temporarily added to sys.path so that
    sibling imports (e.g., ``from MyFile3 import *``) resolve correctly.

    Usage::

        workload = import_from(MIGRATED / 'workload.py')
        workload.main(session=session)
    """
    file_path = Path(file_path)
    parent = str(file_path.parent)
    added = parent not in sys.path
    if added:
        sys.path.insert(0, parent)
    try:
        spec = importlib.util.spec_from_file_location(file_path.stem, file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if added and parent in sys.path:
            sys.path.remove(parent)


def parse_source(value: str) -> dict:
    """Parse the hybrid source format into components.

    Format: ``<path>:<lineno>(::segment)*``

    Returns a dict with keys ``file``, ``lineno``, and optionally
    ``scope`` (list) and ``method`` (str).  The last ``::`` segment is
    always ``method``; any preceding segments form ``scope``.

    Examples::

        parse_source("workload.py:134")
        # -> {"file": "workload.py", "lineno": 134}

        parse_source("workload.py:295::main_entrypoint")
        # -> {"file": "workload.py", "lineno": 295, "method": "main_entrypoint"}

        parse_source("App.scala:5::GlobalTransactions::main")
        # -> {"file": "App.scala", "lineno": 5, "scope": ["GlobalTransactions"], "method": "main"}

        parse_source("file.py:10::Outer::Inner::run")
        # -> {"file": "file.py", "lineno": 10, "scope": ["Outer", "Inner"], "method": "run"}
    """
    segments = value.split("::")
    file_lineno = segments[0].rsplit(":", 1)
    result = {"file": file_lineno[0], "lineno": int(file_lineno[1])}
    qualname = segments[1:]
    if qualname:
        result["method"] = qualname[-1]
        if len(qualname) > 1:
            result["scope"] = qualname[:-1]
    return result


@functools.lru_cache(maxsize=None)
def _load_entrypoints() -> tuple:
    ep_path = RESULTS_DIR / "entrypoints.json"
    with open(ep_path) as f:
        return tuple(json.load(f))


def resolve_entrypoint(source_file: str, base_dir: Path) -> tuple[types.ModuleType, callable]:
    """Resolve an entrypoint from entrypoints.json for a given source file.

    Looks up the first ``detected`` entrypoint matching *source_file*,
    reads ``adapted_source`` (falls back to ``source``), imports the
    module and returns ``(module, callable)``.

    Usage::

        mod, fn = resolve_entrypoint("workload.py", SOURCE_DIR)
        fn(spark=session)
    """
    entries = _load_entrypoints()
    match = None
    for ep in entries:
        if ep.get("status") != "detected":
            continue
        parsed = parse_source(ep.get("adapted_source") or ep["source"])
        if parsed["file"] == source_file or ep.get("name") == Path(source_file).stem:
            match = (ep, parsed)
            break

    if match is None:
        raise LookupError(
            f"No detected entrypoint found for '{source_file}' in entrypoints.json"
        )

    ep, parsed = match
    module_path = base_dir / parsed["file"]
    mod = import_from(module_path)

    method_name = parsed.get("method")
    if method_name:
        fn = getattr(mod, method_name)
    else:
        fn = getattr(mod, "main", None) or getattr(mod, ep["name"], None)
        if fn is None:
            raise AttributeError(
                f"Module {parsed['file']} has no callable for entrypoint '{ep['name']}'"
            )
    return mod, fn


def read_expected_csv(output_name: str) -> list[dict]:
    """Read an expected output CSV and return rows as list of dicts.

    Looks for the file in data/expected_output/<output_name>.csv
    """
    csv_path = EXPECTED_OUTPUT / f"{output_name.lower()}.csv"
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Expected output not found: {csv_path}\n"
            "Run source tests first to generate baseline outputs."
        )
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def compare_row_counts(actual_count: int, expected_count: int, output_name: str):
    """Assert row counts match between actual and expected."""
    assert actual_count == expected_count, (
        f"{output_name}: row count mismatch — "
        f"expected {expected_count} (from source baseline), got {actual_count} (migrated)"
    )


def compare_schemas(
    actual_columns: list[str],
    expected_columns: list[str],
    case_sensitive: bool = False,
) -> tuple[bool, str]:
    """Validate that columns match in name and order.

    Args:
        actual_columns: Column names from migrated output.
        expected_columns: Column names from source baseline.
        case_sensitive: If False, compare uppercase versions.

    Returns:
        (passed, message): True if schemas match, error message if not.
    """
    if not case_sensitive:
        actual = [c.upper() for c in actual_columns]
        expected = [c.upper() for c in expected_columns]
    else:
        actual = list(actual_columns)
        expected = list(expected_columns)

    if actual == expected:
        return True, ""

    missing = set(expected) - set(actual)
    extra = set(actual) - set(expected)

    errors = []
    if missing:
        errors.append(f"missing columns: {sorted(missing)}")
    if extra:
        errors.append(f"extra columns: {sorted(extra)}")
    if not missing and not extra and actual != expected:
        errors.append(f"column order mismatch: expected {expected}, got {actual}")

    return False, "; ".join(errors)


def _is_numeric(value) -> bool:
    """Check if value can be treated as numeric."""
    if value is None:
        return False
    try:
        float(value)
        return True
    except (ValueError, TypeError):
        return False


def _values_equal(expected, actual, tolerance: float = None) -> bool:
    """Compare values with optional numeric tolerance.

    When both values are numeric, compares as floats (avoids string
    mismatches like '400.5' vs Decimal('400.50')). Falls back to stripped
    case-insensitive string comparison for non-numeric values.

    Treats None and empty string as equivalent because baseline CSVs
    represent missing values as empty strings.

    Handles boolean case mismatch: CSV stores 'false'/'true' (lowercase),
    PySpark returns Python False/True where str(False) = 'False' (capital).

    Handles datetime comparison: CSV stores ISO strings (possibly with
    timezone), PySpark returns datetime.datetime objects.
    """
    if expected in (None, "") and actual in (None, ""):
        return True
    if _is_numeric(expected) and _is_numeric(actual):
        return abs(float(expected) - float(actual)) <= (tolerance or 0)
    exp_s = str(expected).strip().lower()
    act_s = str(actual).strip().lower()
    if exp_s == act_s:
        return True
    import datetime
    if isinstance(actual, datetime.datetime):
        act_s = actual.isoformat().lower()
        if exp_s.startswith(act_s):
            return True
        try:
            for fmt in ("%Y-%m-%dt%H:%M:%S.%f%z", "%Y-%m-%dt%H:%M:%S%z",
                        "%Y-%m-%dt%H:%M:%S.%f", "%Y-%m-%dt%H:%M:%S", "%Y-%m-%d"):
                try:
                    parsed = datetime.datetime.strptime(exp_s, fmt)
                    if parsed.replace(tzinfo=None) == (
                        actual.replace(tzinfo=None) if hasattr(actual, 'replace') else actual
                    ):
                        return True
                except ValueError:
                    continue
        except Exception:
            pass
    return False


def compare_dataframes(
    actual_df,
    expected_rows: list[dict],
    key_columns: list[str],
    tolerance: float = None,
) -> tuple[bool, str]:
    """Compare DataFrame row values against expected baseline.

    Assumes schema is already validated via compare_schemas().
    Column names are normalized to lowercase for matching.

    Args:
        actual_df: Snowpark/PySpark DataFrame to validate.
        expected_rows: List of dicts from read_expected_csv().
        key_columns: Columns to use for row matching/sorting.
        tolerance: Numeric tolerance for float comparisons.

    Returns:
        (passed, error_message): True if match, error details if not.
    """
    # 1. Early exit: count check (no collect needed)
    actual_count = actual_df.count()
    expected_count = len(expected_rows)
    if actual_count != expected_count:
        return False, f"Row count: expected {expected_count}, got {actual_count}"

    # 2. Collect and normalize to lowercase keys
    actual_rows = [{k.lower(): v for k, v in r.asDict().items()} for r in actual_df.collect()]
    expected_norm = [{k.lower(): v for k, v in r.items()} for r in expected_rows]
    key_cols = [k.lower() for k in key_columns]

    # 3. Sort both by key columns
    sort_key = lambda row: tuple("" if row.get(k) is None else str(row.get(k, "")) for k in key_cols)
    actual_sorted = sorted(actual_rows, key=sort_key)
    expected_sorted = sorted(expected_norm, key=sort_key)

    # 4. Compare row by row
    cols = list(expected_sorted[0].keys())
    for i, (actual, expected) in enumerate(zip(actual_sorted, expected_sorted)):
        for col in cols:
            if not _values_equal(expected.get(col), actual.get(col), tolerance):
                key_info = ", ".join(f"{k}={actual.get(k)}" for k in key_cols)
                return False, (
                    f"Row {i} ({key_info}), column '{col}': "
                    f"expected {expected.get(col)!r}, got {actual.get(col)!r}"
                )

    return True, ""



def compare_data(
    actual_df,
    expected_rows: list[dict],
    key_columns: list[str],
    tolerance: float = None,
) -> tuple[bool, str]:
    """Compare schema and row values between DataFrame and expected baseline.

    Combines compare_schemas() and compare_dataframes() into a single call.
    Validates column structure first, then compares row values.

    Args:
        actual_df: Snowpark/PySpark DataFrame to validate.
        expected_rows: List of dicts from read_expected_csv().
        key_columns: Columns to use for row matching/sorting.
        tolerance: Numeric tolerance for float comparisons.

    Returns:
        (passed, error_message): True if match, error details if not.

    Usage::

        expected = read_expected_csv("DAILY_SALES_SUMMARY")
        df = session.table("DAILY_SALES_SUMMARY")
        passed, msg = compare_data(df, expected, key_columns=["sale_date"])
        assert passed, f"DAILY_SALES_SUMMARY: {msg}"
    """
    actual_cols = [f.name for f in actual_df.schema.fields]
    expected_cols = list(expected_rows[0].keys())

    ok, msg = compare_schemas(actual_cols, expected_cols)
    if not ok:
        return False, f"schema: {msg}"

    return compare_dataframes(
        actual_df, expected_rows,
        key_columns=key_columns, tolerance=tolerance,
    )


class BaseIOConfig:
    """Common ancestor for all workload test classes (source, migrated, SCOS).

    Holds the declarative I/O configuration and name-extraction properties
    shared by every test flavour.  Also provides the test methods that
    validate outputs — each subclass only needs to implement
    ``_read_output()`` and a ``run_workload`` fixture.

    Subclasses must set::

        MAIN_FN       — callable: the workload entry point (e.g. main)
        INPUT_FILES   — list[dict]: entries from data_io_schema.json (role=input, type=file)
        INPUT_TABLES  — list[dict]: entries from data_io_schema.json (role=input, type=table)
        OUTPUT_FILES  — list[dict]: entries from data_io_schema.json (role=output, type=file)
        OUTPUT_TABLES — list[dict]: entries from data_io_schema.json (role=output, type=table)

    KEY_COLUMNS is auto-derived from the ``key_columns`` field in the
    output entries (or falls back to the first column).  Override it
    explicitly only when the auto-derived keys are wrong.
    """

    MAIN_FN = None
    INPUT_FILES = []
    INPUT_TABLES = []
    OUTPUT_FILES = []
    OUTPUT_TABLES = []
    KEY_COLUMNS = None
    TABLE_NAMESPACE = None

    @property
    def input_file_names(self):
        return [f"{_sanitize_filename(e['name']).lower()}.csv" for e in self.INPUT_FILES]

    @property
    def input_table_map(self):
        """Dict of {table_name: csv_filename} for input tables."""
        return {
            e['name']: f"{_sanitize_filename(e['name']).lower()}.csv"
            for e in self.INPUT_TABLES
        }

    @property
    def output_file_names(self):
        return [e["name"] for e in self.OUTPUT_FILES]

    @property
    def output_table_names(self):
        return [e["name"] for e in self.OUTPUT_TABLES]

    @property
    def all_output_names(self):
        return self.output_file_names + self.output_table_names

    @property
    def key_columns_map(self) -> dict[str, list[str]]:
        """Map of {output_name: key_columns} for row matching/sorting.

        Uses explicit KEY_COLUMNS if set, otherwise auto-derives from
        the ``key_columns`` field in OUTPUT_FILES/OUTPUT_TABLES entries
        (falls back to the first column name).
        """
        if self.KEY_COLUMNS is not None:
            return self.KEY_COLUMNS
        result = {}
        for entry in self.OUTPUT_FILES + self.OUTPUT_TABLES:
            if "key_columns" in entry:
                result[entry["name"]] = entry["key_columns"]
            elif entry.get("columns"):
                result[entry["name"]] = [entry["columns"][0]["name"]]
        return result

    def _read_output(self, session, name, test_stage=None):
        raise NotImplementedError("Subclasses must implement _read_output")

    # -- Shared test methods ----------------------------------------------

    def test_all_outputs_have_data(self, workload_session, test_stage, subtests):
        """Verify all outputs exist and have at least one row."""
        for name in self.all_output_names:
            with subtests.test(msg=name):
                df = self._read_output(workload_session, name, test_stage)
                assert df.count() > 0, f"Output {name} is empty"

    def test_all_outputs_match_baseline_row_count(self, workload_session, test_stage, subtests):
        """Verify outputs have the same row count as source baseline."""
        for name in self.all_output_names:
            with subtests.test(msg=name):
                expected_rows = read_expected_csv(name)
                df = self._read_output(workload_session, name, test_stage)
                compare_row_counts(df.count(), len(expected_rows), name)

    def test_all_outputs_match_baseline(self, workload_session, test_stage, subtests):
        """Verify all outputs match source baseline (schema + values)."""
        key_map = self.key_columns_map
        for name in self.all_output_names:
            with subtests.test(msg=name):
                expected = read_expected_csv(name)
                if not expected:
                    pytest.skip(f"No baseline for {name}")

                df = self._read_output(workload_session, name, test_stage)
                keys = key_map.get(name, list(expected[0].keys())[:1])

                ok, msg = compare_data(df, expected, key_columns=keys)
                assert ok, f"{name}: {msg}"


class BaseWorkloadTest(BaseIOConfig):
    """Base class for migrated workload tests (Snowpark and SCOS).

    Inherits I/O config, properties, and test methods from BaseIOConfig.
    Adds the Snowflake-specific run_workload fixture.

    Infrastructure-specific subclasses (BaseMigratedWorkloadTest,
    BaseScosWorkloadTest) must implement _read_output().  They must also
    provide a ``db_session`` fixture that aliases the appropriate session
    (snowpark_session or scos_spark_session).
    """

    @pytest.fixture(scope="class", autouse=True)
    def _set_table_namespace(self, table_namespace):
        """Propagate the DB.SCHEMA namespace to a class attribute.

        SCOS _read_output uses self.TABLE_NAMESPACE for fully qualified
        table names.  Migrated _read_output ignores it (uses session context).
        """
        type(self).TABLE_NAMESPACE = table_namespace

    @pytest.fixture(scope="class", autouse=True)
    def run_workload(self, snowpark_session, db_session, test_stage, class_monkeypatch):
        """Set up inputs, run main() once, and clean up after all tests.

        Uses snowpark_session for Snowflake infrastructure (PUT, DDL, cleanup)
        and db_session for the workload call (Snowpark or SCOS depending on
        which sub-conftest defines the db_session alias).
        """
        class_monkeypatch.setenv("INPUT_DATA_STAGE", test_stage)
        class_monkeypatch.setenv("OUTPUT_DATA_STAGE", test_stage)

        uploaded_files = upload_stage_files(
            snowpark_session, test_stage, self.input_file_names,
        )
        created_tables, table_files = create_and_load_tables(
            snowpark_session, test_stage, self.input_table_map,
        )
        uploaded_files.extend(table_files)

        cleanup_snowflake(
            snowpark_session, test_stage,
            self.output_table_names, self.output_file_names,
            strict=True,
        )

        if hasattr(self, "_call_main"):
            result = self._call_main(db_session)
        else:
            result = self.MAIN_FN(spark=db_session)
        assert result == 0, f"main() returned {result}, expected 0"

        yield

        cleanup_snowflake(
            snowpark_session, test_stage,
            created_tables, uploaded_files,
        )
        cleanup_snowflake(
            snowpark_session, test_stage,
            self.output_table_names, self.output_file_names,
        )


@pytest.fixture(scope="class")
def class_monkeypatch():
    """Class-scoped monkeypatch — auto-restores env vars after the class."""
    mp = MonkeyPatch()
    yield mp
    mp.undo()


# ---------------------------------------------------------------------------
# Shared Snowflake fixtures (used by migrated and migrated_scos tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def snowpark_session():
    """Create Snowpark session connected to Snowflake.

    Used by both migrated (Snowpark API) and SCOS (Snowpark Connect) tests
    for infrastructure operations and — in the migrated case — as the
    workload session itself.

    Imports are deferred so source-only environments don't need the
    snowflake-snowpark-python package installed.
    """
    from snowflake.snowpark import Session
    from config import TEST_ROLE, TEST_DATABASE, TEST_SCHEMA, CONNECTION_NAME

    session = Session.builder.config("connection_name", CONNECTION_NAME).create()
    import warnings
    if TEST_ROLE:
        try:
            session.sql(f"USE ROLE {TEST_ROLE}").collect()
        except Exception as e:
            warnings.warn(f"Could not USE ROLE {TEST_ROLE}: {e}. Using connection default.")
    if TEST_DATABASE:
        try:
            session.sql(f"USE DATABASE {TEST_DATABASE}").collect()
        except Exception as e:
            warnings.warn(f"Could not USE DATABASE {TEST_DATABASE}: {e}. Using connection default.")
    if TEST_SCHEMA:
        try:
            session.sql(f"USE SCHEMA {TEST_SCHEMA}").collect()
        except Exception as e:
            warnings.warn(f"Could not USE SCHEMA {TEST_SCHEMA}: {e}. Using connection default.")
    yield session
    session.close()


@pytest.fixture(scope="session")
def table_namespace(snowpark_session):
    """Fully qualified DB.SCHEMA prefix for sessions that need it (e.g. SCOS).

    Derived from the snowpark_session which already has USE DATABASE/SCHEMA
    applied, so it stays consistent with the test configuration.
    """
    from config import TEST_DATABASE, TEST_SCHEMA
    return f"{TEST_DATABASE}.{TEST_SCHEMA}"


@pytest.fixture(scope="session")
def test_stage(snowpark_session):
    """Ensure test stage exists and return its qualified path."""
    from config import TEST_DATABASE, TEST_SCHEMA, TEST_STAGE

    snowpark_session.sql(f"CREATE STAGE IF NOT EXISTS {TEST_STAGE}").collect()
    return f"@{TEST_DATABASE}.{TEST_SCHEMA}.{TEST_STAGE}"


# ---------------------------------------------------------------------------
# Shared Snowflake helpers (infrastructure operations via Snowpark session)
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _table_entries_map() -> dict[str, dict]:
    """Build a {table_name: entry} lookup from data_io_schema.json (cached)."""
    return {e["name"]: e for e in load_data_io(io_type="table")}


def get_table_entry(name: str) -> dict:
    """Look up table entry by name from data_io_schema.json."""
    try:
        return _table_entries_map()[name]
    except KeyError:
        raise KeyError(f"Table {name} not found in data_io_schema.json")


def upload_stage_files(snowpark_session, test_stage, filenames):
    """Upload CSV files to a Snowflake stage. Returns list of uploaded names."""
    uploaded = []
    for filename in filenames:
        local_path = str(SYNTHETIC_DATA / filename)
        snowpark_session.file.put(
            local_path, test_stage,
            auto_compress=False, overwrite=True,
        )
        uploaded.append(filename)
    return uploaded


def create_and_load_tables(snowpark_session, test_stage, input_tables):
    """Create tables from DDL and load CSV data via stage. Returns (tables, files)."""
    created_tables = []
    uploaded_files = []
    for table_name, csv_filename in input_tables.items():
        entry = get_table_entry(table_name)
        snowpark_session.sql(build_create_table(entry)).collect()
        created_tables.append(table_name)

        local_path = str(SYNTHETIC_DATA / csv_filename)
        snowpark_session.file.put(
            local_path, f"{test_stage}/{table_name}",
            auto_compress=False, overwrite=True,
        )
        try:
            snowpark_session.sql(f"""
                COPY INTO {table_name}
                FROM {test_stage}/{table_name}
                FILE_FORMAT = (TYPE = 'CSV' SKIP_HEADER = 1
                               FIELD_OPTIONALLY_ENCLOSED_BY = '"')
                ON_ERROR = 'ABORT_STATEMENT'
            """).collect()
        except Exception as e:
            raise RuntimeError(
                f"Failed to load {csv_filename} into {table_name}: {e}"
            ) from e
        uploaded_files.append(f"{table_name}/{csv_filename}")
    return created_tables, uploaded_files


def cleanup_snowflake(snowpark_session, test_stage, tables, stage_files, strict=False):
    """Drop tables and remove stage files.

    When strict=True, raises on first failure (use before running workload).
    When strict=False, warns and continues (use for post-run teardown).
    """
    for table_name in tables:
        try:
            snowpark_session.sql(f"DROP TABLE IF EXISTS {table_name}").collect()
        except Exception as e:
            if strict:
                raise
            logger.warning(f"Teardown: failed to drop {table_name}: {e}")
    for name in stage_files:
        try:
            snowpark_session.sql(f"REMOVE {test_stage}/{name}").collect()
        except Exception as e:
            if strict:
                raise
            logger.warning(f"Teardown: failed to remove {test_stage}/{name}: {e}")


# ---------------------------------------------------------------------------
# Test result tracking — auto-record results in sma_storage.sqlite3
# ---------------------------------------------------------------------------

_WORKLOAD_PATH = DVP_ROOT.parent
_DB_PATH = _WORKLOAD_PATH / "sma_storage.sqlite3"
_TRACKING_ENABLED = None
_TEST_ID_CACHE: dict[tuple[str, str], int] = {}

_STATUS_MAP = {
    "passed": "passed",
    "failed": "failed",
    "skipped": "skipped",
}


def _is_tracking_enabled() -> bool:
    global _TRACKING_ENABLED
    if _TRACKING_ENABLED is not None:
        return _TRACKING_ENABLED
    try:
        if not _DB_PATH.exists():
            _TRACKING_ENABLED = False
            return False
        import sqlite3
        conn = sqlite3.connect(str(_DB_PATH))
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entrypoint_tests'")
        _TRACKING_ENABLED = cur.fetchone() is not None
        conn.close()
    except Exception:
        _TRACKING_ENABLED = False
    return _TRACKING_ENABLED


def _lookup_test_id(entrypoint_name: str, test_type: str) -> int | None:
    # Normalize: strip hyphens/underscores and lowercase so
    # "MyFile" ↔ "my_file" and "pyspark-add-month" ↔ "pyspark_add_month" all match
    norm = entrypoint_name.lower().replace("-", "").replace("_", "")
    cache_key = (norm, test_type)
    if cache_key in _TEST_ID_CACHE:
        return _TEST_ID_CACHE[cache_key]
    try:
        import sqlite3
        conn = sqlite3.connect(str(_DB_PATH))
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM entrypoint_tests WHERE LOWER(REPLACE(REPLACE(entrypoint_name, '-', ''), '_', '')) = ? AND test_type = ?",
            (norm, test_type),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            _TEST_ID_CACHE[cache_key] = row[0]
            return row[0]
    except Exception:
        pass
    return None


def _record_test_run(test_id: int, status: str, error_message: str | None, duration: float | None, test_method: str | None = None):
    try:
        import sqlite3
        conn = sqlite3.connect(str(_DB_PATH))
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO entrypoint_test_runs (test_id, test_method, status, error_message, duration_seconds) VALUES (?, ?, ?, ?, ?)",
            (test_id, test_method, status, error_message, duration),
        )
        cur.execute("UPDATE entrypoint_tests SET status = ? WHERE id = ?", (status, test_id))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _extract_entrypoint_info(item) -> tuple[str, str] | None:
    test_path = Path(item.fspath)
    stem = test_path.stem
    if stem.startswith("test_"):
        stem = stem[5:]
    entrypoint_name = stem

    parent_name = test_path.parent.name
    if parent_name == "source":
        test_type = "source"
    elif parent_name in ("migrated", "migrated_scos"):
        test_type = parent_name
    else:
        return None

    return entrypoint_name, test_type


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    if not _is_tracking_enabled():
        return

    # Record results from the "call" phase, and also setup/teardown failures.
    # Setup errors (e.g. SparkSession creation failure) would otherwise go unrecorded.
    if report.when == "call":
        status = _STATUS_MAP.get(report.outcome, "error")
    elif report.when in ("setup", "teardown") and report.failed:
        status = "error"
    else:
        return

    info = _extract_entrypoint_info(item)
    if not info:
        return

    entrypoint_name, test_type = info
    test_id = _lookup_test_id(entrypoint_name, test_type)
    if test_id is None:
        return

    error_message = None
    if report.failed and report.longrepr:
        error_message = str(report.longrepr)[:2000]

    _record_test_run(test_id, status, error_message, report.duration, test_method=item.name)
