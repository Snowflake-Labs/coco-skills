"""
execute_code_bundle.py — Step 3 helper of deploy-code-bundle (deterministic, OPT-IN).

Runs a deployed Code Bundle via `EXECUTE CODE BUNDLE <fqn> ENTRYPOINT='<path>'`
and reports PASS / FAIL. Used ONLY by the opt-in validation step (when the user
asks to validate / run the bundle). A plain deploy ends at Step 2 (create).

`EXECUTE CODE BUNDLE` runs synchronously here (no `--async`), so a zero return
code means the entrypoint completed. The bundle's run is identified by its Query
ID; richer per-run history is available via
`SNOWFLAKE.INFORMATION_SCHEMA.CODE_BUNDLE_HISTORY(BUNDLE_NAME => '<name>')`
(printed on request with --history).

SQL runs through the shared `sf_exec` executor. With no connection the command
returns verdict SKIPPED (nothing executed).

CLI:
  execute_code_bundle.py --bundle <db.schema.name> --entrypoint <path>
                         [--arguments '<args>'] [--history] --connection <c> [--json]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy-common" / "scripts"))
import sf_exec  # noqa: E402


def build_execute_sql(bundle_fqn: str, entrypoint: str, arguments: str | None) -> str:
    """Pure: build the EXECUTE CODE BUNDLE statement."""
    safe_entry = entrypoint.replace("'", "''")
    sql = f"EXECUTE CODE BUNDLE {bundle_fqn} ENTRYPOINT = '{safe_entry}'"
    if arguments:
        safe_args = arguments.replace("'", "''")
        sql += f" ARGUMENTS = '{safe_args}'"
    return sql


def execute(bundle_fqn: str, entrypoint: str, connection: str,
            arguments: str | None = None, warehouse: str | None = None) -> dict:
    sql = build_execute_sql(bundle_fqn, entrypoint, arguments)
    # Warehouse code bundles run on the session warehouse (query_warehouse is not
    # valid in the spec for compute_type=warehouse), so set it in the same session.
    if warehouse:
        rc, out, err = sf_exec.run_sqls([f"USE WAREHOUSE {warehouse}", sql], connection, timeout=1200)
    else:
        rc, out, err = sf_exec.run_sql(sql, connection, timeout=1200)
    if rc == 0:
        return {"verdict": "PASS", "code_bundle": bundle_fqn, "entrypoint": entrypoint,
                "output": (out or "").strip()[:500]}
    if sf_exec.is_no_connection(err):
        return {"verdict": "SKIPPED", "reason": "no Snowflake connection; code bundle not executed",
                "code_bundle": bundle_fqn}
    return {"verdict": "FAIL", "code_bundle": bundle_fqn, "entrypoint": entrypoint,
            "error": (err or "").strip()[:1500]}


def history(bundle_fqn: str, connection: str, limit: int = 5) -> str:
    """Best-effort recent run history for the bundle (INFORMATION_SCHEMA TVF)."""
    name = bundle_fqn.split(".")[-1]
    sql = (f"SELECT * FROM TABLE(SNOWFLAKE.INFORMATION_SCHEMA.CODE_BUNDLE_HISTORY("
           f"BUNDLE_NAME => '{name}')) ORDER BY 1 DESC LIMIT {limit}")
    rc, out, _ = sf_exec.run_sql(sql, connection)
    return (out or "").strip() if rc == 0 else ""


def main():
    ap = argparse.ArgumentParser(description="Execute a deployed Snowflake Code Bundle")
    ap.add_argument("--bundle", required=True, help="fully-qualified db.schema.name")
    ap.add_argument("--entrypoint", required=True, help="entrypoint file relative to the bundle root, e.g. main.py")
    ap.add_argument("--arguments", default="", help="argument string passed to the entrypoint")
    ap.add_argument("--warehouse", default="", help="warehouse to USE before EXECUTE (required for warehouse compute_type when the connection has no default)")
    ap.add_argument("--history", action="store_true", help="also print recent run history")
    ap.add_argument("--connection", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = execute(args.bundle, args.entrypoint, args.connection, args.arguments or None, args.warehouse or None)

    if args.history and result["verdict"] in ("PASS", "FAIL"):
        result["history"] = history(args.bundle, args.connection)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Execute code bundle: {result['verdict']}  ({result['code_bundle']})")
        if result.get("reason"):
            print(f"  {result['reason']}")
        if result.get("error"):
            print(f"  error: {result['error']}")
        if result.get("history"):
            print(f"  history:\n{result['history']}")

    sys.exit(2 if result["verdict"] == "FAIL" else 0)


if __name__ == "__main__":
    main()
