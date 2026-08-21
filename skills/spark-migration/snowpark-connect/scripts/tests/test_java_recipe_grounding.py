"""Tests for Java recipe grounding — Phase 0.5c fix rules (TDD red phase).

Tests encode INTENDED POST-FIX behavior for the 4 FIX rules in
ScosJavaRewrite.java (Workstream A / GAPs 1, 5, 6, 9).

Pass/fail status against the CURRENT (pre-fix) codebase:

  FAIL pre-fix (source inspection — Java source not yet patched):
    test_checkpoint_ewi_is_1000_in_source           GAP-9
    test_parallelize_comment_no_scala_isms           GAP-5
    test_parallelize_comment_has_java_types          GAP-5
    test_udtf_bases_includes_spark_java_udf_types    GAP-6
    test_temp_view_cache_rule_has_performance_comment  GAP-1

  PASS pre-fix (driver contract — Python driver already correct):
    test_checkpoint_rewrite_anchor_via_driver
    test_parallelize_java_comment_anchor_via_driver
    test_udtf_udf1_annotation_anchor_via_driver
    test_udtf_useraggfunc_annotation_anchor_via_driver
    test_udtf_no_false_positive_class_named_udf5
    test_temp_view_cache_comment_precedes_cache_in_output
    test_temp_view_cache_recipe_edit_anchor_recorded
    test_anchor_format_regex_contract
    test_phase_entry_has_canonical_fields

Run:
    cd /home/repo/Developer/cortex-code-skills/data-engineering/spark-migration/snowpark-connect
    uv run --project . python -m pytest scripts/tests/test_java_recipe_grounding.py -q
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

# ── Module loading ─────────────────────────────────────────────────────────────

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

# ── Paths ──────────────────────────────────────────────────────────────────────

_JAVA_REWRITE_SRC = (
    _SCRIPTS_DIR
    / "javaparser_rules"
    / "com"
    / "snowflake"
    / "scos"
    / "javaparser"
    / "ScosJavaRewrite.java"
)
_FAKE_JAVA = "/usr/bin/java"

# ── Shared test helpers ────────────────────────────────────────────────────────


def _fake_which_java_only(x: str) -> str | None:
    return _FAKE_JAVA if x == "java" else None


def _fake_jar(tmp_path: Path, name: str = "runner.jar") -> Path:
    jar = tmp_path / name
    jar.write_bytes(b"PK")
    return jar


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


def _identity_rewrite(cmd: list, **kwargs) -> MagicMock:
    """Mock subprocess.run: returns --source file content unchanged."""
    r = MagicMock()
    r.returncode = 0
    r.stderr = ""
    if "--source" in cmd:
        r.stdout = Path(cmd[cmd.index("--source") + 1]).read_text(encoding="utf-8")
    else:
        r.stdout = ""
    return r


def _make_rule_mock(rule_outputs: dict[str, str]):
    """Build subprocess.run mock returning canned output for specific rules, identity for rest."""
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


def _extract_between(text: str, start_marker: str, end_marker: str) -> str:
    """Return text between first occurrence of start_marker and next occurrence of end_marker."""
    s = text.find(start_marker)
    if s < 0:
        return ""
    e = text.find(end_marker, s + len(start_marker))
    return text[s:e] if e >= 0 else text[s:]


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE INSPECTION TESTS
# Read ScosJavaRewrite.java and assert post-fix strings are present.
# These FAIL against the current (pre-fix) Java source.
# ══════════════════════════════════════════════════════════════════════════════


def test_checkpoint_ewi_is_1000_in_source():
    """GAP-9: ruleCheckpointToCache must emit SPRKCNTSCL1000, not SPRKCNTSCL1500.

    Dataset.checkpoint() is not an RDD operation; SPRKCNTSCL1500 is the RDD
    library code.  The correct generic unsupported-API code is SPRKCNTSCL1000.

    FAILS pre-fix: current source has SPRKCNTSCL1500 in ruleCheckpointToCache.
    """
    src = _JAVA_REWRITE_SRC.read_text(encoding="utf-8")
    method_body = _extract_between(
        src,
        "private static String ruleCheckpointToCache(",
        "private static String ruleMapSubscriptToElementAt(",
    )
    assert method_body, "Could not locate ruleCheckpointToCache method in ScosJavaRewrite.java"
    assert "SPRKCNTSCL1000" in method_body, (
        "ruleCheckpointToCache must use EWI code SPRKCNTSCL1000 (generic unsupported API). "
        "Dataset.checkpoint() is not an RDD operation — SPRKCNTSCL1500 (RDD layer) is wrong. "
        "See plan GAP-9."
    )
    assert "SPRKCNTSCL1500" not in method_body, (
        "ruleCheckpointToCache must NOT emit SPRKCNTSCL1500 (RDD code). "
        "See plan GAP-9: change comment string from SPRKCNTSCL1500 to SPRKCNTSCL1000."
    )


def test_parallelize_comment_no_scala_isms():
    """GAP-5: PARALLELIZE_COMMENT must not contain Scala-isms (Seq[Row], .asJava).

    This is the Java migration path.  Scala interop syntax confuses Java developers.

    FAILS pre-fix: current source has 'Seq[Row]' and '.asJava' in PARALLELIZE_COMMENT.
    """
    src = _JAVA_REWRITE_SRC.read_text(encoding="utf-8")
    comment_block = _extract_between(
        src,
        "private static final String PARALLELIZE_COMMENT =",
        "private static final String BROADCAST_COMMENT =",
    )
    assert comment_block, "Could not locate PARALLELIZE_COMMENT in ScosJavaRewrite.java"
    assert "Seq[Row]" not in comment_block, (
        "PARALLELIZE_COMMENT must not contain Scala type 'Seq[Row]'. "
        "Replace with Java equivalent 'List<Row>'. See plan GAP-5."
    )
    assert ".asJava" not in comment_block, (
        "PARALLELIZE_COMMENT must not contain '.asJava' (Scala collection interop). "
        "Java code does not use .asJava conversions. See plan GAP-5."
    )


def test_parallelize_comment_has_java_types():
    """GAP-5: PARALLELIZE_COMMENT must mention Java-native List types and createDataFrame API.

    FAILS pre-fix: current source uses Scala Seq types instead of Java List types.
    """
    src = _JAVA_REWRITE_SRC.read_text(encoding="utf-8")
    comment_block = _extract_between(
        src,
        "private static final String PARALLELIZE_COMMENT =",
        "private static final String BROADCAST_COMMENT =",
    )
    assert comment_block, "Could not locate PARALLELIZE_COMMENT in ScosJavaRewrite.java"
    has_java_list = "List<Row>" in comment_block or "List<Object[]>" in comment_block
    has_create_df = "createDataFrame(" in comment_block
    assert has_java_list, (
        "PARALLELIZE_COMMENT must mention Java type 'List<Row>' or 'List<Object[]>'. "
        "See plan GAP-5: replace Scala Seq types with Java List equivalents."
    )
    assert has_create_df, (
        "PARALLELIZE_COMMENT must reference createDataFrame( as the Java migration target. "
        "See plan GAP-5."
    )


def test_udtf_bases_includes_spark_java_udf_types():
    """GAP-6: UDTF_BASES must include UDF1 and UserDefinedAggregateFunction.

    Spark Java UDF types (UDF1-UDF22, UserDefinedAggregateFunction) require the
    same UDTF compatibility-mode annotation as Hive GenericUDTF.

    FAILS pre-fix: UDTF_BASES only contains UserDefinedTableFunction and GenericUDTF.
    """
    src = _JAVA_REWRITE_SRC.read_text(encoding="utf-8")
    udtf_block = _extract_between(
        src,
        "private static final Set<String> UDTF_BASES =",
        "private static final String UDTF_COMMENT =",
    )
    assert udtf_block, "Could not locate UDTF_BASES in ScosJavaRewrite.java"
    assert '"UDF1"' in udtf_block, (
        "UDTF_BASES must include '\"UDF1\"' to detect Spark Java UDF1<T,R> implementations. "
        "See plan GAP-6: expand UDTF_BASES to include UDF1-UDF22."
    )
    assert '"UserDefinedAggregateFunction"' in udtf_block, (
        "UDTF_BASES must include '\"UserDefinedAggregateFunction\"'. "
        "Aggregation UDFs also require compatibility-mode annotation. See plan GAP-6."
    )


def test_temp_view_cache_rule_has_performance_comment():
    """GAP-1: ruleTempViewMultiUseCache must insert a // SCOS: Performance tip - comment.

    The rule currently inserts only 'recv.cache();' with no annotation comment,
    making the rewrite invisible and breaking the annotate-only contract.
    The fix adds a '// SCOS: Performance tip -' comment immediately before the
    cache insertion.

    FAILS pre-fix: current code has no 'SCOS: Performance tip' string in the method.
    """
    src = _JAVA_REWRITE_SRC.read_text(encoding="utf-8")
    method_body = _extract_between(
        src,
        "private static String ruleTempViewMultiUseCache(",
        "private static int fromCount(",
    )
    assert method_body, "Could not locate ruleTempViewMultiUseCache method in ScosJavaRewrite.java"
    assert "SCOS: Performance tip" in method_body, (
        "ruleTempViewMultiUseCache must insert a '// SCOS: Performance tip -' comment "
        "before the .cache() call to make the rewrite visible to the developer. "
        "See plan GAP-1: add annotation comment before recv.cache() insertion."
    )
    # Comment must precede the cache insertion in the source string
    perf_pos = method_body.find("SCOS: Performance tip")
    assert perf_pos >= 0  # guarded by previous assertion
    cache_pos = method_body.find(".cache()", perf_pos)
    assert cache_pos > perf_pos, (
        "The '// SCOS: Performance tip -' comment must appear BEFORE '.cache()' "
        "in the ruleTempViewMultiUseCache insertion string. See plan GAP-1."
    )


# ══════════════════════════════════════════════════════════════════════════════
# DRIVER INTEGRATION TESTS (JAR MOCKED)
# Mock the jar subprocess to return canned post-fix output.
# These test the Python driver contract and PASS pre-fix.
# ══════════════════════════════════════════════════════════════════════════════

# Fixture inputs/outputs ----------------------------------------------------- #

_CHECKPOINT_INPUT = """\
import org.apache.spark.sql.Dataset;
import org.apache.spark.sql.Row;

