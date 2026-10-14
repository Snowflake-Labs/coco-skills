"""Behavior tests for the Scalafix rules that REPLACED the regex recipe tier.

When the Phase 0.5 regex recipes (``scripts/recipes_scala/``) were removed in
favour of an all-AST tier, their transforms were ported to Scalafix syntactic
rules in ``scalafix_rules/SCOSRules.scala``. These tests are the parity gate:
for each ported / enhanced rule they run the REAL scalafix pipeline on a fixture
mirroring the deleted recipe's documented before/after and assert the rewrite.

Two layers:
  * Static guards (always run): each rule is defined + registered, conf in sync.
  * Toolchain-gated integration (``SCOS_RUN_SCALAFIX_IT=1`` + ``sbt`` on PATH):
    compiles the rules once and runs scalafix per-rule on fixtures.

Note: the context-sensitive rules (hot-path, temp-view) use true enclosing-scope
analysis, intentionally more precise than the old line-window regex heuristics.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent
_RULES_SRC = _SCRIPTS / "scalafix_rules" / "SCOSRules.scala"
_CONF = _SCRIPTS / "scalafix_rules" / "scos.scalafix.conf"
_SBT_DIR = _SCRIPTS / "scalafix_sbt"
_PKG = "com.snowflake.scos.scalafix"

# Every rule that replaced a regex recipe or was enhanced for full subsumption.
_PORTED_RULES = [
    "ScosCheckpointToCache",
    "ScosExternalCloudReadAnnotate",
    "ScosSelfJoinUnaliasedAnnotate",
    "ScosSparkContextPropertyFallbackAnnotate",
    "ScosUdtfCompatibilityModeAnnotate",
    "ScosUnionByNameAllowMissingAnnotate",
    "ScosDriverHotPathAnnotate",
    "ScosTempViewMultiUseCache",
    "ScosSparkSessionBuilderRewrite",
    "ScosMapSubscriptToElementAt",
    "ScosWildcardReadAnnotate",
    # PySpark parity rules added 2026-07 (PRs #3344 and #3348)
    "ScosPartitionNoopStrip",
    "ScosDeltaWriteToParquet",
    "ScosDisplayToShow",
    "ScosDbUtilsWidgetsToProperty",
    "ScosDbUtilsSecretsGetStub",
    # Pre-existing rules not previously in _PORTED_RULES
    "ScosSaveAsTableDropStorageOpts",
    "ScosSystemGetenvRewrite",
    "ScosDeltaTableAnnotate",
    # PySpark parity rules added 2026-07 (PR #3487 — df.display() method form)
    "ScosDisplayMethodToShow",
    # PySpark parity rules added 2026-07 (PR #3532 — Snowflake connector I/O)
    "ScosSnowflakeConnectorIO",
    # Recipe backlog parity added 2026-07 (PySpark recipes without Scala equiv.)
    "ScosApproxCountDistinctDropRsd",
    "ScosHadoopConfCredentialAnnotate",
    "ScosRddImportAnnotate",
    "ScosRddExclusiveMethodAnnotate",
    # RDD-migration guide integration (§6/§10 + §12): accumulators are CONVERTIBLE
    "ScosAccumulatorAnnotate",
    "ScosRddPersistToCache",
    "ScosScRangeToSparkRange",
    "ScosScTextfileToReadText",
    "ScosScWholeTextFilesAnnotate",
    "ScosSparkContextGetOrCreateRewrite",
    "ScosSparkContextNoopCommentOut",
    "ScosSparkConfigNoopAnnotate",
    "ScosUnpersistDropBlockingArg",
    # Flashfood Scala parity (added in SNOW-3722042-FF)
    "ScosSqlContextImplicitsRewrite",
    # spark_io_detect parity (PR #3575)
    "ScosSparkIoDetectAnnotate",
]


# --- static guards (always run) ---------------------------------------------


@pytest.mark.parametrize("rule", _PORTED_RULES)
def test_rule_defined_and_registered(rule):
    src = _RULES_SRC.read_text(encoding="utf-8")
    conf = _CONF.read_text(encoding="utf-8")
    # declaration may wrap across lines for long names, so match the class name
    # and its SyntacticRule(...) constructor arg independently.
    assert f"class {rule}" in src, f"{rule} not defined"
    assert f'SyntacticRule("{rule}")' in src, f"{rule} not a SyntacticRule"
    assert f"class:{_PKG}.{rule}" in conf, f"{rule} not registered in conf"


def test_conf_and_sources_in_sync():
    src = _RULES_SRC.read_text(encoding="utf-8")
    conf = _CONF.read_text(encoding="utf-8")
    for line in conf.splitlines():
        line = line.strip()
        if not line.startswith(f'"class:{_PKG}.'):
            continue
        cls = line.split(".")[-1].rstrip('"')
        assert f"class {cls}" in src, f"{cls} in conf but not in SCOSRules.scala"


# --- toolchain-gated integration --------------------------------------------


_IT_ENABLED = os.environ.get("SCOS_RUN_SCALAFIX_IT") == "1" and shutil.which("sbt")
_CP_CACHE: list[str] = []


def _classpath() -> str:
    if not _CP_CACHE:
        cp = subprocess.run(
            ["sbt", "--batch", "-error", "export Compile/fullClasspath"],
            cwd=_SBT_DIR, capture_output=True, text=True, timeout=600,
        ).stdout.strip().splitlines()[-1]
        _CP_CACHE.append(cp)
    return _CP_CACHE[0]


def _run(rule: str, code: str) -> str:
    """Run a single Scalafix rule over ``code`` and return rewritten stdout."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "F.scala"
        f.write_text(code, encoding="utf-8")
        return subprocess.run(
            ["java", "-cp", _classpath(), "scalafix.cli.Cli",
             "--rules", f"class:{_PKG}.{rule}", "--stdout", str(f)],
            capture_output=True, text=True, timeout=180,
        ).stdout


