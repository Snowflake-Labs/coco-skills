"""
scan_migration_gaps.py — Step 0 of deploy-notebook (deterministic).

Collects the migration's flagged gaps so the agent can present a
"fix before deploy" checklist. Reads only local files — no Snowflake connection.

Collected:
  - SCOS markers        : lines containing `# SCOS` in .py/.ipynb, classified as
                          action-required (TODO/WARN) vs informational
  - placeholders        : unresolved <DATABASE> / <SCHEMA> / <WAREHOUSE> / <ROLE>
  - issues_csv          : Reports/Issues.csv (row count + a small sample) if present
  - packages            : requirements.txt entries if present

CLI:
  scan_migration_gaps.py --project <dir> [--json]
Exit code is always 0 (informational); `has_action_items` in the output tells the
caller whether to raise the fix-first checklist.
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

SCOS_RE = re.compile(r"#\s*SCOS[^\n]*", re.IGNORECASE)
ACTION_RE = re.compile(r"\b(TODO|WARN|UNSUPPORTED|MANUAL)\b", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"<(DATABASE|SCHEMA|WAREHOUSE|ROLE)>")
SCAN_SUFFIXES = {".py", ".ipynb", ".sql"}
SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules"}


def _iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in SCAN_SUFFIXES:
            yield p


def scan(project_dir: str) -> dict:
    root = Path(project_dir)
    if not root.exists():
        return {"error": f"project dir not found: {project_dir}"}

    markers, placeholders = [], []
    for f in _iter_files(root):
        rel = str(f.relative_to(root))
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in SCOS_RE.findall(line):
                markers.append({
                    "file": rel, "line": i, "text": m.strip()[:200],
                    "action_required": bool(ACTION_RE.search(m)),
                })
            for ph in PLACEHOLDER_RE.findall(line):
                placeholders.append({"file": rel, "line": i, "placeholder": f"<{ph}>"})

    # Reports/Issues.csv (may sit at project root or a parent Reports/ dir)
    issues = None
    for cand in (root / "Reports" / "Issues.csv", root.parent / "Reports" / "Issues.csv"):
        if cand.exists():
            try:
                with cand.open(newline="", encoding="utf-8", errors="replace") as fh:
                    rows = list(csv.reader(fh))
                data_rows = rows[1:] if rows else []
                issues = {
                    "path": str(cand),
                    "count": len(data_rows),
                    "header": rows[0] if rows else [],
                    "sample": data_rows[:5],
                }
            except OSError:
                pass
            break

    # requirements.txt
    packages = []
    req = root / "requirements.txt"
    if req.exists():
        packages = [ln.strip() for ln in req.read_text(encoding="utf-8", errors="replace").splitlines()
                    if ln.strip() and not ln.strip().startswith("#")]

    action_markers = [m for m in markers if m["action_required"]]
    has_action_items = bool(action_markers or placeholders or (issues and issues["count"]))

    return {
        "project": str(root),
        "has_action_items": has_action_items,
        "markers": markers,
        "action_marker_count": len(action_markers),
        "placeholders": placeholders,
        "issues_csv": issues,
        "packages": packages,
    }


def main():
    ap = argparse.ArgumentParser(description="Scan a migrated project for fix-before-deploy gaps")
    ap.add_argument("--project", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = scan(args.project)
    if "error" in result:
        print(f"ERROR: {result['error']}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Migration gap scan: {result['project']}")
        print(f"  action items: {'YES' if result['has_action_items'] else 'none'}")
        print(f"  SCOS markers (action): {result['action_marker_count']} / {len(result['markers'])} total")
        print(f"  placeholders: {len(result['placeholders'])}")
        if result["issues_csv"]:
            print(f"  Issues.csv rows: {result['issues_csv']['count']}")
        print(f"  packages: {len(result['packages'])}")
    sys.exit(0)


if __name__ == "__main__":
    main()