class Job {
    public void run(Dataset<Row> df) {
        Dataset<Row> checkpointed = df.checkpoint(false);
    }
}
"""

# Post-fix jar output: SPRKCNTSCL1000, not SPRKCNTSCL1500
_CHECKPOINT_OUTPUT = """\
import org.apache.spark.sql.Dataset;
import org.apache.spark.sql.Row;

class Job {
    public void run(Dataset<Row> df) {
        // SCOS: [SPRKCNTSCL1000] checkpoint() not available in Snowpark Connect \u2014 replaced with cache()
        Dataset<Row> checkpointed = df.cache();
    }
}
"""

_PARALLELIZE_INPUT = """\
import java.util.Arrays;
import java.util.List;

class Job {
    public void run(org.apache.spark.SparkContext sc) {
        sc.parallelize(Arrays.asList(1, 2, 3));
    }
}
"""

# Post-fix jar output: Java types in comment, no Seq[Row] / .asJava
_PARALLELIZE_OUTPUT = """\
import java.util.Arrays;
import java.util.List;

class Job {
    public void run(org.apache.spark.SparkContext sc) {
        // SCOS: [SPRKCNTSCL1500] sc.parallelize is unsupported in Snowpark Connect. Convert to createDataFrame(javaList, schema) \u2014 use List<Row> with a schema, or List<Object[]>. Do NOT nest createDataFrame calls.
        sc.parallelize(Arrays.asList(1, 2, 3));
    }
}
"""

_UDTF_UDF1_INPUT = """\
import org.apache.spark.sql.api.java.UDF1;