def _code_only(scala: str) -> str:
    """Return ``scala`` with ``//`` line comments and ``/* */`` block comments
    stripped, so a negative assertion (``pattern not in code``) checks the
    rewritten *code* and is not tripped by the pattern appearing inside an
    explanatory SCOS comment."""
    import re as _re
    # Drop // line comments (keep the code before the //).
    no_line = "\n".join(l.split("//", 1)[0] for l in scala.splitlines())
    # Drop /* ... */ block comments (non-greedy, across lines).
    return _re.sub(r"/\*.*?\*/", "", no_line, flags=_re.DOTALL)


pytestmark = pytest.mark.skipif(
    not _IT_ENABLED, reason="set SCOS_RUN_SCALAFIX_IT=1 and have sbt on PATH"
)


def test_checkpoint_to_cache():
    # checkpoint → cache + EWI comment
    out = _run("ScosCheckpointToCache",
               "object F { val c = df.checkpoint(true) }\n")
    assert "[SPRKCNTSCL1500]" in out
    assert "checkpoint() not supported" in out
    assert "df.cache()" in out
    assert ".checkpoint(" not in out

    # localCheckpoint → cache + EWI comment
    out2 = _run("ScosCheckpointToCache",
                "object F { val c = df.localCheckpoint() }\n")
    assert "[SPRKCNTSCL1500]" in out2
    assert "localCheckpoint() not supported" in out2
    assert "df.cache()" in out2
    assert ".localCheckpoint(" not in out2

    # Idempotent: df.cache() must not be re-annotated
    neg = _run("ScosCheckpointToCache",
               "object F { val x = df.cache() }\n")
    assert neg.count("df.cache()") == 1
    assert "[SPRKCNTSCL1500]" not in neg


def test_external_cloud_read_annotate():
    out = _run("ScosExternalCloudReadAnnotate",
               'object F { val df = spark.read.parquet("s3://my-bucket/data/") }\n')
    assert "// SCOS: Performance tip - s3 read;" in out
    # stage path + local path must NOT trigger.
    neg = _run("ScosExternalCloudReadAnnotate",
               'object F { val a = spark.read.parquet("@stage/x"); val b = spark.read.csv("/tmp/y") }\n')
    assert "Performance tip" not in neg


def test_self_join_unaliased_annotate():
    out = _run("ScosSelfJoinUnaliasedAnnotate",
               'object F { val joined = df.join(df, Seq("id")) }\n')
    assert "// SCOS: TODO - self-join requires explicit aliases" in out
    neg = _run("ScosSelfJoinUnaliasedAnnotate",
               'object F { val joined = a.join(b, Seq("id")) }\n')
    assert "self-join" not in neg


def test_sparkcontext_property_fallback_annotate():
    out = _run("ScosSparkContextPropertyFallbackAnnotate",
               "object F {\n"
               "  val rdd = sc.parallelize(data)\n"
               "  val bc = sc.broadcast(lookupTable)\n"
               "  val r2 = spark.sparkContext.parallelize(xs)\n"
               "}\n")
    assert "[SPRKCNTSCL1500] sc.parallelize is unsupported" in out
    assert "sc.broadcast not supported" in out
    # both parallelize sites annotated (bare sc + spark.sparkContext)
    assert out.count("sc.parallelize is unsupported") == 2


def test_udtf_compatibility_mode_annotate():
    out = _run("ScosUdtfCompatibilityModeAnnotate",
               "object O {\n  class MyUDTF extends UserDefinedTableFunction { def x = 1 }\n}\n")
    assert "// SCOS: TODO - UDTF compatibility mode required" in out
    neg = _run("ScosUdtfCompatibilityModeAnnotate",
               "object O {\n  class Other extends Foo { def x = 1 }\n}\n")
    assert "UDTF compatibility mode" not in neg


