"""Tests for the Phase-0.6 standalone .sql rewrite step (rewrite_sql_files.py)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rewrite_sql_files as rsf  # noqa: E402


def _state(tmp_path: Path, files: dict[str, str]) -> Path:
    conv = tmp_path / "Conversion-SCOS-TEST"
    out = conv / "Output"
    out.mkdir(parents=True)
    for name, src in files.items():
        p = out / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
    state_path = conv / "migration_state.json"
    state_path.write_text(json.dumps({
        "conversion_root": str(conv),
        "migrated_dir": str(out),
        "manifest": [],
    }))
    return state_path


def _load(state_path: Path) -> dict:
    return json.loads(state_path.read_text())


def test_mechanical_rewrite_and_residual_annotation(tmp_path):
    sql = (
        "SELECT a, b, SUM(v) AS s,\n"
        "       ROW_NUMBER() OVER (PARTITION BY a) AS rn\n"
        "FROM t\n"
        "GROUP BY a GROUPING SETS ((b), ());\n"
    )
    state_path = _state(tmp_path, {"q.sql": sql})
    assert rsf.main(["--state", str(state_path)]) == 0

    out = (state_path.parent / "Output" / "q.sql").read_text()
    # mechanical GROUPING SETS fold applied (body rewritten)
    assert "(A, B)" in out.upper()
    assert rsf._SENTINEL in out
    assert "-- SCOS: [detector:grouping_sets_with_groupby]" in out
    assert "--   original:" in out
    # window-missing-ORDER-BY is judgment-heavy → residual TODO for the fixer
    assert "-- SCOS: TODO - [detector:window_without_order_by]" in out

    state = _load(state_path)
    assert state["phases_completed"]["0_6_sql_rewrite"]["status"] == "passed"
    assert "q.sql" in state["sql_rewrite_edits"]
    kinds = {e["kind"] for e in state["sql_rewrite_edits"]["q.sql"]}
    assert {"rewrite", "residual"} <= kinds


def test_clean_sql_file_is_untouched(tmp_path):
    # A clean (parseable) file is always stamped with the sentinel header so it
    # shows it was reviewed, but the body is preserved and no edits are recorded.
    sql = "SELECT a, b FROM t WHERE a > 1;\n"
    state_path = _state(tmp_path, {"clean.sql": sql})
    assert rsf.main(["--state", str(state_path)]) == 0
    out = (state_path.parent / "Output" / "clean.sql").read_text()
    assert rsf._SENTINEL in out
    assert "0 rewrite(s), 0 manual TODO(s)" in out
    assert sql.strip() in out
    assert "-- SCOS: [" not in out
    assert "-- SCOS: TODO" not in out
    state = _load(state_path)
    assert state["sql_rewrite_edits"]["clean.sql"] == []


def test_dry_run_does_not_overstate_clean_file(tmp_path, capsys):
    # A clean, parseable file has no rewrites/TODOs: dry-run must report it as
    # STAMP (reviewed, 0 rewrite(s)), never "WOULD REWRITE ... rewrite(s)".
    sql = "SELECT a, b FROM t WHERE a > 1;\n"
    state_path = _state(tmp_path, {"clean.sql": sql})
    assert rsf.main(["--state", str(state_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "WOULD REWRITE clean.sql" not in out
    assert "0 rewrite(s)" in out
    # Dry-run must not write anything.
    assert rsf._SENTINEL not in (state_path.parent / "Output" / "clean.sql").read_text()


def test_dry_run_reports_rewrite_for_gappy_file(tmp_path, capsys):
    sql = "SELECT a FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a ORDER BY b) = 1;\n"
    state_path = _state(tmp_path, {"q.sql": sql})
    assert rsf.main(["--state", str(state_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "WOULD REWRITE q.sql" in out


def test_idempotent_second_run_makes_no_change(tmp_path):
    sql = "SELECT ROW_NUMBER() OVER (PARTITION BY x) AS rn FROM t;\n"
    state_path = _state(tmp_path, {"q.sql": sql})
    assert rsf.main(["--state", str(state_path)]) == 0
    first = (state_path.parent / "Output" / "q.sql").read_text()
    assert rsf.main(["--state", str(state_path)]) == 0
    second = (state_path.parent / "Output" / "q.sql").read_text()
    assert second == first
    assert second.count(rsf._SENTINEL) == 1


def test_unparseable_sql_is_byte_identical(tmp_path):
    sql = "this is not <<< valid sql at all"
    state_path = _state(tmp_path, {"bad.sql": sql})
    assert rsf.main(["--state", str(state_path)]) == 0
    out = (state_path.parent / "Output" / "bad.sql").read_text()
    assert out == sql


def test_databricks_json_sql_notebook_is_excluded(tmp_path):
    # A .sql file whose first byte is '{' is a Databricks native-JSON notebook,
    # not a plain SQL script — find_plain_sql_files must skip it.
    nb = '{"commands": [{"command": "SELECT ROW_NUMBER() OVER (PARTITION BY x) FROM t"}]}'
    state_path = _state(tmp_path, {"nb.sql": nb})
    assert rsf.main(["--state", str(state_path)]) == 0
    out = (state_path.parent / "Output" / "nb.sql").read_text()
    assert out == nb  # untouched
    state = _load(state_path)
    assert state["phases_completed"]["0_6_sql_rewrite"]["files_processed"] == 0


def test_sql_markers_are_harvested_into_issues_without_noise(tmp_path):
    """generate_scos_reports.scan_scos_comments must pick up `-- SCOS:` markers
    from .sql files (Issues.csv coverage), without surfacing the Phase 0.6
    sentinel or the `original:` continuation as their own rows."""
    import generate_scos_reports as gsr

    sql = "SELECT ROW_NUMBER() OVER (PARTITION BY x) AS rn, SUM(v) AS k FROM t GROUP BY k;\n"
    state_path = _state(tmp_path, {"q.sql": sql})
    assert rsf.main(["--state", str(state_path)]) == 0

    out_dir = str(state_path.parent / "Output")
    rows = gsr.scan_scos_comments(out_dir, "python")  # workload python; .sql scanned as sql
    descs = [r.get("description") or "" for r in rows]
    cats = [r.get("snowpark_connect_category") for r in rows]

    # Both gaps are judgment-heavy (window-missing-ORDER-BY and the LCA
    # collision) → each surfaces once as a TODO.
    assert any("window_without_order_by" in d for d in descs)
    assert any("lca_alias_collision" in d for d in descs)
    assert "Snowpark Connect TODO" in cats
    # No noise rows from the sentinel or the folded `original:` line.
    assert not any("Phase 0.6" in d for d in descs)
    assert not any(d.strip().startswith("original:") for d in descs)
