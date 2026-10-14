"""
create_notebook.py — Step 2 of deploy-notebook (deterministic).

Creates a Snowflake Notebook object from a project already uploaded to a stage
(by upload_project.py), makes it runnable (query warehouse + notebook warehouse +
a LIVE version), self-verifies via SHOW NOTEBOOKS, and returns a Snowsight deeplink.

SQL runs through the shared `sf_exec` executor. With no connection the command
returns verdict SKIPPED (nothing created).

CLI:
  create_notebook.py --database <db> --schema <sch> --stage <stg>
                     --project-name <name> --main-file <notebook.ipynb>
                     --warehouse <wh> --notebook-name <nb> --connection <c> [--json]
"""

import argparse
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy-common" / "scripts"))
import sf_exec  # noqa: E402


def _parse_org_acct(stdout: str):
    """Best-effort parse of '(\"org\", \"acct\")' (connector) output."""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if line.startswith("(") and line.endswith(")"):
            try:
                val = ast.literal_eval(line)
                if isinstance(val, (list, tuple)) and len(val) >= 2:
                    return str(val[0]), str(val[1])
            except (ValueError, SyntaxError):
                pass
    return None, None


def create(db, schema, stage, project_name, main_file, warehouse, notebook_name, connection) -> dict:
    fqn = f"{db}.{schema}.{notebook_name}"
    from_loc = f"@{db}.{schema}.{stage}/{project_name}/"

    steps = []

    def step(label, sql):
        rc, out, err = sf_exec.run_sql(sql, connection)
        steps.append({"step": label, "ok": rc == 0, "error": err.strip()[:200] if rc else ""})
        return rc, out, err

    rc, out, err = step("create_notebook",
        f"CREATE OR REPLACE NOTEBOOK {fqn} FROM '{from_loc}' "
        f"MAIN_FILE='{main_file}' QUERY_WAREHOUSE={warehouse}")
    if rc != 0:
        if sf_exec.is_no_connection(err):
            return {"verdict": "SKIPPED", "reason": "no Snowflake connection; notebook not created",
                    "notebook": fqn, "from": from_loc, "main_file": main_file}
        return {"verdict": "FAIL", "notebook": fqn, "steps": steps,
                "error": f"CREATE NOTEBOOK failed: {err.strip()[:300]}"}

    # Make it runnable (non-fatal if these fail — the object still exists).
    step("set_warehouse", f"ALTER NOTEBOOK {fqn} SET WAREHOUSE={warehouse}")
    step("add_live_version", f"ALTER NOTEBOOK {fqn} ADD LIVE VERSION FROM LAST")

    # Self-verify.
    vrc, vout, _ = sf_exec.run_sql(f"SHOW NOTEBOOKS LIKE '{notebook_name}' IN SCHEMA {db}.{schema}", connection)
    verified = vrc == 0 and bool(vout.strip())

    # Deeplink (best-effort).
    orc, oout, _ = sf_exec.run_sql(
        "SELECT LOWER(CURRENT_ORGANIZATION_NAME()), LOWER(CURRENT_ACCOUNT_NAME())", connection)
    org, acct = _parse_org_acct(oout) if orc == 0 else (None, None)
    if org and acct:
        deeplink = f"https://app.snowflake.com/{org}/{acct}/#/notebooks/{fqn}"
    else:
        deeplink = f"https://app.snowflake.com/<org>/<account>/#/notebooks/{fqn}"

    return {
        "verdict": "PASS" if verified else "FAIL",
        "notebook": fqn,
        "from": from_loc,
        "main_file": main_file,
        "verified": verified,
        "deeplink": deeplink,
        "steps": steps,
    }


def main():
    ap = argparse.ArgumentParser(description="Create a Snowflake Notebook from a staged project")
    ap.add_argument("--database", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--project-name", required=True)
    ap.add_argument("--main-file", required=True, help="notebook path relative to the staged project root")
    ap.add_argument("--warehouse", required=True)
    ap.add_argument("--notebook-name", required=True)
    ap.add_argument("--connection", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = create(args.database, args.schema, args.stage, args.project_name,
                    args.main_file, args.warehouse, args.notebook_name, args.connection)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Create notebook: {result['verdict']}  ({result.get('notebook')})")
        if result.get("reason"):
            print(f"  {result['reason']}")
        if result.get("error"):
            print(f"  error: {result['error']}")
        if result.get("deeplink"):
            print(f"  deeplink: {result['deeplink']}")

    sys.exit(2 if result["verdict"] == "FAIL" else 0)


if __name__ == "__main__":
    main()
