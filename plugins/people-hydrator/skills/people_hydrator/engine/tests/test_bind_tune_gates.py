import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_tuning  # noqa: E402
import check_bindings  # noqa: E402
import match_fields as M  # noqa: E402
import metrics_sql  # noqa: E402


def col(dtype, distinct, values=None, **kw):
    meta = {"data_type": dtype, "distinct": distinct, "null_rate": 0.0,
            "is_id": kw.get("is_id", False), "is_date": dtype == "DATE", "is_number": dtype == "NUMBER",
            "is_text": dtype == "TEXT", "is_boolean": dtype == "BOOLEAN", "pii_risk": kw.get("pii", False),
            "is_code": values is not None}
    if values is not None:
        meta["top_values"] = [[v, 10] for v in values]
    return meta


INDEX = {
    "objects": {
        "DB.S.EMPLOYEE_SNAPSHOT": {"official": True, "row_count": 1000, "comment": None,
            "grain_candidates": [{"columns": ["EMPLOYEE_ID", "SNAPSHOT_DATE"], "unique": True}],
            "cadence": {"SNAPSHOT_DATE": {"month_end_share": 0.95}},
            "columns": {
                "SNAPSHOT_DATE": col("DATE", 10), "EMPLOYEE_ID": col("TEXT", 100, is_id=True),
                "EMPLOYEE_NAME": col("TEXT", 100, pii=True),
                "EMPLOYMENT_STATUS": col("TEXT", 3, ["Active", "Terminated", "On Leave"]),
                "EMPLOYEE_TYPE": col("TEXT", 3, ["Full-Time", "Part-Time", "Contractor"]),
                "DEPARTMENT": col("TEXT", 3, ["Trading", "Operations", "Technology"]),
                "HIRE_DATE": col("DATE", 90),
                "RECORD_TYPE": col("TEXT", 2, ["SNAPSHOT", "TRANSACTION"]),
                "TRANSACTION_TYPE": col("TEXT", 3, [None, "HIRE", "TERMINATION"]),
                "COST_CENTER_ID": col("TEXT", 20, is_id=True)}},
        "DB.S.EXITS": {"official": True, "row_count": 50, "comment": None,
            "grain_candidates": [{"columns": ["EMPLOYEE_ID"], "unique": True}], "cadence": {},
            "columns": {"EMPLOYEE_ID": col("TEXT", 50, is_id=True), "TERMINATION_DATE": col("DATE", 40),
                        "TERM_TYPE": col("TEXT", 2, ["Voluntary", "Involuntary"]),
                        "REGRETTABLE_FLAG": col("BOOLEAN", 2, ["true", "false"]),
                        "DEPARTMENT": col("TEXT", 3, ["Trading", "Operations", "Technology"])}},
    },
    "joins": [], "semantic_views": [], "usage": {},
}
CATALOG = {"skills": [{"name": "s", "tunables": [
    {"id": "regular_employee_types", "label": "x", "type": "enum_multi", "default": ["regular"],
     "infer_from": {"field": "employee_type"}},
    {"id": "voluntary_values", "label": "x", "type": "enum_multi", "default": ["voluntary"],
     "infer_from": {"field": "termination_category"}}],
    "data_needs": [
    {"key": "people.headcount.employee_snapshot", "required": True, "concepts": ["employee snapshot"],
     "grain": ["employee", "snapshot_date"], "fields": {
        "snapshot_date": {"required": True, "aliases": ["snapshot_date"], "semantic_type": "date_or_period"},
        "employee_id": {"required": True, "aliases": ["employee_id"], "semantic_type": "identifier"},
        "employee_type": {"required": True, "aliases": ["employee_type", "worker_type"], "semantic_type": "category"},
        "org_name": {"required": False, "aliases": ["org_name", "org_group"], "semantic_type": "category",
                     "value_hints": ["@context:org_units"]}}},
    {"key": "people.hires.event_records", "required": True, "concepts": ["hire event"], "grain": ["hire_event"],
     "row_filter_hint": {"concepts": ["hire", "new hire"], "applies_when": "shared_table"}, "fields": {
        "employee_id": {"required": True, "aliases": ["employee_id"], "semantic_type": "identifier"},
        "hire_date": {"required": True, "aliases": ["hire_date"], "semantic_type": "date_or_period"}}},
    {"key": "people.exits.event_records", "required": True, "concepts": ["exit event"], "grain": ["exit_event"],
     "fields": {
        "employee_id": {"required": True, "aliases": ["employee_id"], "semantic_type": "identifier"},
        "termination_date": {"required": True, "aliases": ["termination_date"], "semantic_type": "date_or_period"},
        "termination_category": {"required": False, "aliases": ["termination_category", "termination_type"], "semantic_type": "category"},
        "voluntary_flag": {"required": False, "aliases": ["voluntary_flag"], "semantic_type": "boolean"}}},
    {"key": "people.pto.leave_balances", "required": False, "concepts": ["pto balance"], "grain": ["employee"],
     "fields": {
        "employee_id": {"required": True, "aliases": ["employee_id"], "semantic_type": "identifier"},
        "used_days": {"required": False, "aliases": ["used_days", "pto_taken"], "semantic_type": "number"}}},
    {"key": "people.users.org_scoping", "required": False, "concepts": ["user scoping"], "grain": ["user"],
     "fields": {
        "user_id": {"required": True, "aliases": ["user_id", "employee_id"], "semantic_type": "identifier"},
        "position_in_hierarchy": {"required": True, "aliases": ["position_id", "employee_id"], "semantic_type": "identifier"}}},
]}]}
CONTEXT = {"items": [
    {"id": "o1", "kind": "org_units", "term": "org_hierarchy", "value": ["Trading", "Operations", "Technology"],
     "status": "confirmed", "confidence": "HIGH", "source": {"connector": "confluence"}, "text": ""},
    {"id": "d1", "kind": "definition", "term": "regular_headcount", "status": "confirmed", "confidence": "HIGH",
     "value": "counts active Full-Time and Part-Time employees. Contractors are excluded.",
     "source": {"connector": "confluence"}, "text": ""},
    {"id": "d2", "kind": "definition", "term": "voluntary_exit", "status": "confirmed", "confidence": "HIGH",
     "value": "exits with termination type Voluntary. Involuntary exits are reported separately.",
     "source": {"connector": "confluence"}, "text": ""},
    {"id": "f1", "kind": "fiscal_calendar", "term": "fiscal_calendar", "value": {"fiscal_year_start_month": 2},
     "status": "confirmed", "confidence": "HIGH", "source": {"connector": "confluence"}, "text": ""},
]}


