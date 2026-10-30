#!/usr/bin/env python3
"""Minimal Snowflake SQL runner for the v2 engine. Stdlib only.

Shells out to the Snowflake CLI (`snow sql --format json`) so the engine keeps
its no-pip guarantee. Every statement is tagged with ``ENGINE_TAG`` so the
usage miner can exclude the hydrator's own queries from query history.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Callable

ENGINE_TAG = "/* people_hydrator */"
Runner = Callable[[str], list[dict[str, Any]]]


class SqlError(RuntimeError):
    """Raised when a statement fails or times out."""


def quote_ident(name: str) -> str:
    """Quote a Snowflake identifier (handles mixed case and embedded quotes)."""
    return '"' + name.replace('"', '""') + '"'


def quote_fqn(fqn: str) -> str:
    return ".".join(quote_ident(part) for part in split_fqn(fqn))


def split_fqn(fqn: str) -> list[str]:
    parts = re.findall(r'"((?:[^"]|"")*)"|([^.]+)', fqn)
    return [(q.replace('""', '"') if q else u) for q, u in parts]


def quote_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _snowpark_runner() -> Runner:
    """Use the session Snowsight already provides (Workspaces / Cortex Code in the browser)."""
    try:
        from snowflake.snowpark.context import get_active_session  # type: ignore
        session = get_active_session()
    except Exception as exc:  # noqa: BLE001 - surface a clear setup message instead
        raise SqlError("no `snow` CLI and no active Snowpark session; run inside Snowsight "
                       "or install the Snowflake CLI and add a connection") from exc

    def run(sql: str) -> list[dict[str, Any]]:
        try:
            rows = session.sql(f"{ENGINE_TAG} {sql.strip().rstrip(';')}").collect()
        except Exception as exc:  # noqa: BLE001
            raise SqlError(str(exc)[:800]) from exc
        return [{str(k).upper(): v for k, v in r.as_dict().items()} for r in rows]

    return run


def make_runner(connection: str | None, timeout_seconds: int = 300) -> Runner:
    """Return a callable that executes one statement and returns rows as dicts.

    Local Cortex Code: shells out to the `snow` CLI with the named connection.
    Snowsight: falls back to the active Snowpark session (or force with ENGINE_SQL=snowpark).
    """
    if os.environ.get("ENGINE_SQL") == "snowpark" or shutil.which("snow") is None:
        return _snowpark_runner()

    def run(sql: str) -> list[dict[str, Any]]:
        statement = f"{ENGINE_TAG} {sql.strip().rstrip(';')}"
        cmd = ["snow", "sql", "-c", connection, "--format", "json", "-q", statement]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_seconds, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise SqlError(f"timeout after {timeout_seconds}s") from exc
        if proc.returncode != 0:
            raise SqlError((proc.stderr or proc.stdout).strip()[:800])
        out = proc.stdout.strip()
        if not out:
            return []
        data = json.loads(out)
        # Multi-result output is a list of lists; single result is a list of dicts.
        if data and isinstance(data[0], list):
            data = data[-1]
        return [{str(k).upper(): v for k, v in row.items()} for row in data]

    def many(sqls: list[str]) -> list[list[dict[str, Any]]]:
        """Several statements in one CLI call (one login). Raises SqlError if any fails."""
        script = "; ".join(f"{ENGINE_TAG} {s.strip().rstrip(';')}" for s in sqls)
        cmd = ["snow", "sql", "-c", connection, "--format", "json", "-q", script]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout_seconds * max(1, len(sqls) // 10 + 1), check=False)
        except subprocess.TimeoutExpired as exc:
            raise SqlError("batch timeout") from exc
        if proc.returncode != 0:
            raise SqlError((proc.stderr or proc.stdout).strip()[:800])
        data = json.loads(proc.stdout.strip() or "[]")
        if len(data) != len(sqls) or any(not isinstance(d, list) for d in data):
            raise SqlError(f"batch returned {len(data)} results for {len(sqls)} statements")
        return [[{str(k).upper(): v for k, v in row.items()} for row in d] for d in data]

    run.many = many  # type: ignore[attr-defined]
    return run


def run_many(run: Runner, sqls: list[str], chunk: int = 40) -> list[list[dict[str, Any]] | SqlError]:
    """Run statements with as few round trips as possible; same results as one-by-one.

    Each item is that statement's rows, or the SqlError it raised. A batch that fails is
    re-run one statement at a time, so one bad query never hides the others' results.
    Statements containing ';' always run alone (the CLI splits scripts on ';').
    """
    out: list[list[dict[str, Any]] | SqlError | None] = [None] * len(sqls)
    batch = getattr(run, "many", None)
    pending = [i for i, s in enumerate(sqls) if batch and ";" not in s]
    for start in range(0, len(pending), chunk):
        ids = pending[start:start + chunk]
        if len(ids) < 2:
            continue
        try:
            for i, rows in zip(ids, batch([sqls[i] for i in ids])):
                out[i] = rows
        except (SqlError, ValueError):
            pass  # fall through to one-by-one below
    for i, s in enumerate(sqls):
        if out[i] is None:
            try:
                out[i] = run(s)
            except SqlError as exc:
                out[i] = exc
    return out  # type: ignore[return-value]


def to_number(v: Any) -> float | int | None:
    """snow returns NUMBER(p,s) as text ('12.50'); rates must still compare as numbers."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        f = float(str(v))
    except ValueError:
        return None
    return int(f) if f.is_integer() and "." not in str(v) else f
