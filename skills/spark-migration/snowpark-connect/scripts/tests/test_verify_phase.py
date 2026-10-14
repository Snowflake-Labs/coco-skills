"""Tests for verify_phase.py — the deterministic replacement for the 4 critics.

Each test builds a tiny conversion dir on disk (migration_state.json +
Output/*.scala + analysis.json + Reports/*.csv) and asserts the verdict and
specific check statuses for a given phase.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verify_phase import run_phase, STATUS_FAIL, STATUS_GAP, STATUS_OK

HEADER = "/* SCOS Migration\n * generated header\n */\n"


def _check(report, name):
    for c in report.checks:
        if c.name == name:
            return c
    raise AssertionError(f"check {name!r} not found in {[c.name for c in report.checks]}")


def _build(tmp_path: Path, *, scala: dict[str, str], analysis=None,
           reports: dict[str, str] | None = None, manifest=None) -> Path:
    """Create a conversion dir; return the migration_state.json path."""
    conv = tmp_path / "Conversion-SCOS-test"
    out = conv / "Output"
    out.mkdir(parents=True)
    for rel, content in scala.items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    if analysis is not None:
        (conv / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
    if reports is not None:
        rdir = conv / "Reports"
        rdir.mkdir(exist_ok=True)
        for name, body in reports.items():
            (rdir / name).write_text(body, encoding="utf-8")
    state = {
        "conversion_root": str(conv),
        "migrated_dir": str(out),
        "manifest": manifest if manifest is not None else sorted(scala.keys()),
    }
    sp = conv / "migration_state.json"
    sp.write_text(json.dumps(state), encoding="utf-8")
    return sp


# --- Phase 1 ----------------------------------------------------------------


def test_phase1_clean_passes(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + 'import org.apache.spark.sql._\nobject M\n'},
        analysis=[{"file": "Main.scala", "final_risk": 0.8}],
    )
    r = run_phase(1, json.loads(sp.read_text()), sp)
    assert r.verdict == "PASS", [c.__dict__ for c in r.checks]


def test_phase1_uncovered_spark_file_is_gap(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + 'import org.apache.spark.sql._\nobject M\n'},
        analysis=[],  # nothing analyzed
    )
    r = run_phase(1, json.loads(sp.read_text()), sp)
    assert _check(r, "file coverage").status == STATUS_GAP
    assert r.verdict == "PASS_WITH_GAPS"


def test_phase1_invalid_analysis_fails(tmp_path):
    sp = _build(tmp_path, scala={"Main.scala": HEADER + "object M\n"})
    (sp.parent / "analysis.json").write_text("{not json", encoding="utf-8")
    r = run_phase(1, json.loads(sp.read_text()), sp)
    assert r.verdict == "FAIL"


# --- Phase 2 ----------------------------------------------------------------


def test_phase2_clean_passes(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + 'val x = df.select("a")\n'},
        analysis=[],
    )
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert r.verdict == "PASS", [c.__dict__ for c in r.checks]


def test_phase2_import_artifact_fails(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "import org.apache.spark.foo — removed\n"},
        analysis=[],
    )
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "syntax artifacts").status == STATUS_FAIL


def test_phase2_noop_over_annotation_fails(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "val y = df.repartition(4) // SCOS: needless\n"},
        analysis=[],
    )
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "no-op over-annotation").status == STATUS_FAIL


def test_phase2_rdd_repartition_annotation_allowed(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "val y = df.rdd.repartition(4) // SCOS: RDD access\n"},
        analysis=[],
    )
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "no-op over-annotation").status == STATUS_OK


def test_phase2_high_risk_no_markers_fails(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "val z = df.checkpoint()\n"},
        analysis=[{"file": "Main.scala", "final_risk": 0.9, "lines": "4-4"}],
    )
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "high-risk coverage").status == STATUS_FAIL


def test_phase2_high_risk_with_marker_passes(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "val z = df.cache() // SCOS: was checkpoint\n"},
        analysis=[{"file": "Main.scala", "final_risk": 0.9, "lines": "4-4"}],
    )
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "high-risk coverage").status == STATUS_OK


def test_phase2_stale_ref_in_code_fails(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "val fs = new HiveContext(sc)\n"},
        analysis=[],
    )
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "cross-file consistency").status == STATUS_FAIL


def test_phase2_stale_ref_in_comment_ok(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "// removed HiveContext usage\nobject M\n"},
        analysis=[],
    )
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "cross-file consistency").status == STATUS_OK


def test_phase2_preserved_config_marker_dropped_fails(tmp_path):
    # Fixer collapsed the builder and deleted the recipe's preserve-config
    # markers — Phase 2 must catch the dropped recipe work. (Materialization
    # itself is a Phase 3 concern, so Phase 2 only checks marker survival.)
    body = HEADER + "val spark = SnowparkConnectSession.builder().getOrCreate()\n"
    sp = _build(tmp_path, scala={"Main.scala": body}, analysis=[])
    st = json.loads(sp.read_text())
    st["recipe_edits"] = {"Main.scala": [
        {"recipe_id": "scalafix:ScosBuilderPreserveConfig", "src_line": 2}]}
    sp.write_text(json.dumps(st))
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "preserved-config markers survived").status == STATUS_FAIL


def test_phase2_preserved_config_marker_present_passes(tmp_path):
    # Marker survived (even though not yet materialized — that is Phase 3's job).
    body = (HEADER + "// SCOS-RECIPE-PRESERVED-CONFIG: spark.sql.session.timeZone=UTC\n"
            "val spark = SnowparkConnectSession.builder().getOrCreate()\n"
            '// SCOS-RECIPE-INSERT-AFTER-BUILDER: spark.conf.set("spark.sql.session.timeZone", "UTC")\n')
    sp = _build(tmp_path, scala={"Main.scala": body}, analysis=[])
    st = json.loads(sp.read_text())
    st["recipe_edits"] = {"Main.scala": [
        {"recipe_id": "scalafix:ScosBuilderPreserveConfig", "src_line": 2}]}
    sp.write_text(json.dumps(st))
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "preserved-config markers survived").status == STATUS_OK


# --- Phase 3 ----------------------------------------------------------------


def _scos_main(extra: str = "") -> str:
    return (HEADER + "import com.snowflake.snowpark_connect._\n"
            "val spark = SnowparkConnectSession.builder().getOrCreate()\n" + extra)


def test_phase3_clean_passes(tmp_path):
    sp = _build(tmp_path, scala={"Main.scala": _scos_main()}, analysis=[])
    r = run_phase(3, json.loads(sp.read_text()), sp)
    assert r.verdict == "PASS", [c.__dict__ for c in r.checks]


def test_phase3_missing_header_fails(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": "import com.snowflake.snowpark_connect._\n"
               "val spark = SnowparkConnectSession.builder().getOrCreate()\n"},
        analysis=[],
    )
    r = run_phase(3, json.loads(sp.read_text()), sp)
    assert _check(r, "migration header").status == STATUS_FAIL


def test_phase3_unsupported_import_fails(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": _scos_main("import org.apache.hadoop.fs.Path\n")},
        analysis=[],
    )
    r = run_phase(3, json.loads(sp.read_text()), sp)
    assert _check(r, "no unsupported imports").status == STATUS_FAIL


def test_phase3_enable_hive_support_fails(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": _scos_main("val s2 = builder.enableHiveSupport()\n")},
        analysis=[],
    )
    r = run_phase(3, json.loads(sp.read_text()), sp)
    assert _check(r, "session init replaced").status == STATUS_FAIL


def test_phase3_hive_support_in_test_file_ok(tmp_path):
    sp = _build(
        tmp_path,
        scala={
            "Main.scala": _scos_main(),
            "MainSpec.scala": HEADER + "val s = builder.enableHiveSupport()\n",
        },
        analysis=[],
    )
    r = run_phase(3, json.loads(sp.read_text()), sp)
    assert _check(r, "session init replaced").status == STATUS_OK


def test_phase3_no_scos_session_fails(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "object M { val x = 1 }\n"},
        analysis=[],
    )
    r = run_phase(3, json.loads(sp.read_text()), sp)
    assert _check(r, "SnowparkConnectSession init").status == STATUS_FAIL


def test_phase3_preserved_config_unmaterialized_fails(tmp_path):
    body = (HEADER + "// SCOS-RECIPE-PRESERVED-CONFIG: spark.sql.session.timeZone=UTC\n"
            "val spark = SnowparkConnectSession.builder().getOrCreate()\n")
    sp = _build(tmp_path, scala={"Main.scala": body}, analysis=[])
    r = run_phase(3, json.loads(sp.read_text()), sp)
    assert _check(r, "preserved-config").status == STATUS_FAIL


def test_phase3_preserved_config_materialized_passes(tmp_path):
    body = (HEADER + "// SCOS-RECIPE-PRESERVED-CONFIG: spark.sql.session.timeZone=UTC\n"
            "val spark = SnowparkConnectSession.builder()"
            '.config("spark.sql.session.timeZone", "UTC").getOrCreate()\n')
    sp = _build(tmp_path, scala={"Main.scala": body}, analysis=[])
    r = run_phase(3, json.loads(sp.read_text()), sp)
    assert _check(r, "preserved-config").status == STATUS_OK


def test_phase3_preserved_config_quoted_marker_still_matches(tmp_path):
    # C2: a quoted marker `"k"="v"` (legacy Scalafix .syntax form) must still
    # match the materialized `.config("k", "v")` — the verifier normalizes the
    # marker token to its bare inner value before comparing.
    body = (HEADER + '// SCOS-RECIPE-PRESERVED-CONFIG: "spark.sql.session.timeZone"="UTC"\n'
            "val spark = SnowparkConnectSession.builder()"
            '.config("spark.sql.session.timeZone", "UTC").getOrCreate()\n')
    sp = _build(tmp_path, scala={"Main.scala": body}, analysis=[])
    r = run_phase(3, json.loads(sp.read_text()), sp)
    assert _check(r, "preserved-config").status == STATUS_OK


def test_phase3_stale_insert_after_builder_fails(tmp_path):
    body = (HEADER + "val spark = SnowparkConnectSession.builder().getOrCreate()\n"
            '// SCOS-RECIPE-INSERT-AFTER-BUILDER: spark.conf.set("k", "v")\n')
    sp = _build(tmp_path, scala={"Main.scala": body}, analysis=[])
    r = run_phase(3, json.loads(sp.read_text()), sp)
    assert _check(r, "preserved-config").status == STATUS_FAIL


def test_phase3_insert_after_mention_in_changelog_comment_ok(tmp_path):
    # Regression: a header/changelog block-comment that *describes* the marker
    # (" * - Materialized SCOS-RECIPE-INSERT-AFTER-BUILDER: ...") must NOT be
    # treated as a stale active marker. The active marker was already
    # materialized and removed.
    body = (HEADER
            + " * - Materialized SCOS-RECIPE-INSERT-AFTER-BUILDER: spark.conf.set(\"k\", \"v\")\n"
            + "// SCOS-RECIPE-PRESERVED-CONFIG: k=v\n"
            + "val spark = SnowparkConnectSession.builder().getOrCreate()\n"
            + 'spark.conf.set("k", "v")\n')
    sp = _build(tmp_path, scala={"Main.scala": body}, analysis=[])
    r = run_phase(3, json.loads(sp.read_text()), sp)
    assert _check(r, "preserved-config").status == STATUS_OK


def test_phase3_project_under_test_dir_not_misclassified(tmp_path):
    # Regression: a project living under any directory named 'test' must NOT
    # cause every file to be classified as a test file (is_test_path was run on
    # the absolute path, which contained '/test/'). Classification is now
    # relative to the migrated dir.
    base = tmp_path / "test" / "proj"
    base.mkdir(parents=True)
    sp = _build(base, scala={"Main.scala": _scos_main()}, analysis=[])
    r = run_phase(3, json.loads(sp.read_text()), sp)
    assert _check(r, "SnowparkConnectSession init").status == STATUS_OK


def test_phase3_nested_build_file_stale_dep_fails(tmp_path):
    # Recursive build-file check: a NESTED module build file with a forbidden
    # dep (spark-hive) must fail the gate, even when the root build.sbt is clean.
    conv_sp = _build(tmp_path, scala={"Main.scala": _scos_main()}, analysis=[])
    out = conv_sp.parent / "Output"
    (out / "build.sbt").write_text(
        'libraryDependencies += "com.snowflake" % "snowpark-connect-java-client_2.12" % "latest.release"\n',
        encoding="utf-8",
    )
    (out / "moduleA").mkdir()
    (out / "moduleA" / "pom.xml").write_text(
        "<project><dependencies><dependency>"
        "<groupId>org.apache.spark</groupId>"
        "<artifactId>spark-hive_2.12</artifactId><version>3.0.0</version>"
        "</dependency></dependencies></project>\n",
        encoding="utf-8",
    )
    st = json.loads(conv_sp.read_text())
    st["build_files"] = ["build.sbt", "moduleA/pom.xml"]
    conv_sp.write_text(json.dumps(st))
    r = run_phase(3, json.loads(conv_sp.read_text()), conv_sp)
    bf = _check(r, "build files")
    assert bf.status == STATUS_FAIL
    assert "moduleA/pom.xml" in bf.detail


def test_phase3_nested_build_file_clean_passes(tmp_path):
    # A nested module that is clean must NOT be required to declare the client
    # dep itself (it may inherit it) — recursion does negative checks only.
    conv_sp = _build(tmp_path, scala={"Main.scala": _scos_main()}, analysis=[])
    out = conv_sp.parent / "Output"
    (out / "build.sbt").write_text(
        'libraryDependencies += "com.snowflake" % "snowpark-connect-java-client_2.12" % "latest.release"\n',
        encoding="utf-8",
    )
    (out / "moduleA").mkdir()
    (out / "moduleA" / "build.sbt").write_text(
        'name := "moduleA"\nlibraryDependencies += "org.scalatest" %% "scalatest" % "3.2.7" % "test"\n',
        encoding="utf-8",
    )
    st = json.loads(conv_sp.read_text())
    st["build_files"] = ["build.sbt", "moduleA/build.sbt"]
    conv_sp.write_text(json.dumps(st))
    r = run_phase(3, json.loads(conv_sp.read_text()), conv_sp)
    assert _check(r, "build files").status == STATUS_OK


def test_phase3_build_file_missing_artifact_fails(tmp_path):
    conv_sp = _build(tmp_path, scala={"Main.scala": _scos_main()}, analysis=[])
    (conv_sp.parent / "Output" / "build.sbt").write_text(
        'libraryDependencies += "org.apache.spark" %% "spark-connect-client-jvm" % "3.5.1"\n',
        encoding="utf-8",
    )
    r = run_phase(3, json.loads(conv_sp.read_text()), conv_sp)
    assert _check(r, "build files").status == STATUS_FAIL


def test_phase3_build_file_version_placeholder_fails(tmp_path):
    # F13: an unresolved <latest> placeholder must fail the gate.
    conv_sp = _build(tmp_path, scala={"Main.scala": _scos_main()}, analysis=[])
    (conv_sp.parent / "Output" / "build.sbt").write_text(
        'libraryDependencies += "com.snowflake" % "snowpark-connect-java-client_2.12" % "<latest>"\n',
        encoding="utf-8",
    )
    r = run_phase(3, json.loads(conv_sp.read_text()), conv_sp)
    assert _check(r, "build files").status == STATUS_FAIL


def test_phase3_build_file_dynamic_version_ok(tmp_path):
    # latest.release is a valid sbt dynamic version — must NOT be flagged.
    conv_sp = _build(tmp_path, scala={"Main.scala": _scos_main()}, analysis=[])
    (conv_sp.parent / "Output" / "build.sbt").write_text(
        'libraryDependencies += "com.snowflake" % "snowpark-connect-java-client_2.12" % "latest.release"\n',
        encoding="utf-8",
    )
    r = run_phase(3, json.loads(conv_sp.read_text()), conv_sp)
    assert _check(r, "build files").status == STATUS_OK


def test_phase3_maven_bare_version_not_flagged(tmp_path):
    # False-positive guard: a normal Maven <version>X.Y.Z</version> tag must NOT
    # trip the placeholder check.
    conv_sp = _build(tmp_path, scala={"Main.scala": _scos_main()}, analysis=[])
    (conv_sp.parent / "Output" / "pom.xml").write_text(
        "<project><dependencies><dependency>"
        "<groupId>com.snowflake</groupId>"
        "<artifactId>snowpark-connect-java-client_2.12</artifactId>"
        "<version>0.4.1</version></dependency></dependencies></project>\n",
        encoding="utf-8",
    )
    r = run_phase(3, json.loads(conv_sp.read_text()), conv_sp)
    assert _check(r, "build files").status == STATUS_OK


# --- Phase 4 ----------------------------------------------------------------

_ISSUES_OK = "EWI_Code,File,Line,Description\nSPRKCNTSCL5001,Main.scala,3,cast\n"


def test_phase4_clean_passes(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "object M\n"},
        analysis=[],
        reports={
            "Issues.csv": _ISSUES_OK,
            "InputFilesInventory.csv": "File,Lines\nMain.scala,2\n",
            "ArtifactDependencyInventory.csv": "Import,Count\nspark,1\n",
        },
    )
    r = run_phase(4, json.loads(sp.read_text()), sp)
    assert r.verdict == "PASS", [c.__dict__ for c in r.checks]


def test_phase4_missing_csv_fails(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "object M\n"},
        analysis=[],
        reports={"Issues.csv": _ISSUES_OK},  # other two missing
    )
    r = run_phase(4, json.loads(sp.read_text()), sp)
    assert _check(r, "required CSVs").status == STATUS_FAIL


def test_phase4_wrong_language_prefix_fails(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "object M\n"},
        analysis=[],
        reports={
            "Issues.csv": "EWI_Code,File,Line,Description\nSPRKCNTPY1001,x.py,1,foo\n",
            "InputFilesInventory.csv": "File,Lines\nMain.scala,2\n",
            "ArtifactDependencyInventory.csv": "Import,Count\nspark,1\n",
        },
    )
    r = run_phase(4, json.loads(sp.read_text()), sp)
    assert _check(r, "EWI code prefix").status == STATUS_FAIL


def test_phase4_zero_issues_passes(tmp_path):
    # A clean migration with zero issues (header-only Issues.csv) is VALID.
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "object M\n"},
        analysis=[],
        reports={
            "Issues.csv": "EWI_Code,File,Line,Description\n",  # header only
            "InputFilesInventory.csv": "File,Lines\nMain.scala,2\n",
            "ArtifactDependencyInventory.csv": "Import,Count\nspark,1\n",
        },
    )
    r = run_phase(4, json.loads(sp.read_text()), sp)
    assert _check(r, "Issues.csv").status == STATUS_OK
    assert _check(r, "EWI code prefix").status == STATUS_OK
    assert r.verdict in ("PASS", "PASS_WITH_GAPS"), [c.__dict__ for c in r.checks]


def test_phase4_data_files_do_not_false_gap(tmp_path):
    # R3: an inventory with one .scala code row + many ignored data rows must
    # NOT trip the manifest-vs-inventory GAP heuristic (it compares code rows).
    hdr = ("Element,ProjectId,FileId,Count,SessionId,Extension,Technology,"
           "Bytes,CharacterLength,LinesOfCode,ParseResult,Ignored,OriginFilePath")
    code_row = "Main.scala,p,Main.scala,1,e,.scala,Scala,10,10,1,Parsed,False,/x/Main.scala"
    data_rows = "\n".join(
        f"d{i}.csv,p,d{i}.csv,1,e,.csv,Other,5,5,1,Parsed,True,/x/d{i}.csv" for i in range(8)
    )
    inv = f"{hdr}\n{code_row}\n{data_rows}\n"
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "object M\n"},
        analysis=[],
        reports={
            "Issues.csv": _ISSUES_OK,
            "InputFilesInventory.csv": inv,
            "ArtifactDependencyInventory.csv": "Import,Count\nspark,1\n",
        },
        manifest=["Main.scala"],
    )
    r = run_phase(4, json.loads(sp.read_text()), sp)
    assert _check(r, "InputFilesInventory rows").status == STATUS_OK, \
        [c.__dict__ for c in r.checks]


# --- file count guard (shared) ---------------------------------------------


def test_file_count_mismatch_fails_phase2(tmp_path):
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "object M\n"},
        analysis=[],
        manifest=["Main.scala", "Other.scala"],  # 2 expected, 1 on disk
    )
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "file count").status == STATUS_FAIL


# --- fix 2: file-count excludes .scala notebooks + project/ skip-dir ----------


def test_file_count_ignores_scala_notebook_in_manifest(tmp_path):
    # A Databricks exported-Scala notebook ends in .scala and is in the manifest,
    # but iter_scala_files excludes it (it's JSON/notebook). The count must match.
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "object M\n"},
        analysis=[],
        manifest=["Main.scala", "notebooks/job.scala"],
    )
    st = json.loads(sp.read_text())
    st["notebook_index"] = {"notebooks/job.scala": {"language": "scala",
                                                    "format": "exported_text",
                                                    "rel_path": "notebooks/job.scala"}}
    sp.write_text(json.dumps(st))
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "file count").status == STATUS_OK


def test_file_count_ignores_project_dir_scala(tmp_path):
    # sbt project/*.scala are build-tooling files iter_scala_files skips.
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "object M\n"},
        analysis=[],
        manifest=["Main.scala", "project/Build.scala"],
    )
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "file count").status == STATUS_OK


# --- fix 3: stale-ref regex precision -----------------------------------------


def test_stale_ref_java_nio_filesystems_ok(tmp_path):
    # java.nio.file.FileSystems is standard, supported — must NOT be flagged.
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "val d = java.nio.file.FileSystems.getDefault\n"},
        analysis=[],
    )
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "cross-file consistency").status == STATUS_OK


def test_stale_ref_hadoop_filesystem_fails(tmp_path):
    # Hadoop's FileSystem (singular) IS unsupported and should still be flagged.
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER + "val fs = FileSystem.get(conf)\n"},
        analysis=[],
    )
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "cross-file consistency").status == STATUS_FAIL


def test_stale_ref_ewi_annotated_line_ok(tmp_path):
    # A known-unsupported line already carrying an // EWI: marker is a deliberate
    # manual-refactor item, not a stale leftover.
    sp = _build(
        tmp_path,
        scala={"Main.scala": HEADER
               + "// EWI: SPRKCNTSCL1500 sc.hadoopConfiguration unsupported\n"
               + "sc.hadoopConfiguration.set(k, v)\n"},
        analysis=[],
    )
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "cross-file consistency").status == STATUS_OK


# --- fix 4: high-risk coverage keyed by path, not basename --------------------


def test_high_risk_duplicate_basename_correct_file_passes(tmp_path):
    # Two Image.scala; the one with the high-risk issue carries the marker.
    sp = _build(
        tmp_path,
        scala={
            "a/Image.scala": HEADER + "val z = df.checkpoint() // SCOS: reviewed\n",
            "b/Image.scala": HEADER + "object Other\n",
        },
        analysis=[{"file": "a/Image.scala", "final_risk": 0.9, "lines": "4-4"}],
    )
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "high-risk coverage").status == STATUS_OK


def test_high_risk_duplicate_basename_unannotated_file_fails(tmp_path):
    # The issue is in a/Image.scala (no marker); the sibling b/Image.scala has a
    # marker. A basename map would inspect the wrong file and wrongly pass.
    sp = _build(
        tmp_path,
        scala={
            "a/Image.scala": HEADER + "val z = df.checkpoint()\n",
            "b/Image.scala": HEADER + "val q = x // SCOS: unrelated\n",
        },
        analysis=[{"file": "a/Image.scala", "final_risk": 0.9, "lines": "4-4"}],
    )
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "high-risk coverage").status == STATUS_FAIL


# --- PySpark-parity high-risk coverage: marker-near window + resolution field --


def _hr(tmp_path, body, analysis):
    return _build(tmp_path, scala={"Main.scala": HEADER + body}, analysis=analysis)


def test_high_risk_marker_within_window_passes(tmp_path):
    # HEADER is 3 lines; issue on line 5 with a // SCOS marker on line 4 (within +/-3).
    body = "object M {\n  // SCOS: reviewed\n  val z = df.checkpoint()\n}\n"
    sp = _hr(tmp_path, body, [{"file": "Main.scala", "final_risk": 0.9, "lines": "6-6"}])
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "high-risk coverage").status == STATUS_OK


def test_high_risk_marker_outside_window_fails(tmp_path):
    # Marker is on line 4 but the issue is far away (line 12) -> not "near" -> FAIL.
    body = ("  // SCOS: unrelated note\n" + "  val a = 1\n" * 10 + "  val z = df.checkpoint()\n")
    sp = _hr(tmp_path, body, [{"file": "Main.scala", "final_risk": 0.9, "lines": "14-14"}])
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "high-risk coverage").status == STATUS_FAIL


def test_high_risk_resolution_todo_passes_without_marker(tmp_path):
    # resolution verdict in analysis.json satisfies coverage without an inline marker.
    body = "val z = df.checkpoint()\n"
    sp = _hr(tmp_path, body, [{"file": "Main.scala", "final_risk": 0.9,
                               "lines": "4-4", "resolution": "todo"}])
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "high-risk coverage").status == STATUS_OK


def test_high_risk_resolution_safe_without_reason_fails(tmp_path):
    body = "val z = df.checkpoint()\n"
    sp = _hr(tmp_path, body, [{"file": "Main.scala", "final_risk": 0.9,
                               "lines": "4-4", "resolution": "safe"}])
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "high-risk coverage").status == STATUS_FAIL


def test_high_risk_resolution_safe_with_reason_passes(tmp_path):
    body = "val z = df.checkpoint()\n"
    sp = _hr(tmp_path, body, [{"file": "Main.scala", "final_risk": 0.9, "lines": "4-4",
                               "resolution": "safe", "resolution_reason": "idempotent recompute"}])
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "high-risk coverage").status == STATUS_OK


def test_high_risk_ewi_marker_recognized(tmp_path):
    # Legacy // EWI: marker still counts as coverage (backward-compat).
    body = "// EWI: SPRKCNTSCL1500 RDD unsupported; manual refactor\nval z = sc.parallelize(xs)\n"
    sp = _hr(tmp_path, body, [{"file": "Main.scala", "final_risk": 1.0, "lines": "5-5"}])
    r = run_phase(2, json.loads(sp.read_text()), sp)
    assert _check(r, "high-risk coverage").status == STATUS_OK
