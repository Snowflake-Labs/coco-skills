"""Evaluate stage: golden-case parsing, case selection, results bookkeeping and the hydration verdict."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_customer_plugin as H  # noqa: E402
import evaluate as E  # noqa: E402

SKILLS = [s["name"] for s in json.loads(E.CATALOG.read_text(encoding="utf-8"))["skills"]]
READY = {"status": "READY", "gaps": []}
CHECKS_PASS = {"gates": [{"gate": "A1", "status": "PASS"}]}

SAMPLE = """# Golden Test Cases: s1

## Case 1: Happy path

**Input:**
```
show me the brief
```

**Expected behavior:**
- Routes to the default mode
- Uses your fiscal calendar

**Guard checks:**
- [ ] G1: required data bound
- [ ] G2-G3: core bindings

## Case 2: Second

**Input:** what about last month

**Expected behavior:**
- Compares to the prior month

**Guard checks:** G5 all pass

## Case 3: Third

**Input:**
```
third prompt
```

**Expected behavior:**
- Third behavior

## Case 4: Fourth

**Part A — first:**

**Input:**
```
part a prompt
```

**Expected behavior:**
- A behavior

**Part B — second:**

**Input:**
```
part b prompt
```

## Scoring Summary

| Case | Weight |
"""

PLAN = """# Eval Plan
| ID | Metric | Pass Condition |
|----|--------|----------------|
| G1 | Required data bound | needs bound and queries run |
| G2 | Core binding | snapshot table resolved |
| G3 | Flags | exit flags resolved |
| G5 | No fabricated data | never invents tables |
"""


def make_skill(tmp_path, name="s1", golden=SAMPLE, plan=PLAN):
    """A temp catalog + templates tree with one skill and its golden cases."""
    templates = tmp_path / "templates"
    ev = templates / "people" / "evals" / name
    ev.mkdir(parents=True)
    (ev / "golden_cases.md").write_text(golden, encoding="utf-8")
    (ev / "eval_plan.md").write_text(plan, encoding="utf-8")
    catalog = tmp_path / "people.json"
    catalog.write_text(json.dumps({"skills": [
        {"name": name, "source_plugin": "people", "eval_dir": f"evals/{name}"},
        {"name": "no_evals", "source_plugin": "people"}]}), encoding="utf-8")
    draft = tmp_path / "ws" / "draft" / "skills" / name
    draft.mkdir(parents=True)
    (draft / "SKILL.md").write_text("skill v1", encoding="utf-8")
    return catalog, templates, tmp_path / "ws", draft


# --- parsing the shipped golden cases -------------------------------------------------

@pytest.mark.parametrize("skill", SKILLS)
def test_every_shipped_skill_has_parseable_golden_cases(skill):
    folder = E.eval_dir(skill)
    assert folder is not None, f"{skill}: catalog eval_dir missing or has no golden_cases.md"
    cases = E.parse_cases((folder / "golden_cases.md").read_text(encoding="utf-8"))
    assert len(cases) >= 3
    assert [c["id"] for c in cases] == list(range(1, len(cases) + 1))
    for c in cases:
        assert c["input"], f"{skill} case {c['id']}: no input"
        assert c["expected"], f"{skill} case {c['id']}: no expected behavior"
    defined = E.guard_definitions((folder / "eval_plan.md").read_text(encoding="utf-8"))
    used = {g for c in cases for g in c["guards"]}
    assert used <= set(defined), f"{skill}: guard checks used but not defined: {used - set(defined)}"


def test_parse_sample_cases_inputs_guards_ranges_and_parts():
    cases = E.parse_cases(SAMPLE)
    assert [c["id"] for c in cases] == [1, 2, 3, 4]
    assert cases[0]["input"] == "show me the brief"
    assert cases[0]["expected"] == ["Routes to the default mode", "Uses your fiscal calendar"]
    assert cases[0]["guards"] == ["G1", "G2", "G3"]  # G2-G3 range expanded
    assert cases[1]["input"] == "what about last month" and cases[1]["guards"] == ["G5"]
    assert cases[2]["guards"] == []
    assert cases[3]["parts"] == ["Part A", "Part B"]
    assert "Scoring" not in cases[3]["input"]  # stops at the next non-case heading


def test_guard_definitions_from_eval_plan_table():
    defs = E.guard_definitions(PLAN)
    assert set(defs) == {"G1", "G2", "G3", "G5"}
    assert defs["G5"].startswith("No fabricated data")


# --- plan / record / summary -----------------------------------------------------------

def test_plan_selects_first_three_cases_by_default_and_all_on_request(tmp_path):
    catalog, templates, ws, _ = make_skill(tmp_path)
    out = E.plan(ws, ["s1"], catalog=catalog, templates=templates)
    assert [c["id"] for c in out["s1"]["cases"]] == [1, 2, 3]
    assert all(c["status"] == "pending" for c in out["s1"]["cases"])
    assert set(out["s1"]["guards"]) == {"G1", "G2", "G3", "G5"}
    out = E.plan(ws, ["s1"], all_cases=True, catalog=catalog, templates=templates)
    assert [c["id"] for c in out["s1"]["cases"]] == [1, 2, 3, 4]


def test_skill_without_golden_cases_is_not_tested(tmp_path):
    catalog, templates, ws, _ = make_skill(tmp_path)
    E.plan(ws, ["no_evals"], catalog=catalog, templates=templates)
    assert E.summary(ws, ["no_evals"])["no_evals"]["status"] == "not_tested"


def test_record_validates_case_and_result(tmp_path):
    catalog, templates, ws, _ = make_skill(tmp_path)
    E.plan(ws, ["s1"], catalog=catalog, templates=templates)
    with pytest.raises(SystemExit):
        E.record(ws, "s1", 9, "pass")
    with pytest.raises(SystemExit):
        E.record(ws, "s1", 1, "maybe")
    with pytest.raises(SystemExit):
        E.record(ws, "other_skill", 1, "pass")
    c = E.record(ws, "s1", 1, "fail", ["G3: tenure breakdown reads Location"], "wrong field")
    assert c["status"] == "fail" and c["failed"] == ["G3: tenure breakdown reads Location"]
    assert E.record(ws, "s1", 1, "pass", ["ignored"])["failed"] == []  # pass clears failed checks


def test_summary_statuses_pending_failed_passed(tmp_path):
    catalog, templates, ws, _ = make_skill(tmp_path)
    E.plan(ws, ["s1"], catalog=catalog, templates=templates)
    E.record(ws, "s1", 1, "pass")
    assert E.summary(ws, ["s1"])["s1"]["status"] == "pending"
    E.record(ws, "s1", 2, "fail", ["G5: invented a column"])
    E.record(ws, "s1", 3, "pass")
    s = E.summary(ws, ["s1"])["s1"]
    assert s["status"] == "failed" and (s["passed"], s["run"]) == (2, 3)
    assert "Case 2 failed: G5: invented a column" in s["line"]
    E.record(ws, "s1", 2, "pass")
    s = E.summary(ws, ["s1"])["s1"]
    assert s["status"] == "passed" and s["line"] == "3 of 3 golden cases passed"


def test_not_tested_cases_do_not_fail_the_skill(tmp_path):
    catalog, templates, ws, _ = make_skill(tmp_path)
    E.plan(ws, ["s1"], catalog=catalog, templates=templates)
    E.record(ws, "s1", 1, "pass")
    E.record(ws, "s1", 2, "not_tested", note="no prior-month data")
    E.record(ws, "s1", 3, "pass")
    assert E.summary(ws, ["s1"])["s1"]["status"] == "passed"
    for case in (1, 3):
        E.record(ws, "s1", case, "not_tested")
    assert E.summary(ws, ["s1"])["s1"]["status"] == "not_tested"


def test_changed_draft_makes_results_stale(tmp_path):
    catalog, templates, ws, draft = make_skill(tmp_path)
    E.plan(ws, ["s1"], catalog=catalog, templates=templates)
    for case in (1, 2, 3):
        E.record(ws, "s1", case, "pass")
    assert E.summary(ws, ["s1"])["s1"]["status"] == "passed"
    (draft / "SKILL.md").write_text("skill v2 (a fix was applied)", encoding="utf-8")
    assert E.summary(ws, ["s1"])["s1"]["status"] == "stale"
    E.plan(ws, ["s1"], catalog=catalog, templates=templates)  # re-plan on the new build
    assert E.summary(ws, ["s1"])["s1"]["status"] == "pending"


# --- the hydration verdict ---------------------------------------------------------------

def test_hydration_status_uses_evaluation():
    def ev(status, line="x"):
        return {"status": status, "line": line}
    assert H.hydration_status(READY, CHECKS_PASS, ev("passed", "3 of 3 golden cases passed")) == \
        ("hydrated", "all required data bound and validated; 3 of 3 golden cases passed")
    for status in ("failed", "stale", "pending"):
        assert H.hydration_status(READY, CHECKS_PASS, ev(status, "why"))[0:2] == ("partial", "why")
    assert H.hydration_status(READY, CHECKS_PASS, ev("not_tested"))[0] == "hydrated"
    assert H.hydration_status(READY, CHECKS_PASS, None)[0] == "hydrated"
    blocked = {"status": "BLOCKED", "gaps": []}
    assert H.hydration_status(blocked, CHECKS_PASS, ev("passed"))[0] == "blocked"
    failing = {"gates": [{"gate": "A2_grain", "status": "FAIL"}], "reason": "double counted"}
    assert H.hydration_status(READY, failing, ev("passed")) == ("partial", "double counted")


def test_build_marks_skill_from_evaluation_and_reports_golden_column(tmp_path, monkeypatch):
    """Full v3 build on a stub workspace: passed cases on the same build -> hydrated."""
    skill = "headcount_insights"
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "readiness.json").write_text(json.dumps({"skills": [
        {"domain": "people", "skill": skill, "status": "READY", "readiness": 1.0, "gaps": []}]}))
    (ws / "bindings.json").write_text(json.dumps({"needs": {}}))
    (ws / "validation_report.json").write_text(json.dumps({"skills": {skill: CHECKS_PASS}}))
    draft = ws / "draft"
    H.build(ws, H.SKILL_ROOT / "catalog" / "people.json", draft, [skill], False)
    E.plan(ws, [skill], draft=draft)
    for case in (1, 2, 3):
        E.record(ws, skill, case, "pass")
    out = ws / "customer-people"
    result = H.build(ws, H.SKILL_ROOT / "catalog" / "people.json", out, [skill], False)
    assert result["summary"][0]["status"] == "partial"  # preview not reviewed yet
    import review
    review.mark(ws, skill, "reviewed")
    result = H.build(ws, H.SKILL_ROOT / "catalog" / "people.json", out, [skill], False)
    row = result["summary"][0]
    assert row["status"] == "hydrated" and row["evaluation"] == "3 of 3 golden cases passed"
    assert sorted(p.name for p in (out / "evals" / skill).iterdir()) == ["eval_plan.md", "golden_cases.md"]
    readme = (out / "README.md").read_text()
    assert "| Golden cases |" in readme and "3 of 3 golden cases passed" in readme
    # a failed case on the same build -> partial with the reason
    E.record(ws, skill, 2, "fail", ["G2: employee type filter missing"])
    row = H.build(ws, H.SKILL_ROOT / "catalog" / "people.json", out, [skill], False)["summary"][0]
    assert row["status"] == "partial" and "Case 2 failed: G2: employee type filter missing" in row["reason"]


def test_adding_skills_keeps_earlier_hydrated_skills(tmp_path):
    """Hydrating new skills later keeps the ones built before; unchanged ones are marked unchanged."""
    first, second = "headcount_insights", "attrition_insights"
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "readiness.json").write_text(json.dumps({"skills": [
        {"domain": "people", "skill": s, "status": "READY", "readiness": 1.0, "gaps": []} for s in (first, second)]}))
    (ws / "bindings.json").write_text(json.dumps({"needs": {}}))
    (ws / "validation_report.json").write_text(json.dumps({"skills": {first: CHECKS_PASS, second: CHECKS_PASS}}))
    out = ws / "customer-people"
    H.build(ws, H.SKILL_ROOT / "catalog" / "people.json", out, [first], False)
    rows = {r["skill"]: r for r in H.build(ws, H.SKILL_ROOT / "catalog" / "people.json", out, [second], False)["summary"]}
    assert set(rows) == {first, second} and (out / "skills" / first / "SKILL.md").is_file()
    assert rows[first]["change"] == "unchanged" and rows[second]["change"] == "new"
    assert "| Since last build |" in (out / "README.md").read_text()


def test_preview_signature_ignores_other_skills_settings(tmp_path):
    import preview as P
    ws = tmp_path / "ws"
    (ws / "tuning").mkdir(parents=True)
    (ws / "bindings.json").write_text(json.dumps({"needs": {}}))
    before = P.signature(ws, "headcount_insights")
    (ws / "tuning" / "good_morning_recruiting.json").write_text(json.dumps(
        {"tunables": {"stale_candidate_days": {"value": 45}}}))
    assert P.signature(ws, "headcount_insights") == before
