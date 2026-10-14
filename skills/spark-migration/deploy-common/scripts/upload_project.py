"""
upload_project.py — Step 1 of deploy-notebook (deterministic).

Uploads the WHOLE migrated project to a Snowflake stage, preserving relative
paths so the notebook's imports resolve. Creates the stage if needed, PUTs every
file to `@<db>.<schema>.<stage>/<project>/<relpath>`, then LISTs to confirm.

SQL/PUT go through the shared `sf_exec` executor (snow CLI or snowflake.connector).
When no connection is available the command returns verdict SKIPPED with the
planned upload map, so the caller can report and stop without a hard failure.

CLI:
  upload_project.py --project <dir> --database <db> --schema <sch> --stage <stg>
                    [--project-name <name>] --connection <c> [--json]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sf_exec  # noqa: E402

SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules", ".pytest_cache", ".ipynb_checkpoints"}
SKIP_FILES = {".DS_Store", ".gitignore"}

# Files/extensions that commonly hold secrets — never upload these to a stage.
SENSITIVE_FILES = {".env", "credentials.json", ".netrc", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
SENSITIVE_EXTS = {".pem", ".key", ".p8", ".p12", ".pfx", ".env", ".keytab"}


def _is_sensitive(p: Path) -> bool:
    return p.name in SENSITIVE_FILES or p.suffix.lower() in SENSITIVE_EXTS


def plan_uploads(project_dir: str, project_name: str, db: str, schema: str, stage: str):
    """Pure: build the (local_path, stage_target_dir) list for every project file.

    stage_target_dir ends with '/', e.g. '@DB.SCH.STG/proj/src/pipeline/'.
    Returns (stage_root, uploads, skipped_sensitive) where skipped_sensitive lists any
    secret-looking files (.env, private keys, credentials.json, ...) that were EXCLUDED.
    """
    root = Path(project_dir)
    stage_root = f"@{db}.{schema}.{stage}/{project_name}"
    uploads = []
    skipped_sensitive = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.name in SKIP_FILES:
            continue
        if _is_sensitive(p):
            skipped_sensitive.append(str(p.relative_to(root)).replace("\\", "/"))
            continue
        rel = p.relative_to(root)
        rel_dir = str(rel.parent).replace("\\", "/")
        target = f"{stage_root}/" if rel_dir in ("", ".") else f"{stage_root}/{rel_dir}/"
        uploads.append({"local": str(p), "stage_target": target, "rel": str(rel).replace("\\", "/")})
    return stage_root, uploads, skipped_sensitive


def upload(project_dir: str, project_name: str, db: str, schema: str, stage: str, connection: str) -> dict:
    root = Path(project_dir)
    if not root.exists():
        return {"verdict": "FAIL", "error": f"project dir not found: {project_dir}"}

    stage_root, uploads, skipped_sensitive = plan_uploads(project_dir, project_name, db, schema, stage)
    if not uploads:
        return {"verdict": "FAIL", "error": "no files found to upload",
                "skipped_sensitive": skipped_sensitive}

    # Create the stage (directory-enabled). This is also our connectivity probe.
    rc, out, err = sf_exec.run_sql(
        f"CREATE STAGE IF NOT EXISTS {db}.{schema}.{stage} DIRECTORY = (ENABLE = TRUE)",
        connection,
    )
    if rc != 0:
        if sf_exec.is_no_connection(err):
            return {"verdict": "SKIPPED", "reason": "no Snowflake connection; upload not performed",
                    "stage_root": stage_root, "planned": uploads,
                    "skipped_sensitive": skipped_sensitive}
        return {"verdict": "FAIL", "error": f"CREATE STAGE failed: {err.strip()[:300]}",
                "skipped_sensitive": skipped_sensitive}

    uploaded, failures = [], []
    for u in uploads:
        prc, _, perr = sf_exec.put_file(u["local"], u["stage_target"], connection)
        (uploaded if prc == 0 else failures).append(
            u | ({"error": perr.strip()[:200]} if prc != 0 else {}))

    lrc, lout, _ = sf_exec.run_sql(f"LIST {stage_root}/", connection)
    listed = lout.count("\n") + 1 if (lrc == 0 and lout.strip()) else 0

    verdict = "FAIL" if failures else "PASS"
    return {
        "verdict": verdict,
        "stage_root": stage_root,
        "uploaded_count": len(uploaded),
        "failed_count": len(failures),
        "failures": failures,
        "listed_on_stage": listed,
        "skipped_sensitive": skipped_sensitive,
    }


def main():
    ap = argparse.ArgumentParser(description="Upload a migrated project to a Snowflake stage")
    ap.add_argument("--project", required=True)
    ap.add_argument("--database", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--project-name", default="")
    ap.add_argument("--connection", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    project_name = args.project_name or Path(args.project).name
    result = upload(args.project, project_name, args.database, args.schema, args.stage, args.connection)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Upload: {result['verdict']}")
        if result.get("skipped_sensitive"):
            print(f"  WARNING: skipped {len(result['skipped_sensitive'])} sensitive file(s) "
                  "(not uploaded to the stage):")
            for s in result["skipped_sensitive"]:
                print(f"    - {s}")
        if result.get("error"):
            print(f"  error: {result['error']}")
        if result.get("reason"):
            print(f"  {result['reason']} ({len(result.get('planned', []))} files planned)")
        if result.get("stage_root"):
            print(f"  stage: {result['stage_root']}  uploaded={result.get('uploaded_count')} listed={result.get('listed_on_stage')}")
        for f in result.get("failures", []):
            print(f"  FAIL {f['rel']}: {f.get('error')}")

    # PASS or SKIPPED -> 0; FAIL -> 2.
    sys.exit(2 if result["verdict"] == "FAIL" else 0)


if __name__ == "__main__":
    main()
