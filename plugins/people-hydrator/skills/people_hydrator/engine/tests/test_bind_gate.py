"""Binding gate (eval_gate.py), its Tune questions, and how it decides hydration status."""

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import build_customer_plugin as H  # noqa: E402
import eval_gate as G  # noqa: E402
import questions as Q  # noqa: E402

NEED = "people.headcount.employee_snapshot"


def field(column, confidence="HIGH"):
    return {"column": column, "confidence": confidence, "status": "auto"}


def needs(**over):
    base = {NEED: {"fqn": "HR.PUBLIC.EMPLOYEE_SNAPSHOT", "status": "auto",
                   "alternatives": [{"fqn": "HR.PUBLIC.WORKERS", "score": 0.4}],
                   "fields": {"snapshot_date": field("SNAPSHOT_DATE"),
                              "employment_status": field("STATUS", "LOW")}},
            "people.exits.event_records": {"fqn": None, "status": "unresolved", "fields": {}}}
    base.update(over)
    return base


RULES = [
    {"id": "G1", "title": "Snapshot connected", "check": "min_needs", "needs": [NEED], "min": 1},
    {"id": "G2", "title": "Snapshot fields", "check": "need", "need": NEED,
     "fields": ["snapshot_date", "employee_type|employment_status"], "min_confidence": "LOW"},
    {"id": "P1", "title": "Exits connected", "check": "any_need", "needs": ["people.exits.event_records"]},
    {"id": "S1", "title": "Nice to have", "check": "need", "need": "people.calendar.fiscal", "fields": []},
]


def test_need_rule_field_alternatives_and_confidence():
    r = G.check_rule(RULES[1], needs())
    assert r["passed"]
    strict = dict(RULES[1], min_confidence="MEDIUM")
    r = G.check_rule(strict, needs())
    assert not r["passed"] and r["weak_fields"] == ["employee_type|employment_status"]


def test_grades():
    results = [G.check_rule(r, needs()) for r in RULES]
    assert G.grade(results) == "DEGRADED"  # G pass, P1 fails, S ignored
    assert G.grade([dict(results[0], passed=False)] + results[1:]) == "INCOMPATIBLE"
    assert G.grade([r for r in results if r["level"] != "P"]) == "EQUIVALENT"


def test_rules_block_parsed_from_eval_plan(tmp_path, monkeypatch):
    plan = tmp_path / "people" / "evals" / "headcount_insights" / "eval_plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# Plan\n\n```binding-gate\n" + json.dumps({"rules": RULES}) + "\n```\n")
    monkeypatch.setattr(G, "TEMPLATES", tmp_path)
    assert [r["id"] for r in G.load_rules("headcount_insights")] == ["G1", "G2", "P1", "S1"]


def test_every_shipped_eval_plan_has_valid_rules():
    cat = json.loads(G.CATALOG.read_text())
    for s in cat["skills"]:
        rules = G.load_rules(s["name"])
        assert rules, f"{s['name']}: no binding-gate block"
        keys = {n["key"] for n in s["data_needs"]}
        fields = {n["key"]: set(n["fields"]) for n in s["data_needs"]}
        assert any(r["id"].startswith("G") for r in rules), s["name"]
        for r in rules:
            for k in [r.get("need")] + r.get("needs", []):
                if k:
                    assert k in keys, f"{s['name']} {r['id']}: unknown need {k}"
            for f in r.get("fields", []):
                for alt in f.split("|"):
                    assert alt in fields[r["need"]], f"{s['name']} {r['id']}: unknown field {alt}"


