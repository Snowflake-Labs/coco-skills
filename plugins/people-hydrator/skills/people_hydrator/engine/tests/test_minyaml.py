import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import _minyaml  # noqa: E402


def test_roundtrip_nested_and_special_chars():
    data = {
        "format_version": 1,
        "plugin": "sample",
        "data_needs": {
            "a.b.c": {
                "required": "yes",
                "synonyms": ["has: colon", 'has "quotes"', "star*"],
                "grain": ["date", "segment"],
                "sources": [
                    {"fqn": "DB.SCH.T", "columns": {"x": "X", "y": "Y"}},
                    {"fqn": "DB.SCH.U", "columns": {}},
                ],
            },
            "d": {"required": "no", "sources": [{"fqn": "E.F.G", "columns": {"n": "N"}}]},
        },
    }
    assert _minyaml.load(_minyaml.dump(data)) == data


def test_empty_and_scalars():
    assert _minyaml.load(_minyaml.dump({})) == {}
    data = {"a": [1, 2, 3], "b": {"t": True, "f": False, "z": None, "flt": 3.5}}
    assert _minyaml.load(_minyaml.dump(data)) == data


def test_output_is_json_scalar_encoded():
    # A value containing a colon must not break the key/value split on reload.
    data = {"note": "grain: period, cost_category"}
    text = _minyaml.dump(data)
    assert '"grain: period, cost_category"' in text
    assert _minyaml.load(text) == data
