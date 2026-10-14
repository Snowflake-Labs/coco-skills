"""Regression tests for the Java↔Scala/PySpark parity fixes (SNOW-3715354).

Each test pins a specific defect found during the parity audit. Grouped by the
failure mode they prevent:

1. ``--language java`` rejected by argparse — Java migrate Phase 1a and Phase 4
   both died with ``invalid choice: 'java'``, so a Java migration could not
   complete at all.
2. Java silently routed down the *Python* branch of ``language == "scala"``
   two-way tests, producing ``.py`` extensions, ``#`` comments, ``SPRKCNTPY``
   EWI codes and a Python docstring header in Java output.
3. Java files/notebooks invisible to gates (data-edge scan, notebook coverage).
4. Inconsistent "is this a Java test file?" definitions across three scripts,
   which made Phase 3 verification demand a session rewrite in files the
   updater had deliberately skipped.
5. ``1_5_adjudication`` unrecognized by the state validator.

Run from snowpark-connect/:
    uv run --project . python -m pytest scripts/tests/test_java_parity_fixes.py -v
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
_ROOT = _SCRIPTS_DIR.parent


def _load(name: str, rel: str):
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / rel)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    # Register BEFORE exec: @dataclass resolves its own module via
    # sys.modules[cls.__module__], which is None for an unregistered module.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


GSR = _load("generate_scos_reports", "generate_scos_reports.py")
GT = _load("generate_transformations", "generate_transformations.py")
NB = _load("notebook_io", "notebook_io.py")
VP = _load("verify_phase", "verify_phase.py")
VMS = _load("validate_migration_state", "validate_migration_state.py")


# ---------------------------------------------------------------------------
# 1. argparse must accept --language java
# ---------------------------------------------------------------------------

_LANG_SCRIPTS = [
    "generate_scos_reports.py",
    "generate_transformations.py",
    "fallback_transform.py",
    "assessment/render_assessment.py",
]


@pytest.mark.parametrize("rel", _LANG_SCRIPTS)
def test_language_choices_include_java(rel: str):
    """`--language java` must not be rejected by argparse.

    migrate-spark-java SKILL.md passes `--language java` to render_assessment.py
    (Phase 1a) and generate_scos_reports.py (Phase 4); both are MUST-RUN gated
    phases, so an argparse rejection made Java migration impossible to finish.
    """
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / rel), "--help"],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "java" in proc.stdout, f"{rel} --help does not advertise a java language choice"


@pytest.mark.parametrize("rel", _LANG_SCRIPTS)
def test_language_java_not_invalid_choice(rel: str):
    """Invoking with --language java must not produce 'invalid choice'."""
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / rel), "--language", "java"],
        capture_output=True, text=True, timeout=120,
    )
    assert "invalid choice: 'java'" not in proc.stderr, (
        f"{rel} still rejects --language java:\n{proc.stderr}"
    )


# ---------------------------------------------------------------------------
# 2. generate_scos_reports: Java must not fall into the Python branch
# ---------------------------------------------------------------------------


def test_source_ext_per_language():
    assert GSR._source_ext("python") == ".py"
    assert GSR._source_ext("scala") == ".scala"
    assert GSR._source_ext("java") == ".java", "Java output was being written as .py"


def test_comment_prefix_per_language():
    assert GSR._comment_prefix("python") == "#"
    assert GSR._comment_prefix("scala") == "//"
    assert GSR._comment_prefix("java") == "//", "Java is a C-family language, not #"
    assert GSR._comment_prefix("sql") == "--"


def test_cell_language_and_label_per_language():
    assert GSR._lang_conv("java")["cell_language"] == "java"
    assert GSR._lang_conv("java")["label"] == "Java", (
        "Java runs produced PythonSnowConvert-Log-*.log"
    )
    # Unknown languages still fall back to Python rather than raising.
    assert GSR._lang_conv("cobol")["ext"] == ".py"


def test_java_migration_header_is_a_line_comment(tmp_path):
    """Java must get `// SCOS Migration Output`, never a Python docstring."""
    (tmp_path / "Job.java").write_text("package a;\nclass Job {}\n", encoding="utf-8")
    patched = GSR.ensure_migration_headers(str(tmp_path), "java")
    assert patched == 1
    text = (tmp_path / "Job.java").read_text(encoding="utf-8")
    assert text.startswith("// SCOS Migration Output"), text[:60]
    assert '"""' not in text, "a Python docstring header is not valid Java"