def test_gate_questions_and_answer(tmp_path, monkeypatch):
    (tmp_path / "bindings.json").write_text(json.dumps({"needs": needs()}))
    G_res = {"grade": "DEGRADED", "rules": [G.check_rule(r, needs()) for r in RULES]}
    (tmp_path / "binding_gate.json").write_text(json.dumps({"skills": {"headcount_insights": G_res}}))
    qs = Q.gate_questions(tmp_path, ["headcount_insights"], {})
    assert [q["id"] for q in qs] == ["gate:people.exits.event_records"]
    assert qs[0]["options"][-1]["value"] == Q.NOT_HAVE
    (tmp_path / "questions.json").write_text(json.dumps({"ask": qs}))
    import fix_ledger
    seen = {}

    def fake_from_gate(ws, skill, need, field, value, connection=None):
        seen.update(need=need, field=field, value=value)
        return {"id": "fx1", "status": "proposed", "proposal": {"changes": ["exits table: HR.PUBLIC.EXITS"]}}
    monkeypatch.setattr(fix_ledger, "from_gate", fake_from_gate)
    out = Q.answer(tmp_path, qs[0]["id"], "HR.PUBLIC.EXITS")
    assert seen == {"need": "people.exits.event_records", "field": None, "value": "HR.PUBLIC.EXITS"}
    assert out["fix_status"] == "proposed" and "fix_ledger.py apply" in out["next"] and "eval_gate.py" in out["then"]
    assert Q.gate_questions(tmp_path, ["headcount_insights"],
                            json.loads((tmp_path / "questions_state.json").read_text())) == []


def test_from_gate_builds_exact_validated_change(tmp_path, monkeypatch):
    import fix_ledger as F
    f = {"needs": {NEED: {"table": "HR.PUBLIC.EMPLOYEE_SNAPSHOT", "skills": ["headcount_insights"],
                          "fields": {"employment_status": "STATUS"},
                          "columns": {"STATUS": "TEXT", "ACTIVE_FLAG": "BOOLEAN"}}},
         "tables": {}, "catalog_needs": {}, "tunables": {}}
    monkeypatch.setattr(F, "facts", lambda ws: f)
    monkeypatch.setattr(F, "effects", lambda *a, **k: {})
    monkeypatch.setattr(F.subprocess, "run", lambda *a, **k: None)
    item = F.from_gate(tmp_path, "headcount_insights", NEED, "employment_status", "ACTIVE_FLAG")
    assert item["status"] == "proposed"
    assert item["proposal"]["ops"] == [{"op": "bind_column", "need": NEED, "field": "employment_status",
                                        "column": "ACTIVE_FLAG"}]
    item = F.from_gate(tmp_path, "headcount_insights", NEED, "employment_status", "STATUS")
    assert item["status"] == "confirmed"
    assert json.loads((tmp_path / "learned.json").read_text())["fields"][f"{NEED}.employment_status"] == "STATUS"
    assert F.from_gate(tmp_path, "headcount_insights", NEED, "employment_status", "NOPE")["status"] == "unresolved"


def test_gate_questions_merge_rules_and_skills(tmp_path):
    (tmp_path / "bindings.json").write_text(json.dumps({"needs": needs()}))
    strict = [dict(RULES[1], id="G2", min_confidence="HIGH"), dict(RULES[1], id="P9", min_confidence="HIGH")]
    res = {"grade": "INCOMPATIBLE", "rules": [G.check_rule(r, needs()) for r in strict]}
    (tmp_path / "binding_gate.json").write_text(json.dumps({"skills": {"a_skill": res, "b_skill": res}}))
    qs = Q.gate_questions(tmp_path, ["a_skill", "b_skill"], {})
    assert len(qs) == 1  # same column, two rules, two skills -> one question
    q = qs[0]
    assert q["affects"] == ["a_skill", "b_skill"] and q["impact"] == 3
    assert "employee type or employment status" in q["prompt"] and "|" not in q["prompt"]
    assert q["preselected"] == "STATUS" and q["options"][0]["value"] == "STATUS"


def test_question_cap_counts_every_affected_skill():
    assert Q.MAX_QUESTIONS == 5


def test_hydration_status_with_gate():
    ready = {"status": "READY", "gaps": []}
    checks = {"gates": [{"gate": "G1", "status": "PASS"}]}
    bad = {"grade": "INCOMPATIBLE", "rules": [{"id": "G2", "level": "G", "title": "Snapshot fields",
                                                "passed": False, "reason": "missing: snapshot_date"}]}
    status, reason = H.hydration_status(ready, checks, None, bad)
    assert status == "partial" and reason.startswith("warning:") and "snapshot_date" in reason
    meh = {"grade": "DEGRADED", "rules": [{"id": "P1", "level": "P", "title": "Exits connected",
                                           "passed": False, "reason": "none"}]}
    status, reason = H.hydration_status(ready, checks, None, meh)
    assert status == "hydrated" and "works with gaps: Exits connected" in reason