class MyTransform implements UDF1<String, String> {
    public String call(String input) { return input.toUpperCase(); }
}
"""

# Post-fix jar output: UDTF compat comment before class declaration
_UDTF_UDF1_OUTPUT = """\
import org.apache.spark.sql.api.java.UDF1;

// SCOS: TODO - UDF/UDTF compatibility mode may be required; set spark.sql.udtf.compatibility.mode=true if UDF returns unexpected results
class MyTransform implements UDF1<String, String> {
    public String call(String input) { return input.toUpperCase(); }
}
"""

_UDTF_USERAGG_INPUT = """\
import org.apache.spark.sql.expressions.UserDefinedAggregateFunction;
import org.apache.spark.sql.types.StructType;

abstract class MyAgg extends UserDefinedAggregateFunction {
    public abstract StructType inputSchema();
}
"""

_UDTF_USERAGG_OUTPUT = """\
import org.apache.spark.sql.expressions.UserDefinedAggregateFunction;
import org.apache.spark.sql.types.StructType;

// SCOS: TODO - UDF/UDTF compatibility mode may be required; set spark.sql.udtf.compatibility.mode=true if UDF returns unexpected results
abstract class MyAgg extends UserDefinedAggregateFunction {
    public abstract StructType inputSchema();
}
"""

# Class merely NAMED UDF5 — does not implement any Spark UDF interface
_UDTF_FALSE_POS_INPUT = """\
class UDF5 {
    // A user class that happens to be named UDF5 but implements nothing Spark-related
    public void process() {}
}
"""

_TEMP_VIEW_INPUT = """\
import org.apache.spark.sql.Dataset;
import org.apache.spark.sql.Row;

