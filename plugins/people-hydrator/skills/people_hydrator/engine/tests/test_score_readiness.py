import json
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
SCRIPT = ENGINE / "scripts" / "score_readiness.py"
INVENTORY = ENGINE / "testdata" / "sample_inventory.json"
CATALOG = ENGINE / "testdata" / "catalog" / "sample_domain.json"


def run_scorer():
    cmd = [sys.executable, str(SCRIPT), "--inventory", str(INVENTORY), "--catalog", str(CATALOG)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def skill(result, name):
    return next(s for s in result["skills"] if s["skill"] == name)


def test_ready_skill_has_bindings_and_include():
    r = skill(run_scorer(), "ready_skill")
    assert r["status"] == "READY"
    assert r["recommendation"] == "include"
    assert r["readiness"] >= 80
    assert r["bindings"]["s.ready.main"]["fqn"] == "SAMPLE_DB.X.READY_MAIN"
    assert r["gaps"] == []


def test_missing_required_source_is_blocked():
    r = skill(run_scorer(), "blocked_source")
    assert r["status"] == "BLOCKED"
    assert r["recommendation"] == "exclude"
    assert r["readiness"] <= 40
    assert any("s.blocked.main" in g for g in r["gaps"])


def test_missing_unsafe_scoping_column_is_blocked():
    r = skill(run_scorer(), "blocked_unsafe")
    assert r["status"] == "BLOCKED"
    assert r["readiness"] <= 40
    assert any("tenant_id" in g for g in r["gaps"])


def test_optional_gap_is_partial_and_clamped():
    r = skill(run_scorer(), "partial_skill")
    assert r["status"] == "PARTIAL"
    assert r["recommendation"] == "ask"
    assert r["readiness"] <= 74
    assert r["bindings"]["s.partial.main"]["fqn"] == "SAMPLE_DB.X.PARTIAL_MAIN"
