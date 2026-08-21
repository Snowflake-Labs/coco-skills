#!/usr/bin/env python3
"""
sqlite_collector.py — Read-only poller for SMA-path metrics via sma_api.py.

Used on the snowpark-api path. Polls sma_storage.sqlite3 via the
existing sma_api.py getters every 3-5 s and returns structured metrics.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any


def _load_sma_api(skill_dir: str):
    """Dynamically import sma_api from the snowpark-api scripts directory."""
    api_path = os.path.join(skill_dir, "snowpark-api", "scripts", "sma_api.py")
    if not os.path.exists(api_path):
        return None
    spec = importlib.util.spec_from_file_location("sma_api", api_path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def collect(run_dir: str, skill_dir: str) -> dict[str, Any]:
    """Return SMA metrics from sma_storage.sqlite3."""
    snapshot: dict[str, Any] = {
        "readiness_pct": None,
        "blocker_ewis": None,
        "ewi_status": None,
        "ewi_code_stats": None,
        "fix_summary": None,
        "test_results": None,
    }

    # Locate the sqlite db
    db_path = None
    for root, _, files in os.walk(run_dir):
        if "sma_storage.sqlite3" in files:
            db_path = os.path.join(root, "sma_storage.sqlite3")
            break

    if not db_path or not os.path.exists(db_path):
        return snapshot

    sma_api = _load_sma_api(skill_dir)
    if sma_api is None:
        return snapshot

    try:
        summary = sma_api.get_migration_summary(db_path)
        if summary:
            snapshot["readiness_pct"] = getattr(summary, "readiness_percentage", None) or (
                summary.get("readiness_percentage") if isinstance(summary, dict) else None
            )
            snapshot["blocker_ewis"] = getattr(summary, "blocker_ewis", None) or (
                summary.get("blocker_ewis") if isinstance(summary, dict) else None
            )
    except Exception:
        pass

    try:
        stats = sma_api.get_summary_stats(db_path)
        if stats:
            snapshot["ewi_status"] = stats if isinstance(stats, dict) else vars(stats)
    except Exception:
        pass

    try:
        code_stats = sma_api.get_ewi_code_stats(db_path)
        if code_stats:
            snapshot["ewi_code_stats"] = code_stats if isinstance(code_stats, list) else list(code_stats)
    except Exception:
        pass

    try:
        fix_sum = sma_api.get_fix_summary(db_path)
        if fix_sum:
            snapshot["fix_summary"] = fix_sum if isinstance(fix_sum, dict) else vars(fix_sum)
    except Exception:
        pass

    # test_results.json (DVP)
    for root, _, files in os.walk(run_dir):
        if "test_results.json" in files:
            import json
            try:
                with open(os.path.join(root, "test_results.json")) as fh:
                    snapshot["test_results"] = json.load(fh)
            except Exception:
                pass
            break

    return snapshot
