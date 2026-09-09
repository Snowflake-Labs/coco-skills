#!/usr/bin/env python3
"""Decide which escalation labels a PR needs, from its diff.

Reads a list of changed files and their added lines, and emits label names.
Detection only -- the labels prompt a human to get a second approval. They do
not gate the merge; CODEOWNERS does that.

Runs in the privileged lane, so it must never execute contributor code. It
reads the diff as text via `git`, and nothing more.

Usage:
  route_review.py --base origin/main --head HEAD
  route_review.py --base origin/main --head HEAD --format json
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Only added lines are inspected, so removing a risky line never adds a label.
SQL_WRITE = re.compile(
    r"(?i)\b(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW|DATABASE|SCHEMA|WAREHOUSE|ROLE|STAGE|TASK|PIPE|FUNCTION|PROCEDURE)"
    r"|DROP\s+(?:TABLE|VIEW|DATABASE|SCHEMA|WAREHOUSE|ROLE|STAGE|TASK|PIPE)"
    r"|ALTER\s+(?:TABLE|VIEW|DATABASE|SCHEMA|WAREHOUSE|ROLE|ACCOUNT|USER)"
    r"|\bGRANT\s+\w+|\bREVOKE\s+\w+"
    r"|\bINSERT\s+INTO|\bMERGE\s+INTO|\bUPDATE\s+\w+\s+SET|\bDELETE\s+FROM|\bTRUNCATE\s+TABLE"
    r"|\bCOPY\s+INTO)\b"
)

SECURITY_DOMAIN = re.compile(
    r"(?i)\b(?:masking\s+policy|row\s+access\s+policy|projection\s+policy"
    r"|aggregation\s+policy|network\s+policy|network\s+rule|authentication\s+policy"
    r"|password\s+policy|tokeniz|external\s+oauth|key\s+pair\s+auth"
    r"|SECURITY\s+INTEGRATION|CREATE\s+SECRET|SYSTEM\$)\b"
)

EGRESS = re.compile(
    r"(?i)(?:\bcurl\s+|\bwget\s+|requests\.(?:get|post|put|patch|delete)\("
    r"|urllib\.request|httpx\.|fetch\(\s*['\"]https?://|axios\.)"
)

PREVIEW = re.compile(
    r"(?i)\b(?:private\s+preview|public\s+preview|not\s+yet\s+(?:GA|generally\s+available)"
    r"|unreleased|coming\s+soon|behind\s+a\s+feature\s+flag)\b"
)

DEPENDENCY_FILES = {
    "pyproject.toml", "requirements.txt", "package.json", "Pipfile",
    "poetry.lock", "uv.lock", "setup.py", "environment.yml",
}

# Content matching applies only to submitted material. Repo infrastructure and
# process docs are excluded: they legitimately quote the patterns being hunted
# (a rubric naming "preview", a regex containing GRANT, a fixture built to trip
# the scanner), and matching there produces labels describing the tooling rather
# than the submission.
#
# This creates no hiding place. Everything outside these prefixes is either
# under .github/ -- which earns security-review and code-review by path rule
# alone -- or a top-level doc requiring maintainer review via CODEOWNERS.
CONTENT_SCANNED_PREFIXES = ("skills/", "plugins/")

# Fixtures inside a skill exist to trip scanners on purpose.
CONTENT_EXEMPT_SUBSTRINGS = ("tests/fixtures/",)


def run_git(args):
    result = subprocess.run(
        ["git"] + args, capture_output=True, text=True, cwd=REPO_ROOT
    )
    if result.returncode != 0:
        print(f"ERROR: git {' '.join(args)} failed: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


def changed_files(base, head):
    out = run_git(["diff", "--name-only", f"{base}...{head}"])
    return [f for f in out.strip().splitlines() if f]


def added_lines(base, head):
    """Added lines only, so a PR that deletes risky code is not flagged for it.

    Tracks which file each hunk belongs to, so exempt paths can be skipped.
    """
    out = run_git(["diff", "--unified=0", f"{base}...{head}"])
    lines = []
    current = None
    scanned = False
    for line in out.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            current = path[2:] if path.startswith("b/") else path
            scanned = current.startswith(CONTENT_SCANNED_PREFIXES) and not any(
                s in current for s in CONTENT_EXEMPT_SUBSTRINGS
            )
            continue
        if line.startswith("+") and not line.startswith("+++") and scanned:
            lines.append(line[1:])
    return "\n".join(lines)


def decide_labels(files, added):
    labels = {}

    if any(f.startswith(".github/") for f in files):
        labels["needs: security-review"] = (
            "touches CI, which runs with repository credentials"
        )

    script_paths = [
        f for f in files
        if "/scripts/" in f or Path(f).name in DEPENDENCY_FILES
    ]
    if script_paths:
        labels["needs: code-review"] = (
            f"ships or changes executable code ({len(script_paths)} file(s))"
        )

    if SQL_WRITE.search(added):
        labels["needs: sql-write-review"] = (
            "contains SQL that writes, grants, or drops"
        )

    if SECURITY_DOMAIN.search(added):
        labels["needs: security-review"] = (
            "covers a security or governance domain"
        )

    if EGRESS.search(added):
        labels["needs: egress-review"] = (
            "makes outbound network calls"
        )

    if PREVIEW.search(added):
        labels["needs: product-confirmation"] = (
            "references preview or unreleased functionality"
        )

    if re.search(r"(?m)^type:\s*partner\s*$", added):
        labels["needs: positioning-review"] = (
            "partner submission naming a partner product"
        )

    return labels


def main():
    parser = argparse.ArgumentParser(description="Decide escalation labels.")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--format", choices=["lines", "json"], default="lines")
    args = parser.parse_args()

    files = changed_files(args.base, args.head)
    if not files:
        print("", end="")
        return

    added = added_lines(args.base, args.head)
    labels = decide_labels(files, added)

    if args.format == "json":
        print(json.dumps(labels, indent=2))
        return

    # One label per line, for the workflow to consume.
    for label in sorted(labels):
        print(label)


if __name__ == "__main__":
    main()
