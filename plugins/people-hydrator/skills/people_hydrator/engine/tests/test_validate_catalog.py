import json
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
SCRIPT = ENGINE / "scripts" / "validate_catalog.py"
CATALOG = ENGINE / "testdata" / "catalog" / "sample_domain.json"
TEMPLATES = ENGINE / "testdata" / "templates"


def run_validate(path=CATALOG):
    cmd = [
        sys.executable, str(SCRIPT), "--validate-catalog",
        "--catalog", str(path),
        "--templates-root", str(TEMPLATES),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True)
    return out.returncode, json.loads(out.stdout)


def test_catalog_is_structurally_valid():
    code, result = run_validate()
    assert code == 0, result
    assert result["status"] == "OK"


def test_missing_golden_cases_emits_warning_not_error():
    code, result = run_validate()
    # partial_skill / blocked_* eval dirs ship no golden_cases.md -> non-fatal warnings.
    assert code == 0
    assert result["warnings"], "expected golden_cases warnings"
    assert any("golden_cases.md" in w for w in result["warnings"])


def test_missing_context_query_is_error(tmp_path):
    data = json.loads(CATALOG.read_text())
    first = data["skills"][0]
    first["context_queries"].pop(first["data_needs"][0]["key"])
    bad = tmp_path / "bad_catalog.json"
    bad.write_text(json.dumps(data))

    code, result = run_validate(bad)

    assert code == 1
    assert any("context_queries missing entry" in e for e in result["errors"])


def test_unknown_emitter_is_error(tmp_path):
    data = json.loads(CATALOG.read_text())
    data["skills"][0]["supported_emitters"] = ["unknown_emitter"]
    bad = tmp_path / "bad_catalog.json"
    bad.write_text(json.dumps(data))

    code, result = run_validate(bad)

    assert code == 1
    assert any("unknown supported_emitter" in e for e in result["errors"])


def test_unknown_finance_subdepartment_is_error(tmp_path):
    data = json.loads(CATALOG.read_text())
    data["skills"][0]["finance_subdepartments"] = ["made_up_department"]
    bad = tmp_path / "bad_catalog.json"
    bad.write_text(json.dumps(data))

    code, result = run_validate(bad)

    assert code == 1
    assert any("unknown subdepartment" in e for e in result["errors"])


def test_catalog_borne_subdepartments(tmp_path):
    data = json.loads(CATALOG.read_text())
    data["subdepartments"] = ["chro", "hrbp", "talent_acquisition"]
    for skill in data["skills"]:
        skill["subdepartments"] = ["hrbp"]
        skill.pop("finance_subdepartments", None)
    ok = tmp_path / "people_catalog.json"
    ok.write_text(json.dumps(data))

    code, result = run_validate(ok)

    assert code == 0, result

    data["skills"][0]["subdepartments"] = ["made_up_department"]
    bad = tmp_path / "bad_people_catalog.json"
    bad.write_text(json.dumps(data))

    code, result = run_validate(bad)

    assert code == 1
    assert any("unknown subdepartment" in e for e in result["errors"])
