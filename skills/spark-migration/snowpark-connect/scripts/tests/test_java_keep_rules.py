"""Tests for Phase 0.5c KEEP rules — anchor shape and idempotency.

Covers the 8 KEEP rules (not modified by Workstream A / WS-A fix rules):
  ScosSparkSessionBuilderRewrite, ScosMapSubscriptToElementAt,
  ScosWildcardReadAnnotate, ScosSaveAsTableDropStorageOpts,
  ScosExternalCloudReadAnnotate, ScosSelfJoinUnaliasedAnnotate,
  ScosUnionByNameAllowMissingAnnotate, ScosDriverHotPathAnnotate

All tests mock the jar subprocess — no JDK required.
Run from snowpark-connect/:
    uv run --project . python -m pytest scripts/tests/test_java_keep_rules.py -v
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ── Module loading ──────────────────────────────────────────────────────────────

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "preprocess_javaparser",
    _SCRIPTS_DIR / "preprocess_javaparser.py",
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

main = _mod.main
PHASE_KEY = _mod.PHASE_KEY
JAVA_RULES = _mod.JAVA_RULES
RULE_PREFIX = _mod.RULE_PREFIX

_FAKE_JAVA = "/usr/bin/java"
_ANCHOR_RE = re.compile(r"^javaparser:[A-Za-z]+:[0-9]+:[0-9a-f]{8}$")

# (rule_name, expected_comment_prefix) for each KEEP rule
_KEEP_RULES: list[tuple[str, str]] = [
    ("ScosSparkSessionBuilderRewrite",      "SCOS-RECIPE-PRESERVED-CONFIG"),
    ("ScosMapSubscriptToElementAt",         "SCOS: TODO"),
    ("ScosWildcardReadAnnotate",            "SCOS: TODO"),
    ("ScosSaveAsTableDropStorageOpts",      "SCOS:"),
    ("ScosExternalCloudReadAnnotate",       "SCOS: Performance tip"),
    ("ScosSelfJoinUnaliasedAnnotate",       "SCOS: TODO"),
    ("ScosUnionByNameAllowMissingAnnotate", "SCOS: TODO"),
    ("ScosDriverHotPathAnnotate",           "SCOS: Performance tip"),
]

# Minimal Java snippets that would trigger each KEEP rule
_RULE_INPUTS: dict[str, str] = {
    "ScosSparkSessionBuilderRewrite": (
        "class Job {\n"
        "    void run() {\n"
        '        SparkSession spark = SparkSession.builder().master("local").getOrCreate();\n'
        "    }\n"
        "}\n"
    ),
    "ScosMapSubscriptToElementAt": (
        "class Job {\n"
        "    void run() {\n"
        '        Column c = mapCol.getItem("key");\n'
        "    }\n"
        "}\n"
    ),
    "ScosWildcardReadAnnotate": (
        "class Job {\n"
        "    void run() {\n"
        '        Dataset<Row> df = spark.read().parquet("s3://bucket/*.parquet");\n'
        "    }\n"
        "}\n"
    ),
    "ScosSaveAsTableDropStorageOpts": (
        "class Job {\n"
        "    void run() {\n"
        '        df.write().format("parquet").option("path","s3://b/d").saveAsTable("t");\n'
        "    }\n"
        "}\n"
    ),
    "ScosExternalCloudReadAnnotate": (
        "class Job {\n"
        "    void run() {\n"
        '        Dataset<Row> df = spark.read().parquet("s3://bucket/data");\n'
        "    }\n"
        "}\n"
    ),
    "ScosSelfJoinUnaliasedAnnotate": (
        "class Job {\n"
        "    void run() {\n"
        '        Dataset<Row> r = df.join(df, df.col("id").equalTo(df.col("id2")));\n'
        "    }\n"
        "}\n"
    ),
    "ScosUnionByNameAllowMissingAnnotate": (
        "class Job {\n"
        "    void run() {\n"
        "        Dataset<Row> r = df1.unionByName(df2, true);\n"
        "    }\n"
        "}\n"
    ),
    "ScosDriverHotPathAnnotate": (
        "class Job {\n"
        "    void run() {\n"
        "        for (int i = 0; i < 10; i++) {\n"
        "            List<Row> rows = df.collect();\n"
        "        }\n"
        "    }\n"
        "}\n"
    ),
}

# Canned outputs: input with a // SCOS: comment prepended to the trigger line
_RULE_OUTPUTS: dict[str, str] = {
    "ScosSparkSessionBuilderRewrite": (
        "class Job {\n"
        "    void run() {\n"
        '        // SCOS-RECIPE-PRESERVED-CONFIG: .master("local")\n'
        "        SparkSession spark = SparkSession.builder().getOrCreate();\n"
        "    }\n"
        "}\n"
    ),
    "ScosMapSubscriptToElementAt": (
        "class Job {\n"
        "    void run() {\n"
        "        // SCOS: TODO - use element_at(col, key) instead of col.getItem(key)\n"
        '        Column c = mapCol.getItem("key");\n'
        "    }\n"
        "}\n"
    ),
    "ScosWildcardReadAnnotate": (
        "class Job {\n"
        "    void run() {\n"
        "        // SCOS: TODO - wildcard path not supported in DataFrameReader\n"
        '        Dataset<Row> df = spark.read().parquet("s3://bucket/*.parquet");\n'
        "    }\n"
        "}\n"
    ),
    "ScosSaveAsTableDropStorageOpts": (
        "class Job {\n"
        "    void run() {\n"
        "        // SCOS: dropped .format() and .option(path,...) — storage managed by Snowflake\n"
        '        df.write().saveAsTable("t");\n'
        "    }\n"
        "}\n"
    ),
    "ScosExternalCloudReadAnnotate": (
        "class Job {\n"
        "    void run() {\n"
        "        // SCOS: Performance tip - migrate s3:// read to Snowflake stage\n"
        '        Dataset<Row> df = spark.read().parquet("s3://bucket/data");\n'
        "    }\n"
        "}\n"
    ),
    "ScosSelfJoinUnaliasedAnnotate": (
        "class Job {\n"
        "    void run() {\n"
        "        // SCOS: TODO - self-join on unaliased DataFrame; use .alias()\n"
        '        Dataset<Row> r = df.join(df, df.col("id").equalTo(df.col("id2")));\n'
        "    }\n"
        "}\n"
    ),
    "ScosUnionByNameAllowMissingAnnotate": (
        "class Job {\n"
        "    void run() {\n"
        "        // SCOS: TODO - allowMissingColumns=true may produce unexpected schema padding\n"
        "        Dataset<Row> r = df1.unionByName(df2, true);\n"
        "    }\n"
        "}\n"
    ),
    "ScosDriverHotPathAnnotate": (
        "class Job {\n"
        "    void run() {\n"
        "        for (int i = 0; i < 10; i++) {\n"
        "            // SCOS: Performance tip - avoid driver-side collect() in hot loops\n"
        "            List<Row> rows = df.collect();\n"
        "        }\n"
        "    }\n"
        "}\n"
    ),
}


# ── Helpers ─────────────────────────────────────────────────────────────────────


def _fake_jar(tmp_path: Path, name: str = "runner.jar") -> Path:
    jar = tmp_path / name
    jar.write_bytes(b"PK")
    return jar


def _fake_which_java_only(x: str) -> str | None:
    return _FAKE_JAVA if x == "java" else None


def _make_state(tmp_path: Path, java_content: str) -> tuple[Path, Path]:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    java_file = src_dir / "Job.java"
    java_file.write_text(java_content, encoding="utf-8")
    state: dict[str, Any] = {
        "migrated_dir": str(src_dir),
        "manifest": ["Job.java"],
        "recipe_edits": {},
        "phases_completed": {},
    }
    state_file = tmp_path / "migration_state.json"
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state_file, java_file


def _safe_main(argv: list[str]) -> int:
    try:
        return main(argv)
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0


def _make_rule_mock(rule_outputs: dict[str, str]):
    """Return a subprocess.run mock that yields canned output per rule, identity otherwise."""

    def _run(cmd: list, **kwargs) -> MagicMock:
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        if "--rule" in cmd and "--source" in cmd:
            rule = cmd[cmd.index("--rule") + 1]
            src = Path(cmd[cmd.index("--source") + 1]).read_text(encoding="utf-8")
            r.stdout = rule_outputs.get(rule, src)
        else:
            r.stdout = ""
        return r

    return _run


# ── Constant checks ──────────────────────────────────────────────────────────────


def test_keep_rules_are_subset_of_java_rules():
    """All 8 KEEP rule names are present in the module's JAVA_RULES list."""
    keep_names = {r for r, _ in _KEEP_RULES}
    missing = keep_names - set(JAVA_RULES)
    assert not missing, f"KEEP rules absent from JAVA_RULES: {missing}"


