"""Tests for update_imports_scala.py — the deterministic Phase-3 import-updater
(Row G parity with the PySpark update_imports.py).

Coverage:
  * Unit tests for each transform (session-init, imports, header, build files).
  * An end-to-end test that builds a synthetic migrated project, runs the
    script's main(), then runs ``verify_phase.run_phase(3, …)`` and asserts the
    Phase-3 gate PASSES (no FAIL checks) — the real contract.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import update_imports_scala as uis
from verify_phase import run_phase, STATUS_FAIL


# --- session init -----------------------------------------------------------


def test_session_init_renames_and_drops_hive_master():
    src = (
        "package com.x\n"
        "import org.apache.spark.sql.SparkSession\n"
        "object Job {\n"
        '  val spark = SparkSession.builder().appName("j").master("yarn").enableHiveSupport().getOrCreate()\n'
        "}\n"
    )
    out, n = uis.replace_session_init(src, is_test=False)
    assert n == 1
    assert "SnowparkConnectSession.builder()" in out
    assert "enableHiveSupport" not in out
    assert '.master("yarn")' not in out
    assert uis._SCOS_IMPORT in out


def test_session_init_materializes_preserved_config():
    src = (
        '// SCOS-RECIPE-PRESERVED-CONFIG: spark.sql.shuffle.partitions=200\n'
        '// SCOS-RECIPE-PRESERVED-CONFIG: spark.sql.session.timeZone=UTC\n'
        'val spark = SparkSession.builder().getOrCreate()\n'
    )
    out, _ = uis.replace_session_init(src, is_test=False)
    assert '.config("spark.sql.shuffle.partitions", "200")' in out
    assert '.config("spark.sql.session.timeZone", "UTC")' in out


def test_session_init_skips_hive_only_config_keys():
    src = (
        '// SCOS-RECIPE-PRESERVED-CONFIG: hive.metastore.uris=thrift://x\n'
        'val spark = SparkSession.builder().getOrCreate()\n'
    )
    out, _ = uis.replace_session_init(src, is_test=False)
    # The marker comment still mentions the hive key, but no .config() is created.
    assert '.config("hive.metastore.uris"' not in out


def test_session_init_test_file_kept_local():
    src = 'val spark = SparkSession.builder().master("local[*]").getOrCreate()\n'
    out, n = uis.replace_session_init(src, is_test=True)
    assert n == 0
    assert "SnowparkConnectSession.builder" not in out  # not converted (TODO text aside)
    assert 'master("local[*]")' in out
    assert "SCOS: TODO" in out


def test_session_init_multiline_builder_is_converted():
    # Idiomatic Scala fluent style splits the object from `.builder` across lines.
    # A substring `"SparkSession.builder"` check misses this and leaves the
    # entry-point session unconverted (regression observed on a real workload).
    src = (
        "package com.x\n"
        "import org.apache.spark.sql.SparkSession\n"
        "object ETL {\n"
        "  lazy val spark: SparkSession = SparkSession\n"
        "    .builder()\n"
        '    .appName("PETL")\n'
        "    .getOrCreate()\n"
        "}\n"
    )
    out, n = uis.replace_session_init(src, is_test=False)
    assert n == 1
    assert "SnowparkConnectSession" in out
    # The receiver object is renamed; the type annotation `: SparkSession` stays.
    assert "lazy val spark: SparkSession = SnowparkConnectSession" in out
    assert ".builder()" in out
    assert uis._SCOS_IMPORT in out


def test_session_init_multiline_preserved_config_materialized():
    # Multi-line builder + preserved-config markers: config must still be appended
    # onto the (whitespace-tolerant) builder factory call.
    src = (
        "// SCOS-RECIPE-PRESERVED-CONFIG: spark.sql.session.timeZone=UTC\n"
        "val spark = SparkSession\n"
        "  .builder()\n"
        "  .getOrCreate()\n"
    )
    out, _ = uis.replace_session_init(src, is_test=False)
    assert "SnowparkConnectSession" in out
    assert '.config("spark.sql.session.timeZone", "UTC")' in out


def test_session_init_phase05_already_renamed_materializes_config():
    """Phase 0.5 already renamed SparkSession → SnowparkConnectSession.
    Phase 3 must still materialize PRESERVED-CONFIG markers and inject the import.
    """
    src = (
        "// SCOS-RECIPE-PRESERVED-CONFIG: spark.sql.session.timeZone=UTC\n"
        "// SCOS-RECIPE-PRESERVED-CONFIG: spark.app.name=App\n"
        "object Job {\n"
        "  val spark = SnowparkConnectSession.builder().getOrCreate()\n"
        "}\n"
    )
    out, n = uis.replace_session_init(src, is_test=False)
    assert n == 1
    assert '.config("spark.sql.session.timeZone", "UTC")' in out
    assert '.config("spark.app.name", "App")' in out
    assert uis._SCOS_IMPORT in out
    # SparkSession rename steps must NOT have added a duplicate .builder
    assert out.count("builder") == 1


def test_session_init_phase05_already_renamed_no_extra_drops():
    """When Phase 0.5 already renamed, Phase 3 must not re-run _drop_call.
    The source has no SparkSession — only SnowparkConnectSession — so only
    config materialization and import injection should happen.
    """
    src = (
        "// SCOS-RECIPE-PRESERVED-CONFIG: spark.shuffle.partitions=200\n"
        "object Job {\n"
        "  val spark = SnowparkConnectSession.builder().getOrCreate()\n"
        "}\n"
    )
    out, n = uis.replace_session_init(src, is_test=False)
    assert n == 1
    assert '.config("spark.shuffle.partitions", "200")' in out
    assert "SparkSession" not in out  # no spurious rename artefact


# --- unsupported imports ----------------------------------------------------


def test_remove_unsupported_imports():
    src = (
        "import org.apache.spark.sql.functions._\n"
        "import org.apache.hadoop.fs.Path\n"
        "import org.apache.spark.sql.hive.HiveContext\n"
        "import za.co.absa.spline.harvester.SparkLineageInitializer\n"
        "object X\n"
    )
    out, n = uis.comment_unsupported_imports(src)
    assert n == 3
    assert "org.apache.spark.sql.functions._" in out  # supported import kept
    assert "org.apache.hadoop" not in out
    assert "spline" not in out


# --- header -----------------------------------------------------------------


def test_header_added_and_idempotent():
    src = "object X\n"
    out, added = uis.add_migration_header(src, "X.scala")
    assert added and "SCOS Migration Output" in out
    out2, added2 = uis.add_migration_header(out, "X.scala")
    assert added2 is False and out2 == out


# --- build files ------------------------------------------------------------


def test_sbt_transform():
    sbt = (
        'scalaVersion := "2.12.18"\n'
        'libraryDependencies += "org.apache.spark" %% "spark-hive" % "3.3.0"\n'
        'libraryDependencies += "org.apache.spark" %% "spark-connect-client-jvm" % "3.5.0"\n'
    )
    out, changed = uis.transform_build_file("build.sbt", sbt)
    assert changed
    assert "snowpark-connect-java-client" in out
    assert "spark-connect-client-jvm" not in out
    assert "spark-hive" not in out
    assert "add-opens" in out
    assert "1.0.0" in out  # pinned concrete version, not a floating dynamic keyword


def test_gradle_transform():
    g = (
        'dependencies {\n'
        '    implementation "org.apache.spark:spark-connect-client-jvm:3.5.0"\n'
        '    implementation "org.apache.spark:spark-hive_2.11:3.3.0"\n'
        '}\n'
    )
    out, changed = uis.transform_build_file("build.gradle", g)
    assert changed
    assert "snowpark-connect-java-client" in out
    assert "spark-connect-client-jvm" not in out
    assert "spark-hive" not in out
    assert "add-opens" in out


def test_gradle_kotlin_transform():
    out, changed = uis.transform_build_file("build.gradle.kts", "dependencies {\n}\n")
    assert changed and "snowpark-connect-java-client" in out and "add-opens" in out


# --- end-to-end: the Phase-3 gate must PASS ---------------------------------


def _no_fail(report) -> list[str]:
    return [f"{c.name}: {c.detail}" for c in report.checks if c.status == STATUS_FAIL]


def test_end_to_end_phase3_gate_passes(tmp_path):
    conv = tmp_path / "Conversion-SCOS-test"
    out = conv / "Output"
    out.mkdir(parents=True)
    (out / "Job.scala").write_text(
        "package com.x\n"
        "import org.apache.spark.sql.SparkSession\n"
        "import org.apache.hadoop.fs.Path\n"
        '// SCOS-RECIPE-PRESERVED-CONFIG: spark.sql.shuffle.partitions=200\n'
        "object Job extends App {\n"
        '  val spark = SparkSession.builder().appName("j").enableHiveSupport().getOrCreate()\n'
        "}\n",
        encoding="utf-8",
    )
    (out / "build.sbt").write_text(
        'scalaVersion := "2.12.18"\n'
        'libraryDependencies += "org.apache.spark" %% "spark-connect-client-jvm" % "3.5.0"\n',
        encoding="utf-8",
    )
    state = {
        "conversion_root": str(conv),
        "migrated_dir": str(out),
        "manifest": ["Job.scala"],
        "build_files": ["build.sbt"],
    }
    sp = conv / "migration_state.json"
    sp.write_text(json.dumps(state), encoding="utf-8")

    rc = uis.main(["--state", str(sp)])
    assert rc == 0

    report = run_phase(3, json.loads(sp.read_text()), sp)
    fails = _no_fail(report)
    assert not fails, f"Phase 3 gate FAILED: {fails}"


# --- process_insert_import_markers ------------------------------------------


def test_insert_import_marker_injects_import():
    src = (
        "package com.x\n"
        "import org.apache.spark.sql.SparkSession\n"
        "// SCOS-RECIPE-INSERT-IMPORT: com.snowflake.snowpark_connect.client.SnowflakeSession\n"
        "object F {\n"
        "  val sf = new SnowflakeSession(spark)\n"
        "}\n"
    )
    out, n = uis.process_insert_import_markers(src)
    assert n == 1
    assert "import com.snowflake.snowpark_connect.client.SnowflakeSession" in out
    assert "SCOS-RECIPE-INSERT-IMPORT" not in out


def test_insert_import_marker_deduplicates():
    src = (
        "package com.x\n"
        "// SCOS-RECIPE-INSERT-IMPORT: com.snowflake.snowpark_connect.client.SnowflakeSession\n"
        "// SCOS-RECIPE-INSERT-IMPORT: com.snowflake.snowpark_connect.client.SnowflakeSession\n"
        "object F {}\n"
    )
    out, n = uis.process_insert_import_markers(src)
    assert n == 1  # deduplicated
    assert out.count("import com.snowflake.snowpark_connect.client.SnowflakeSession") == 1


def test_insert_import_marker_skips_if_already_imported():
    src = (
        "package com.x\n"
        "import com.snowflake.snowpark_connect.client.SnowflakeSession\n"
        "// SCOS-RECIPE-INSERT-IMPORT: com.snowflake.snowpark_connect.client.SnowflakeSession\n"
        "object F {}\n"
    )
    out, n = uis.process_insert_import_markers(src)
    assert n == 1  # marker consumed
    assert out.count("import com.snowflake.snowpark_connect.client.SnowflakeSession") == 1  # no dupe


def test_insert_import_marker_no_op_when_absent():
    src = "package com.x\nobject F {}\n"
    out, n = uis.process_insert_import_markers(src)
    assert n == 0
    assert out == src