def test_drift_section_lists_bound_columns():
    entry = {"name": "headcount_insights",
             "data_needs": [{"key": NEED, "required": True, "fields": ["snapshot_date", "employment_status"]}]}
    text = H.drift_section(entry, {"needs": needs()})
    assert "HR.information_schema.columns" in text
    assert "('PUBLIC', 'EMPLOYEE_SNAPSHOT', 'SNAPSHOT_DATE')" in text
    assert "re-hydrated" in text
    assert H.drift_section(entry, {"needs": {}}) == ""


def test_attrition_default_is_start_end_average_and_overridable():
    import fix_ledger as F
    import metrics_sql as M
    assert M.rate_method({}) == {"denominator": "start_end", "annualized": False}
    sql = M.rate_sql("10", "select 1 as hc, current_date as month_end", M.rate_method({}), 12)
    assert "order by month_end asc limit 1) + (" in sql and ") / 2" in sql and "* 12" not in sql
    f = {"tunables": {"attrition_rate_method": {"method": None}}, "needs": {}, "catalog_needs": {}, "tables": {}}
    ops = F.rule_proposal("we divide by headcount at the start and end of the period", f)
    assert {"op": "set_method", "value": {"denominator": "start_end", "annualized": False}} not in ops  # already default
    ops = F.rule_proposal("we use average monthly headcount", f)
    assert {"op": "set_method", "value": {"denominator": "average", "annualized": False}} in ops


def test_attrition_exits_match_headcount_population_and_period_start():
    import metrics_sql as M
    snap = M.Need("people.attrition.snapshot_monthly", {"fqn": "HR.PUBLIC.SNAP", "fields": {
        "snapshot_date": {"column": "SNAPSHOT_DATE"}, "employee_id": {"column": "EMPLOYEE_ID"},
        "employee_type": {"column": "EMPLOYEE_TYPE"}, "employment_status": {"column": "STATUS"}}})
    exits = M.Need("people.exits.event_records", {"fqn": "HR.PUBLIC.EXITS", "fields": {
        "employee_id": {"column": "EMPLOYEE_ID"}, "termination_date": {"column": "TERMINATION_DATE"}}})
    tuning = {"x": {"tunables": {"regular_employee_types": {"value": ["Full-Time"]}}}}
    sql = M.attrition_metrics(exits, snap, tuning)["attrition_rate"]
    # type on the person's latest snapshot row on or before the exit, not "ever had that type"
    assert ('(select max_by("EMPLOYEE_TYPE", "SNAPSHOT_DATE") from "HR"."PUBLIC"."SNAP" latest_snap where '
            '"EMPLOYEE_ID" = "HR"."PUBLIC"."EXITS"."EMPLOYEE_ID"') in sql
    assert '"SNAPSHOT_DATE" <= "HR"."PUBLIC"."EXITS"."TERMINATION_DATE"' in sql
    assert "\"TERMINATION_DATE\") in ('Full-Time')" in sql
    assert "\"SNAPSHOT_DATE\" >= last_day(dateadd('month', -12," in sql  # start headcount at the period start


def test_exit_counts_on_day_after_termination_by_default():
    import metrics_sql as M
    ex = M.Need("e", {"fqn": "HR.PUBLIC.EXITS", "fields": {"termination_date": {"column": "TERMINATION_DATE"}}})
    assert M.exit_day(ex, {}) == "dateadd('day', 1, \"TERMINATION_DATE\")"
    assert M.exit_day(ex, {"x": {"tunables": {"exit_date_offset_days": {"value": 0}}}}) == '"TERMINATION_DATE"'


def test_optional_org_summary_never_reads_an_employee_level_table():
    import match_fields as MF
    summary = {"key": "people.headcount.exec_summary", "grain": ["org_group", "snapshot_date"], "required_in": []}
    assert MF.summary_need(summary)
    assert not MF.summary_need({**summary, "required_in": ["headcount_insights"]})  # required: always matched
    assert not MF.summary_need({"key": "people.ats.offers", "grain": ["offer", "application"], "required_in": []})
    assert MF.person_level({"columns": {"EMPLOYEE_ID": {}, "DEPARTMENT": {}}})
    assert not MF.person_level({"columns": {"ORG_GROUP": {}, "SNAPSHOT_DATE": {}, "HEADCOUNT": {}}})