# ── Anchor-shape tests (one per KEEP rule) ────────────────────────────────────


@pytest.mark.parametrize("rule,marker", _KEEP_RULES)
def test_keep_rule_anchor_shape(rule: str, marker: str, monkeypatch, tmp_path):
    """Each KEEP rule: when the jar returns annotated output the driver records
    at least one recipe_edit whose output_line_anchor matches
    javaparser:<RuleName>:<line>:<8hex>, and src_line equals the embedded integer.
    """
    state_file, java_file = _make_state(tmp_path, _RULE_INPUTS[rule])
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)
    monkeypatch.setattr(subprocess, "run", _make_rule_mock({rule: _RULE_OUTPUTS[rule]}))

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0

    output_text = java_file.read_text(encoding="utf-8")
    assert marker in output_text, (
        f"{rule!r}: expected marker {marker!r} not found in output file"
    )

    state = json.loads(state_file.read_text())
    edits = state["recipe_edits"].get("Job.java", [])
    rule_edits = [e for e in edits if e.get("recipe_id") == f"javaparser:{rule}"]
    assert rule_edits, f"No anchor recorded for KEEP rule {rule!r}"

    for edit in rule_edits:
        anchor = edit["output_line_anchor"]
        assert _ANCHOR_RE.match(anchor), (
            f"{rule!r}: anchor {anchor!r} does not match "
            r"^javaparser:[A-Za-z]+:[0-9]+:[0-9a-f]{8}$"
        )
        assert edit["src_line"] == int(anchor.split(":")[2]), (
            f"{rule!r}: edit src_line {edit['src_line']} "
            f"!= anchor line component {anchor.split(':')[2]}"
        )


