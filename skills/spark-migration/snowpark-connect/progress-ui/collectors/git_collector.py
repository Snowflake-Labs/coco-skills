#!/usr/bin/env python3
"""
git_collector.py — Read-only git diff stats for the migration branch.

Runs `git diff --stat` between the pre-migration commit (stored in
run.json as data.base_commit) and HEAD to track lines added/removed
and files changed during the migration.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any


def _run(cmd: list[str], cwd: str) -> str:
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=15
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _parse_diff_stat(stat_output: str) -> dict[str, Any]:
    """Parse `git diff --stat` summary line."""
    files_changed = 0
    insertions = 0
    deletions = 0

    # Summary line: "N files changed, N insertions(+), N deletions(-)"
    summary = stat_output.splitlines()[-1] if stat_output else ""
    m = re.search(r"(\d+) file", summary)
    if m:
        files_changed = int(m.group(1))
    m = re.search(r"(\d+) insertion", summary)
    if m:
        insertions = int(m.group(1))
    m = re.search(r"(\d+) deletion", summary)
    if m:
        deletions = int(m.group(1))

    return {
        "files_changed": files_changed,
        "insertions": insertions,
        "deletions": deletions,
    }


def _parse_diff_numstat(numstat_output: str) -> list[dict[str, Any]]:
    """Parse `git diff --numstat` into per-file records."""
    files = []
    for line in numstat_output.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            added_s, removed_s, path = parts
            try:
                files.append({
                    "file": path,
                    "added": int(added_s) if added_s != "-" else None,
                    "removed": int(removed_s) if removed_s != "-" else None,
                })
            except ValueError:
                pass
    return files


def collect(run_dir: str) -> dict[str, Any]:
    """Return git diff stats relative to the run's base commit."""
    snapshot: dict[str, Any] = {
        "files_changed": None,
        "insertions": None,
        "deletions": None,
        "changed_files": [],
    }

    # Locate the actual conversion directory (may be inside run_dir)
    conv_dir = run_dir
    for entry in sorted(os.listdir(run_dir), reverse=True):
        full = os.path.join(run_dir, entry)
        if os.path.isdir(full) and (entry.startswith("Conversion-SCOS-") or os.path.exists(os.path.join(full, ".git"))):
            conv_dir = full
            break

    # Determine git root
    git_root = _run(["git", "rev-parse", "--show-toplevel"], cwd=conv_dir)
    if not git_root:
        git_root = _run(["git", "rev-parse", "--show-toplevel"], cwd=run_dir)
    if not git_root:
        return snapshot

    # Read base commit from run.json
    run_json_path = os.path.join(run_dir, ".migration-ui", "run.json")
    base_commit = None
    if os.path.exists(run_json_path):
        try:
            run_data = json.loads(open(run_json_path).read())
            base_commit = run_data.get("data", {}).get("base_commit")
        except Exception:
            pass

    # Fallback: first commit in the current branch
    if not base_commit:
        base_commit = _run(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=git_root)

    if not base_commit:
        return snapshot

    ref = f"{base_commit}..HEAD"

    stat  = _run(["git", "diff", "--stat",    ref, "--", conv_dir], cwd=git_root)
    numst = _run(["git", "diff", "--numstat", ref, "--", conv_dir], cwd=git_root)

    if stat:
        summary = _parse_diff_stat(stat)
        snapshot.update(summary)

    if numst:
        snapshot["changed_files"] = _parse_diff_numstat(numst)

    return snapshot
