"""Output-directory safety and input validation for build_customer_plugin.py."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from _support import PLUGIN_DIR, SKILL_DIR, catalog_args, run, write_json

import build_customer_plugin as bcp  # noqa: E402

ASSESSMENT = {
    "skills": [
        {
            "domain": "accounting",
            "skill": "flux_analysis",
            "status": "READY",
            "readiness": 100,
            "recommendation": "include",
            "bindings": {
                "accounting.gl.journal_entries": {
                    "fqn": "ERP_PROD.GL.JOURNAL_LINES",
                    "confidence": "HIGH",
                    "columns": {"period": "PERIOD", "account": "ACCOUNT", "amount": "AMOUNT"},
                }
            },
        }
    ]
}


class BuildSafetyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.assessment = write_json(self.tmp / "assessment.json", ASSESSMENT)

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, output: Path, *flags: str, assessment: Path | None = None, catalogs=None):
        return run("build_customer_plugin.py", "--assessment", assessment or self.assessment,
                   *(catalogs or catalog_args()), "--output", output, *flags)

    def test_skills_allow_list(self):
        rows = [dict(ASSESSMENT["skills"][0]),
                dict(ASSESSMENT["skills"][0], skill="ar_tracker", recommendation="ask",
                     bindings={}),
                dict(ASSESSMENT["skills"][0], skill="good_morning_collector",
                     final_status="BLOCKED", recommendation="exclude", bindings={})]
        assessment = write_json(self.tmp / "multi.json", {"skills": rows})
        out = self.tmp / "allow"
        proc = self.build(out, "--skills", "flux_analysis", assessment=assessment)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        built = sorted(p.name for p in (out / "skills").iterdir())
        self.assertEqual(built, ["flux_analysis"])
        for bad in ("nope", "good_morning_collector"):
            proc = self.build(self.tmp / f"x_{bad}", "--skills", bad, assessment=assessment)
            self.assertNotEqual(proc.returncode, 0, bad)

    def test_new_output_gets_marker(self):
        out = self.tmp / "new"
        proc = self.build(out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((out / bcp.MARKER_NAME).exists())
        self.assertTrue((out / "skills" / "flux_analysis" / "hydration_hints.yaml").exists())

    def test_non_empty_unmarked_dir_refused(self):
        out = self.tmp / "precious"
        out.mkdir()
        (out / "notes.txt").write_text("keep me")
        proc = self.build(out)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--force", proc.stderr)
        self.assertEqual((out / "notes.txt").read_text(), "keep me")

    def test_marker_dir_is_replaced(self):
        out = self.tmp / "gen"
        self.assertEqual(self.build(out).returncode, 0)
        (out / "stale.txt").write_text("old")
        proc = self.build(out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse((out / "stale.txt").exists())
        self.assertTrue((out / bcp.MARKER_NAME).exists())

    def test_force_replaces_unmarked_dir(self):
        out = self.tmp / "forced"
        out.mkdir()
        (out / "notes.txt").write_text("x")
        proc = self.build(out, "--force")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse((out / "notes.txt").exists())

    def test_empty_existing_dir_ok(self):
        out = self.tmp / "empty"
        out.mkdir()
        self.assertEqual(self.build(out).returncode, 0)

    def test_home_root_and_plugin_ancestors_refused(self):
        for target in (Path.home(), Path("/"), PLUGIN_DIR, PLUGIN_DIR.parent):
            with self.assertRaises(bcp.RefuseOutput, msg=str(target)):
                bcp.check_output_dir(target.resolve(), force=True)

    def test_home_refused_via_cli(self):
        proc = self.build(Path.home(), "--force")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("refusing", proc.stderr)

    def test_traversal_skill_name_rejected(self):
        catalog = json.loads((SKILL_DIR / "catalog" / "accounting.json").read_text())
        catalog["skills"][0]["name"] = "../escape"
        bad = write_json(self.tmp / "bad.json", catalog)
        proc = self.build(self.tmp / "out", catalogs=["--catalog", str(bad)])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("invalid skill name", proc.stderr)
        self.assertFalse((self.tmp / "escape").exists())
        lint = run("validate_catalog.py", "--validate-catalog", "--catalog", bad)
        self.assertNotEqual(lint.returncode, 0)
        self.assertIn("invalid skill name", lint.stdout)

    def test_source_dir_traversal_rejected(self):
        catalog = json.loads((SKILL_DIR / "catalog" / "accounting.json").read_text())
        catalog["skills"][0]["source_dir"] = "../../../engine"
        bad = write_json(self.tmp / "bad.json", catalog)
        proc = self.build(self.tmp / "out", catalogs=["--catalog", str(bad)])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("escapes", proc.stderr)

    def test_zero_skills_fails(self):
        empty = write_json(self.tmp / "none.json", {"skills": [
            dict(ASSESSMENT["skills"][0], recommendation="exclude")]})
        out = self.tmp / "out"
        proc = self.build(out, assessment=empty)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no skills selected", proc.stderr)
        self.assertFalse(out.exists())

    def test_split_placeholders(self):
        kept, moved = bcp.split_placeholders(
            {"a": "<YOUR_X>", "b": "ok", "c": {"d": "YOUR_Y", "e": 1}, "f": []})
        self.assertEqual(kept, {"b": "ok", "c": {"e": 1}, "f": []})
        self.assertEqual(moved, {"a": "<YOUR_X>", "c": {"d": "YOUR_Y"}})


if __name__ == "__main__":
    unittest.main()