# ── Idempotency test ──────────────────────────────────────────────────────────


def test_keep_rules_idempotency_two_runs(monkeypatch, tmp_path):
    """Second driver run on already-processed content adds no new anchors.

    After the first pass the file has javaparser: entries in recipe_edits;
    the driver must skip it on the second pass.  The jar is mocked to return
    already-annotated content on both passes (as the plan specifies),
    but the driver should not call it at all on the second pass.
    """
    rule = "ScosSparkSessionBuilderRewrite"
    state_file, _ = _make_state(tmp_path, _RULE_INPUTS[rule])
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)

    # First run: jar produces annotated output → driver records anchor.
    monkeypatch.setattr(subprocess, "run", _make_rule_mock({rule: _RULE_OUTPUTS[rule]}))
    rc1 = _safe_main(["--state", str(state_file)])
    assert rc1 == 0

    state_after_first = json.loads(state_file.read_text())
    assert state_after_first["recipe_edits"], "Expected at least one anchor after first run"
    edits_snapshot = json.dumps(state_after_first["recipe_edits"], sort_keys=True)

    # Second run: jar would return already-annotated content, but file is skipped.
    monkeypatch.setattr(subprocess, "run", _make_rule_mock({rule: _RULE_OUTPUTS[rule]}))
    rc2 = _safe_main(["--state", str(state_file)])
    assert rc2 == 0

    state_after_second = json.loads(state_file.read_text())
    assert json.dumps(state_after_second["recipe_edits"], sort_keys=True) == edits_snapshot, (
        "recipe_edits changed on second run — driver is not idempotent for KEEP rule content"
    )


# ── Phase entry covers all 12 rules ───────────────────────────────────────────


def test_keep_rules_all_in_phases_completed_rules_run(monkeypatch, tmp_path):
    """phases_completed['0_5c_javaparser'] has all required fields and lists
    all 12 JAVA_RULES in rules_run (covering all 8 KEEP rules).
    """
    state_file, _ = _make_state(tmp_path, _RULE_INPUTS["ScosSparkSessionBuilderRewrite"])
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)
    monkeypatch.setattr(subprocess, "run", _make_rule_mock({}))  # identity — no changes

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0

    phase = json.loads(state_file.read_text())["phases_completed"][PHASE_KEY]

    required_fields = {"status", "ran_at", "files_processed", "files_modified", "total_edits", "rules_run"}
    missing_fields = required_fields - set(phase.keys())
    assert not missing_fields, (
        f"phases_completed[{PHASE_KEY!r}] is missing fields: {missing_fields}"
    )
    assert phase["status"] == "passed"
    assert isinstance(phase["rules_run"], list)

    rules_run = set(phase["rules_run"])
    assert rules_run == set(JAVA_RULES), (
        f"rules_run does not cover all 12 JAVA_RULES; "
        f"missing: {set(JAVA_RULES) - rules_run}"
    )

    keep_names = {r for r, _ in _KEEP_RULES}
    missing_keeps = keep_names - rules_run
    assert not missing_keeps, f"KEEP rules absent from rules_run: {missing_keeps}"
