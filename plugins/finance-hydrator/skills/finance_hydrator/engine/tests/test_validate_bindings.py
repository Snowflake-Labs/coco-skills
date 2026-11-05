"""validate_bindings.py: placeholders, empty strings, FQN shape, parse errors, inventory."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _support import run_json, write_json

import _minyaml  # noqa: E402

GOOD = {
    "data_needs": {
        "accounting.gl.journal_entries": {
            "required": "yes",
            "sources": [{"fqn": "ERP_PROD.GL.JOURNAL_LINES",
                         "columns": {"period": "PERIOD", "amount": "AMOUNT"}}],
        }
    }
}
INVENTORY = {"objects": [{"data_need_key": "accounting.gl.journal_entries",
                          "fqn": "ERP_PROD.GL.JOURNAL_LINES",
                          "columns_present": {"period": "PERIOD", "amount": "AMOUNT"},
                          "official": True}]}


def with_need(**changes) -> dict:
    data = json.loads(json.dumps(GOOD))
    data["data_needs"]["accounting.gl.journal_entries"].update(changes)
    return data


class ValidateBindingsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.plugin = Path(self._tmp.name) / "plugin"
        (self.plugin / ".cortex-plugin").mkdir(parents=True)
        (self.plugin / ".cortex-plugin" / "plugin.json").write_text("{}")
        self.inventory = write_json(Path(self._tmp.name) / "inv.json", INVENTORY)

    def tearDown(self):
        self._tmp.cleanup()

    def validate(self, hints: dict | str, *flags: str) -> dict:
        skill = self.plugin / "skills" / "flux_analysis"
        skill.mkdir(parents=True, exist_ok=True)
        text = hints if isinstance(hints, str) else _minyaml.dump(hints)
        (skill / "hydration_hints.yaml").write_text(text)
        return run_json("validate_bindings.py", "--plugin-dir", self.plugin, *flags, ok=False)

    def skill_status(self, report: dict) -> str:
        return {s["skill"]: s["status"] for s in report["skills"]}["flux_analysis"]

    def test_clean_is_ready(self):
        report = self.validate(GOOD, "--metadata-only", "--inventory", str(self.inventory))
        self.assertEqual(report["status"], "READY", report)
        self.assertEqual(self.skill_status(report), "READY")

    def test_optional_placeholder_is_error(self):
        data = with_need(required="no", sources=[{"fqn": "<YOUR_DB>.S.T", "columns": {}}])
        report = self.validate(data)
        self.assertEqual(self.skill_status(report), "BLOCKED")
        self.assertTrue(any("placeholder" in e for e in report["errors"]))

    def test_extra_settings_placeholder_is_error(self):
        data = with_need(extra_settings={"version_column": "YOUR_VERSION_COLUMN"})
        self.assertEqual(self.skill_status(self.validate(data)), "BLOCKED")

    def test_empty_string_is_error(self):
        data = with_need(sources=[{"fqn": "ERP_PROD.GL.JOURNAL_LINES", "columns": {"period": ""}}])
        report = self.validate(data)
        self.assertTrue(any("empty string" in e for e in report["errors"]), report)

    def test_fqn_must_have_three_parts(self):
        for fqn in ("GL.JOURNAL_LINES", "A.B.C.D", "A..C"):
            report = self.validate(with_need(sources=[{"fqn": fqn, "columns": {}}]))
            self.assertEqual(self.skill_status(report), "BLOCKED", fqn)
        report = self.validate(with_need(sources=[{"fqn": '"My.Db".S.T', "columns": {}}]))
        self.assertEqual(self.skill_status(report), "READY", report)

    def test_unresolved_block_is_warning_only(self):
        data = dict(GOOD, unresolved={"extra_settings": {"k": {"v": "<YOUR_V>"}},
                                      "data_needs": {"x.y": {"required": "no"}}})
        report = self.validate(data)
        self.assertEqual(report["status"], "PARTIAL", report)
        self.assertEqual(report["errors"], [])
        self.assertEqual(len(report["warnings"]), 2)

    def test_binding_disagreeing_with_inventory_mapping_is_error(self):
        # AMOUNT exists on the table, but the inventory maps period -> PERIOD.
        data = with_need(sources=[{"fqn": "ERP_PROD.GL.JOURNAL_LINES",
                                   "columns": {"period": "AMOUNT", "amount": "AMOUNT"}}])
        report = self.validate(data, "--metadata-only", "--inventory", str(self.inventory))
        self.assertEqual(self.skill_status(report), "BLOCKED", report)
        self.assertTrue(any("disagrees with inventory mapping" in e for e in report["errors"]))

    def test_parse_error_is_reported(self):
        report = self.validate("data_needs:\n  a:\n    required: {not json\n")
        self.assertEqual(self.skill_status(report), "BLOCKED")
        self.assertTrue(any("cannot parse" in e for e in report["errors"]), report)

    def test_inventory_cross_check(self):
        data = with_need(sources=[{"fqn": "ERP_PROD.GL.OTHER", "columns": {}}])
        report = self.validate(data, "--metadata-only", "--inventory", str(self.inventory))
        self.assertTrue(any("not found in inventory" in e for e in report["errors"]), report)
        data = with_need(sources=[{"fqn": "erp_prod.gl.journal_lines",
                                   "columns": {"period": "PERIOD", "x": "NOPE"}}])
        report = self.validate(data, "--metadata-only", "--inventory", str(self.inventory))
        self.assertEqual(len(report["errors"]), 1, report)
        self.assertIn("NOPE", report["errors"][0])

    def test_missing_hints_blocked(self):
        (self.plugin / "skills" / "bare").mkdir(parents=True)
        report = run_json("validate_bindings.py", "--plugin-dir", self.plugin, ok=False)
        self.assertEqual(report["status"], "BLOCKED")


class SmokePlanTests(unittest.TestCase):
    def test_missing_plugin_dir_errors(self):
        from _support import run

        proc = run("smoke_test_plan.py", "--plugin-dir", "/nonexistent/customer-finance")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no generated plugin", proc.stderr)


if __name__ == "__main__":
    unittest.main()