class Job {
    public void run() {
        Dataset<Row> df = spark.sql("SELECT 1 AS val");
        df.createOrReplaceTempView("myview");
        Dataset<Row> r1 = spark.sql("SELECT * FROM myview WHERE val > 0");
        Dataset<Row> r2 = spark.sql("SELECT count(*) FROM myview");
    }
}
"""

# Post-fix jar output: // SCOS: Performance tip - comment + df.cache() before createOrReplaceTempView
_TEMP_VIEW_OUTPUT = """\
import org.apache.spark.sql.Dataset;
import org.apache.spark.sql.Row;

class Job {
    public void run() {
        Dataset<Row> df = spark.sql("SELECT 1 AS val");
        // SCOS: Performance tip - multi-use temp view 'myview'; .cache() inserted below
        df.cache();
        df.createOrReplaceTempView("myview");
        Dataset<Row> r1 = spark.sql("SELECT * FROM myview WHERE val > 0");
        Dataset<Row> r2 = spark.sql("SELECT count(*) FROM myview");
    }
}
"""

# ── Driver tests ─────────────────────────────────────────────────────────────


def test_checkpoint_rewrite_anchor_via_driver(monkeypatch, tmp_path):
    """GAP-9 (driver layer): anchor recorded for ScosCheckpointToCache; output has SPRKCNTSCL1000.

    PASSES pre-fix — driver is already correct; this is a regression guard for
    the contract that checkpoint rewrite produces SPRKCNTSCL1000 in the output file.
    """
    state_file, _ = _make_state(tmp_path, _CHECKPOINT_INPUT)
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)
    monkeypatch.setattr(
        subprocess, "run",
        _make_rule_mock({"ScosCheckpointToCache": _CHECKPOINT_OUTPUT}),
    )

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0

    state = json.loads(state_file.read_text())
    edits = state["recipe_edits"].get("Job.java", [])
    rule_ids = [e["recipe_id"] for e in edits]
    assert "javaparser:ScosCheckpointToCache" in rule_ids, (
        "Expected recipe_edit anchor for ScosCheckpointToCache after checkpoint rewrite"
    )

    output_text = (tmp_path / "src" / "Job.java").read_text(encoding="utf-8")
    assert "SPRKCNTSCL1000" in output_text, (
        "Output file must contain SPRKCNTSCL1000 EWI code for checkpoint rewrite"
    )
    assert "SPRKCNTSCL1500" not in output_text, (
        "Output file must NOT contain SPRKCNTSCL1500 for a Dataset.checkpoint() rewrite"
    )


def test_parallelize_java_comment_anchor_via_driver(monkeypatch, tmp_path):
    """GAP-5 (driver layer): anchor recorded; output has Java types, no Scala-isms.

    PASSES pre-fix — regression guard for Java-safe parallelize guidance.
    """
    state_file, _ = _make_state(tmp_path, _PARALLELIZE_INPUT)
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)
    monkeypatch.setattr(
        subprocess, "run",
        _make_rule_mock({"ScosSparkContextPropertyFallbackAnnotate": _PARALLELIZE_OUTPUT}),
    )

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0

    output_text = (tmp_path / "src" / "Job.java").read_text(encoding="utf-8")
    # Java-native types must appear
    assert "List<Row>" in output_text or "List<Object[]>" in output_text, (
        "Post-fix comment must mention Java type List<Row> or List<Object[]>"
    )
    # Scala-isms must be absent
    assert "Seq[Row]" not in output_text, (
        "Post-fix comment must not contain Scala type Seq[Row]"
    )
    assert ".asJava" not in output_text, (
        "Post-fix comment must not contain .asJava (Scala interop)"
    )

    state = json.loads(state_file.read_text())
    edits = state["recipe_edits"].get("Job.java", [])
    assert any(
        e["recipe_id"] == "javaparser:ScosSparkContextPropertyFallbackAnnotate" for e in edits
    ), "Expected anchor for ScosSparkContextPropertyFallbackAnnotate"


def test_udtf_udf1_annotation_anchor_via_driver(monkeypatch, tmp_path):
    """GAP-6: class implements UDF1<T,R> triggers UDTF compat annotation anchor.

    PASSES pre-fix — regression guard for UDF1 detection.
    """
    state_file, _ = _make_state(tmp_path, _UDTF_UDF1_INPUT)
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)
    monkeypatch.setattr(
        subprocess, "run",
        _make_rule_mock({"ScosUdtfCompatibilityModeAnnotate": _UDTF_UDF1_OUTPUT}),
    )

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0

    state = json.loads(state_file.read_text())
    edits = state["recipe_edits"].get("Job.java", [])
    assert any(
        e["recipe_id"] == "javaparser:ScosUdtfCompatibilityModeAnnotate" for e in edits
    ), "Expected anchor for ScosUdtfCompatibilityModeAnnotate when class implements UDF1"

    output_text = (tmp_path / "src" / "Job.java").read_text(encoding="utf-8")
    assert "SCOS: TODO" in output_text, (
        "Post-fix output must include SCOS: TODO comment for UDF1 implementation"
    )


def test_udtf_useraggfunc_annotation_anchor_via_driver(monkeypatch, tmp_path):
    """GAP-6: class extends UserDefinedAggregateFunction triggers UDTF compat annotation.

    PASSES pre-fix — regression guard for UserDefinedAggregateFunction detection.
    """
    state_file, _ = _make_state(tmp_path, _UDTF_USERAGG_INPUT)
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)
    monkeypatch.setattr(
        subprocess, "run",
        _make_rule_mock({"ScosUdtfCompatibilityModeAnnotate": _UDTF_USERAGG_OUTPUT}),
    )

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0

    state = json.loads(state_file.read_text())
    edits = state["recipe_edits"].get("Job.java", [])
    assert any(
        e["recipe_id"] == "javaparser:ScosUdtfCompatibilityModeAnnotate" for e in edits
    ), "Expected anchor for ScosUdtfCompatibilityModeAnnotate on UserDefinedAggregateFunction"


def test_udtf_no_false_positive_class_named_udf5(monkeypatch, tmp_path):
    """GAP-6 false-positive guard: class merely NAMED UDF5 (no implements) must not trigger.

    A class whose name is UDF5 but which does not implement/extend any Spark UDF
    interface must NOT receive the UDTF compatibility annotation.  This pins the
    false-positive mitigation flagged in the plan's risk section.

    PASSES pre-fix and post-fix — pins the safe boundary of the rule.
    """
    state_file, _ = _make_state(tmp_path, _UDTF_FALSE_POS_INPUT)
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)
    # Jar returns input unchanged — no annotation triggered for a bare class UDF5 {}
    monkeypatch.setattr(subprocess, "run", _identity_rewrite)

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0

    state = json.loads(state_file.read_text())
    edits = state["recipe_edits"].get("Job.java", [])
    udtf_anchors = [
        e for e in edits
        if e["recipe_id"] == "javaparser:ScosUdtfCompatibilityModeAnnotate"
    ]
    assert not udtf_anchors, (
        "Class merely NAMED 'UDF5' without implementing a Spark UDF interface "
        "must NOT produce a ScosUdtfCompatibilityModeAnnotate anchor."
    )

    output_text = (tmp_path / "src" / "Job.java").read_text(encoding="utf-8")
    assert "SCOS: TODO" not in output_text, (
        "No SCOS TODO comment must be inserted for a class merely named UDF5 with no implements."
    )


def test_temp_view_cache_comment_precedes_cache_in_output(monkeypatch, tmp_path):
    """GAP-1: post-fix output has // SCOS: Performance tip - comment BEFORE .cache().

    The comment must visually precede the cache call so developers can find it.
    PASSES pre-fix — driver writes exactly what the jar returns; regression guard.
    """
    state_file, _ = _make_state(tmp_path, _TEMP_VIEW_INPUT)
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)
    monkeypatch.setattr(
        subprocess, "run",
        _make_rule_mock({"ScosTempViewMultiUseCache": _TEMP_VIEW_OUTPUT}),
    )

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0

    output_text = (tmp_path / "src" / "Job.java").read_text(encoding="utf-8")
    assert "SCOS: Performance tip" in output_text, (
        "Output must contain '// SCOS: Performance tip -' comment for multi-use temp view"
    )
    assert "df.cache();" in output_text, "Output must contain df.cache(); statement"

    perf_pos = output_text.find("SCOS: Performance tip")
    cache_pos = output_text.find("df.cache();", perf_pos)
    assert cache_pos > perf_pos, (
        "The '// SCOS: Performance tip -' comment must appear BEFORE 'df.cache();' "
        "in the rewritten output"
    )


def test_temp_view_cache_recipe_edit_anchor_recorded(monkeypatch, tmp_path):
    """GAP-1: driver records recipe_edit anchor for ScosTempViewMultiUseCache.

    PASSES pre-fix — regression guard.
    """
    state_file, _ = _make_state(tmp_path, _TEMP_VIEW_INPUT)
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)
    monkeypatch.setattr(
        subprocess, "run",
        _make_rule_mock({"ScosTempViewMultiUseCache": _TEMP_VIEW_OUTPUT}),
    )

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0

    state = json.loads(state_file.read_text())
    edits = state["recipe_edits"].get("Job.java", [])
    assert any(
        e["recipe_id"] == "javaparser:ScosTempViewMultiUseCache" for e in edits
    ), "Expected recipe_edit anchor for ScosTempViewMultiUseCache"


# ══════════════════════════════════════════════════════════════════════════════
# ANCHOR CONTRACT TESTS
# Verify recipe_edits format and phase_completed canonical fields.
# These PASS pre-fix (driver already generates correct shapes).
# ══════════════════════════════════════════════════════════════════════════════

_ANCHOR_RE = re.compile(r"^javaparser:[A-Za-z]+:[0-9]+:[0-9a-f]{8}$")

_BUILDER_INPUT = """\
import org.apache.spark.sql.SparkSession;

