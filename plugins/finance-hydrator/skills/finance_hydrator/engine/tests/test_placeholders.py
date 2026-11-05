"""Template {{token}} contract: catalog defines every token, builder fills them."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _support import SKILL_DIR, TESTDATA, run, run_json, write_json

import _minyaml  # noqa: E402
import score_readiness  # noqa: E402

ACCOUNTING = SKILL_DIR / "catalog" / "accounting.json"


def load_catalog() -> dict:
    return json.loads(ACCOUNTING.read_text(encoding="utf-8"))


def skill(catalog: dict, name: str) -> dict:
    return next(s for s in catalog["skills"] if s["name"] == name)


def need(entry: dict, key: str) -> dict:
    return next(n for n in entry["data_needs"] if n["key"] == key)


class CatalogTokenTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def lint(self, catalog: dict) -> dict:
        path = write_json(self.tmp / "catalog.json", catalog)
        return run_json("validate_catalog.py", "--validate-catalog", "--catalog", path, ok=False)

    def test_accounting_templates_fully_mapped(self):
        report = self.lint(load_catalog())
        self.assertEqual(report["status"], "OK", report.get("errors", []))
        token_warnings = [w for w in report.get("warnings", []) if "token" in w or "produced by both" in w]
        self.assertEqual(token_warnings, [])

    def test_undefined_token_fails(self):
        catalog = load_catalog()
        del need(skill(catalog, "ar_tracker"), "accounting.ar.aging")["fields"]["currency"]
        report = self.lint(catalog)
        self.assertEqual(report["status"], "FAILED")
        self.assertTrue(any("{{cols.currency}}" in e for e in report.get("errors", [])), report.get("errors", []))

    def test_token_collision_fails(self):
        catalog = load_catalog()
        flux = skill(catalog, "flux_analysis")
        del need(flux, "accounting.calendar.fiscal_periods")["fields"]["period"]["placeholder"]
        report = self.lint(catalog)
        self.assertTrue(any("cols.period is produced by both" in e for e in report.get("errors", [])),
                        report.get("errors", []))

    def test_enrichment_flag_on_required_field_fails(self):
        catalog = load_catalog()
        field = need(skill(catalog, "ar_tracker"), "accounting.ar.aging")["fields"]["balance"]
        field["affects_readiness"] = False
        report = self.lint(catalog)
        self.assertTrue(any("affects_readiness false on a required field" in e
                            for e in report.get("errors", [])), report.get("errors", []))

    def test_unmigrated_skill_only_warns(self):
        catalog = load_catalog()
        for n in skill(catalog, "ar_tracker")["data_needs"]:
            n.pop("placeholder", None)
        del need(skill(catalog, "ar_tracker"), "accounting.ar.aging")["fields"]["currency"]
        report = self.lint(catalog)
        self.assertEqual(report["status"], "OK", report.get("errors", []))
        self.assertTrue(any("not mapped" in w for w in report.get("warnings", [])))


class EnrichmentScoringTests(unittest.TestCase):
    def test_missing_enrichment_field_keeps_optional_ratio(self):
        entry = skill(load_catalog(), "accounts_payable_pulse")
        header = need(entry, "accounting.ap.invoice_header")
        obj = {
            "fqn": "ERP_PROD.AP.BILLS",
            "official": True,
            "confidence": "HIGH",
            "columns_present": ["INVOICE_ID", "SUPPLIER", "INVOICE_AMOUNT", "INVOICE_STATUS",
                                "INVOICE_DATE", "DUE_DATE", "OPEN_AMOUNT"],
        }
        result = score_readiness.score_need(header, obj)
        self.assertEqual(result["optional_ratio"], 1.0)
        self.assertNotIn("currency", result["binding"]["columns"])


class HintsPlaceholderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        catalog = ["--catalog", str(ACCOUNTING)]
        fit = run_json("score_fit.py", "--profile", TESTDATA / "profile_controller.json", *catalog)
        ready = run_json("score_readiness.py", "--inventory", TESTDATA / "inv_rich.json", *catalog)
        recs = run_json("combine_recommendations.py",
                        "--fit", write_json(tmp / "fit.json", fit),
                        "--readiness", write_json(tmp / "ready.json", ready))
        cls.plugin = tmp / "plugin"
        cls.build = run("build_customer_plugin.py", "--assessment", write_json(tmp / "recs.json", recs),
                        *catalog, "--output", cls.plugin)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def hints(self, name: str) -> dict:
        path = self.plugin / "skills" / name / "hydration_hints.yaml"
        return _minyaml.load(path.read_text(encoding="utf-8"))

    def test_placeholders_map_tokens_to_physical_names(self):
        self.assertEqual(self.build.returncode, 0, self.build.stderr)
        ph = self.hints("duplicate_invoice_check")["placeholders"]
        self.assertEqual(ph["invoice_header_fqn"], "ERP_PROD.AP.VENDOR_BILLS")
        self.assertEqual(ph["cols.supplier_id"], "VENDOR_ID")
        self.assertEqual(ph["cols.vendor_invoice_reference"], "SUPPLIER_INVOICE_NUMBER")
        self.assertEqual(ph["settings.excluded_statuses"], "'VOID', 'CANCELLED', 'REVERSED'")

    def test_overridden_tokens_do_not_collide(self):
        ph = self.hints("flux_analysis")["placeholders"]
        self.assertIn("cols.period", ph)
        self.assertIn("cols.cal_period", ph)
        self.assertNotEqual(ph["cols.period"], ph["cols.cal_period"])

    def test_unmapped_fields_listed_not_dropped(self):
        unresolved = self.hints("duplicate_invoice_check")["unresolved"]
        self.assertIn("currency", unresolved["enrichment_columns"]["accounting.ap.invoice_header"])
        self.assertIn("accounting.ap.invoice_lines", unresolved["enrichment_data_needs"])

    def test_enrichment_gaps_do_not_demote_status(self):
        report = run_json("validate_bindings.py", "--plugin-dir", self.plugin, "--metadata-only",
                          "--inventory", TESTDATA / "inv_rich.json", ok=False)
        statuses = {s["skill"]: s for s in report["skills"]}
        dup = statuses["duplicate_invoice_check"]
        self.assertEqual(dup["status"], "READY", dup)
        self.assertTrue(dup["notes"])


if __name__ == "__main__":
    unittest.main()
