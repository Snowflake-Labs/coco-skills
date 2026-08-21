"""
execute_notebook.py — Step 3 helper of deploy-notebook (deterministic, OPT-IN).

Runs a deployed notebook headless via `EXECUTE NOTEBOOK <fqn>()` and reports
PASS / FAIL. Used ONLY by the opt-in validation step (when the user asks to
validate / run the notebook).

Note: headless `EXECUTE NOTEBOOK` returns only the failing cell's exception, not
full cell stdout. To capture rich diagnostics, instrument the notebook's run
cell to write a log to a stage (see SKILL.md Step 3) — that is workload-specific
and not done here.

SQL runs through the shared `sf_exec` executor. With no connection the command
returns verdict SKIPPED (nothing executed).

CLI:
  execute_notebook.py --notebook <db.schema.name> --connection <c> [--json]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy-common" / "scripts"))
import sf_exec  # noqa: E402


def execute(notebook_fqn: str, connection: str) -> dict:
    rc, out, err = sf_exec.run_sql(f"EXECUTE NOTEBOOK {notebook_fqn}()", connection, timeout=1200)
    if rc == 0:
        return {"verdict": "PASS", "notebook": notebook_fqn, "output": (out or "").strip()[:500]}
    if sf_exec.is_no_connection(err):
        return {"verdict": "SKIPPED", "reason": "no Snowflake connection; notebook not executed",
                "notebook": notebook_fqn}
    return {"verdict": "FAIL", "notebook": notebook_fqn, "error": (err or "").strip()[:1500]}


def main():
    ap = argparse.ArgumentParser(description="Execute a deployed Snowflake notebook headless")
    ap.add_argument("--notebook", required=True, help="fully-qualified db.schema.name")
    ap.add_argument("--connection", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = execute(args.notebook, args.connection)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Execute notebook: {result['verdict']}  ({result['notebook']})")
        if result.get("reason"):
            print(f"  {result['reason']}")
        if result.get("error"):
            print(f"  error: {result['error']}")

    sys.exit(2 if result["verdict"] == "FAIL" else 0)


if __name__ == "__main__":
    main()
