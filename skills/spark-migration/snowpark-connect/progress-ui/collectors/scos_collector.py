#!/usr/bin/env python3
"""
scos_collector.py — Read-only poller for SCOS (snowpark-connect) artifacts.

Reads: state.json / migration_state.json, analysis.json, Reports/*.csv
Never writes. Called periodically by the progress server to inject
synthetic metric events into the event bus.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _count_csv_rows(path: str) -> int:
    """Count data rows (excluding header) in a CSV file."""
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            rows = sum(1 for _ in reader)
            return max(0, rows - 1)  # subtract header
    except Exception:
        return 0


def _read_issues_csv(path: str) -> dict:
    """Parse Issues.csv into counts by category."""
    categories: dict[str, int] = {}
    total = 0
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                total += 1
                cat = row.get("Category") or row.get("category") or row.get("Type") or "unknown"
                categories[cat] = categories.get(cat, 0) + 1
    except Exception:
        pass
    return {"total": total, "by_category": categories}


def _read_inventory_csv(path: str) -> dict:
    """Parse InputFilesInventory.csv — count by converted status."""
    statuses: dict[str, int] = {}
    total = 0
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                total += 1
                status = row.get("Status") or row.get("status") or "unknown"
                statuses[status] = statuses.get(status, 0) + 1
    except Exception:
        pass
    return {"total": total, "by_status": statuses}


def _find_scos_conversion_dir(run_dir: str) -> str | None:
    """Locate the Conversion-SCOS-* directory inside run_dir."""
    try:
        for entry in sorted(os.listdir(run_dir), reverse=True):
            full = os.path.join(run_dir, entry)
            if os.path.isdir(full) and entry.startswith("Conversion-SCOS-"):
                return full
    except OSError:
        pass
    return None


# Known report artifacts, in display priority order.
# (basename, kind, human label). Assessment report first so the UI can feature it.
_KNOWN_REPORTS = [
    ("MigrationReadinessReport.html", "assessment", "Migration Readiness Report"),
    ("AssessmentIR.json",            "ir",         "Assessment IR"),
    ("Issues.csv",                   "csv",        "Issues"),
    ("InputFilesInventory.csv",      "csv",        "Input Files Inventory"),
    ("ArtifactDependencyInventory.csv", "csv",     "Artifact Dependencies"),
]


def _discover_reports(conv_dir: str) -> list[dict]:
    """Scan for report artifacts already on disk, so they are linked in the UI
    even when no `report-ready` event was emitted (e.g. the assessment report,
    which is rendered before the reports phase). Returns absolute paths."""
    found: list[dict] = []
    seen: set[str] = set()
    reports_dir = os.path.join(conv_dir, "Reports")
    search_dirs = [reports_dir, conv_dir, os.path.join(conv_dir, "Output")]

    def _add(path: str, kind: str, label: str) -> None:
        real = os.path.realpath(path)
        if real in seen or not os.path.isfile(path):
            return
        seen.add(real)
        found.append({
            "file": os.path.abspath(path),
            "kind": kind,
            "label": label,
            "ts": _mtime_iso(path),
        })

    # Named artifacts first (priority order preserved).
    for base, kind, label in _KNOWN_REPORTS:
        for d in search_dirs:
            _add(os.path.join(d, base), kind, label)

    # Any other HTML report in Reports/ (e.g. validation reports).
    if os.path.isdir(reports_dir):
        try:
            for name in sorted(os.listdir(reports_dir)):
                if name.lower().endswith((".html", ".htm")):
                    _add(os.path.join(reports_dir, name), "html",
                         name.rsplit(".", 1)[0].replace("_", " "))
                elif name.lower().endswith(".csv"):
                    _add(os.path.join(reports_dir, name), "csv",
                         name.rsplit(".", 1)[0].replace("_", " "))
        except OSError:
            pass

    return found


def _mtime_iso(path: str) -> str | None:
    try:
        import datetime
        ts = os.path.getmtime(path)
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)\
            .isoformat(timespec="seconds").replace("+00:00", "Z")
    except Exception:
        return None


def collect(run_dir: str) -> dict[str, Any]:
    """
    Return a snapshot dict with all SCOS metrics we can read right now.
    All values are None / empty when the artifact doesn't exist yet.
    """
    snapshot: dict[str, Any] = {
        "files_discovered": None,
        "feasibility_score": None,
        "risk_buckets": None,
        "api_calls_found": None,
        "unsupported_apis": None,
        "issues": None,
        "inventory": None,
        "dependency_edges": None,
        "state": None,
        "discovered_reports": [],
    }

    # Try the run_dir itself, then look for Conversion-SCOS-* subdirectory
    conv_dir = _find_scos_conversion_dir(run_dir) or run_dir

    reports_dir = os.path.join(conv_dir, "Reports")
    output_dir  = os.path.join(conv_dir, "Output")

    # Report artifacts already on disk (assessment report, IR, dashboard CSVs).
    snapshot["discovered_reports"] = _discover_reports(conv_dir)

    # analysis.json / AssessmentIR.json
    for name in ("analysis.json", "AssessmentIR.json"):
        path = os.path.join(conv_dir, name)
        if not os.path.exists(path):
            path = os.path.join(output_dir, name)
        if os.path.exists(path):
            data = _read_json(path)
            snapshot["files_discovered"] = data.get("total_files") or data.get("file_count")
            snapshot["api_calls_found"]  = data.get("total_api_calls") or data.get("api_call_count")
            snapshot["unsupported_apis"] = data.get("unsupported_api_count") or data.get("unsupported_count")
            break

    # feasibility_assessment output (may be embedded in analysis.json or a separate file)
    for name in ("feasibility_assessment.json", "feasibility.json"):
        path = os.path.join(conv_dir, name)
        if os.path.exists(path):
            data = _read_json(path)
            snapshot["feasibility_score"] = data.get("score") or data.get("feasibility_score")
            break

    # risk_scoring output
    for name in ("risk_scoring.json", "risk_assessment.json"):
        path = os.path.join(conv_dir, name)
        if os.path.exists(path):
            data = _read_json(path)
            snapshot["risk_buckets"] = data.get("risk_buckets") or data.get("buckets")
            break

    # Reports/*.csv
    issues_csv = os.path.join(reports_dir, "Issues.csv")
    if os.path.exists(issues_csv):
        snapshot["issues"] = _read_issues_csv(issues_csv)

    inventory_csv = os.path.join(reports_dir, "InputFilesInventory.csv")
    if os.path.exists(inventory_csv):
        snapshot["inventory"] = _read_inventory_csv(inventory_csv)

    dep_csv = os.path.join(reports_dir, "ArtifactDependencyInventory.csv")
    if os.path.exists(dep_csv):
        snapshot["dependency_edges"] = _count_csv_rows(dep_csv)

    # migration_state.json
    for name in ("migration_state.json", "state.json"):
        path = os.path.join(conv_dir, name)
        if os.path.exists(path):
            snapshot["state"] = _read_json(path)
            break

    return snapshot
