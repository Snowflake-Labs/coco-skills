import json
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
SCORER = ENGINE / "scripts" / "score_readiness.py"


def _run(catalog: dict, inventory: dict, tmp: Path):
    cat = tmp / "catalog.json"
    inv = tmp / "inventory.json"
    cat.write_text(json.dumps(catalog))
    inv.write_text(json.dumps(inventory))
    out = subprocess.run(
        [sys.executable, str(SCORER), "--inventory", str(inv), "--catalog", str(cat)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)["skills"][0]


# A skill whose required fields have canonical names that do NOT match the real
# physical column names, but DO match via aliases.
FIELDS_CATALOG = {
    "domain": "sample_domain",
    "source_plugin": "sample",
    "skills": [{
        "name": "cogs_like",
        "source_dir": "skills/cogs_like",
        "data_needs": [{
            "key": "s.cogs.summary",
            "required": True,
            "concepts": ["cogs"],
            "grain": ["period"],
            "fields": {
                "period": {"required": True, "aliases": ["period", "fiscal_quarter_start"]},
                "cost_amount": {"required": True, "aliases": ["cost_amount", "cogs"]},
            },
        }],
    }],
}

# Inventory reports the REAL physical column names only.
PHYSICAL_INVENTORY = {
    "domain_access": {"sample_domain": True},
    "objects": [{
        "data_need_key": "s.cogs.summary",
        "fqn": "ACME_FINANCE.REPORTING.QUARTERLY_COGS_BY_TYPE",
        "official": True,
        "confidence": "HIGH",
        "columns_present": ["FISCAL_QUARTER_START", "COGS_TYPE", "COGS", "REVENUE"],
    }],
}


def test_alias_mapping_resolves_non_identical_physical_columns():
    with tempfile.TemporaryDirectory() as tmp:
        skill = _run(FIELDS_CATALOG, PHYSICAL_INVENTORY, Path(tmp))
    assert skill["status"] == "READY", skill
    # Bindings must carry the REAL physical columns, not canonical placeholders.
    binding = skill["bindings"]["s.cogs.summary"]
    assert binding["columns"]["period"] == "FISCAL_QUARTER_START"
    assert binding["columns"]["cost_amount"] == "COGS"


def test_missing_alias_still_blocks_with_field_kind():
    # No alias for cost_amount matches the physical columns -> blocked.
    catalog = json.loads(json.dumps(FIELDS_CATALOG))
    catalog["skills"][0]["data_needs"][0]["fields"]["cost_amount"]["aliases"] = ["cost_amount", "expense_amount"]
    with tempfile.TemporaryDirectory() as tmp:
        skill = _run(catalog, PHYSICAL_INVENTORY, Path(tmp))
    assert skill["status"] == "BLOCKED", skill
    assert "MISSING_REQUIRED_FIELD" in skill["block_kinds"]


def test_legacy_columns_still_work():
    legacy = {
        "domain": "sample_domain",
        "source_plugin": "sample",
        "skills": [{
            "name": "legacy_skill",
            "source_dir": "skills/legacy_skill",
            "data_needs": [{
                "key": "s.legacy.main",
                "required": True,
                "concepts": ["legacy"],
                "grain": ["id"],
                "columns": {"id": "required", "amount": "optional"},
            }],
        }],
    }
    inventory = {
        "domain_access": {"sample_domain": True},
        "objects": [{
            "data_need_key": "s.legacy.main",
            "fqn": "DB.X.LEGACY",
            "official": True,
            "confidence": "HIGH",
            "columns_present": ["ID", "AMOUNT"],
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        skill = _run(legacy, inventory, Path(tmp))
    assert skill["status"] == "READY", skill
    assert skill["bindings"]["s.legacy.main"]["columns"]["id"] == "ID"