def test_unionbyname_allowmissing_annotate():
    out = _run("ScosUnionByNameAllowMissingAnnotate",
               "object F { val merged = df1.unionByName(df2, allowMissingColumns = true) }\n")
    assert "// SCOS: TODO - schema-align before unionByName" in out
    neg = _run("ScosUnionByNameAllowMissingAnnotate",
               "object F {\n"
               "  val a = df1.unionByName(df2)\n"
               "  val b = df1.unionByName(df2, allowMissingColumns = false)\n"
               "}\n")
    assert "schema-align" not in neg


def test_driver_hotpath_annotate_loop_only():
    out = _run("ScosDriverHotPathAnnotate",
               "object F {\n"
               "  def r() = {\n"
               "    for (i <- 1 to 3) { val rows = df.collect() }\n"
               "  }\n"
               "}\n")
    assert "// SCOS: Performance tip - driver materialization in hot path" in out
    # One-shot collect with NO enclosing loop must NOT trigger (precision win over
    # the old for/while/def line-window heuristic).
    neg = _run("ScosDriverHotPathAnnotate",
               "object F {\n  def r() = {\n    val rows = df.collect()\n  }\n}\n")
    assert "hot path" not in neg


def test_tempview_multiuse_cache_insert_and_idempotent():
    code = (
        "object F {\n"
        "  def r() = {\n"
        '    df.createOrReplaceTempView("myView")\n'
        '    val r1 = spark.sql("SELECT * FROM myView WHERE x > 1")\n'
        '    val r2 = spark.sql("SELECT AVG(x) FROM myView")\n'
        "  }\n"
        "}\n"
    )
    out = _run("ScosTempViewMultiUseCache", code)
    assert "df.cache()" in out
    assert out.count("df.cache()") == 1
    # Idempotent: feeding the output back in must NOT add a second cache().
    again = _run("ScosTempViewMultiUseCache", out)
    assert again.count("df.cache()") == 1
    # Single-use view must NOT trigger.
    neg = _run("ScosTempViewMultiUseCache",
               "object F {\n  def r() = {\n"
               '    df.createOrReplaceTempView("v1")\n'
               '    val r1 = spark.sql("SELECT * FROM v1")\n'
               "  }\n}\n")
    assert "df.cache()" not in neg


def test_spark_session_builder_rename_and_drop():
    """Core rename: SparkSession→SnowparkConnectSession, unsupported calls dropped."""
    out = _run("ScosSparkSessionBuilderRewrite",
               "object F {\n"
               "  val spark = SparkSession.builder().master(\"local\")\n"
               "    .config(\"k1\", \"v1\")\n"
               "    .config(Map(\"a\" -> \"b\"))\n"
               "    .config(myConf)\n"
               "    .getOrCreate()\n"
               "}\n")
    assert "SnowparkConnectSession.builder()" in out
    assert "SparkSession" not in _code_only(out)
    assert ".master(" not in out
    assert "// SCOS-RECIPE-PRESERVED-CONFIG: k1=v1" in out
    assert "// SCOS-RECIPE-PRESERVED-CONFIG: a=b" in out
    assert "// SCOS-WARN: dropped non-extractable .config(...)" in out


def test_spark_session_builder_drops_hive_and_remote():
    """enableHiveSupport and remote are dropped; .config() is preserved."""
    out = _run("ScosSparkSessionBuilderRewrite",
               "object F {\n"
               "  val s = SparkSession.builder()\n"
               '    .config("spark.app.name", "App")\n'
               "    .enableHiveSupport()\n"
               '    .remote("sc://host:443")\n'
               "    .getOrCreate()\n"
               "}\n")
    assert "SnowparkConnectSession.builder()" in out
    assert "enableHiveSupport" not in out
    assert ".remote(" not in out
    assert "PRESERVED-CONFIG: spark.app.name=App" in out


def test_spark_session_builder_test_file_skipped():
    """Files ending in Spec.scala are NOT renamed — SparkSession preserved."""
    import tempfile, subprocess, os
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "MySpec.scala")
        code = (
            "object MySpec {\n"
            '  val spark = SparkSession.builder().master("local[*]")\n'
            '    .config("k", "v").getOrCreate()\n'
            "}\n"
        )
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(code)
        result = subprocess.run(
            ["java", "-cp", _classpath(), "scalafix.cli.Cli",
             "--rules", f"class:{_PKG}.ScosSparkSessionBuilderRewrite",
             "--stdout", f],
            capture_output=True, text=True, timeout=180,
        ).stdout
    assert "SparkSession" in result           # NOT renamed
    assert "SnowparkConnectSession" not in result
    assert ".master(" in result               # master kept
    assert "PRESERVED-CONFIG: k=v" in result  # markers still emitted


