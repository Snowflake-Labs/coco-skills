"""End-to-end engine pipeline: score_fit -> score_readiness -> combine -> build -> validate."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _support import TESTDATA, by_skill, catalog_args, run, run_json, write_json

import score_readiness  # noqa: E402

CONTROLLER_SKILLS = ("flux_analysis", "accounts_payable_pulse", "duplicate_invoice_check")


class Pipeline:
    """Run the full deterministic pipeline for one inventory + profile."""

    def __init__(self, tmp: Path, inventory: str, profile: str, *build_flags: str):
        self.tmp = tmp
        self.inventory = TESTDATA / inventory
        self.fit = run_json("score_fit.py", "--profile", TESTDATA / profile, *catalog_args())
        self.readiness = run_json(
            "score_readiness.py", "--inventory", self.inventory, *catalog_args()
        )
        fit_path = write_json(tmp / "fit.json", self.fit)
        ready_path = write_json(tmp / "readiness.json", self.readiness)
        self.recs = run_json("combine_recommendations.py", "--fit", fit_path, "--readiness", ready_path)
        self.recs_path = write_json(tmp / "recs.json", self.recs)
        self.plugin = tmp / "customer-finance"
        self.build = run(
            "build_customer_plugin.py", "--assessment", self.recs_path, *catalog_args(),
            "--output", self.plugin, *build_flags,
        )

    def validate(self) -> dict:
        return run_json(
            "validate_bindings.py", "--plugin-dir", self.plugin, "--metadata-only",
            "--inventory", self.inventory, ok=False,
        )


class FitTests(unittest.TestCase):
    def test_controller_fit_is_high(self):
        fit = by_skill(run_json("score_fit.py", "--profile", TESTDATA / "profile_controller.json",
                                *catalog_args()))
        for skill in CONTROLLER_SKILLS:
            self.assertGreaterEqual(fit[skill]["business_fit"], 70, skill)

    def test_cfo_budget_vs_actuals_fit_is_high(self):
        fit = by_skill(run_json("score_fit.py", "--profile", TESTDATA / "profile_cfo.json",
                                *catalog_args()))
        self.assertGreaterEqual(fit["budget_vs_actuals"]["business_fit"], 70)


class RichPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.p = Pipeline(Path(cls._tmp.name), "inv_rich.json", "profile_controller.json")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_controller_skills_recommended(self):
        recs = by_skill(self.p.recs)
        for skill in CONTROLLER_SKILLS:
            self.assertEqual(recs[skill]["final_status"], "RECOMMENDED", (skill, recs[skill]["reason"]))

    def test_realistic_column_names_bound(self):
        bindings = by_skill(self.p.readiness)["duplicate_invoice_check"]["bindings"]
        cols = bindings["accounting.ap.invoice_header"]["columns"]
        self.assertEqual(cols["vendor_invoice_reference"], "SUPPLIER_INVOICE_NUMBER")
        self.assertEqual(cols["supplier_id"], "VENDOR_ID")
        gl = by_skill(self.p.readiness)["flux_analysis"]["bindings"]["accounting.gl.journal_entries"]
        self.assertEqual(gl["columns"], {"period": "POSTING_PERIOD", "account": "GL_ACCOUNT",
                                         "amount": "NET_AMOUNT"})

    def test_build_and_validate(self):
        self.assertEqual(self.p.build.returncode, 0, self.p.build.stderr)
        self.assertTrue((self.p.plugin / ".finance-hydrator").exists())
        built = {p.name for p in (self.p.plugin / "skills").iterdir()}
        self.assertTrue(set(CONTROLLER_SKILLS) <= built, built)
        report = self.p.validate()
        self.assertIn(report["status"], {"READY", "PARTIAL"}, report["errors"])
        statuses = {s["skill"]: s["status"] for s in report["skills"]}
        for skill in CONTROLLER_SKILLS:
            self.assertEqual(statuses[skill], "READY", report)

    def test_readme_has_no_local_path(self):
        readme = (self.p.plugin / "README.md").read_text()
        self.assertIn("<path-to-plugin>", readme)
        self.assertNotIn(str(self.p.tmp), readme)


class SparsePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.p = Pipeline(Path(cls._tmp.name), "inv_sparse.json", "profile_controller.json",
                         "--include-partial")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_gl_only_statuses(self):
        recs = by_skill(self.p.recs)
        # Fiscal calendar is optional for flux: GL alone is enough to run it.
        self.assertEqual(recs["flux_analysis"]["data_status"], "PARTIAL")
        self.assertEqual(recs["flux_analysis"]["final_status"], "ASK")
        for skill in ("accounts_payable_pulse", "duplicate_invoice_check"):
            self.assertEqual(recs[skill]["final_status"], "BLOCKED", skill)
            self.assertIn("missing required source", recs[skill]["reason"])

    def test_controller_skills_never_not_relevant(self):
        recs = by_skill(self.p.recs)
        for skill in CONTROLLER_SKILLS:
            self.assertNotEqual(recs[skill]["final_status"], "NOT_RELEVANT", skill)

    def test_lowercase_confidence_normalized(self):
        binding = by_skill(self.p.readiness)["flux_analysis"]["bindings"]["accounting.gl.journal_entries"]
        self.assertEqual(binding["confidence"], "MEDIUM")

    def test_unbound_optional_goes_to_unresolved(self):
        self.assertEqual(self.p.build.returncode, 0, self.p.build.stderr)
        hints = (self.p.plugin / "skills" / "flux_analysis" / "hydration_hints.yaml").read_text()
        self.assertIn("unresolved:", hints)
        self.assertNotIn("<", hints)
        report = self.p.validate()
        statuses = {s["skill"]: s["status"] for s in report["skills"]}
        self.assertEqual(statuses["flux_analysis"], "PARTIAL", report)


class MessyPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.p = Pipeline(Path(cls._tmp.name), "inv_messy.json", "profile_controller.json")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_all_blocked_non_official(self):
        for item in self.p.readiness["skills"]:
            self.assertEqual(item["status"], "BLOCKED", item["skill"])
            self.assertIn("NON_OFFICIAL", item["block_kinds"], item["skill"])

    def test_controller_skills_blocked_with_reason(self):
        recs = by_skill(self.p.recs)
        for skill in CONTROLLER_SKILLS:
            self.assertEqual(recs[skill]["final_status"], "BLOCKED", skill)
            self.assertIn("non-official", recs[skill]["reason"])

    def test_nothing_to_build(self):
        self.assertNotEqual(self.p.build.returncode, 0)
        self.assertIn("no skills selected", self.p.build.stderr)


class ReadinessUnitTests(unittest.TestCase):
    def test_non_official_tokens(self):
        bad = ["DEV_DB.X.Y", "DB.SANDBOX.T", "DB.S.T_BKP", "DB.S.SNAPSHOT_T", "DB.S.T_20260101",
               "DB.S.T_202601", '"DB"."TEMP"."T"', "DB.ARCHIVE.T", "DB.S.CLONE"]
        good = ["DEVICE_DB.X.Y", "DB.CONTEST.T", "DB.S.TEMPLATES", "DB.S.LATEST", "DB.S.T_2026",
                "ERP_PROD.GL.JOURNAL_LINES"]
        for fqn in bad:
            self.assertIsNotNone(score_readiness.non_official_reason(fqn), fqn)
        for fqn in good:
            self.assertIsNone(score_readiness.non_official_reason(fqn), fqn)

    def test_staging_tokens_need_confirmation(self):
        for fqn in ["DB.S.PREP_AR_AGING", "DB.RAW.T", "DB.STG_ERP.GL", "LANDING.S.T"]:
            self.assertIsNotNone(score_readiness.non_official_reason(fqn), fqn)
            self.assertIsNone(score_readiness.non_official_reason(fqn, staging_confirmed=True), fqn)
        self.assertIsNone(score_readiness.non_official_reason("DB.S.PREPAID_EXPENSES"))

    def _readiness(self, inventory: object) -> "subprocess.CompletedProcess":
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inv.json"
            path.write_text(json.dumps(inventory))
            return run("score_readiness.py", "--inventory", path, *catalog_args())

    def test_duplicate_key_is_clear_error(self):
        obj = {"data_need_key": "accounting.ar.aging", "fqn": "DB.S.T", "columns_present": [],
               "official": True}
        proc = self._readiness({"objects": [obj, dict(obj)]})
        self.assertEqual(proc.returncode, 2)
        self.assertIn("duplicate data_need_key", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_malformed_inputs_are_friendly(self):
        for inventory in ([1, 2], {"objects": [{"fqn": "DB.S.T"}]}, {"objects": ["x"]}):
            proc = self._readiness(inventory)
            self.assertEqual(proc.returncode, 2, inventory)
            self.assertNotIn("Traceback", proc.stderr)

    def test_quoted_column_names_match(self):
        inv = {"objects": [{"data_need_key": "accounting.gl.journal_entries", "fqn": "DB.GL.LINES",
                            "columns_present": ['"Period"', '"Account"', '"Amount"'],
                            "official": True, "confidence": "high"}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inv.json"
            path.write_text(json.dumps(inv))
            out = by_skill(run_json("score_readiness.py", "--inventory", path, *catalog_args()))
        self.assertEqual(out["flux_analysis"]["status"], "PARTIAL")

    def test_score_fit_rejects_non_dict_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text('{"profile": ["x"]}')
            proc = run("score_fit.py", "--profile", path, *catalog_args())
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("Traceback", proc.stderr)


class CombineUnitTests(unittest.TestCase):
    def test_decide(self):
        import combine_recommendations as cr

        self.assertEqual(cr.decide(50, "BLOCKED")[0], "BLOCKED")
        self.assertEqual(cr.decide(80, "BLOCKED")[0], "BLOCKED")
        self.assertEqual(cr.decide(30, "BLOCKED")[0], "NOT_RELEVANT")
        self.assertEqual(cr.decide(50, "READY")[0], "OPTIONAL")
        self.assertEqual(cr.decide(80, "READY")[0], "RECOMMENDED")
        self.assertEqual(cr.decide(80, "PARTIAL")[0], "ASK")


if __name__ == "__main__":
    unittest.main()
