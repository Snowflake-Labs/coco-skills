import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import discover_topics as T  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "testdata" / "scan_metadata_synthetic.json"
CATALOG = json.loads((T.CATALOG_DIR / "people.json").read_text())
HS = "people.headcount.employee_snapshot"
SURVEY = "HR_SYNTHETIC_DATA.PUBLIC.HM_FEEDBACK_SURVEY"


def scanned(tmp_path, skills):
    shutil.copy(FIXTURE, tmp_path / "scan_metadata.json")
    data = T.scan(tmp_path, None, [], skills, use_usage=False)
    return {t["id"]: t for t in data["topics"]}


def with_survey(tmp_path):
    data = json.loads(FIXTURE.read_text())
    cols = {c: {"data_type": d, "is_id": c.endswith("_ID"), "is_date": d == "DATE", "is_number": d == "NUMBER",
                "is_text": d == "TEXT", "is_boolean": False}
            for c, d in [("SURVEY_ID", "TEXT"), ("SUBMITTED_ON", "DATE"), ("HIRING_MANAGER_ID", "TEXT"),
                         ("RECRUITER_ID", "TEXT"), ("QUESTION_CATEGORY", "TEXT"), ("QUESTION_TEXT", "TEXT"),
                         ("RESPONSE_SCORE", "NUMBER")]}
    data["objects"][SURVEY] = {"kind": "table", "row_count": 500, "official": True, "columns": cols}
    (tmp_path / "scan_metadata.json").write_text(json.dumps(data))
    return {t["id"]: t for t in T.scan(tmp_path, None, [], ["good_morning_recruiting"], use_usage=False)["topics"]}


def test_one_unit_per_need_with_roles_from_the_skills(tmp_path):
    t = scanned(tmp_path, ["headcount_insights"])
    keys = {n["key"] for s in CATALOG["skills"] for n in s["data_needs"] if not n["key"].endswith(".semantic_view")}
    assert set(t) == keys
    assert t[HS]["role"] == "required" and t[HS]["required_by"] == ["headcount_insights"]
    assert t["people.ats.scorecards"]["role"] == "other"


def test_recommendations_on_synthetic_hr_schemas(tmp_path):
    t = scanned(tmp_path, [])
    short = {k: [f.split(".", 1)[1] for f in v["recommended"]] for k, v in t.items()}
    assert short[HS] == ["PUBLIC.EMPLOYEE_SNAPSHOT"]
    assert short["people.exits.event_records"] == ["PUBLIC.EXITS"]
    assert short["people.calendar.fiscal"] == ["PUBLIC.FISCAL_CALENDAR"]
    assert short["people.ta.offers"] == ["PUBLIC.RECRUITING_APPLICATION"]  # found through offer_* columns
    assert short["people.ta.applications"] == short["people.ta.candidates_in_stage"]  # one table, two needs
    for empty in ["people.ats.scorecards", "people.ta.hm_sentiment", "people.compensation.ranges"]:
        assert short[empty] == [], empty  # no false recommendation when the data isn't there
    assert len({f.rsplit(".", 1)[1] for f in t[HS]["recommended"]}) == len(t[HS]["recommended"])


def test_a_table_is_found_by_what_it_holds_not_its_name(tmp_path):
    t = with_survey(tmp_path)
    assert t["people.ta.hm_sentiment"]["recommended"] == [SURVEY]


def test_candidates_explain_themselves(tmp_path):
    top = scanned(tmp_path, [])[HS]["candidates"][0]
    assert any(r.startswith("name mentions") for r in top["reasons"])
    assert any("same table also in" in r for r in top["reasons"])


def test_confirm_survives_rescan_with_more_skills(tmp_path):
    scanned(tmp_path, ["headcount_insights"])
    T.confirm(tmp_path, HS, ["HR_SYNTHETIC_DATA.ORIGINAL_SHAPE.EMPLOYEE_SNAPSHOT"])
    t = scanned(tmp_path, ["headcount_insights", "good_morning_recruiting"])
    assert t[HS]["chosen"] == ["HR_SYNTHETIC_DATA.ORIGINAL_SHAPE.EMPLOYEE_SNAPSHOT"]
    assert t["people.ta.applications"]["role"] == "required" and t["people.ta.applications"]["status"] == "proposed"


def test_old_area_topics_carry_analyst_choices_to_each_need(tmp_path):
    (tmp_path / "topics.json").write_text(json.dumps({"topics": [
        {"id": "headcount", "needs": [HS], "status": "confirmed", "chosen": ["X.Y.SNAP"], "auto": False},
        {"id": "exits", "needs": ["people.exits.event_records"], "status": "confirmed", "chosen": ["X.Y.E"],
         "auto": True}]}))
    t = scanned(tmp_path, ["headcount_insights", "attrition_insights"])
    assert t[HS]["chosen"] == ["X.Y.SNAP"] and t[HS]["status"] == "confirmed"
    assert t["people.exits.event_records"]["status"] == "proposed"  # automatic picks are re-ranked per need


def test_accept_recommended_only_touches_open_relevant_needs(tmp_path):
    scanned(tmp_path, ["headcount_insights"])
    T.confirm(tmp_path, HS, ["X.Y.Z"])
    done = T.accept_recommended(tmp_path)
    assert HS not in done and "people.ta.applications" not in done
    assert "X.Y.Z" in T.confirmed_tables(tmp_path)


def test_system_databases_are_skipped(tmp_path):
    data = T.scan(tmp_path, lambda sql: [], ["SNOWFLAKE_SAMPLE_DATA"], [], use_usage=False)
    assert data["objects_scanned"] == 0 and "skipped" in data["errors"][0]["error"]


def test_mapping_review_lists_found_and_missing_per_skill(tmp_path):
    import mapping_review as MR
    scanned(tmp_path, ["good_morning_recruiting"])
    (tmp_path / "bindings.json").write_text(json.dumps({"needs": {
        "people.ta.applications": {"fqn": "HR_SYNTHETIC_DATA.PUBLIC.RECRUITING_APPLICATION", "status": "auto",
                                   "fields": {"application_id": {"column": "APPLICATION_ID", "status": "auto"}}},
        "people.ta.hm_sentiment": {"fqn": None, "status": "unresolved",
                                   "suggestion": {"fqn": "X.Y.SURVEY", "missing": ["sentiment_score"]}}}}))
    r = MR.review(tmp_path)["skills"]["good_morning_recruiting"]
    assert "Hiring manager sentiment" in r["needs_input"]
    row = next(n for n in r["needs"] if n["need"] == "people.ta.hm_sentiment")
    assert row["suggestion"]["table"] == "X.Y.SURVEY" and "no sentiment_score" in row["why"][0]
    assert r["needs"][0]["status"] == "needs_input"  # what needs input comes first