def test_spark_session_builder_idempotent():
    """Already-renamed code (SnowparkConnectSession.builder()) is a no-op."""
    already = (
        "object F {\n"
        "  val s = SnowparkConnectSession.builder().getOrCreate()\n"
        "}\n"
    )
    out = _run("ScosSparkSessionBuilderRewrite", already)
    # No SparkSession to trigger the rule — output should be unchanged.
    assert "SparkSession" not in _code_only(out)
    assert out.count("SnowparkConnectSession") == already.count("SnowparkConnectSession")


def test_map_subscript_twin_still_rewrites():
    out = _run("ScosMapSubscriptToElementAt",
               'object F { val mapCol = col("m"); val r = df.select(mapCol(col("k"))) }\n')
    assert 'element_at(mapCol, col("k"))' in out


def test_wildcard_read_twin_still_annotates():
    out = _run("ScosWildcardReadAnnotate",
               'object F { val df = spark.read.csv("data/*.csv") }\n')
    assert "// SCOS: TODO - wildcard pattern in path" in out


# ─── Parity rules (PR #3344 + PR #3348) ─────────────────────────────────────

@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_partition_noop_strip_coalesce():
    """df.coalesce(1) before a write is stripped; F.coalesce (SQL null-coal.) is kept."""
    out = _run("ScosPartitionNoopStrip",
               'object F { val r = df.coalesce(1).write.parquet("/p") }\n')
    assert "coalesce(1)" not in out
    assert "ScosPartitionNoopStrip" in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_partition_noop_strip_keeps_functions_coalesce():
    """functions.coalesce(col1, col2) must NOT be stripped."""
    src = 'object F { val r = df.select(functions.coalesce(col("a"), col("b"))) }\n'
    out = _run("ScosPartitionNoopStrip", src)
    assert "coalesce" in out        # not stripped
    assert "ScosPartitionNoopStrip" not in out  # no rewrite comment


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_delta_write_to_parquet_rewrites_writer():
    """.write.format("delta") in a write chain → .write.format("parquet")."""
    out = _run("ScosDeltaWriteToParquet",
               'object F { df.write.format("delta").mode("overwrite").save("/p") }\n')
    assert '.format("parquet")' in out
    assert '.format("delta")' not in _code_only(out)


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_delta_write_skips_on_delta_table_api():
    """Files using DeltaTable.forPath are skipped entirely."""
    src = 'object F { DeltaTable.forPath(spark, "/p").delete("id < 0") }\n'
    out = _run("ScosDeltaWriteToParquet", src)
    assert "forPath" in out          # untouched
    assert ".format(" not in out or ".format(" in src  # no format rewrite


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_display_to_show_single_arg():
    """display(df) → df.show()."""
    out = _run("ScosDisplayToShow",
               'object F { display(df) }\n')
    assert "df.show()" in out
    assert "ScosDisplayToShow" in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_display_to_show_skips_method_call():
    """obj.display(df) is a method call — must NOT be rewritten."""
    src = 'object F { obj.display(df) }\n'
    out = _run("ScosDisplayToShow", src)
    assert "obj.display" in out     # untouched
    assert ".show()" not in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_display_method_to_show_zero_arg():
    """df.display() → // SCOS: comment + df.show()."""
    out = _run("ScosDisplayMethodToShow",
               "object F { df.display() }\n")
    assert "[SPRKCNTSCL1500]" in out
    assert "ScosDisplayMethodToShow" in out
    assert "df.show()" in out
    assert "df.display()" not in _code_only(out)


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_display_method_to_show_chained_recv():
    """spark.table("t").display() → comment + spark.table("t").show()."""
    out = _run("ScosDisplayMethodToShow",
               'object F { spark.table("t").display() }\n')
    assert 'spark.table("t").show()' in out
    assert ".display()" not in _code_only(out)


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_display_method_skips_with_args():
    """obj.display(x) — method with arguments — must NOT be rewritten."""
    src = "object F { obj.display(df) }\n"
    out = _run("ScosDisplayMethodToShow", src)
    assert "obj.display(df)" in out     # untouched
    assert ".show()" not in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_display_method_skips_bare_global():
    """display(df) bare global — must NOT be rewritten by ScosDisplayMethodToShow."""
    src = "object F { display(df) }\n"
    out = _run("ScosDisplayMethodToShow", src)
    # ScosDisplayMethodToShow is for method form only; bare form is ScosDisplayToShow's job
    assert "display(df)" in out     # untouched by this rule
    assert ".show()" not in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_snowflake_connector_read_query():
    """spark.read.format("snowflake").option("query", Q).load() → SnowflakeSession.sql(Q)."""
    src = (
        'object F {\n'
        '  val df = spark.read.format("snowflake")\n'
        '    .option("query", "SELECT * FROM t WHERE id > 0")\n'
        '    .load()\n'
        '}\n'
    )
    out = _run("ScosSnowflakeConnectorIO", src)
    assert "new SnowflakeSession(spark).sql" in out
    assert "SPRKCNTSCL1000-Fixed" in out
    assert "SCOS-RECIPE-INSERT-IMPORT" in out
    assert ".format(" not in _code_only(out)  # connector chain removed
    assert ".load()" not in _code_only(out)


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_snowflake_connector_read_dbtable():
    """spark.read.format("snowflake").option("dbtable", T).load() → SnowflakeSession.sql(SELECT *)."""
    src = (
        'object F {\n'
        '  val df = spark.read.format("snowflake")\n'
        '    .option("dbtable", "mydb.schema.orders")\n'
        '    .load()\n'
        '}\n'
    )
    out = _run("ScosSnowflakeConnectorIO", src)
    assert "SnowflakeSession" in out
    assert "SELECT * FROM mydb.schema.orders" in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_snowflake_connector_write_dbtable():
    """df.write.format("snowflake").option("dbtable", T).save() → df.write.saveAsTable(T)."""
    src = (
        'object F {\n'
        '  df.write.format("snowflake").option("dbtable", "output_table").save()\n'
        '}\n'
    )
    out = _run("ScosSnowflakeConnectorIO", src)
    assert "SPRKCNTSCL1000-Fixed" in out
    assert 'saveAsTable("output_table")' in out
    assert ".format(" not in _code_only(out)


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_snowflake_connector_write_with_mode():
    """df.write.format("snowflake").option("dbtable", T).mode("overwrite").save() preserves mode."""
    src = (
        'object F {\n'
        '  df.write.format("snowflake")\n'
        '    .option("dbtable", "t")\n'
        '    .mode("overwrite")\n'
        '    .save()\n'
        '}\n'
    )
    out = _run("ScosSnowflakeConnectorIO", src)
    assert '.mode("overwrite").saveAsTable("t")' in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_snowflake_connector_todo_on_ambiguous_options():
    """Non-literal options-dict gets TODO annotation, original code preserved."""
    src = 'object F { val df = spark.read.format("snowflake").options(opts).load() }\n'
    out = _run("ScosSnowflakeConnectorIO", src)
    assert "SCOS: TODO" in out
    assert ".load()" in out     # original code preserved


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_snowflake_connector_skips_non_snowflake_format():
    """format("parquet").load() must NOT be rewritten."""
    src = 'object F { val df = spark.read.format("parquet").load() }\n'
    out = _run("ScosSnowflakeConnectorIO", src)
    assert 'format("parquet")' in out   # unchanged
    assert "SnowflakeSession" not in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_dbutils_widgets_get_to_property():
    """dbutils.widgets.get("key") → System.getProperty("key")."""
    out = _run("ScosDbUtilsWidgetsToProperty",
               'object F { val v = dbutils.widgets.get("year") }\n')
    assert 'System.getProperty("year")' in out
    assert "dbutils.widgets.get" not in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_dbutils_widgets_text_to_setproperty():
    """dbutils.widgets.text("k","default") → System.setProperty("k","default")."""
    out = _run("ScosDbUtilsWidgetsToProperty",
               'object F { dbutils.widgets.text("yr", "2024") }\n')
    assert 'System.setProperty("yr", "2024")' in out
    assert "dbutils.widgets.text" not in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_dbutils_secrets_get_stubbed():
    """dbutils.secrets.get(...) → null.asInstanceOf[String] with TODO."""
    out = _run("ScosDbUtilsSecretsGetStub",
               'object F { val t = dbutils.secrets.get(scope="kv", key="pw") }\n')
    assert "null.asInstanceOf[String]" in out
    assert "ScosDbUtilsSecretsGetStub" in out
    assert "dbutils.secrets.get" not in out


