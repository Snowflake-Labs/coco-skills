#!/usr/bin/env python3
"""Fail-loud preflight for Cortex LLM access.

This check is intended to run before any conversion folder or git state is
created. It verifies that the selected Snowflake connection can execute
``SNOWFLAKE.CORTEX.COMPLETE``.
"""

from __future__ import annotations

import argparse
import logging
import sys

from scos_session import (
    DEFAULT_LLM_MODEL,
    get_session_identity,
    open_session,
    verify_cortex_complete_access,
)

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-loud preflight check for CORTEX.COMPLETE access"
    )
    parser.add_argument(
        "--connection",
        type=str,
        default="default",
        help="Snowflake connection name (default: default)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_LLM_MODEL,
        help=f"Cortex model used for probe (default: {DEFAULT_LLM_MODEL})",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
    logger.setLevel(logging.INFO)

    session = open_session(args.connection)
    try:
        account, user, role = get_session_identity(session)
        sample = verify_cortex_complete_access(session, model=args.model)
    except Exception as exc:
        print("CORTEX_LLM_PREFLIGHT=FAIL", file=sys.stderr)
        print(f"connection={args.connection}", file=sys.stderr)
        print(f"error={exc}", file=sys.stderr)
        user_hint = "<target_user>"
        role_hint = "<target_role>"
        try:
            _, user_hint, role_hint = get_session_identity(session)
        except Exception:
            pass
        print(
            "Remediation (run as ACCOUNTADMIN, then re-run preflight):",
            file=sys.stderr,
        )
        print(
            f"  GRANT ROLE CORTEX_USER_ROLE TO USER {user_hint};",
            file=sys.stderr,
        )
        print(
            f"  GRANT USE AI FUNCTIONS ON ACCOUNT TO ROLE {role_hint};",
            file=sys.stderr,
        )
        return 2
    finally:
        try:
            session.close()
        except Exception:
            pass

    print("CORTEX_LLM_PREFLIGHT=PASS")
    print(f"connection={args.connection}")
    print(f"account={account}")
    print(f"user={user}")
    print(f"role={role}")
    print(f"model={args.model}")
    print(f"probe_sample={sample[:80]}")
    # Tell downstream analyzer scripts (analyze_pyspark.py / analyze_scala.py)
    # that the preflight has already passed so they can skip their own probe.
    print("export SCOS_LLM_PREFLIGHT_VERIFIED=1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