def graph():
    import build_context_graph
    return build_context_graph.build(INDEX, CONTEXT)


def bindings(learned=None):
    return M.run_match(INDEX, graph(), CATALOG, learned or {}, context=CONTEXT)["needs"]


def test_snapshot_need_gets_snapshot_row_filter():
    b = bindings()["people.headcount.employee_snapshot"]
    assert b["fqn"] == "DB.S.EMPLOYEE_SNAPSHOT"
    assert b["row_filter"] == {**b["row_filter"], "column": "RECORD_TYPE", "values": ["SNAPSHOT"]}


def test_org_name_bound_via_context_org_units():
    f = bindings()["people.headcount.employee_snapshot"]["fields"]["org_name"]
    assert f["column"] == "DEPARTMENT"
    assert any("org units" in e for e in f["evidence"])


def test_hires_bound_to_shared_table_with_event_filter():
    b = bindings()["people.hires.event_records"]
    assert b["fqn"] == "DB.S.EMPLOYEE_SNAPSHOT"
    assert b["row_filter"]["column"] == "TRANSACTION_TYPE" and b["row_filter"]["values"] == ["HIRE"]


def test_boolean_flag_derived_from_code_value():
    f = bindings()["people.exits.event_records"]["fields"]["voluntary_flag"]
    assert f["column"] == "TERM_TYPE" and f["expression"] == "\"TERM_TYPE\" = 'Voluntary'"


def test_absent_data_is_unresolved_not_force_bound():
    b = bindings()
    assert b["people.pto.leave_balances"]["status"] == "unresolved"
    assert b["people.users.org_scoping"]["status"] == "unresolved"   # ids collapse onto EMPLOYEE_ID


def test_fiscal_calendar_derived_from_context_when_no_table():
    cal = M.derive_fiscal_calendar(CONTEXT)
    assert cal["derived"]["fiscal_year_start_month"] == 2 and cal["status"] == "auto"


def test_learned_correction_and_drop_need_respected():
    b = bindings({"fields": {"people.headcount.employee_snapshot.org_name": "EMPLOYEE_TYPE"},
                  "drop_needs": {"people.exits.event_records": "validator"}})
    assert b["people.headcount.employee_snapshot"]["fields"]["org_name"]["status"] == "confirmed"
    assert b["people.headcount.employee_snapshot"]["fields"]["org_name"]["column"] == "EMPLOYEE_TYPE"
    assert b["people.exits.event_records"]["status"] == "unresolved"


def test_tuning_uses_context_definition_with_exclusions():
    result = build_tuning.build(CATALOG, INDEX, {"needs": bindings()}, CONTEXT, {})
    t = result["s"]["tunables"]
    assert t["regular_employee_types"]["value"] == ["Full-Time", "Part-Time"]
    assert t["voluntary_values"]["value"] == ["Voluntary"]
    assert t["regular_employee_types"]["source"] == "context"


def test_sanity_flags_voluntary_equal_total():
    issues = check_bindings.sanity({"attrition_rate": {"VALUE": "20"}, "voluntary_attrition_rate": {"VALUE": "20"}})
    assert any("voluntary attrition equals total" in i for i in issues)


def test_reconcile_gate_tolerance():
    ctx = {"items": [{"kind": "reference_number", "status": "proposed", "source": {"connector": "confluence"},
                      "value": {"metric": "active_headcount", "value": 323}}]}
    ok = check_bindings.reconcile_gate({"s": {"active_regular_hc": {"VALUE": 325}}}, ctx)
    bad = check_bindings.reconcile_gate({"s": {"active_regular_hc": {"VALUE": 289}}}, ctx)
    assert ok["status"] == "PASS" and bad["status"] == "FAIL"


def test_metric_sql_applies_row_filter_and_definitions():
    need = metrics_sql.Need("k", bindings()["people.headcount.employee_snapshot"])
    sql = metrics_sql.active_regular_hc(need, {"s": {"tunables": {"regular_employee_types": {"value": ["Full-Time"]}}}})
    assert "\"RECORD_TYPE\" in ('SNAPSHOT')" in sql and "('Full-Time')" in sql