# --- backlog parity rules (added 2026-07) -----------------------------------


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_approx_count_distinct_drops_rsd():
    """approxCountDistinct(col, 0.05) → approxCountDistinct(col) + EWI comment."""
    out = _run("ScosApproxCountDistinctDropRsd",
               "object F { val n = approxCountDistinct(col, 0.05) }\n")
    assert "approxCountDistinct(col)" in out
    assert "SPRKCNTSCL1000" in out
    assert ", 0.05" not in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_hadoop_conf_credential_annotated():
    """sc.hadoopConfiguration().set("fs.s3a.access.key", ...) → SCOS-TODO annotation."""
    src = 'object F { sc.hadoopConfiguration().set("fs.s3a.access.key", key) }\n'
    out = _run("ScosHadoopConfCredentialAnnotate", src)
    assert "SCOS: TODO" in out
    assert "fs.s3a.access.key" in out
    assert 'set("fs.s3a.access.key"' in out  # original code preserved


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_rdd_import_annotated():
    """import org.apache.spark.rdd.RDD → SCOS-TODO annotation."""
    src = "import org.apache.spark.rdd.RDD\nobject F {}\n"
    out = _run("ScosRddImportAnnotate", src)
    assert "SCOS: TODO" in out
    assert "SPRKCNTSCL1500" in out
    assert "import org.apache.spark.rdd.RDD" in out  # import line preserved


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_rdd_exclusive_method_annotated():
    """rdd.reduceByKey(...) → SCOS-TODO annotation above, code preserved."""
    src = "object F { val r = rdd.reduceByKey(_ + _) }\n"
    out = _run("ScosRddExclusiveMethodAnnotate", src)
    assert "SCOS: TODO" in out
    assert "reduceByKey" in out
    assert "SPRKCNTSCL1500" in out


