import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_customer_plugin as H  # noqa: E402
import prewarm as P  # noqa: E402
import profile_data  # noqa: E402
from _sql import SqlError  # noqa: E402


def fake(rows_by_prefix):
    def run(sql):
        for prefix, rows in rows_by_prefix.items():
            if sql.lower().startswith(prefix):
                if isinstance(rows, Exception):
                    raise rows
                return rows
        return []
    return run


def test_hydration_status():
    assert H.hydration_status({"status": "BLOCKED", "gaps": ["missing required source for people.ta.hm_sentiment"]},
                              None)[0] == "blocked"
    assert "Hiring manager sentiment" in H.hydration_status({"status": "BLOCKED", "gaps": ["missing required source for people.ta.hm_sentiment"]}, None)[1]
    assert H.hydration_status({"status": "READY", "gaps": []}, {"gates": [{"gate": "G1", "status": "PASS"}]}) == \
        ("hydrated", "all required data bound and validated")
    assert H.hydration_status({"status": "READY", "gaps": []}, {"gates": [{"gate": "A2_grain", "status": "FAIL"}]})[0] == "partial"
    assert H.hydration_status({"status": "READY", "gaps": []}, None)[1] == "not validated yet"


def test_prewarm_hides_system_and_personal_databases():
    run = fake({"show databases": [{"NAME": "HR_DB", "KIND": "STANDARD"}, {"NAME": "SNOWFLAKE", "KIND": "APPLICATION"},
                                   {"NAME": "SNOWFLAKE_SAMPLE_DATA"}, {"NAME": "USER$ME"}, {"NAME": "SOME_APP", "KIND": "APPLICATION"}]})
    assert [d["name"] for d in P.list_databases(run)] == ["HR_DB"]


def test_profile_only_chosen_tables_and_reuse_prior(monkeypatch):
    listed = {"HR_DB.PUBLIC.EMP": {"kind": "table", "row_count": 5, "official": True, "columns": {}},
              "HR_DB.PUBLIC.EXITS": {"kind": "table", "row_count": 2, "official": True, "columns": {}},
              "HR_DB.PUBLIC.OTHER": {"kind": "table", "row_count": 9, "official": True, "columns": {}}}
    monkeypatch.setattr(profile_data, "list_objects", lambda run, db, schemas: {k: dict(v) for k, v in listed.items()})
    monkeypatch.setattr(profile_data, "list_semantic_views", lambda run, db: [])
    monkeypatch.setattr(profile_data, "infer_joins", lambda run, objs: [])
    profiled = []
    for fn in ("profile_columns", "profile_code_values", "detect_cadence"):
        monkeypatch.setattr(profile_data, fn, lambda run, fqn, obj: None)
    monkeypatch.setattr(profile_data, "detect_grain", lambda run, fqn, obj: (profiled.append(fqn), obj.update(grain_candidates=[])))
    prior = {"objects": {"HR_DB.PUBLIC.EMP": {**listed["HR_DB.PUBLIC.EMP"], "grain_candidates": [], "cached": True}},
             "scope": {"tables": ["HR_DB.PUBLIC.EMP"]}}
    idx = profile_data.build_index(None, "c", [], [], ["HR_DB.PUBLIC.EMP", "HR_DB.PUBLIC.EXITS"], prior)
    assert set(idx["objects"]) == {"HR_DB.PUBLIC.EMP", "HR_DB.PUBLIC.EXITS"}  # OTHER never profiled
    assert profiled == ["HR_DB.PUBLIC.EXITS"]  # EMP reused from the earlier run
    assert idx["objects"]["HR_DB.PUBLIC.EMP"].get("cached")


def test_dropped_tables_are_cached_but_not_matchable(monkeypatch):
    listed = {"HR_DB.PUBLIC.EMP": {"kind": "table", "row_count": 5, "official": True, "columns": {}}}
    monkeypatch.setattr(profile_data, "list_objects", lambda run, db, schemas: {k: dict(v) for k, v in listed.items()})
    monkeypatch.setattr(profile_data, "list_semantic_views", lambda run, db: [])
    monkeypatch.setattr(profile_data, "infer_joins", lambda run, objs: [])
    prior = {"objects": {"HR_DB.PUBLIC.EMP": {**listed["HR_DB.PUBLIC.EMP"], "grain_candidates": []},
                         "HR_DB.OLD.EMP": {**listed["HR_DB.PUBLIC.EMP"], "grain_candidates": []}}}
    idx = profile_data.build_index(None, "c", [], [], ["HR_DB.PUBLIC.EMP"], prior)
    assert set(idx["objects"]) == {"HR_DB.PUBLIC.EMP"}
    assert set(idx["profile_cache"]) == {"HR_DB.OLD.EMP"}
    again = profile_data.build_index(None, "c", [], [], ["HR_DB.PUBLIC.EMP"], idx)
    assert set(again["profile_cache"]) == {"HR_DB.OLD.EMP"}  # cache survives further runs


def test_build_copies_golden_cases_and_eval_plan_only(tmp_path):
    src = tmp_path / "templates" / "people" / "evals" / "s1"
    src.mkdir(parents=True)
    for name in ("golden_cases.md", "eval_plan.md", "eval_results_old.md"):
        (src / name).write_text(name, encoding="utf-8")
    entry = {"name": "s1", "source_plugin": "people", "eval_dir": "evals/s1"}
    golden = H.copy_evals(entry, tmp_path / "templates", tmp_path / "out")
    dest = tmp_path / "out" / "evals" / "s1"
    assert golden == str(dest / "golden_cases.md")
    assert sorted(p.name for p in dest.iterdir()) == ["eval_plan.md", "golden_cases.md"]
    assert H.copy_evals({**entry, "eval_dir": None}, tmp_path / "templates", tmp_path / "out2") is None
