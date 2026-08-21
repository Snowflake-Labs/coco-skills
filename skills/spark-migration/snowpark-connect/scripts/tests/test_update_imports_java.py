"""Tests for update_imports_java.py — the deterministic Phase-3 import-updater
for the SCOS Spark-Java migration skill (parity with update_imports_scala.py).

Coverage focuses on the Java-specific Phase-3 transforms added for SCOS parity:
  * session-init rename (SparkSession -> SnowparkConnectSession)
  * PARITY-3a: System.getenv -> System.getProperty
  * B3: io.delta.tables import removal
  * PARITY-3b: DeltaTable.forPath/forName/forUid/forAddress site annotation
    (regression: the assignment form ``DeltaTable dt = DeltaTable.forPath(...)``
    must be annotated, not only bare-statement calls)
  * B5: Delta session-extension config is not re-materialized
  * migration-header idempotency
"""
from __future__ import annotations

import update_imports_java as uij


# --- session init -----------------------------------------------------------


def test_session_init_renames_spark_session():
    src = (
        "import org.apache.spark.sql.SparkSession;\n"
        "public class Job {\n"
        '  static SparkSession spark = SparkSession.builder().appName("j").getOrCreate();\n'
        "}\n"
    )
    out, n = uij.replace_session_init(src, is_test=False)
    assert n >= 1
    assert "SnowparkConnectSession.builder()" in out
    assert uij._SCOS_IMPORT in out


# --- PARITY-3a: System.getenv -> System.getProperty -------------------------


def test_getenv_rewritten_to_getproperty():
    src = (
        "public class App {\n"
        '  String v = System.getenv("HOME");\n'
        "}\n"
    )
    out, _ = uij.transform_java_source(src, "App.java")
    assert "System.getProperty(" in out
    assert "System.getenv(" not in out


# --- B3: io.delta.tables import removal --------------------------------------


def test_delta_tables_import_removed():
    src = (
        "import io.delta.tables.DeltaTable;\n"
        "public class App {}\n"
    )
    out, n = uij.comment_unsupported_imports(src)
    assert "import io.delta.tables" not in out
    assert n >= 1


# --- PARITY-3b: DeltaTable annotation (regression) --------------------------


def test_delta_table_assignment_form_annotated():
    """Regression: the assignment form must be annotated, not just bare calls."""
    src = (
        "public class App {\n"
        '  void m() {\n'
        '    DeltaTable dt = DeltaTable.forPath(spark, "/t");\n'
        "  }\n"
        "}\n"
    )
    out = uij.annotate_delta_table_usages(src)
    assert "DeltaTable API not available" in out
    # annotation must appear on the line above the call
    lines = out.split("\n")
    call_idx = next(i for i, ln in enumerate(lines) if "DeltaTable.forPath" in ln)
    assert "DeltaTable API not available" in lines[call_idx - 1]


def test_delta_table_all_factory_forms_annotated():
    src = (
        "public class App {\n"
        '  DeltaTable a = DeltaTable.forPath(spark, "/t");\n'
        '  DeltaTable b = DeltaTable.forName("db.t");\n'
        "  Object c = DeltaTable.forUid(spark, uid);\n"
        "}\n"
    )
    out = uij.annotate_delta_table_usages(src)
    assert out.count("DeltaTable API not available") == 3


def test_delta_table_annotation_idempotent():
    src = (
        "public class App {\n"
        '  DeltaTable dt = DeltaTable.forName("db.t");\n'
        "}\n"
    )
    once = uij.annotate_delta_table_usages(src)
    twice = uij.annotate_delta_table_usages(once)
    assert once == twice
    assert twice.count("DeltaTable API not available") == 1


def test_transform_annotates_delta_when_import_present():
    src = (
        "import io.delta.tables.DeltaTable;\n"
        "public class App {\n"
        '  DeltaTable dt = DeltaTable.forPath(spark, "/t");\n'
        "}\n"
    )
    out, stats = uij.transform_java_source(src, "App.java")
    assert "import io.delta.tables" not in out
    assert "DeltaTable API not available" in out
    assert stats["delta_sites_annotated"] is True


def test_transform_annotates_delta_bare_prefix():
    """Regression: bare 'delta.tables' import (older/unshaded form) must
    also trigger call-site annotation (Fix 6 from reviewer feedback)."""
    src = (
        "import delta.tables.DeltaTable;\n"
        "public class App {\n"
        '  DeltaTable dt = DeltaTable.forName("db.t");\n'
        "}\n"
    )
    out, stats = uij.transform_java_source(src, "App.java")
    assert "import delta.tables" not in out
    assert "DeltaTable API not available" in out
    assert stats["delta_sites_annotated"] is True


# --- B5: Delta session-extension config is not re-materialized --------------


def test_delta_session_extension_config_skipped():
    src = (
        "// SCOS-RECIPE-PRESERVED-CONFIG: spark.sql.extensions=io.delta.sql.DeltaSparkSessionExtension\n"
        "// SCOS-RECIPE-PRESERVED-CONFIG: spark.app.name=myapp\n"
    )
    pairs = uij._collect_preserved_config(src)
    keys = {k for k, _ in pairs}
    assert "spark.sql.extensions" not in keys
    assert "spark.app.name" in keys


# --- header idempotency -----------------------------------------------------


def test_migration_header_idempotent():
    src = "public class App {}\n"
    once, added1 = uij.add_migration_header(src, "App.java")
    twice, added2 = uij.add_migration_header(once, "App.java")
    assert added1 is True
    assert added2 is False
    assert once == twice