def test_java_migration_header_is_idempotent(tmp_path):
    (tmp_path / "Job.java").write_text("package a;\n", encoding="utf-8")
    assert GSR.ensure_migration_headers(str(tmp_path), "java") == 1
    assert GSR.ensure_migration_headers(str(tmp_path), "java") == 0


def test_extract_java_imports_handles_semicolons_and_static(tmp_path):
    """Java imports end in `;` and may be `import static a.b.C.m;`.

    The Python extractor found nothing in Java files, so
    ArtifactDependencyInventory.csv was empty for every Java workload.
    """
    f = tmp_path / "Job.java"
    f.write_text(
        "package com.acme.etl;\n"
        "import org.apache.spark.sql.Dataset;\n"
        "import static org.apache.spark.sql.functions.col;\n"
        "import com.acme.util.Helper;\n",
        encoding="utf-8",
    )
    got = dict(GSR.extract_java_imports(str(f)))
    assert "org.apache.spark.sql.Dataset" in got
    # `import static x.y.Z.m` must resolve to the member path, not "static".
    assert "org.apache.spark.sql.functions.col" in got
    assert "static" not in got
    assert got["org.apache.spark.sql.functions.col"].startswith("import static ")
    assert "com.acme.util.Helper" in got


def test_detect_user_packages_reads_java_package_declarations(tmp_path):
    (tmp_path / "Job.java").write_text("package com.acme.etl;\nclass Job {}\n", encoding="utf-8")
    assert GSR.detect_user_packages(str(tmp_path), "java") == {"com.acme.etl"}
    # Default arg stays Scala so existing callers are unaffected.
    assert GSR.detect_user_packages(str(tmp_path)) == set()


# ---------------------------------------------------------------------------
# 3. generate_transformations: .java annotations
# ---------------------------------------------------------------------------


def test_annotation_style_by_extension():
    """A .java file must get `//` comments and SPRKCNTSCL codes, not `#`/SPRKCNTPY."""
    assert GT._annotation_style("a/B.java") == ("//", "SPRKCNTSCL")
    assert GT._annotation_style("a/B.scala") == ("//", "SPRKCNTSCL")
    assert GT._annotation_style("a/b.py") == ("#", "SPRKCNTPY")


def test_java_session_patterns_target_snowpark_connect_session():
    """Must agree with update_imports_java.py, which renames to SnowparkConnectSession."""
    assert GT._LANG_EXT["java"] == ".java"
    pats = GT._LANG_SESSION_PATTERNS["java"]
    assert pats, "no Java session-init patterns registered"
    for _search, replace in pats:
        assert "SnowparkConnectSession.builder()" in replace
        assert ".remote(" not in replace, "SCOS Java does not use SparkSession.remote()"


# ---------------------------------------------------------------------------
# 4. notebook_io: Java cell comments
# ---------------------------------------------------------------------------


def _write_ipynb(path: Path, language: str, cells: list[tuple[str, str]]) -> None:
    path.write_text(json.dumps({
        "cells": [
            {"cell_type": ct, "source": src.splitlines(keepends=True),
             "metadata": {}, "outputs": [], "execution_count": None}
            for ct, src in cells
        ],
        "metadata": {"kernelspec": {"language": language}},
        "nbformat": 4, "nbformat_minor": 5,
    }), encoding="utf-8")


def test_flatten_java_notebook_uses_slash_comments(tmp_path):
    """`target_language != "scala"` gave Java `#`, which is a Java syntax error."""
    nb = tmp_path / "n.ipynb"
    _write_ipynb(nb, "java", [("markdown", "notes here"), ("code", "df.show();")])
    out = NB.flatten_cells_to_script(str(nb), target_language="java")
    assert "// --- cell" in out, out[:200]
    assert "# --- cell" not in out
    # Non-target cells are commented with the same prefix.
    assert "// notes here" in out


def test_flatten_python_notebook_still_uses_hash(tmp_path):
    nb = tmp_path / "n.ipynb"
    _write_ipynb(nb, "python", [("code", "df.show()")])
    out = NB.flatten_cells_to_script(str(nb), target_language="python")
    assert "# --- cell" in out
    assert "// --- cell" not in out


# ---------------------------------------------------------------------------
# 5. Gates must see .java
# ---------------------------------------------------------------------------


def test_data_edge_gate_scans_java_files():
    gate = _load("check_data_edges_gate", "assessment/check_data_edges_gate.py")
    assert ".java" in gate._DATA_SUFFIXES, "Java sources were invisible to the data-edge gate"
    for expected in (".py", ".sql", ".ipynb", ".scala"):
        assert expected in gate._DATA_SUFFIXES


