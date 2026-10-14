"""
ensure_namespace.py — Step 0.5 of deploy-notebook (deterministic).

Verifies that the target database and schema exist before the deploy steps
(upload_project / create_notebook / the opt-in validate step) run. The deploy
scripts use fully-qualified `db.schema` names and the `snow` CLI needs a real
database to exist, so a missing namespace otherwise fails deep inside a stage /
notebook create with an opaque error.

Two modes:
- check (default): SHOW DATABASES / SHOW SCHEMAS and report existence flags.
  Exit 0 if both exist, exit 2 if either is missing (so the SKILL step can
  branch and prompt the user).
- create (`--create`): CREATE DATABASE IF NOT EXISTS + CREATE SCHEMA IF NOT
  EXISTS, then re-check. Exit 0 on success.

SQL runs through the shared `sf_exec` executor. With no connection the command
returns verdict SKIPPED (nothing checked/created).

CLI:
  ensure_namespace.py --database <db> --schema <sch> --connection <c>
                      [--create] [--json]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sf_exec  # noqa: E402


def _exists(sql: str, connection: str) -> tuple[bool, str]:
    """Run a SHOW ... LIKE and return (has_rows, stderr)."""
    rc, out, err = sf_exec.run_sql(sql, connection)
    if rc != 0:
        return False, err
    return bool((out or "").strip()), ""


def check(db: str, schema: str, connection: str) -> dict:
    db_sql = f"SHOW DATABASES LIKE '{db}'"
    db_exists, err = _exists(db_sql, connection)
    if err and sf_exec.is_no_connection(err):
        return {"verdict": "SKIPPED", "reason": "no Snowflake connection; namespace not checked",
                "database": db, "schema": schema}
    schema_exists = False
    if db_exists:
        schema_exists, _ = _exists(f"SHOW SCHEMAS LIKE '{schema}' IN DATABASE {db}", connection)
    return {
        "verdict": "PASS" if (db_exists and schema_exists) else "MISSING",
        "database": db,
        "schema": schema,
        "database_exists": db_exists,
        "schema_exists": schema_exists,
    }


def create(db: str, schema: str, connection: str) -> dict:
    steps = []

    def step(label, sql):
        rc, _, err = sf_exec.run_sql(sql, connection)
        ok = rc == 0
        steps.append({"step": label, "ok": ok, "error": err.strip()[:200] if not ok else ""})
        return ok, err

    ok, err = step("create_database", f"CREATE DATABASE IF NOT EXISTS {db}")
    if not ok and sf_exec.is_no_connection(err):
        return {"verdict": "SKIPPED", "reason": "no Snowflake connection; namespace not created",
                "database": db, "schema": schema}
    if not ok:
        return {"verdict": "FAIL", "database": db, "schema": schema, "steps": steps,
                "error": f"CREATE DATABASE failed: {err.strip()[:300]}"}

    ok, err = step("create_schema", f"CREATE SCHEMA IF NOT EXISTS {db}.{schema}")
    if not ok:
        return {"verdict": "FAIL", "database": db, "schema": schema, "steps": steps,
                "error": f"CREATE SCHEMA failed: {err.strip()[:300]}"}

    result = check(db, schema, connection)
    result["steps"] = steps
    result["created"] = True
    return result


def main():
    ap = argparse.ArgumentParser(description="Verify (and optionally create) the target database + schema")
    ap.add_argument("--database", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--connection", required=True)
    ap.add_argument("--create", action="store_true", help="create the database/schema if missing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = create(args.database, args.schema, args.connection) if args.create \
        else check(args.database, args.schema, args.connection)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Namespace {args.database}.{args.schema}: {result['verdict']}")
        if result.get("reason"):
            print(f"  {result['reason']}")
        if result.get("error"):
            print(f"  error: {result['error']}")
        if result["verdict"] in ("PASS", "MISSING"):
            print(f"  database_exists={result.get('database_exists')} schema_exists={result.get('schema_exists')}")

    # PASS/SKIPPED -> 0; MISSING -> 2 (caller prompts); FAIL -> 2.
    sys.exit(0 if result["verdict"] in ("PASS", "SKIPPED") else 2)


if __name__ == "__main__":
    main()