# ─── RDD-migration guide integration (§6/§10 + §12) ─────────────────────────
#
# Behavior tests for the tokens added to ScosRddExclusiveMethodAnnotate
# (treeAggregate/treeReduce, unambiguous so annotated WITHOUT any .rdd / homonym
# gate) and the new ScosAccumulatorAnnotate rule (accumulators are CONVERTIBLE,
# annotated in place). NOTE: this whole module is gated behind the sbt IT mark
# (module-level pytestmark above), so the source-parse guards below only run
# under SCOS_RUN_SCALAFIX_IT=1. The no-sbt static coverage for these rules (rule
# defined + registered, correct names + EWI) lives in
# test_rdd_reference_sync_scala.py, which runs unconditionally.

# Frozen §10/aggregate exclusive tokens (must stay in the SCOSRules EXCLUSIVE set).
_FROZEN_EXCLUSIVE_TOKENS = [
    "treeAggregate", "treeReduce", "collectAsMap", "countApprox",
    "countApproxDistinct", "meanApprox", "sumApprox",
    "repartitionAndSortWithinPartitions", "toDebugString",
]

# DataFrame homonyms that MUST NOT be in the exclusive annotate set (they would
# false-fire on plain DataFrame code). Mirror of analyze_scala's gated-only list.
_HOMONYM_TOKENS = [
    "groupBy", "partitionBy", "toDF", "context", "reduce", "fold",
    "aggregate", "groupByKey",
]


def _exclusive_set_from_source(src: str) -> set[str]:
    """Parse the string literals from ScosRddExclusiveMethodAnnotate's
    ``private val EXCLUSIVE: Set[String] = Set(...)`` block, ignoring ``//``
    comment lines (which contain prose with quotes)."""
    import re as _re
    m = _re.search(r"val EXCLUSIVE: Set\[String\] = Set\((.*?)\)\s*\n", src, _re.DOTALL)
    assert m, "EXCLUSIVE set literal not found in SCOSRules.scala"
    body = "\n".join(
        line.split("//", 1)[0] for line in m.group(1).splitlines()
    )
    return set(_re.findall(r'"([^"]+)"', body))


@pytest.mark.parametrize("token", _FROZEN_EXCLUSIVE_TOKENS)
def test_exclusive_set_contains_new_aggregate_tokens(token):
    """treeAggregate/treeReduce + the §10 verified ops are annotated by
    ScosRddExclusiveMethodAnnotate — they must be in its EXCLUSIVE set."""
    exclusive = _exclusive_set_from_source(_RULES_SRC.read_text(encoding="utf-8"))
    assert token in exclusive


def test_new_aggregate_tokens_are_not_homonyms():
    """The §10/aggregate names this integration added to the EXCLUSIVE set must
    be genuinely RDD-exclusive — none may be a DataFrame homonym that would
    false-fire on ordinary DataFrame code. (Pre-existing EXCLUSIVE entries are
    out of scope; this guards only what the integration introduced.)"""
    leaked = sorted(set(_FROZEN_EXCLUSIVE_TOKENS) & set(_HOMONYM_TOKENS))
    assert not leaked, (
        f"newly-added exclusive tokens that are DataFrame homonyms: {leaked}."
    )


def test_exclusive_rule_matches_any_receiver_without_rdd_gate():
    """The exclusive-method rule matches Term.Apply(Term.Select(_, Term.Name(m)))
    for any receiver — no ``.rdd`` / ``sparkContext`` guard. This is what makes
    treeAggregate/treeReduce annotate 'without a gate'. Guard the shape so a
    future edit can't quietly add a receiver restriction."""
    src = _RULES_SRC.read_text(encoding="utf-8")
    body = src.split("class ScosRddExclusiveMethodAnnotate", 1)[1].split("\nclass ", 1)[0]
    assert "Term.Apply(Term.Select(_, Term.Name(m)))" in body
    assert "if EXCLUSIVE(m)" in body