def test_iter_java_files_returns_only_flat_sources(tmp_path):
    """iter_java_files must not double-count a unit the notebook check also owns.

    NOTE ``.java`` is deliberately NOT in ``notebook_io.NOTEBOOK_EXTS`` — a
    Databricks-exported Java notebook is not a supported input shape, so Java
    notebooks arrive as ``.ipynb`` with a java kernel. The ``is_notebook`` guard
    in ``iter_java_files`` therefore mirrors ``iter_scala_files`` defensively; the
    load-bearing separation is that ``.ipynb`` never matches the ``*.java`` glob.
    """
    (tmp_path / "Plain.java").write_text("class Plain {}\n", encoding="utf-8")
    nb = tmp_path / "Nb.ipynb"
    _write_ipynb(nb, "java", [("code", "df.show();")])
    found = {p.name for p in VP.iter_java_files(tmp_path)}
    assert found == {"Plain.java"}
    # The notebook is picked up by the notebook path instead — exactly once.
    units = {u["display"] for u in VP._jvm_notebook_units(tmp_path, {}, "java")}
    assert units == {"Nb.ipynb"}
    assert ".java" not in NB.NOTEBOOK_EXTS


def test_jvm_notebook_units_are_language_scoped(tmp_path):
    scala_nb = tmp_path / "S.ipynb"
    java_nb = tmp_path / "J.ipynb"
    _write_ipynb(scala_nb, "scala", [("code", "df.show()")])
    _write_ipynb(java_nb, "java", [("code", "df.show();")])
    java_units = {u["display"] for u in VP._jvm_notebook_units(tmp_path, {}, "java")}
    scala_units = {u["display"] for u in VP._jvm_notebook_units(tmp_path, {}, "scala")}
    assert java_units == {"J.ipynb"}
    assert scala_units == {"S.ipynb"}


@pytest.mark.parametrize("path,is_test", [
    ("src/main/java/Job.java", False),
    ("src/main/java/JobTest.java", True),
    ("src/main/java/JobTests.java", True),   # was missed
    ("src/main/java/JobIT.java", True),      # was missed
    ("src/main/java/JobSpec.java", True),    # was missed
    ("src/main/java/JobSuite.java", True),   # was missed
    ("src/test/java/Anything.java", True),
])
def test_java_test_path_detection(path: str, is_test: bool):
    """Must match update_imports_java.py `_TEST_FILE_RE` and ScosJavaRewrite isTest.

    A mismatch makes Phase 3 verification demand SnowparkConnectSession in files
    the updater intentionally left on SparkSession — an unfixable gate failure.
    """
    assert VP.is_test_java_path(path) is is_test


def test_java_test_detection_agrees_across_implementations():
    """The three definitions of 'Java test file' must stay in sync."""
    ui = (_SCRIPTS_DIR / "update_imports_java.py").read_text(encoding="utf-8")
    rewrite = (
        _SCRIPTS_DIR / "javaparser_rules" / "com" / "snowflake" / "scos"
        / "javaparser" / "ScosJavaRewrite.java"
    ).read_text(encoding="utf-8")
    for suffix in ("Test", "Tests", "IT", "Spec", "Suite"):
        assert suffix in ui, f"{suffix} missing from update_imports_java._TEST_FILE_RE"
        assert f'"{suffix}.java"' in rewrite, f"{suffix}.java missing from ScosJavaRewrite isTest"


# ---------------------------------------------------------------------------
# 6. validate_migration_state
# ---------------------------------------------------------------------------


def test_adjudication_phase_key_is_recognized():
    """Phase 1.1 writes 1_5_adjudication; it must not be an 'unrecognized' key."""
    keys = {k for k, _, _ in VMS.OPTIONAL_PHASES}
    assert "1_5_adjudication" in keys


def test_java_required_phases_cover_the_java_flow():
    keys = {k for k, _, _ in VMS.REQUIRED_PHASES_JAVA}
    for expected in (
        "0_5c_javaparser", "1_analysis", "1a_assessment_report", "2_fixes",
        "2a_fallback", "2b_compilation", "2c_verification", "3_imports", "4_reports",
    ):
        assert expected in keys, f"{expected} missing from REQUIRED_PHASES_JAVA"


def _java_state_with_all_phases(migrated: Path) -> dict:
    return {
        "schema_version": 1,
        "migrated_dir": str(migrated),
        "manifest": ["Job.java"],
        "phases_completed": {
            k: {"status": "passed"} for k, _, _ in VMS.REQUIRED_PHASES_JAVA
        },
    }


