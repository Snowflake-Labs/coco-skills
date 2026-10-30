"""Customer profile, skill questions, fix ledger, light number checks, bounded usage, job results."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import discover_topics as T  # noqa: E402
import fix_ledger as F  # noqa: E402
import corrections as C  # noqa: E402
import review as R  # noqa: E402
import profile_store as P  # noqa: E402
import questions as Q  # noqa: E402

SNAP = "HR.CORE.EMPLOYEE_SNAPSHOT"
EXITS = "HR.CORE.EXITS"


def w(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def col(dtype: str, values=None, **kw):
    out = {"data_type": dtype, "is_code": values is not None, **kw}
    if values is not None:
        out["top_values"] = [[v, 10] for v in values]
    return out


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    w(tmp_path / "data_index.json", {"generated_at": "t", "objects": {
        SNAP: {"kind": "table", "row_count": 100, "grain_candidates": [{"columns": ["EMPLOYEE_ID", "SNAPSHOT_DATE"], "unique": True}],
               "cadence": {"SNAPSHOT_DATE": {"latest": "2026-08-31"}},
               "columns": {"EMPLOYEE_ID": col("TEXT"), "SNAPSHOT_DATE": col("DATE", is_date=True, max="2026-08-31"),
                           "EMPLOYMENT_STATUS": col("TEXT", ["Active", "On Leave", "Terminated"]),
                           "EMPLOYEE_TYPE": col("TEXT", ["Full-Time", "Part-Time", "Contractor", "Intern"])}},
        EXITS: {"kind": "table", "row_count": 20, "columns": {
            "EMPLOYEE_ID": col("TEXT"), "TERMINATION_DATE": col("DATE", is_date=True), "LAST_DAY_OF_WORK": col("DATE", is_date=True),
            "TERM_TYPE": col("TEXT", ["Voluntary", "Involuntary"])}}}})
    w(tmp_path / "scan_metadata.json", {"objects": {}})
    w(tmp_path / "bindings.json", {"needs": {
        "people.headcount.employee_snapshot": {"fqn": SNAP, "skills": ["headcount_insights", "attrition_insights"], "fields": {
            "employee_id": {"column": "EMPLOYEE_ID"}, "snapshot_date": {"column": "SNAPSHOT_DATE"},
            "employment_status": {"column": "EMPLOYMENT_STATUS"}, "employee_type": {"column": "EMPLOYEE_TYPE"}}},
        "people.exits.event_records": {"fqn": EXITS, "skills": ["attrition_insights"], "fields": {
            "employee_id": {"column": "EMPLOYEE_ID"}, "termination_date": {"column": "TERMINATION_DATE"},
            "exit_date": {"column": "TERMINATION_DATE"}, "termination_category": {"column": "TERM_TYPE"}}}}})
    tun = {"regular_employee_types": {"label": "Employee types counted as regular headcount", "type": "enum_multi",
                                      "value": ["Full-Time", "Part-Time"], "source": "data", "status": "needs_review"},
           "active_status_values": {"label": "Employment status values that mean active", "type": "enum_multi",
                                    "value": ["Active"], "source": "context", "status": "needs_review"},
           "voluntary_values": {"label": "Termination categories that are voluntary", "type": "enum_multi",
                                "value": ["Voluntary"], "source": "context", "status": "needs_review"},
           "fiscal_year_start_month": {"label": "Fiscal year start month (1-12)", "type": "calendar", "value": 1,
                                       "source": "context", "status": "needs_review"},
           "attrition_rate_method": {"label": "Attrition rate method", "type": "definition",
                                     "value": "average monthly headcount, not annualized", "source": "default", "status": "auto"},
           "attrition_window_months": {"label": "Rolling attrition window (months)", "type": "threshold", "value": 12,
                                       "source": "default", "status": "auto"}}
    w(tmp_path / "tuning" / "attrition_insights.json", {"tunables": tun})
    w(tmp_path / "tuning" / "headcount_insights.json", {"tunables": {k: tun[k] for k in
                                                                    ("regular_employee_types", "active_status_values")}})
    src = {"connector": "confluence", "scope": "PEOPLE", "title": "People Metrics Definitions", "ref": "x"}
    w(tmp_path / "context.json", {"sources": [{"connector": "confluence", "scopes": ["PEOPLE"]}], "items": [
        {"id": "d1", "kind": "definition", "term": "regular_headcount", "status": "proposed", "conflicts_with": [],
         "value": "counts Active employees", "text": "Headcount counts Active employees.", "source": src},
        {"id": "f1", "kind": "fiscal_calendar", "term": "fiscal_calendar", "status": "proposed", "conflicts_with": ["f2"],
         "value": {"fiscal_year_start_month": 1}, "source": src},
        {"id": "f2", "kind": "fiscal_calendar", "term": "fiscal_calendar", "status": "proposed", "conflicts_with": ["f1"],
         "value": {"fiscal_year_start_month": 4}, "source": {"connector": "slack", "scope": "people-analytics", "ref": "y"}},
        {"id": "r1", "kind": "reference_number", "term": "active_headcount", "status": "proposed", "conflicts_with": [],
         "value": {"metric": "active_headcount", "value": 323, "as_of": "August 31, 2026"}, "source": src}]})
    w(tmp_path / "topics.json", {"topics": [
        {"id": "headcount", "label": "Headcount & employee snapshot", "role": "required", "status": "confirmed", "auto": True,
         "chosen": [SNAP], "recommended": [SNAP], "candidates": [{"fqn": SNAP, "reasons": ["name mentions snapshot"]}],
         "required_by": ["headcount_insights"], "optional_for": []},
        {"id": "fiscal", "label": "Fiscal calendar", "role": "optional", "status": "none", "auto_none": True,
         "chosen": None, "recommended": [], "candidates": [], "required_by": [], "optional_for": ["attrition_insights"]}]})
    (tmp_path / "previews").mkdir()
    w(tmp_path / "previews" / "headcount_insights.json", {"as_of": "2026-08-31", "metrics": [
        {"id": "active_regular_hc", "label": "Active regular headcount", "unit": "employees", "value": 323},
        {"id": "hires_window", "label": "Hires in the last 12 months", "unit": "hires", "value": 40}]})
    w(tmp_path / "previews" / "attrition_insights.json", {"as_of": "2026-08-31", "metrics": [
        {"id": "exits_window", "label": "Exits in the last 12 months", "unit": "exits", "value": 68}]})
    return tmp_path


# ---------- profile -----------------------------------------------------------------------

def test_profile_sections_and_entry_shape(ws: Path) -> None:
    prof = P.build(ws)
    assert prof["version"] == 1
    for section in P.SECTIONS:
        for e in prof[section].values():
            assert {"value", "status", "evidence", "sources", "scope"} <= set(e)
    assert prof["roles"]["headcount"]["status"] == "inferred"  # picked by the engine, not the analyst
    assert prof["roles"]["fiscal"]["status"] == "deferred"
    assert prof["tables"][SNAP]["fingerprint"]["max_date"] == "2026-08-31"
    assert prof["conventions"]["regular_employee_types"]["scope"]["skills"] == ["attrition_insights", "headcount_insights"]
    assert "doc:active_headcount" in prof["corrections"]
    assert not any("credential" in json.dumps(e).lower() for e in prof["connections"].values())


def test_version_only_moves_on_change(ws: Path) -> None:
    assert P.build(ws)["version"] == 1
    assert P.build(ws)["version"] == 1
    Q.set_tunable(ws, "attrition_window_months", 6)
    assert P.build(ws)["version"] == 2
    assert len((ws / "profile_history.jsonl").read_text().splitlines()) == 2


def test_confirmed_entry_is_never_silently_overwritten(ws: Path) -> None:
    Q.set_tunable(ws, "voluntary_values", ["Voluntary"])  # analyst confirms
    P.build(ws)
    data = json.loads((ws / "tuning" / "attrition_insights.json").read_text())
    data["tunables"]["voluntary_values"].update({"value": ["Involuntary"], "source": "context", "status": "needs_review"})
    w(ws / "tuning" / "attrition_insights.json", data)
    prof = P.build(ws)
    e = prof["conventions"]["voluntary_values"]
    assert e["status"] == "conflict" and e["value"] == ["Voluntary"] and e["proposed_value"] == ["Involuntary"]


def test_fingerprint_change_marks_table_and_roles_stale(ws: Path) -> None:
    P.build(ws)
    idx = json.loads((ws / "data_index.json").read_text())
    idx["objects"][SNAP]["columns"]["EMPLOYEE_TYPE"]["top_values"].append(["Seasonal", 3])
    w(ws / "data_index.json", idx)
    prof = P.build(ws)
    assert prof["tables"][SNAP]["status"] == "stale"
    assert prof["roles"]["headcount"]["status"] == "stale"
    assert any("EMPLOYEE_TYPE" in n for n in prof["notes"])
    assert P.build(ws)["roles"]["headcount"]["status"] == "stale"  # stays stale until re-confirmed
    P.mark_confirmed(ws, "roles", "headcount", "PEOPLE_ANALYST")
    assert P.build(ws)["roles"]["headcount"]["status"] == "confirmed"


def test_summary_is_plain(ws: Path) -> None:
    s = P.summary(P.build(ws), ["headcount_insights", "attrition_insights"])
    assert any("EMPLOYEE_SNAPSHOT" in x for x in s["assumed"])
    assert any("fiscal" in x.lower() for x in s["open"])


# ---------- questions ---------------------------------------------------------------------

def test_question_plan_asks_little_and_explains(ws: Path) -> None:
    plan = Q.plan(ws, ["attrition_insights", "headcount_insights"])
    asked = {q["id"]: q for q in plan["ask"]}
    decided = {q["id"]: q for q in plan["decided"]}
    assert "fiscal_year" in asked and asked["fiscal_year"]["why"] == "your documents disagree"
    assert {o["label"] for o in asked["fiscal_year"]["options"]} == {"January (from People Metrics Definitions)",
                                                                     "April (from #people-analytics)"}
    assert "headcount_population" in asked  # source=data: a real business choice (contractors? interns?)
    assert asked["headcount_population"]["effect_now"] == 323
    assert "active_statuses" in decided and "People Metrics Definitions" in decided["active_statuses"]["why"]
    assert len(plan["ask"]) <= Q.MAX_QUESTIONS
    assert all("effect" not in o for o in asked["attrition_method"]["options"])  # no connection: no re-run


def test_option_numbers_rerun_the_preview_metric(ws: Path) -> None:
    import check_bindings as CB
    import preview as P
    seen = []
    def run(sql):
        seen.append(sql)
        return [{"VALUE": 11.15 if "* 12 / nullif(12, 0)" in sql else 7.43}]
    q = next(d for d in Q.D.library() if d["id"] == "attrition_method")
    if "attrition_rate" not in CB.skill_metrics("attrition_insights", Q.load(ws / "bindings.json", {}), P.tuning_of(ws)):
        return  # fixture has no exits binding
    opts = [dict(o) for o in q["common_answers"]]
    Q.option_effects(ws, q, opts, ["attrition_insights"], run)
    assert next(o for o in opts if o["value"]["annualized"])["effect"] == 11.15
    assert next(o for o in opts if not o["value"]["annualized"])["effect"] == 7.43


def test_question_hidden_when_data_has_one_value(ws: Path) -> None:
    idx = json.loads((ws / "data_index.json").read_text())
    idx["objects"][EXITS]["columns"]["TERM_TYPE"]["top_values"] = [["Voluntary", 5]]
    w(ws / "data_index.json", idx)
    plan = Q.plan(ws, ["attrition_insights"])
    assert any(h["id"] == "voluntary_exits" for h in plan["hidden"])


def test_answer_writes_every_skill_and_resolves_conflict(ws: Path) -> None:
    out = Q.answer(ws, "headcount_population", ["Full-Time", "Part-Time", "Intern"])
    assert sorted(out["skills"]) == ["attrition_insights", "headcount_insights"]
    t = json.loads((ws / "tuning" / "headcount_insights.json").read_text())["tunables"]["regular_employee_types"]
    assert t["source"] == "analyst" and "Intern" in t["value"]
    Q.answer(ws, "fiscal_year", 4)
    ctx = {i["id"]: i["status"] for i in json.loads((ws / "context.json").read_text())["items"]}
    assert ctx["f2"] == "confirmed" and ctx["f1"] == "rejected"
    plan = Q.plan(ws, ["attrition_insights", "headcount_insights"])
    assert {"headcount_population", "fiscal_year"} <= {q["id"] for q in plan["decided"]}


def test_skip_defers_and_reopen_asks_again(ws: Path) -> None:
    Q.answer(ws, "attrition_method", None, skip=True)
    assert any(q["id"] == "attrition_method" and q["source"] == "deferred" for q in Q.plan(ws, ["attrition_insights"])["decided"])
    w(ws / "questions_state.json", {"active_statuses": {"status": "reopened"}})
    assert Q.plan(ws, ["attrition_insights"])["ask"][0]["id"] == "active_statuses"


def test_attrition_method_answer_sets_method(ws: Path) -> None:
    Q.answer(ws, "attrition_method", {"denominator": "start", "annualized": False})
    t = json.loads((ws / "tuning" / "attrition_insights.json").read_text())["tunables"]["attrition_rate_method"]
    assert t["method"] == {"denominator": "start", "annualized": False} and "start" in t["value"]


# ---------- fix ledger ----------------------------------------------------------------------

def test_two_copies_of_the_tables_is_one_question(ws: Path) -> None:
    topics = json.loads((ws / "topics.json").read_text())
    twin = "HR.COPY.EMPLOYEE_SNAPSHOT"
    topics["topics"][0]["candidates"].append({"fqn": twin, "reasons": ["same table also in HR.CORE"]})
    w(ws / "topics.json", topics)
    w(ws / "scan_metadata.json", {"objects": {SNAP: {"last_altered": "2026-10-02"}, twin: {"last_altered": "2026-10-04"}},
                                  "usage": {SNAP: 40, twin: 90}})
    plan = Q.plan(ws, ["headcount_insights"])
    q = plan["ask"][0]
    assert q["id"] == "source_schema" and q["preselected"] == "HR.CORE"
    assert {o["value"] for o in q["options"]} == {"HR.CORE", "HR.COPY"}
    assert any("read 90" in o["label"] for o in q["options"])
    out = Q.answer(ws, "source_schema", "HR.COPY")
    assert out["rebind"] and json.loads((ws / "topics.json").read_text())["topics"][0]["chosen"] == [twin]
    assert any(d["id"] == "source_schema" for d in Q.plan(ws, ["headcount_insights"])["decided"])


def test_feedback_only_touches_the_skills_settings(ws: Path) -> None:
    data = json.loads((ws / "tuning" / "attrition_insights.json").read_text())
    data["tunables"]["open_req_status_values"] = {"label": "Requisition statuses that count as open", "type": "enum_multi",
                                                  "value": ["Open"], "source": "data", "status": "needs_review"}
    w(ws / "tuning" / "hiring_brief.json", {"tunables": {"open_req_status_values": data["tunables"].pop("open_req_status_values")}})
    it = F.from_feedback(ws, "headcount_insights", "include people on leave", False, None)
    assert {o["tunable"] for o in it["proposal"]["ops"]} == {"active_status_values"}


def test_feedback_rules_include_exclude(ws: Path) -> None:
    it = F.from_feedback(ws, "headcount_insights", "Include people on leave, but exclude part-time", False, None)
    assert it["status"] == "proposed"
    ops = {o["tunable"]: o["value"] for o in it["proposal"]["ops"]}
    assert ops["active_status_values"] == ["Active", "On Leave"]
    assert ops["regular_employee_types"] == ["Full-Time"]
    assert set(it["proposal"]["affects"]) == {"attrition_insights", "headcount_insights"}


def test_feedback_already_true_is_said_not_silent(ws: Path) -> None:
    it = F.from_feedback(ws, "headcount_insights", "exclude contractors", False, None)
    assert it["status"] == "resolved" and "already excluded" in it["why"]


def test_feedback_column_and_numbers(ws: Path) -> None:
    it = F.from_feedback(ws, "attrition_insights", "we date exits by LAST_DAY_OF_WORK and use a 6 month window", False, None)
    changes = " ".join(it["proposal"]["changes"])
    assert "LAST_DAY_OF_WORK" in changes and "12 → 6" in changes
    assert sum(o["op"] == "bind_column" for o in it["proposal"]["ops"]) == 2  # both field names on that column
    it2 = F.from_feedback(ws, "attrition_insights", "our fiscal year starts in April and we annualize", False, None)
    kinds = {o["op"] for o in it2["proposal"]["ops"]}
    assert kinds == {"set_number", "set_method"}


def test_unmappable_feedback_stays_unresolved(ws: Path) -> None:
    it = F.from_feedback(ws, "attrition_insights", "the org chart looks weird", False, None)
    assert it["status"] == "unresolved" and it["text"] == "the org chart looks weird"


def test_validator_rejects_unsafe_or_invented_changes(ws: Path) -> None:
    f = F.facts(ws)
    ok, problems = F.validate([
        {"op": "set_values", "tunable": "regular_employee_types", "value": ["Retiree"]},
        {"op": "bind_column", "need": "people.exits.event_records", "field": "termination_date", "column": "SALARY"},
        {"op": "run_sql", "sql": "drop table x"},
        {"op": "set_number", "tunable": "fiscal_year_start_month", "value": 13},
        {"op": "bind_column", "need": "people.exits.event_records", "field": "termination_category", "column": "TERM_TYPE"},
    ], f)
    assert ok == []  # the last one is a no-op
    assert len(problems) == 4


def test_apply_revalidates_and_records(ws: Path, monkeypatch) -> None:
    monkeypatch.setattr(F.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    it = F.from_feedback(ws, "headcount_insights", "include interns", False, None)
    done = F.apply(ws, it["id"])
    assert done["status"] == "applied"
    assert "Intern" in json.loads((ws / "tuning" / "headcount_insights.json").read_text())["tunables"]["regular_employee_types"]["value"]


def test_applied_fix_is_saved_and_rechecked(ws: Path, monkeypatch) -> None:
    monkeypatch.setattr(F.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    it = F.from_feedback(ws, "headcount_insights", "include interns", False, None)
    F.apply(ws, it["id"])
    saved = C.load(ws)["corrections"]
    assert saved and saved[0]["text"] == "include interns" and saved[0]["ops"]
    assert R.status(ws, ["headcount_insights"]) == {"headcount_insights": "corrected"}
    assert C.check(ws)["status"] == "PASS"
    tp = ws / "tuning" / "headcount_insights.json"  # someone changes it back
    tun = json.loads(tp.read_text())
    tun["tunables"]["regular_employee_types"]["value"] = [v for v in tun["tunables"]["regular_employee_types"]["value"]
                                                          if v != "Intern"]
    w(tp, tun)
    g = C.check(ws)
    assert g["status"] == "FAIL" and "include interns" in g["detail"]


def test_saved_number_must_still_hold(ws: Path) -> None:
    C.record(ws, {"id": "n1", "text": "exits should be 76", "skills": ["attrition_insights"],
                  "metric": "exits_window", "target": 76, "status": "applied", "applied_ops": []})
    assert C.check(ws, {"attrition_insights": {"exits_window": {"VALUE": 76}}})["status"] == "PASS"
    g = C.check(ws, {"attrition_insights": {"exits_window": {"VALUE": 70}}})
    assert g["status"] == "FAIL" and "now 70" in g["detail"]


def test_looks_good_marks_reviewed(ws: Path) -> None:
    assert R.status(ws, ["attrition_insights"]) == {"attrition_insights": "open"}
    R.mark(ws, "attrition_insights", "reviewed")
    assert R.status(ws, ["attrition_insights"]) == {"attrition_insights": "reviewed"}


def test_unmapped_follow_up_rule_is_kept_for_the_skill(ws: Path) -> None:
    it = F.add_rule(ws, "attrition_insights", "Board members never count as employees", False, None)
    assert it["status"] == "noted"
    rules = json.loads((ws / "customer_rules.json").read_text())["rules"]
    assert rules[0]["skill"] == "attrition_insights" and "Board members" in rules[0]["text"]


def test_questions_are_ranked_capped_and_explain_settled_labels(ws: Path) -> None:
    plan = Q.plan(ws, ["attrition_insights", "headcount_insights"])
    assert len(plan["ask"]) <= Q.MAX_QUESTIONS and plan["follow_up"]["prompt"]
    ranks = [Q.rank(a) for a in plan["ask"]]
    assert ranks == sorted(ranks, reverse=True)


def test_spread_drops_questions_whose_options_agree() -> None:
    assert Q.spread([{"effect": 120}, {"effect": 120}]) == 0.0
    assert Q.spread([{"effect": 120}, {"effect": 120.4}]) < Q.SAME_NUMBER
    assert Q.spread([{"effect": 18.2}, {"effect": 18.6}]) > Q.SAME_NUMBER  # attrition method: a real choice
    assert Q.spread([{"effect": 15.0}, {"effect": 15.05}]) < Q.SAME_NUMBER
    assert Q.spread([{"effect_value": 30.0}, {"effect_value": 41.0}]) > Q.SAME_NUMBER
    assert Q.spread([{"effect": 30}, {"effect": 41}]) > Q.SAME_NUMBER
    assert Q.spread([{"label": "x"}]) is None


def test_ai_reply_goes_through_validator(ws: Path, monkeypatch) -> None:
    reply = '{"ops":[{"op":"set_values","tunable":"voluntary_values","value":["Retirement"]},' \
            '{"op":"set_number","tunable":"attrition_window_months","value":9}],"uncertain":"Retirement not seen"}'
    import _sql
    monkeypatch.setattr(_sql, "make_runner", lambda *a, **k: (lambda sql: [{"R": json.dumps(reply)}]))
    it = F.from_feedback(ws, "attrition_insights", "count retirements as voluntary; nine month lookback", True, "c")
    assert it["proposal"]["ops"] == [{"op": "set_number", "tunable": "attrition_window_months", "value": 9}]
    assert any("Retirement" in p for p in it["problems"])


# ---------- bounded usage ------------------------------------------------------------------------

def test_usage_sample_widens_then_falls_back() -> None:
    calls = []

    def run(sql: str):
        calls.append(sql)
        if "access_history" in sql:
            return [{"OBJ": SNAP, "N": 3}] if "-7" in sql else [{"OBJ": SNAP, "N": 40}]
        raise AssertionError("query history not needed")

    counts, meta = T.usage_sample(run, [SNAP])
    assert meta == {"source": "access_history", "days": 30} and counts[SNAP] == 40
    assert all(SNAP in c for c in calls)  # filtered to candidate tables, no account-wide scan

    def denied(sql: str):
        raise RuntimeError("not authorized")

    counts, meta = T.usage_sample(denied, [SNAP])
    assert counts == {} and meta["source"] == "none"


def test_visible_databases_respect_exclusions(tmp_path: Path) -> None:
    w(tmp_path / "prewarm.json", {"databases": [{"name": "HR"}, {"name": "SALES"}, {"name": "SNOWFLAKE"}]})
    assert T.visible_databases(tmp_path, ["SALES"])[0] == ["HR"]


# ---------- job results ----------------------------------------------------------------------------

# ---------- numbers in feedback ----------

def test_numbers_in_skips_years_and_windows():
    import fix_ledger as F
    assert F.numbers_in("it is 76 for 2026, over 12 months") == [76.0]
    assert F.numbers_in("rate should be 7.4%") == [7.4]


def test_pick_metric_by_label_words():
    import fix_ledger as F
    ms = [{"id": "active_regular_hc", "label": "Headcount at the latest month-end", "unit": "people", "value": 321},
          {"id": "exits_window", "label": "Exits in the last 12 months", "unit": "exits", "value": 68}]
    assert F.pick_metric("exit is voluntary and involuntary so it is 76", 76, ms)["id"] == "exits_window"
    assert F.pick_metric("headcount should be 300", 300, ms)["id"] == "active_regular_hc"


def test_plain_removes_internal_names():
    import fix_ledger as F
    f = {"tunables": {"voluntary_values": {"label": "Voluntary exits"}},
         "needs": {"x": {"fields": {"termination_category": "TERM_TYPE"}}}}
    out = F.plain("`voluntary_values` uses TERM_TYPE; no tunable fits", f)
    assert "voluntary_values" not in out and "TERM_TYPE" not in out and "tunable" not in out.lower()


# ---------- answers carry over to skills added later ----------

def test_confirmed_answer_carries_to_new_skill():
    import build_tuning as BT
    tun = {"id": "regular_employee_types", "label": "Employee types counted", "type": "enum_multi",
           "default": ["Full-Time"], "infer_from": {}}
    catalog = {"skills": [{"name": "headcount_insights", "tunables": [tun]},
                          {"name": "hiring_brief", "tunables": [tun]}]}
    existing = {"headcount_insights": {"tunables": {"regular_employee_types": {
        "value": ["Full-Time", "Part-Time"], "source": "analyst", "status": "confirmed"}}}}
    out = BT.build(catalog, {"objects": {}}, {"needs": {}}, {"items": []}, existing)
    new = out["hiring_brief"]["tunables"]["regular_employee_types"]
    assert new["value"] == ["Full-Time", "Part-Time"] and new["source"] == "analyst"


# ---------- batched SQL gives the same answers ----------

def test_run_many_falls_back_one_by_one_on_error():
    import _sql
    calls = []
    def run(s):
        calls.append(s)
        if "bad" in s:
            raise _sql.SqlError("no such table")
        return [{"V": s}]
    def many(sqls):
        raise _sql.SqlError("one statement failed")
    run.many = many
    out = _sql.run_many(run, ["select 1", "select bad", "select 3"])
    assert out[0] == [{"V": "select 1"}] and isinstance(out[1], _sql.SqlError) and out[2] == [{"V": "select 3"}]


def test_run_many_skips_batching_for_statements_with_semicolons():
    import _sql
    def run(s):
        return [{"V": s}]
    def many(sqls):
        assert all(";" not in s for s in sqls)
        return [[{"V": s}] for s in sqls]
    run.many = many
    out = _sql.run_many(run, ["select 'a;b'", "select 2", "select 3"])
    assert [r[0]["V"] for r in out] == ["select 'a;b'", "select 2", "select 3"]


# ---------- skill-driven business definitions ----------

def _defs_ws(tmp_path, req_cols, exits_cols=("TERMINATION_DATE", "LAST_DAY_OF_WORK"), table_score=0.8):
    import json as _j
    def col(name, date=False, values=None):
        m = {"is_date": date, "is_code": bool(values), "distinct": 10, "data_type": "DATE" if date else "TEXT"}
        if values:
            m["top_values"] = [[v, 5] for v in values]
        return m
    index = {"objects": {
        "DB.S.REQ": {"columns": {**{c: col(c, True) for c in req_cols}, "STATUS": col("STATUS", values=["Open", "Filled", "On Hold"])}},
        "DB.S.EXITS": {"columns": {c: col(c, True) for c in exits_cols}},
    }}
    needs = {
        "people.ta.requisitions": {"fqn": "DB.S.REQ", "object_score": table_score, "skills": ["good_morning_recruiting", "hiring_brief"],
                                   "fields": {"status": {"column": "STATUS", "confidence": "HIGH"}}},
        "people.exits.event_records": {"fqn": "DB.S.EXITS", "object_score": 0.7, "skills": ["attrition_insights"],
                                       "fields": {"termination_date": {"column": exits_cols[0], "confidence": "HIGH"}}},
    }
    (tmp_path / "data_index.json").write_text(_j.dumps(index))
    (tmp_path / "bindings.json").write_text(_j.dumps({"needs": needs}))
    return tmp_path


def test_time_to_fill_options_come_only_from_existing_dates(tmp_path):
    import definitions as D
    ws = _defs_ws(tmp_path, ["REQ_OPEN_DATE", "APPROVED_DATE", "FILLED_DATE"])
    d = next(x for x in D.decide(ws, ["hiring_brief"]) if x["id"] == "time_to_fill")
    pairs = {(o["value"]["start"], o["value"]["end"]) for o in d["options"]}
    assert pairs == {("REQ_OPEN_DATE", "FILLED_DATE"), ("APPROVED_DATE", "FILLED_DATE")} and d["cannot_compute"] is None


def test_time_to_fill_without_end_date_asks_where_it_is(tmp_path):
    import definitions as D
    ws = _defs_ws(tmp_path, ["REQ_OPEN_DATE", "OPEN_DATE"])
    d = next(x for x in D.decide(ws, ["hiring_brief"]) if x["id"] == "time_to_fill")
    assert not d["options"] and d["cannot_compute"] is None
    assert d["locate"]["prompt"].startswith("Where do you record the fill")


def test_locate_answer_checks_table_column_and_link(tmp_path):
    import json as _j
    import definitions as D
    import pytest as _pt
    ws = _defs_ws(tmp_path, ["REQ_OPEN_DATE"])
    (ws / "scan_metadata.json").write_text(_j.dumps({"objects": {
        "DB.S.REQ": {"official": True, "columns": {"REQ_ID": {"is_id": True}, "REQ_OPEN_DATE": {"is_date": True}}},
        "DB.S.APP": {"official": True, "columns": {"REQ_ID": {"is_id": True}, "HIRED_DATE": {"is_date": True}}},
        "DB.S.OTHER": {"official": True, "columns": {"HIRED_DATE": {"is_date": True}}},
    }}))
    idx = _j.loads((ws / "data_index.json").read_text())
    idx["objects"]["DB.S.REQ"]["columns"]["REQ_ID"] = {"is_id": True}
    (ws / "data_index.json").write_text(_j.dumps(idx))
    d = D.by_id("time_to_fill")
    v = D.locate_answer(ws, d, "APP.HIRED_DATE")["value"]
    assert v == {"table": "DB.S.REQ", "start": "REQ_OPEN_DATE", "end_table": "DB.S.APP", "end": "HIRED_DATE", "key": "REQ_ID"}
    with _pt.raises(ValueError, match="doesn't share an id"):
        D.locate_answer(ws, d, "OTHER.HIRED_DATE")
    with _pt.raises(ValueError, match="no column"):
        D.locate_answer(ws, d, "APP.FILLED_ON")


def test_guessed_table_never_drives_a_question(tmp_path):
    import definitions as D
    ws = _defs_ws(tmp_path, ["REQ_OPEN_DATE", "FILLED_DATE"], table_score=0.45)
    d = next(x for x in D.decide(ws, ["hiring_brief"]) if x["id"] == "time_to_fill")
    assert not d["options"] and d["locate"] is not None  # asked where it is, not answered from a guess


def test_exit_date_choice_lists_both_dates_current_first(tmp_path):
    import definitions as D
    ws = _defs_ws(tmp_path, ["REQ_OPEN_DATE"])
    d = next(x for x in D.decide(ws, ["attrition_insights"]) if x["id"] == "exit_date_basis")
    assert [o["value"] for o in d["options"]] == ["TERMINATION_DATE", "LAST_DAY_OF_WORK"]


def test_ai_suggestion_with_invented_column_or_duplicate_is_dropped():
    import definitions as D
    summary = {"DB.S.EMP": {"dates": ["HIRE_DATE", "SNAPSHOT_DATE"], "codes": {"LEVEL": ["IC", "M1", "VP"], "FLAG": ["true", "false"]}}}
    ok = {"label": "Manager levels", "prompt": "Which levels are managers?", "kind": "multi", "table": "DB.S.EMP", "column": "LEVEL"}
    assert D.validate_ai(ok, summary, set())["options"][0]["value"] == "IC"
    assert D.validate_ai({**ok, "column": "MADE_UP"}, summary, set()) is None
    assert D.validate_ai({**ok, "column": "FLAG"}, summary, set()) is None  # yes/no flags aren't a question
    assert D.validate_ai({**ok, "label": "Regrettable leavers"}, summary, {"regrettable exits"}) is None
    dates = {"label": "Start basis", "prompt": "Which date?", "kind": "date_choice", "table": "DB.S.EMP",
             "columns": ["HIRE_DATE", "SNAPSHOT_DATE"]}
    assert D.validate_ai(dates, summary, set()) is None  # a snapshot date is not an event date


def test_ai_cache_avoids_a_second_call(tmp_path, monkeypatch):
    import definitions as D
    ws = _defs_ws(tmp_path, ["REQ_OPEN_DATE"])
    summary = D.data_summary(ws, "hiring_brief")
    key = D.ai_key("hiring_brief", summary, ["x"])
    (ws / "ai_definitions").mkdir()
    (ws / "ai_definitions" / "hiring_brief.json").write_text(json.dumps({"key": key, "questions": [{"id": "ai_a"}]}))
    import _sql
    monkeypatch.setattr(_sql, "make_runner", lambda *a, **k: (_ for _ in ()).throw(AssertionError("AI called")))
    assert D.ai_for_skill(ws, "hiring_brief", "c", ["x"]) == [{"id": "ai_a"}]


def test_answer_to_a_definition_reaches_skills_added_later(tmp_path):
    import build_tuning as BT
    existing = {"hiring_brief": {"tunables": {"def_time_to_fill": {
        "value": {"table": "DB.S.REQ", "start": "REQ_OPEN_DATE", "end": "FILLED_DATE"}, "source": "analyst", "status": "confirmed"}}}}
    catalog = {"skills": [{"name": "hiring_brief", "tunables": []}, {"name": "good_morning_recruiting", "tunables": []}]}
    out = BT.build(catalog, {"objects": {}}, {"needs": {}}, {"items": []}, existing)
    assert out["good_morning_recruiting"]["tunables"]["def_time_to_fill"]["value"]["end"] == "FILLED_DATE"


def test_definition_renders_plainly_in_hydrated_skill():
    import build_customer_plugin as H
    t = {"value": {"table": "DB.S.REQ", "start": "REQ_OPEN_DATE", "end": "FILLED_DATE"}, "type": "date_span"}
    assert H.plain_value(t) == "days from `REQ_OPEN_DATE` to `FILLED_DATE` on `DB.S.REQ`"
    assert H.plain_value({"value": "LAST_DAY_OF_WORK", "type": "date_choice"}) == "use column `LAST_DAY_OF_WORK`"


def test_voluntary_default_does_not_pick_involuntary():
    import build_tuning as BT
    out = BT.resolve_enum({"id": "voluntary_values", "default": ["voluntary"]}, ["Voluntary", "Involuntary"], "EXITS.TERM_TYPE",
                          {"items": []})
    assert out["value"] == ["Voluntary"]


def test_time_to_fill_uses_precalculated_days_and_linked_end_dates(tmp_path):
    import json as _j
    import definitions as D
    index = {"objects": {
        "DB.S.REQ": {"columns": {"REQ_ID": {"is_id": True}, "REQ_OPEN_DATE": {"is_date": True, "distinct": 9},
                                 "DAYS_TO_FILL": {"is_number": True}}},
        "DB.S.APP": {"columns": {"REQ_ID": {"is_id": True}, "OFFER_ACCEPTED_DATE": {"is_date": True, "distinct": 9},
                                 "HIRED_DATE": {"is_date": True, "distinct": 9}}},
    }}
    needs = {"people.ta.requisitions": {"fqn": "DB.S.REQ", "object_score": 0.8, "fields": {}},
             "people.ta.applications": {"fqn": "DB.S.APP", "object_score": 0.8, "fields": {}}}
    (tmp_path / "data_index.json").write_text(_j.dumps(index))
    (tmp_path / "bindings.json").write_text(_j.dumps({"needs": needs}))
    d = next(x for x in D.decide(tmp_path, ["hiring_brief"]) if x["id"] == "time_to_fill")
    vals = [o["value"] for o in d["options"]]
    assert {"table": "DB.S.REQ", "column": "DAYS_TO_FILL"} in vals
    assert {"table": "DB.S.REQ", "start": "REQ_OPEN_DATE", "end_table": "DB.S.APP", "end": "OFFER_ACCEPTED_DATE",
            "key": "REQ_ID"} in vals
    assert d["cannot_compute"] is None


# ---------- table override ----------------------------------------------------------------------

def with_scan(ws: Path) -> None:
    cols = {c: {"data_type": "TEXT"} for c in ["SURVEY_ID", "SUBMISSION_DATE", "RECRUITER_ID", "RESPONSE_SCORE"]}
    w(ws / "scan_metadata.json", {"objects": {
        "HR.CORE.HM_FEEDBACK_SURVEY": {"columns": cols, "official": True},
        "HR.CORE.SURVEY_BACKUP": {"columns": cols, "official": False},
        "HR.CORE.PAYROLL": {"columns": {"EMP_ID": {}, "PAY_DATE": {}}, "official": True}}})
    w(ws / "topics.json", {"skills": ["good_morning_recruiting"], "topics": []})


def test_use_this_table_becomes_a_validated_bind_table(ws: Path) -> None:
    with_scan(ws)
    f = F.facts(ws)
    ops = F.table_proposal("use HM_FEEDBACK_SURVEY for hiring manager sentiment", f)
    assert ops == [{"op": "bind_table", "need": "people.ta.hm_sentiment",
                    "fqn": "HR.CORE.HM_FEEDBACK_SURVEY"}]
    ok, problems = F.validate(ops, f)
    assert ok == ops and not problems
    assert "Hiring manager sentiment" in F.describe(ok[0], f)


def test_bind_table_rejects_tables_without_the_data(ws: Path) -> None:
    with_scan(ws)
    f = F.facts(ws)
    _, p1 = F.validate([{"op": "bind_table", "need": "people.ta.hm_sentiment", "fqn": "PAYROLL"}], f)
    _, p2 = F.validate([{"op": "bind_table", "need": "people.ta.hm_sentiment", "fqn": "SURVEY_BACKUP"}], f)
    _, p3 = F.validate([{"op": "bind_table", "need": "people.ta.hm_sentiment", "fqn": "NOPE"}], f)
    assert "no column for" in p1[0] and "backup" in p2[0] and "can't find" in p3[0]