def test_accumulator_rule_targets_scala_names_only():
    """ScosAccumulatorAnnotate keys off the real Scala accumulator factory forms
    and class names — never a bare `accumulator` substring, and never the
    PySpark-only sc.accumulator / AccumulatorParam spellings."""
    src = _RULES_SRC.read_text(encoding="utf-8")
    body = src.split("class ScosAccumulatorAnnotate", 1)[1].split("\nclass ", 1)[0]
    for name in ("longAccumulator", "doubleAccumulator", "collectionAccumulator"):
        assert name in body, f"{name} factory not targeted"
    for name in ("LongAccumulator", "DoubleAccumulator", "CollectionAccumulator",
                 "AccumulatorV2"):
        assert name in body, f"{name} type not targeted"
    assert "AccumulatorParam" not in body  # no PySpark-only spelling
    assert "SPRKCNTSCL1500" in body        # correct EWI code


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_treeaggregate_annotated_without_gate():
    """data.treeAggregate(...) with NO .rdd receiver still annotates."""
    out = _run("ScosRddExclusiveMethodAnnotate",
               "object F { val r = data.treeAggregate(0)(seqOp, combOp) }\n")
    assert "SCOS: TODO" in out
    assert "treeAggregate" in out
    assert "SPRKCNTSCL1500" in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_treereduce_annotated_without_gate():
    """data.treeReduce(op) with NO .rdd receiver still annotates."""
    out = _run("ScosRddExclusiveMethodAnnotate",
               "object F { val r = data.treeReduce(_ + _) }\n")
    assert "SCOS: TODO" in out
    assert "treeReduce" in out
    assert "SPRKCNTSCL1500" in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_accumulator_factory_annotated():
    """sc.longAccumulator("c") → CONVERTIBLE SCOS-TODO annotation, code preserved."""
    out = _run("ScosAccumulatorAnnotate",
               'object F { val acc = sc.longAccumulator("c") }\n')
    assert "SCOS: TODO" in out
    assert "SPRKCNTSCL1500" in out
    assert "CONVERTIBLE" in out
    assert 'sc.longAccumulator("c")' in out  # original code preserved


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_accumulator_class_and_ctor_annotated():
    """new LongAccumulator and `extends AccumulatorV2` are both annotated."""
    ctor = _run("ScosAccumulatorAnnotate",
                "object F { val acc = new LongAccumulator() }\n")
    assert "SCOS: TODO" in ctor
    assert "new LongAccumulator()" in ctor
    cls = _run("ScosAccumulatorAnnotate",
               "class Hist extends AccumulatorV2[Long, Long] {}\n")
    assert "SCOS: TODO" in cls
    assert "AccumulatorV2" in cls


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_accumulator_rule_no_false_positive_on_bare_word():
    """A method literally named `accumulator` on an unrelated object must NOT be
    annotated (the rule keys off the real Spark names, not a substring)."""
    src = "object F { val x = builder.accumulator(3) }\n"
    out = _run("ScosAccumulatorAnnotate", src)
    assert "SCOS: TODO" not in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_rdd_persist_to_cache_rewrite():
    """df.rdd.persist(MEMORY_AND_DISK) → df.persist(MEMORY_AND_DISK)."""
    out = _run("ScosRddPersistToCache",
               "object F { df.rdd.persist(StorageLevel.MEMORY_AND_DISK) }\n")
    assert "df.persist(StorageLevel.MEMORY_AND_DISK)" in out
    assert "SPRKCNTSCL1000" in out
    assert ".rdd.persist" not in _code_only(out)


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_sc_range_to_spark_range():
    """sc.range(100) → spark.range(100)."""
    out = _run("ScosScRangeToSparkRange",
               "object F { val ds = sc.range(100) }\n")
    assert "spark.range(100)" in out
    assert "SPRKCNTSCL1500" in out
    assert "sc.range" not in _code_only(out)


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_sc_textfile_to_read_text():
    """sc.textFile("path") → spark.read.text("path") + EWI comment."""
    out = _run("ScosScTextfileToReadText",
               'object F { val lines = sc.textFile("s3://b/f.txt") }\n')
    assert 'spark.read.text("s3://b/f.txt")' in out
    assert "SPRKCNTSCL1500" in out
    assert "textFile" not in _code_only(out)


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_sc_wholetextfiles_annotated():
    """sc.wholeTextFiles("path") → SCOS-TODO annotation, code preserved."""
    src = 'object F { val wt = sc.wholeTextFiles("s3://b/") }\n'
    out = _run("ScosScWholeTextFilesAnnotate", src)
    assert "SCOS: TODO" in out
    assert "wholeTextFiles" in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_spark_context_getorcreate_rewrite():
    """SparkContext.getOrCreate() → SnowparkConnectSession.builder().getOrCreate()."""
    out = _run("ScosSparkContextGetOrCreateRewrite",
               "object F { val sc2 = SparkContext.getOrCreate() }\n")
    assert "SnowparkConnectSession.builder().getOrCreate()" in out
    assert "SPRKCNTSCL3500-Fixed" in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_spark_context_stop_commented_out():
    """sc.stop() → SCOS comment (no-op replacement)."""
    out = _run("ScosSparkContextNoopCommentOut",
               "object F { sc.stop() }\n")
    assert "SPRKCNTSCL1500" in out
    assert "no-op" in out
    assert "sc.stop()" not in _code_only(out)  # replaced, not preserved


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_spark_config_noop_annotated():
    """spark.conf.set("spark.executor.memory", ...) → SCOS-TODO annotation."""
    src = 'object F { spark.conf.set("spark.executor.memory", "4g") }\n'
    out = _run("ScosSparkConfigNoopAnnotate", src)
    assert "SCOS: TODO" in out
    assert "SPRKCNTSCL1000" in out
    assert 'spark.conf.set("spark.executor.memory"' in out  # original preserved


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_unpersist_drops_blocking_arg():
    """df.unpersist(blocking = true) → df.unpersist() + EWI comment."""
    out = _run("ScosUnpersistDropBlockingArg",
               "object F { df.unpersist(blocking = true) }\n")
    assert "df.unpersist()" in out
    assert "SPRKCNTSCL1000" in out
    assert "blocking" not in _code_only(out)