def test_standalone_sql_promotes_phase_0_6_for_java(tmp_path):
    """Java had no Phase 0.6; the validator hard-fails without it when .sql exists.

    This is why Phase 0.6 had to be added to the Java SKILL: any Java workload
    carrying standalone .sql files failed Phase 4a with an unactionable error.
    """
    migrated = tmp_path / "Output"
    migrated.mkdir()
    (migrated / "Job.java").write_text("class Job {}\n", encoding="utf-8")
    (migrated / "load.sql").write_text("SELECT 1;\n", encoding="utf-8")
    state = _java_state_with_all_phases(migrated)
    report = VMS.validate(state, str(tmp_path / "migration_state.json"), language="java")
    misses = [r.key for r in report.results if r.status == "missing"]
    assert "0_6_sql_rewrite" in misses, (
        "standalone .sql present but Phase 0.6 was not required — its SCOS gaps "
        "would be detected and never rewritten"
    )


def test_no_sql_files_does_not_require_phase_0_6(tmp_path):
    migrated = tmp_path / "Output"
    migrated.mkdir()
    (migrated / "Job.java").write_text("class Job {}\n", encoding="utf-8")
    state = _java_state_with_all_phases(migrated)
    report = VMS.validate(state, str(tmp_path / "migration_state.json"), language="java")
    misses = [r.key for r in report.results if r.status == "missing"]
    assert misses == [], f"unexpected missing phases for a clean Java run: {misses}"


def test_adjudication_key_not_reported_as_unrecognized(tmp_path):
    """A run that adjudicated must not have 1_5_adjudication flagged as an extra key."""
    migrated = tmp_path / "Output"
    migrated.mkdir()
    (migrated / "Job.java").write_text("class Job {}\n", encoding="utf-8")
    state = _java_state_with_all_phases(migrated)
    state["phases_completed"]["1_5_adjudication"] = {
        "status": "passed", "confirmed": 2, "dismissed": 1, "chunks": 1,
    }
    report = VMS.validate(state, str(tmp_path / "migration_state.json"), language="java")
    assert "1_5_adjudication" not in report.extra_keys
    # It should surface as a recognized OPTIONAL phase instead.
    assert "1_5_adjudication" in {r.key for r in report.optional_results}


# ---------------------------------------------------------------------------
# 7. Java skill wiring (docs must not promise phases that do not exist)
# ---------------------------------------------------------------------------


_JAVA_SKILL = _ROOT / "migrate-spark-java-to-snowpark-connect" / "SKILL.md"


@pytest.mark.parametrize("heading", [
    "### Phase 0.6: Standalone SQL Rewrite",
    "### Phase 1.1: Adjudication",
    "### Phase 4b: Generate Migration Feedback File",
])
def test_java_skill_documents_phase(heading: str):
    assert heading in _JAVA_SKILL.read_text(encoding="utf-8"), (
        f"{heading} missing — PySpark and Scala both have it"
    )


def test_java_skill_routes_to_the_java_validator():
    """The skill used to claim the Java validator 'does not exist'."""
    text = _JAVA_SKILL.read_text(encoding="utf-8")
    assert "validate-spark-java-to-snowpark-connect/SKILL.md" in text
    assert "does not exist" not in text
    assert (_ROOT / "validate-spark-java-to-snowpark-connect" / "SKILL.md").is_file()


def test_java_adjudicator_agent_exists_and_is_java_flavoured():
    agent = (
        _ROOT / "migrate-spark-java-to-snowpark-connect" / "agents" / "adjudicator.md"
    )
    assert agent.is_file(), "Phase 1.1 dispatches agents/adjudicator.md"
    text = agent.read_text(encoding="utf-8")
    assert "analyze_java.py" in text
    assert "references/java/rdd-conversion.md" in text
    # No stray Scala references (SPRKCNTSCL codes are shared and expected).
    assert "analyze_scala.py" not in text
    assert "references/scala/" not in text


def test_analyzer_no_longer_advertises_removed_flag():
    """--require-llm was deleted from analyze_java.py; docs must not pass it."""
    agent = (
        _ROOT / "migrate-spark-java-to-snowpark-connect" / "agents" / "analyzer.md"
    ).read_text(encoding="utf-8")
    assert "--require-llm" not in agent
    src = (_SCRIPTS_DIR / "analyze_java.py").read_text(encoding="utf-8")
    assert "require_llm" not in src
    assert "predict_compatibility_batch" not in src, (
        "the analyzer must make no CORTEX.COMPLETE calls (PySpark/Scala parity)"
    )
