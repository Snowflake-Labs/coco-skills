import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import discover_topics as T  # noqa: E402
import recommend_skills as R  # noqa: E402

CATALOG = json.loads((R.CATALOG_DIR / "people.json").read_text())
GOALS = json.loads((R.CATALOG_DIR / "goals.json").read_text())


def labels(res):
    return {r["skill"]: r["label"] for r in res["skills"]}


def test_goals_and_roles_point_at_real_skills_and_personas():
    names = {s["name"] for s in CATALOG["skills"]}
    personas = {p for s in CATALOG["skills"] for p in s.get("persona", [])}
    for g in GOALS["goals"]:
        assert set(g["skills"]) <= names, g["id"]
    for r in GOALS["roles"]:
        assert set(r["matches"]) & personas, r["id"]
    covered = {s for g in GOALS["goals"] for s in g["skills"]}
    assert covered == names  # every skill is reachable from at least one goal


def test_plain_language_goals():
    assert labels(R.recommend("We're losing too many good people and want to know why"))["attrition_insights"] == "recommended"
    assert labels(R.recommend("show me current headcount by department"))["headcount_insights"] == "recommended"
    weekly = labels(R.recommend("I need a weekly update for our CEO on hiring progress vs plan"))
    assert weekly["hiring_brief"] == "recommended"
    assert labels(R.recommend("help recruiters write better job postings"))["job_description_builder"] == "recommended"


def test_every_recommendation_has_a_reason_and_nothing_for_unrelated_text():
    res = R.recommend("our org health and managers with too many direct reports", ["hrbp"])
    for r in res["skills"]:
        if r["label"] != "not_relevant":
            assert r["reasons"], r["skill"]
    assert all(r["label"] == "not_relevant" for r in R.recommend("office snacks and parking")["skills"])


def test_role_alone_suggests_and_goal_confirms():
    role_only = labels(R.recommend("", ["ta_lead"]))
    assert role_only["good_morning_recruiting"] == "consider"
    both = labels(R.recommend("", ["ta_lead"], ["hiring_speed"]))
    assert both["good_morning_recruiting"] == "recommended"
    assert both["attrition_insights"] == "not_relevant"


def test_plurals_match():
    assert R.phrase_in("job posting", "Write our job postings")
    assert not R.phrase_in("hc", "which")


def test_usage_falls_back_to_access_history():
    calls = []

    def run(sql):
        calls.append(sql)
        if "query_history" in sql.lower():
            raise RuntimeError("no access to query history")
        if "access_history" in sql.lower():
            return [{"OBJ": "HR_DB.PUBLIC.EMP", "N": 12}, {"OBJ": "OTHER_DB.X.Y", "N": 3}]
        return []

    assert T.query_counts(run, ["HR_DB.PUBLIC.EMP"]) == {"HR_DB.PUBLIC.EMP": 12}
    assert any("access_history" in c.lower() for c in calls)


def test_usage_signal_never_blocks():
    def run(sql):
        raise RuntimeError("nothing allowed")
    assert T.query_counts(run, ["HR_DB.PUBLIC.EMP"]) == {}