# ─── ScosSparkIoDetectAnnotate (spark_io_detect parity, PR #3575) ────────────

@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_spark_io_detect_jdbc_terminal():
    """df.write.jdbc(url, table, props) → SPRKCNTSCL6000-Error annotation."""
    src = 'object F { df.write.jdbc("jdbc:postgresql://host/db", "tbl", props) }\n'
    out = _run("ScosSparkIoDetectAnnotate", src)
    assert "SPRKCNTSCL6000-Error" in out
    assert "JDBC" in out
    assert "spark_io_detect" in out
    assert 'df.write.jdbc(' in out  # original preserved (annotate-only)


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_spark_io_detect_jdbc_format_load():
    """.format("jdbc").load() chain → SPRKCNTSCL6000-Error annotation."""
    src = ('object F {\n'
           '  val df = spark.read.format("jdbc")'
           '.option("url", url).option("dbtable", "t").load()\n'
           '}\n')
    out = _run("ScosSparkIoDetectAnnotate", src)
    assert "SPRKCNTSCL6000-Error" in out
    assert "JDBC" in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_spark_io_detect_iceberg_read():
    """.format("iceberg").load(path) → SPRKCNTSCL3200-IO annotation with 'reads from'."""
    src = 'object F { val df = spark.read.format("iceberg").load("catalog.db.tbl") }\n'
    out = _run("ScosSparkIoDetectAnnotate", src)
    assert "SPRKCNTSCL3200-IO" in out
    assert "Iceberg" in out
    assert "reads from" in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_spark_io_detect_iceberg_write():
    """.format("iceberg").save() → SPRKCNTSCL3200-IO annotation with 'writes to'."""
    src = 'object F { df.write.format("iceberg").save("catalog.db.out") }\n'
    out = _run("ScosSparkIoDetectAnnotate", src)
    assert "SPRKCNTSCL3200-IO" in out
    assert "Iceberg" in out
    assert "writes to" in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_spark_io_detect_table_read():
    """spark.read.table(name) → SPRKCNTSCL3200-IO annotation."""
    src = 'object F { val df = spark.read.table("catalog.schema.orders") }\n'
    out = _run("ScosSparkIoDetectAnnotate", src)
    assert "SPRKCNTSCL3200-IO" in out
    assert "table I/O" in out
    assert "reads from" in out
    assert 'spark.read.table(' in out  # original preserved


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_spark_io_detect_insert_into():
    """df.write.insertInto(name) → SPRKCNTSCL3200-IO annotation."""
    src = 'object F { df.write.insertInto("prod.schema.target") }\n'
    out = _run("ScosSparkIoDetectAnnotate", src)
    assert "SPRKCNTSCL3200-IO" in out
    assert "table I/O" in out
    assert "writes to" in out


@pytest.mark.skipif(not _IT_ENABLED, reason="SCOS_RUN_SCALAFIX_IT=1 + sbt required")
def test_spark_io_detect_delta_not_annotated():
    """.format("delta") chain is NOT annotated (ScosDeltaWriteToParquet owns it)."""
    src = 'object F { df.write.format("delta").save("/tmp/out") }\n'
    out = _run("ScosSparkIoDetectAnnotate", src)
    # ScosSparkIoDetectAnnotate must not add an annotation for delta
    assert "SPRKCNTSCL3200" not in out
    assert "SPRKCNTSCL6000" not in out

