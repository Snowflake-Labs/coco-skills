"""
Shared fixtures for entity-resolution integration tests.

Three-phase test structure
--------------------------
Phase 1 (setup)    — Create source tables in Snowflake from fixture SQL.
Phase 2 (invoke)   — Invoke the entity-resolution skill via ``cortex -p``.
Phase 3 (validate) — Verify the Snowflake objects the skill created.

Each phase can be run independently.  The ephemeral schema name is
persisted to ``.test_schema`` so that Phase 2 and 3 can reuse the schema
created in Phase 1 without re-creating it.

Connection setup
~~~~~~~~~~~~~~~~
Set env vars (or use the checked-in .env with ``uv run --env-file .env``):

    SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD,
    SNOWFLAKE_WAREHOUSE, SNOWFLAKE_DATABASE, SNOWFLAKE_ROLE

Optionally set CORTEX_CONNECTION to the named connection to pass via ``-c``.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest
import snowflake.connector

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SKILL_DIR = Path(__file__).parent.parent.parent          # skills/entity-resolution
REPO_ROOT = SKILL_DIR.parent.parent                       # cortex-code-skills
SRC_DIR = Path(__file__).parent.parent                    # skills/entity-resolution/src
SCHEMA_FILE = SRC_DIR / ".test_schema"

# How long to wait (seconds) for a single CoCo skill invocation.
DEFAULT_INVOKE_TIMEOUT = 900


# ---------------------------------------------------------------------------
# Schema persistence helpers
# ---------------------------------------------------------------------------

def _write_schema(name: str) -> None:
    """Persist the test schema name so subsequent phases can reuse it."""
    SCHEMA_FILE.write_text(name)


def _read_schema() -> str:
    """Read the persisted test schema name.  Raises if not found."""
    if not SCHEMA_FILE.exists():
        raise RuntimeError(
            "No .test_schema file found.  Run Phase 1 (setup) first:\n"
            "  make setup-pharma   (or whichever domain)"
        )
    name = SCHEMA_FILE.read_text().strip()
    if not name:
        raise RuntimeError(".test_schema file is empty.  Re-run Phase 1.")
    return name


# ---------------------------------------------------------------------------
# Session-scoped Snowflake connection
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sf_connection():
    """Return a live Snowflake connection for the entire test session."""
    conn = snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "ENTITY_RESOLUTION_TEST"),
        role=os.environ.get("SNOWFLAKE_ROLE"),
        authenticator=os.environ.get("SNOWFLAKE_AUTHENTICATOR", "snowflake"),
    )
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Schema fixtures — Phase 1 creates, Phase 2/3 reuse
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_schema_create(sf_connection):
    """Phase 1: create a new ephemeral schema and persist its name."""
    schema = f"ER_TEST_{uuid.uuid4().hex[:8].upper()}"
    cur = sf_connection.cursor()
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    cur.execute(f"USE SCHEMA {schema}")
    _write_schema(schema)
    yield schema
    # NOTE: no automatic teardown — use ``make clean`` to drop explicitly.
    cur.close()


@pytest.fixture(scope="session")
def test_schema(sf_connection):
    """Phase 2 & 3: load the schema name persisted by Phase 1."""
    schema = _read_schema()
    cur = sf_connection.cursor()
    cur.execute(f"USE SCHEMA {schema}")
    yield schema
    cur.close()


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def run_sql(sf_connection, test_schema):
    """Execute one or more SQL statements.  Returns rows for the last one."""

    def _run(sql: str, *, fetch: bool = True):
        cur = sf_connection.cursor()
        cur.execute(f"USE SCHEMA {test_schema}")
        for stmt in _split_statements(sql):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
        if fetch:
            try:
                return cur.fetchall()
            except snowflake.connector.errors.ProgrammingError:
                return []
        return []

    return _run


@pytest.fixture(scope="session")
def run_sql_setup(sf_connection, test_schema_create):
    """Like run_sql but uses the Phase 1 schema (test_schema_create)."""

    def _run(sql: str, *, fetch: bool = True):
        cur = sf_connection.cursor()
        cur.execute(f"USE SCHEMA {test_schema_create}")
        for stmt in _split_statements(sql):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
        if fetch:
            try:
                return cur.fetchall()
            except snowflake.connector.errors.ProgrammingError:
                return []
        return []

    return _run


@pytest.fixture(scope="session")
def load_fixture(run_sql_setup):
    """Execute a ``.sql`` fixture file (Phase 1 only)."""

    def _load(filename: str):
        path = FIXTURES_DIR / filename
        run_sql_setup(path.read_text(), fetch=False)

    return _load


# ---------------------------------------------------------------------------
# CoCo invocation helper (Phase 2)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def cortex_connection():
    """Named connection for ``cortex -c``."""
    return os.environ.get("CORTEX_CONNECTION", "demoaccount")


@pytest.fixture(scope="session")
def invoke_skill(test_schema, cortex_connection):
    """Invoke the entity-resolution skill via ``cortex -p`` in headless mode.

    Returns a dict: ``ok`` (bool), ``output`` (stdout+stderr), ``returncode``.
    """

    def _invoke(
        prompt: str,
        *,
        timeout: int = DEFAULT_INVOKE_TIMEOUT,
    ) -> dict:
        cmd = [
            "cortex",
            "-p", prompt,
            "-c", cortex_connection,
            "-w", str(REPO_ROOT),
            "--bypass",
            "--auto-accept-plans",
            "--no-auto-update",
            "--no-mcp",
            "--output-format", "stream-json",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": "TIMEOUT", "returncode": -1}

        return {
            "ok": result.returncode == 0,
            "output": result.stdout + result.stderr,
            "returncode": result.returncode,
        }

    return _invoke


# ---------------------------------------------------------------------------
# Snowflake assertion helpers (Phase 3)
# ---------------------------------------------------------------------------

class SnowflakeAssertions:
    """Convenience assertions against the test schema."""

    def __init__(self, run_sql_fn, schema: str):
        self._sql = run_sql_fn
        self._schema = schema

    def table_exists(self, name: str) -> bool:
        rows = self._sql(
            f"SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            f"WHERE TABLE_SCHEMA = '{self._schema}' "
            f"AND UPPER(TABLE_NAME) = '{name.upper()}'"
        )
        return rows[0][0] > 0

    def assert_table_exists(self, name: str):
        assert self.table_exists(name), (
            f"Expected table {self._schema}.{name} to exist"
        )

    def assert_table_not_exists(self, name: str):
        assert not self.table_exists(name), (
            f"Expected table {self._schema}.{name} to NOT exist"
        )

    def columns(self, table: str) -> set[str]:
        rows = self._sql(
            f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{self._schema}' "
            f"AND UPPER(TABLE_NAME) = '{table.upper()}'"
        )
        return {r[0].upper() for r in rows}

    def assert_columns_include(self, table: str, expected: set[str]):
        actual = self.columns(table)
        missing = {c.upper() for c in expected} - actual
        assert not missing, (
            f"Table {table} missing columns: {missing}. Has: {actual}"
        )

    def row_count(self, table: str) -> int:
        rows = self._sql(f"SELECT COUNT(*) FROM {table}")
        return rows[0][0]

    def assert_row_count_between(self, table: str, lo: int, hi: int):
        n = self.row_count(table)
        assert lo <= n <= hi, (
            f"Expected {table} to have {lo}-{hi} rows, got {n}"
        )

    def query_set(self, sql: str) -> set:
        """Run sql that returns single-column rows; return as a set."""
        rows = self._sql(sql)
        return {r[0] for r in rows}

    def query_map(self, sql: str) -> dict:
        """Run sql that returns (key, value) rows; return as a dict."""
        rows = self._sql(sql)
        return {r[0]: r[1] for r in rows}


@pytest.fixture(scope="session")
def sf(run_sql, test_schema):
    """Session-scoped Snowflake assertion helper."""
    return SnowflakeAssertions(run_sql, test_schema)


@pytest.fixture(scope="session")
def sf_setup(run_sql_setup, test_schema_create):
    """Snowflake assertion helper for Phase 1 (uses test_schema_create)."""
    return SnowflakeAssertions(run_sql_setup, test_schema_create)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _split_statements(sql: str) -> list[str]:
    """Split SQL on semicolons, ignoring semicolons inside string literals."""
    stmts: list[str] = []
    buf: list[str] = []
    in_str = False
    for ch in sql:
        if ch == "'" and not in_str:
            in_str = True
        elif ch == "'" and in_str:
            in_str = False
        if ch == ";" and not in_str:
            stmts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts
