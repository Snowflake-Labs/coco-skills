"""Test configuration for DVP migrated tests.

Connection settings resolve in this order:
  1. Environment variables (highest priority)
  2. ~/.snowflake/connections.toml defaults
  3. Hardcoded fallbacks

The TOML reading is necessary because conftest.py issues explicit
USE ROLE / USE DATABASE / USE SCHEMA statements on the Snowpark session,
which require the individual values — not just a connection_name.
The snowflake.connector reads the TOML internally for connect(), but
does not expose role/database/schema as separate attributes.

Usage:
    export SNOWFLAKE_CONNECTION_NAME=my_connection
    export SNOWFLAKE_TEST_SCHEMA=MY_SCHEMA
    pytest 03-tests/migrated -v
"""
import os
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def _load_toml_connection() -> tuple:
    """Read the default connection from ~/.snowflake/connections.toml.
    Returns (connection_name, connection_dict).
    """
    toml_path = Path.home() / ".snowflake" / "connections.toml"
    if not toml_path.exists():
        return "default", {}
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)
    conn_name = data.get("default_connection_name", "default")
    return conn_name, data.get(conn_name, data.get("default", {}))


_conn_name, _toml = _load_toml_connection()

CONNECTION_NAME = os.getenv(
    "SNOWFLAKE_CONNECTION_NAME",
    _conn_name,
)
TEST_ROLE = os.getenv("SNOWFLAKE_TEST_ROLE", _toml.get("role", ""))
TEST_DATABASE = os.getenv("SNOWFLAKE_TEST_DATABASE", _toml.get("database", ""))
TEST_SCHEMA = os.getenv("SNOWFLAKE_TEST_SCHEMA", _toml.get("schema", ""))
TEST_STAGE = os.getenv("SNOWFLAKE_TEST_STAGE", "DVP_TEST_STAGE")