class Job {
    public static void main(String[] args) {
        SparkSession spark = SparkSession.builder()
            .master("local")
            .getOrCreate();
    }
}
"""

_BUILDER_OUTPUT = """\
import org.apache.spark.sql.SparkSession;

class Job {
    public static void main(String[] args) {
        // SCOS-RECIPE-PRESERVED-CONFIG: .master("local")
        SparkSession spark = SparkSession.builder()
            .getOrCreate();
    }
}
"""


def test_anchor_format_regex_contract(monkeypatch, tmp_path):
    """All recipe_edits entries match ^javaparser:[A-Za-z]+:[0-9]+:[0-9a-f]{8}$.

    Also asserts phases_completed["0_5c_javaparser"] has all canonical fields.
    PASSES pre-fix — driver anchor generation and phase entry are already correct.
    """
    state_file, _ = _make_state(tmp_path, _BUILDER_INPUT)
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)
    monkeypatch.setattr(
        subprocess, "run",
        _make_rule_mock({"ScosSparkSessionBuilderRewrite": _BUILDER_OUTPUT}),
    )

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0

    state = json.loads(state_file.read_text())
    all_edits = [e for edits in state["recipe_edits"].values() for e in edits]
    assert all_edits, "Expected at least one recipe_edit after SparkSessionBuilderRewrite"

    for edit in all_edits:
        anchor = edit["output_line_anchor"]
        assert _ANCHOR_RE.match(anchor), (
            f"Anchor {anchor!r} does not match "
            r"^javaparser:[A-Za-z]+:[0-9]+:[0-9a-f]{8}$"
        )
        assert edit["recipe_id"].startswith("javaparser:"), (
            f"recipe_id {edit['recipe_id']!r} must start with 'javaparser:'"
        )
        # src_line must be consistent with the anchor's line component
        anchor_parts = anchor.split(":")
        assert edit["src_line"] == int(anchor_parts[2]), (
            f"edit['src_line'] {edit['src_line']} must match anchor line {anchor_parts[2]}"
        )


def test_phase_entry_has_canonical_fields(monkeypatch, tmp_path):
    """phases_completed['0_5c_javaparser'] has canonical fields:
    {status, ran_at, files_processed, files_modified, total_edits, rules_run}.

    PASSES pre-fix — driver phase entry shape is already correct.
    """
    state_file, _ = _make_state(tmp_path, _BUILDER_INPUT)
    jar = _fake_jar(tmp_path)
    monkeypatch.setattr(_mod, "MAVEN_JAR", jar)
    monkeypatch.setattr(shutil, "which", _fake_which_java_only)
    monkeypatch.setattr(subprocess, "run", _identity_rewrite)

    rc = _safe_main(["--state", str(state_file)])
    assert rc == 0

    state = json.loads(state_file.read_text())
    phase = state["phases_completed"][PHASE_KEY]
    required_fields = {
        "status", "ran_at", "files_processed", "files_modified", "total_edits", "rules_run"
    }
    missing = required_fields - set(phase.keys())
    assert not missing, (
        f"phases_completed[{PHASE_KEY!r}] is missing required fields: {missing}"
    )
    assert phase["status"] == "passed", f"Expected status=passed, got {phase['status']!r}"
    assert isinstance(phase["rules_run"], list), "'rules_run' must be a list"
    assert set(phase["rules_run"]) == set(JAVA_RULES), (
        f"rules_run must list all 12 JAVA_RULES; "
        f"missing: {set(JAVA_RULES) - set(phase['rules_run'])}"
    )


# ── _classify_recipe_kind: javaparser prefix support ──────────────────────────

def _load_classify_recipe_kind():
    """Extract _classify_recipe_kind from analyze_java.py without importing the full module."""
    import ast as _ast

    src = (_SCRIPTS_DIR / "analyze_java.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)
    for node in _ast.walk(tree):
        if isinstance(node, _ast.FunctionDef) and node.name == "_classify_recipe_kind":
            func_src = _ast.get_source_segment(src, node)
            ns: dict = {}
            exec(func_src, ns)  # noqa: S102
            return ns["_classify_recipe_kind"]
    raise RuntimeError("_classify_recipe_kind not found in analyze_java.py")


_classify_recipe_kind = _load_classify_recipe_kind()


def test_classify_recipe_kind_javaparser_rewrite():
    """javaparser:<RuleName> rules that are not Annotate-suffix return 'rewrite'."""
    assert _classify_recipe_kind("javaparser:ScosCheckpointToCache") == "rewrite"
    assert _classify_recipe_kind("javaparser:ScosSparkSessionBuilderRewrite") == "rewrite"
    assert _classify_recipe_kind("javaparser:ScosTempViewMultiUseCache") == "rewrite"
    assert _classify_recipe_kind("javaparser:ScosMapSubscriptToElementAt") == "rewrite"
    assert _classify_recipe_kind("javaparser:ScosSaveAsTableDropStorageOpts") == "rewrite"


def test_classify_recipe_kind_javaparser_annotate():
    """javaparser:<RuleName>Annotate returns 'annotate'."""
    assert _classify_recipe_kind("javaparser:ScosWildcardReadAnnotate") == "annotate"
    assert _classify_recipe_kind("javaparser:ScosUdtfCompatibilityModeAnnotate") == "annotate"
    assert _classify_recipe_kind("javaparser:ScosDriverHotPathAnnotate") == "annotate"


def test_classify_recipe_kind_legacy_suffixes_unchanged():
    """Existing underscore-suffix IDs (PySpark/Scala) still classify correctly."""
    assert _classify_recipe_kind("spark_builder_drop_master_init_session_rewrite") == "rewrite"
    assert _classify_recipe_kind("some_recipe_annotate") == "annotate"
    assert _classify_recipe_kind("other_recipe_comment") == "comment"
    assert _classify_recipe_kind("unknown") == "other"
