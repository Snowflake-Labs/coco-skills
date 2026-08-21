"""
create_code_bundle.py — Step 2 of deploy-code-bundle (deterministic).

Creates a Snowflake **Code Bundle** object from a project already uploaded to a
stage (by deploy-common/upload_project.py). The staged project must contain a
`code_bundle.yml` at its root (written by prepare_code_bundle.py). Self-verifies
via DESCRIBE CODE BUNDLE.

SQL runs through the shared `sf_exec` executor (snow CLI `snow sql` first, then
the Python connector). This is the "CLI + SQL" path: the same
`CREATE ... CODE BUNDLE ... FROM '@stage/...'` DDL runs under either backend, so
no separate `snow bundle create` invocation is needed. With no connection the
command returns verdict SKIPPED (nothing created).

CLI:
  create_code_bundle.py --database <db> --schema <sch> --stage <stg>
                        --project-name <name> --bundle-name <bundle>
                        [--comment <text>] --connection <c> [--json]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy-common" / "scripts"))
import sf_exec  # noqa: E402


def build_create_sql(bundle_fqn: str, from_loc: str, comment: str | None) -> str:
    """Pure: build the CREATE OR REPLACE CODE BUNDLE DDL."""
    sql = f"CREATE OR REPLACE CODE BUNDLE {bundle_fqn} FROM '{from_loc}'"
    if comment:
        safe = comment.replace("'", "''")
        sql += f" COMMENT = '{safe}'"
    return sql


def create(db, schema, stage, project_name, bundle_name, connection, comment=None) -> dict:
    fqn = f"{db}.{schema}.{bundle_name}"
    from_loc = f"@{db}.{schema}.{stage}/{project_name}/"

    steps = []

    def step(label, sql):
        rc, out, err = sf_exec.run_sql(sql, connection)
        steps.append({"step": label, "ok": rc == 0, "error": err.strip()[:200] if rc else ""})
        return rc, out, err

    rc, out, err = step("create_code_bundle", build_create_sql(fqn, from_loc, comment))
    if rc != 0:
        if sf_exec.is_no_connection(err):
            return {"verdict": "SKIPPED", "reason": "no Snowflake connection; code bundle not created",
                    "code_bundle": fqn, "from": from_loc}
        return {"verdict": "FAIL", "code_bundle": fqn, "steps": steps,
                "error": f"CREATE CODE BUNDLE failed: {err.strip()[:300]}"}

    # Self-verify: DESCRIBE returns rows if the object exists, errors otherwise.
    vrc, vout, _ = sf_exec.run_sql(f"DESCRIBE CODE BUNDLE {fqn}", connection)
    verified = vrc == 0

    return {
        "verdict": "PASS" if verified else "FAIL",
        "code_bundle": fqn,
        "from": from_loc,
        "verified": verified,
        "steps": steps,
    }


def main():
    ap = argparse.ArgumentParser(description="Create a Snowflake Code Bundle from a staged project")
    ap.add_argument("--database", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--project-name", required=True)
    ap.add_argument("--bundle-name", required=True)
    ap.add_argument("--comment", default="")
    ap.add_argument("--connection", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = create(args.database, args.schema, args.stage, args.project_name,
                    args.bundle_name, args.connection, args.comment or None)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Create code bundle: {result['verdict']}  ({result.get('code_bundle')})")
        if result.get("reason"):
            print(f"  {result['reason']}")
        if result.get("error"):
            print(f"  error: {result['error']}")
        if result.get("from"):
            print(f"  from: {result['from']}")

    sys.exit(2 if result["verdict"] == "FAIL" else 0)


if __name__ == "__main__":
    main()
