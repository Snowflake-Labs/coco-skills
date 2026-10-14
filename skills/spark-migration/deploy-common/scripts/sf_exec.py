"""
sf_exec.py — Shared Snowflake executor that works with OR without the snow CLI.

Runs SQL and PUTs local files, preferring the `snow` CLI and transparently
falling back to the Python connector (`snowflake.connector`), which is almost
always installed and handles local-file PUT robustly.

- `backend(connection)` probes PATH so callers/tests know which path is taken.
- `run_sql` / `put_file` return a uniform `(returncode, stdout, stderr)` tuple:
  rc==0 success, rc!=0 failure with the message in stderr.
- Side effects (subprocess, connector) are isolated so tests can monkeypatch them.
"""

import shutil
import subprocess


def snow_cli_available() -> bool:
    """True if the `snow` CLI is resolvable on PATH."""
    return shutil.which("snow") is not None


def backend(connection: str | None) -> str:
    """Return which backend run_sql/put_file will use: 'cli' or 'connector'."""
    return "cli" if snow_cli_available() else "connector"


def _run_sql_cli(sql: str, connection: str, timeout: int) -> tuple[int, str, str]:
    cmd = ["snow", "sql", "-q", sql, "--connection", connection]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def _connect(connection: str):
    """Open a connector session from a named connection in connections.toml.

    Isolated so tests can monkeypatch it with a fake connection object.
    """
    import snowflake.connector

    return snowflake.connector.connect(connection_name=connection)


def _run_sql_connector(sql: str, connection: str) -> tuple[int, str, str]:
    try:
        conn = _connect(connection)
    except Exception as e:  # connection/auth failure
        return 1, "", f"connector connect failed: {e}"
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            out = "\n".join(str(r) for r in rows)
            return 0, out, ""
        except Exception as e:
            return 1, "", str(e)
        finally:
            cur.close()
    finally:
        conn.close()


def _put_file_connector(local_path: str, stage: str, connection: str) -> tuple[int, str, str]:
    put_sql = f"PUT 'file://{local_path}' {stage} AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
    return _run_sql_connector(put_sql, connection)


def run_sql(sql: str, connection: str, timeout: int = 300) -> tuple[int, str, str]:
    """Execute SQL, preferring the snow CLI and falling back to the connector."""
    if snow_cli_available():
        try:
            return _run_sql_cli(sql, connection, timeout)
        except subprocess.TimeoutExpired:
            return 1, "", "snow CLI timeout"
        except FileNotFoundError:
            pass
    return _run_sql_connector(sql, connection)


def is_no_connection(stderr: str) -> bool:
    """Shared: True when stderr indicates a missing/failed connection rather than
    a real SQL error. Covers the CLI and connector phrasings used across scripts."""
    s = (stderr or "").lower()
    return ("connect failed" in s or "no default connection" in s
            or "not available for put" in s)


def run_sqls(sqls: list[str], connection: str, timeout: int = 300) -> tuple[int, str, str]:
    """Run several statements in ONE session (e.g. `USE WAREHOUSE ...` then EXECUTE).

    A single `run_sql` opens a fresh session per call, so session state does not
    carry across calls; this keeps them together. Returns the LAST statement's
    (returncode, stdout, stderr); stops and returns on the first failure.
    """
    if snow_cli_available():
        try:
            return _run_sql_cli("; ".join(sqls), connection, timeout)
        except subprocess.TimeoutExpired:
            return 1, "", "snow CLI timeout"
        except FileNotFoundError:
            pass
    # Connector: one connection, statements in order.
    try:
        conn = _connect(connection)
    except Exception as e:
        return 1, "", f"connector connect failed: {e}"
    try:
        cur = conn.cursor()
        try:
            out = ""
            for sql in sqls:
                cur.execute(sql)
                rows = cur.fetchall()
                out = "\n".join(str(r) for r in rows)
            return 0, out, ""
        except Exception as e:
            return 1, "", str(e)
        finally:
            cur.close()
    finally:
        conn.close()


def put_file(local_path: str, stage: str, connection: str, timeout: int = 300) -> tuple[int, str, str]:
    """PUT a local file to a stage. Uses the connector (robust for local files);
    falls back to the CLI only if the connector import is unavailable.

    `stage` should be a stage path ending in '/', e.g. '@DB.SCHEMA.STAGE/'.
    """
    try:
        import snowflake.connector  # noqa: F401
        return _put_file_connector(local_path, stage, connection)
    except ImportError:
        if snow_cli_available():
            put_sql = f"PUT file://{local_path} {stage} AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
            try:
                return _run_sql_cli(put_sql, connection, timeout)
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                return 1, "", f"snow CLI PUT failed: {e}"
        return 1, "", "neither snowflake.connector nor snow CLI available for PUT"
