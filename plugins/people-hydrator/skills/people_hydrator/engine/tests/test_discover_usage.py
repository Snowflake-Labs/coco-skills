import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import mine_usage  # noqa: E402
import profile_data  # noqa: E402

KNOWN = {"DB.S.EMP": "DB.S.EMP", "DB.S.EXITS": "DB.S.EXITS"}


def test_sanitize_strips_literals_and_comments():
    text = "select * from emp where name = 'Jane Doe' and id = 42 -- secret\n"
    clean = mine_usage.sanitize(text)
    assert "Jane" not in clean and "42" not in clean and "secret" not in clean


def test_parse_statement_extracts_structure_only():
    sql = (
        "select e.department, count(x.employee_id) from db.s.emp e "
        "join db.s.exits x on e.employee_id = x.employee_id "
        "where e.snapshot_date = '2026-01-31' group by e.department"
    )
    parsed = mine_usage.parse_statement(sql, KNOWN)
    assert parsed["tables"] == ["DB.S.EMP", "DB.S.EXITS"]
    assert parsed["joins"] == ["DB.S.EMP.EMPLOYEE_ID=DB.S.EXITS.EMPLOYEE_ID"]
    assert "DB.S.EMP.SNAPSHOT_DATE" in parsed["filters"]
    assert "DB.S.EMP.DEPARTMENT" in parsed["group_by"]
    assert "COUNT(DB.S.EXITS.EMPLOYEE_ID)" in parsed["aggregates"]


def test_reserved_words_not_captured_as_columns():
    sql = "select count(case when a = 1 then 1 end) from db.s.emp group by department union all select 1"
    parsed = mine_usage.parse_statement(sql, KNOWN)
    assert not any(c.endswith(".UNION") for c in parsed["group_by"])
    assert not any("CASE" in a for a in parsed["aggregates"])


def test_mine_never_returns_query_text():
    out = mine_usage.mine(["select * from db.s.emp where name = 'Jane Doe'"], list(KNOWN))
    assert "Jane" not in repr(out)


def test_classify_column_flags_pii_and_ids():
    assert profile_data.classify_column("EMPLOYEE_NAME", "TEXT")["pii_risk"]
    assert profile_data.classify_column("EMPLOYEE_ID", "TEXT")["is_id"]
    assert not profile_data.classify_column("DEPARTMENT", "TEXT")["pii_risk"]


def test_non_official_objects_filtered():
    assert not profile_data.is_official("HR_DEV.PUBLIC.EMP")
    assert not profile_data.is_official("HR.PUBLIC.EMP_BACKUP")
    assert profile_data.is_official("HR.PUBLIC.EMPLOYEE_SNAPSHOT")


def test_code_values_skip_pii_and_id_columns():
    calls = []

    def fake_run(sql):
        calls.append(sql)
        if "count(*) as \"__ROWS\"" in sql and "approx_count_distinct" in sql:
            return [{"__ROWS": 10, "C0_NN": 10, "C0_ND": 3, "C1_NN": 10, "C1_ND": 10,
                     "C2_NN": 10, "C2_ND": 9}]
        return [{"COL": "DEPARTMENT", "V": "Tech", "N": 6}, {"COL": "DEPARTMENT", "V": "Ops", "N": 4}]

    obj = {"columns": {
        "DEPARTMENT": profile_data.classify_column("DEPARTMENT", "TEXT") | {"data_type": "TEXT"},
        "EMPLOYEE_ID": profile_data.classify_column("EMPLOYEE_ID", "TEXT") | {"data_type": "TEXT"},
        "EMPLOYEE_NAME": profile_data.classify_column("EMPLOYEE_NAME", "TEXT") | {"data_type": "TEXT"},
    }}
    profile_data.profile_columns(fake_run, "DB.S.EMP", obj)
    profile_data.profile_code_values(fake_run, "DB.S.EMP", obj)
    assert obj["columns"]["DEPARTMENT"]["is_code"]
    assert not obj["columns"]["EMPLOYEE_ID"]["is_code"]
    assert not obj["columns"]["EMPLOYEE_NAME"]["is_code"]
    assert "EMPLOYEE_NAME" not in calls[-1]
    assert obj["columns"]["DEPARTMENT"]["top_values"][0] == ["Tech", 6]
