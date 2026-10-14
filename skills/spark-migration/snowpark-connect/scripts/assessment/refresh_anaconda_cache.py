#!/usr/bin/env python3
"""One-time seeder for the Snowflake Anaconda-package cache.

The assessment scanner classifies imports as AR-required by checking each
against Snowflake's Anaconda channel. That list is authoritative only when
queried against a live Snowflake account. This helper runs the query once
using a Snowpark session and writes the result to disk so subsequent scans
(which typically have no session) can consult the cached list.

Usage::

    # From a machine with configured Snowflake credentials:
    python scripts/assessment/refresh_anaconda_cache.py

Session lookup follows Snowpark's default resolution — environment
variables (``SNOWFLAKE_ACCOUNT``, ``SNOWFLAKE_USER``, …), ``~/.snowsql/config``,
or an explicit connection profile via ``--connection-name``.

The cached file lives at ``~/.cache/snowpark-migration/anaconda_packages.json``
with a ``generated_at`` timestamp. The scanner honors entries younger than
30 days and falls back to a hardcoded snapshot if the cache is stale or
absent — running this script periodically (or in CI) keeps the AR flag
accurate without turning the scanner into a Snowflake client.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from file_info import _CACHE_FILE, refresh_anaconda_cache  # noqa: E402


def _open_session(connection_name: str | None):
    """Create a Snowpark session using default resolution or a named profile."""
    try:
        from snowflake.snowpark import Session  # type: ignore[import-not-found]
    except ImportError as e:
        raise SystemExit(
            "snowflake-snowpark-python is not installed in this environment. "
            "Install it with `pip install snowflake-snowpark-python` and re-run."
        ) from e
    builder = Session.builder
    if connection_name:
        return builder.config("connection_name", connection_name).create()
    return builder.create()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--connection-name",
        default=None,
        help="Optional Snowpark connection profile name. If omitted, uses the "
        "default resolution (env vars / ~/.snowsql/config).",
    )
    args = parser.parse_args(argv)

    session = _open_session(args.connection_name)
    try:
        packages = refresh_anaconda_cache(session)
    finally:
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass

    print(
        f"Cached {len(packages)} Anaconda package name(s) to {_CACHE_FILE}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
