"""Round-trip tests for the dependency-free YAML shim."""

from __future__ import annotations

import unittest

import _support  # noqa: F401  (puts engine/scripts on sys.path)
import _minyaml


class MinYamlTests(unittest.TestCase):
    def test_roundtrip_nested_and_special_chars(self):
        data = {
            "format_version": 1,
            "plugin": "sample",
            "data_needs": {
                "a.b.c": {
                    "required": "yes",
                    "synonyms": ["has: colon", 'has "quotes"', "star*", "# not a comment"],
                    "grain": ["date", "segment"],
                    "sources": [
                        {"fqn": "DB.SCH.T", "columns": {"x": "X", "y": "Y"}},
                        {"fqn": "DB.SCH.U", "columns": {}},
                    ],
                },
                "d": {"required": "no", "sources": [{"fqn": "E.F.G", "columns": {"n": "N"}}]},
            },
            "unresolved": {"extra_settings": {"a.b.c": {"v": "<YOUR_V>"}}},
        }
        self.assertEqual(_minyaml.load(_minyaml.dump(data)), data)

    def test_empty_and_scalars(self):
        self.assertEqual(_minyaml.load(_minyaml.dump({})), {})
        data = {"a": [1, 2, 3], "b": {"t": True, "f": False, "z": None, "flt": 3.5},
                "e": [], "m": {}, "u": "naïve – é"}
        self.assertEqual(_minyaml.load(_minyaml.dump(data)), data)

    def test_nested_lists(self):
        data = {"x": [[1, 2], [{"k": "v"}], []]}
        self.assertEqual(_minyaml.load(_minyaml.dump(data)), data)

    def test_output_is_json_scalar_encoded(self):
        data = {"note": "grain: period, cost_category\nsecond line"}
        text = _minyaml.dump(data)
        self.assertIn('"grain: period, cost_category\\nsecond line"', text)
        self.assertEqual(_minyaml.load(text), data)


if __name__ == "__main__":
    unittest.main()
