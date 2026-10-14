"""The published JSON Schema stays in lockstep with the pydantic models and is a
valid, enforceable schema."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ASSESS = Path(__file__).resolve().parent.parent
if str(_ASSESS) not in sys.path:
    sys.path.insert(0, str(_ASSESS))

import export_llm_schema


def test_committed_schema_matches_models():
    """Drift guard: the committed .json equals what the models generate now.
    If this fails, run `python scripts/assessment/export_llm_schema.py`."""
    committed = json.loads(export_llm_schema.SCHEMA_PATH.read_text())
    assert committed == export_llm_schema.build()


def test_schema_is_valid_draft_2020_12():
    import jsonschema
    jsonschema.Draft202012Validator.check_schema(export_llm_schema.build())


def test_valid_instance_passes():
    import jsonschema
    schema = export_llm_schema.build()
    good = {
        "model": "t",
        "analyzed_files": ["a.py"],
        "excluded_files": [],
        "edges": [{"file": "a.py", "line": 1, "kind": "read",
                   "resolved_signature": "t", "resolution_type": "traced",
                   "source": "resolved_unresolved"}],
        "unresolvable_edges": [],
        "resolved_imports": [],
        "orchestration_edges": [],
        "llm_insights": [],
    }
    jsonschema.Draft202012Validator(schema).validate(good)


def test_bad_enum_and_missing_field_are_caught():
    import jsonschema
    v = jsonschema.Draft202012Validator(export_llm_schema.build())
    # bad `source` enum value + missing required `resolved_signature`
    bad = {"edges": [{"file": "a.py", "line": 1, "kind": "read",
                      "resolution_type": "traced", "source": "bogus"}]}
    errors = list(v.iter_errors(bad))
    assert errors, "schema should reject a bad source enum / missing required field"
