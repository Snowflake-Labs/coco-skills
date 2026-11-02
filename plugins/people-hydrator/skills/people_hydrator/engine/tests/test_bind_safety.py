"""Binding safety: no binding on usage / semantic-view membership alone, word overlap must hit
the field's own name, and fields that are constant on the rows a need reads are dropped."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_customer_plugin as H  # noqa: E402
import filtered_fields as FF  # noqa: E402
import match_fields as M  # noqa: E402


def spec(*aliases, sem="boolean"):
    return {"aliases": list(aliases), "semantic_type": sem, "value_hints": []}


def test_partial_name_match_needs_a_word_of_the_field_itself():
    regrettable = spec("regrettable_flag", "regrettable_exit_flag", "is_regrettable")
    assert M.name_score("regrettable_flag", regrettable, "EXIT_FLAG") == 0.0
    assert M.name_score("regrettable_flag", regrettable, "REGRETTABLE_FLAG") >= 0.85
    assert M.name_score("regrettable_flag", regrettable, "REGRETTABLE_IND") >= 0.5
    reason = spec("termination_reason_category", "termination_reason", "exit_reason", sem="category")
    assert M.name_score("termination_reason_category", reason, "TERM_REASON") >= 0.5


def _obj(columns):
    return {"row_count": 100, "columns": {c: {"distinct": 5, "is_code": True, "data_type": t, "top_values": []}
                                          for c, t in columns.items()}}


def test_usage_and_semantic_view_alone_never_qualify_a_column():
    ev = M.Evidence({"nodes": [], "edges": []})
    ev.sv_members = {M.norm_value("LOCATION")}
    tenure = spec("tenure_group", "tenure_bucket", "tenure_band", sem="category")
    obj = _obj({"LOCATION": "TEXT"})
    ranked = M.score_field("tenure_group", tenure, {"grain": [], "fields": {"tenure_group": tenure}},
                           "DB.S.T", obj, ev, M.DEFAULT_WEIGHTS, {"name", "type", "structure", "semview"})
    assert ranked and not ranked[0]["semantic"]  # ranked, but not a qualifying match


def fake_run(rows):
    seen = []

    def run(sql):
        seen.append(sql)
        return [rows]
    run.seen = seen
    return run


CATALOG = {"skills": [{"name": "attrition_insights", "data_needs": [{
    "key": "people.attrition.snapshot_monthly", "required": True, "fields": {
        "exit_flag": {"required": True, "semantic_type": "boolean", "aliases": ["exit_flag"]},
        "regrettable_flag": {"required": False, "semantic_type": "boolean", "aliases": ["regrettable_flag"]},
        "management_level": {"required": False, "semantic_type": "category", "aliases": ["management_level"]},
        "employee_id": {"required": True, "semantic_type": "identifier", "aliases": ["employee_id"]}}}]}]}


def bindings(row_filter=True):
    return {"needs": {"people.attrition.snapshot_monthly": {
        "fqn": "DB.S.SNAP", "status": "auto",
        "row_filter": {"column": "RECORD_TYPE", "op": "in", "values": ["SNAPSHOT"]} if row_filter else None,
        "fields": {"exit_flag": {"column": "EXIT_FLAG", "status": "auto"},
                   "regrettable_flag": {"column": "REG_FLAG", "status": "auto"},
                   "management_level": {"column": "MGMT_LEVEL", "status": "auto"},
                   "employee_id": {"column": "EMPLOYEE_ID", "status": "auto"}}}}}


def test_constant_fields_on_filtered_rows_are_dropped_or_flagged():
    b = bindings()
    # exit flag and regrettable flag constant on SNAPSHOT rows; level varies; ids are never checked
    run = fake_run({"D0": 1, "V0": False, "D1": 1, "V1": False, "D2": 6, "V2": "M1"})
    notes = FF.check(b, CATALOG, run)
    fields = b["needs"]["people.attrition.snapshot_monthly"]["fields"]
    assert fields["regrettable_flag"]["column"] is None          # optional -> unbound
    assert fields["exit_flag"]["column"] == "EXIT_FLAG"           # required -> kept, flagged
    assert fields["exit_flag"]["constant"] and fields["exit_flag"]["confidence"] == "LOW"
    assert fields["management_level"]["column"] == "MGMT_LEVEL"
    assert "\"RECORD_TYPE\" in ('SNAPSHOT')" in run.seen[0] and "EMPLOYEE_ID" not in run.seen[0]
    assert [d["field"] for d in notes[0]["dropped"]] == ["regrettable_flag"]
    assert [d["field"] for d in notes[0]["constant"]] == ["exit_flag"]


def test_no_row_filter_means_no_check_and_no_changes():
    b = bindings(row_filter=False)
    run = fake_run({})
    assert FF.check(b, CATALOG, run) == [] and run.seen == []
    assert b["needs"]["people.attrition.snapshot_monthly"]["fields"]["regrettable_flag"]["column"] == "REG_FLAG"


def test_layout_notes_reach_the_hydrated_skill():
    b = bindings()
    FF.check(b, CATALOG, fake_run({"D0": 1, "V0": False, "D1": 1, "V1": False, "D2": 6, "V2": "M1"}))
    b["layout_notes"] = FF.check(bindings(), CATALOG, fake_run({"D0": 1, "V0": False, "D1": 1, "V1": False,
                                                                 "D2": 6, "V2": "M1"}))
    entry = {"data_needs": [{"key": "people.attrition.snapshot_monthly", "fields": {}}]}
    text = H.config_section(entry, b, None, {"items": []})
    assert "### Data layout (checked on your data)" in text
    assert "regrettable_flag" in text and "exit_flag" in text and "SNAPSHOT" in text
