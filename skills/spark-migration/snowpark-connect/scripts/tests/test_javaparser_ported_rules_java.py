"""Tests for the 19 Scalafix rules ported to JavaParser (SNOW-3715354).

Two tiers:

* **Static guards** (always run, no JDK/Maven needed) — every ported rule must be
  registered in all THREE places that must stay in sync, and every EWI code the
  ported rules emit must exist in ``data/java/ewi_code_mapping.csv``:
    1. ``preprocess_javaparser.JAVA_RULES``     (the Python driver's rule list)
    2. ``ScosJavaRewrite.ALL_RULES``            (the Java canonical list)
    3. the ``applyRule`` switch                 (actual dispatch)
  A rule missing from (3) silently no-ops — the driver would run it, the switch
  would hit ``default``, and the file would come back unchanged with no error.

* **Behavioral integration** (gated on ``SCOS_RUN_JAVAPARSER_IT=1`` + ``mvn``) —
  builds the fat-jar and asserts on the actual rewritten code.

  Assertions target concrete CODE (``spark.read().text(``), never the explanatory
  comment text: every rule's comment mentions the API it rewrites, so matching the
  comment passes even when the rewrite never happened.

Run from snowpark-connect/:
    uv run --project . python -m pytest scripts/tests/test_javaparser_ported_rules_java.py -v
    SCOS_RUN_JAVAPARSER_IT=1 uv run --project . python -m pytest \
        scripts/tests/test_javaparser_ported_rules_java.py -v
"""
from __future__ import annotations

import csv
import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
_REWRITE_SRC = (
    _SCRIPTS_DIR / "javaparser_rules" / "com" / "snowflake" / "scos" / "javaparser"
    / "ScosJavaRewrite.java"
)
_POM = _SCRIPTS_DIR / "javaparser_maven" / "pom.xml"
_JAR = _SCRIPTS_DIR / "javaparser_maven" / "target" / "scos-javaparser-runner.jar"
_EWI_CSV = _SCRIPTS_DIR / "data" / "java" / "ewi_code_mapping.csv"
_MAIN_CLASS = "com.snowflake.scos.javaparser.ScosJavaRewrite"

_spec = importlib.util.spec_from_file_location(
    "preprocess_javaparser", _SCRIPTS_DIR / "preprocess_javaparser.py"
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
JAVA_RULES = _mod.JAVA_RULES

# The 19 rules ported from SCOSRules.scala.
PORTED_RULES = [
    "ScosApproxCountDistinctDropRsd",
    "ScosDbUtilsSecretsGetStub",
    "ScosDbUtilsWidgetsToProperty",
    "ScosDeltaWriteToParquet",
    "ScosDisplayToShow",
    "ScosDisplayMethodToShow",
    "ScosHadoopConfCredentialAnnotate",
    "ScosPartitionNoopStrip",
    "ScosRddExclusiveMethodAnnotate",
    "ScosRddImportAnnotate",
    "ScosRddPersistToCache",
    "ScosScTextfileToReadText",
    "ScosScWholeTextFilesAnnotate",
    "ScosSnowflakeConnectorIO",
    "ScosSparkConfigNoopAnnotate",
    "ScosSparkContextGetOrCreateRewrite",
    "ScosSparkContextNoopCommentOut",
    "ScosSparkIoDetectAnnotate",
    "ScosUnpersistDropBlockingArg",
]

# Deliberately NOT ported — Scala-only language/API features.
NOT_PORTED_RULES = ["ScosSqlContextImplicitsRewrite", "ScosScRangeToSparkRange"]


# --- static guards ----------------------------------------------------------


@pytest.mark.parametrize("rule", PORTED_RULES)
def test_ported_rule_in_driver_list(rule: str):
    assert rule in JAVA_RULES, f"{rule} missing from preprocess_javaparser.JAVA_RULES"


@pytest.mark.parametrize("rule", PORTED_RULES)
def test_ported_rule_in_all_rules(rule: str):
    src = _REWRITE_SRC.read_text(encoding="utf-8")
    all_rules_block = src.split("ALL_RULES", 1)[1].split(");", 1)[0]
    assert f'"{rule}"' in all_rules_block, f"{rule} missing from ScosJavaRewrite.ALL_RULES"


@pytest.mark.parametrize("rule", PORTED_RULES)
def test_ported_rule_dispatched(rule: str):
    """A rule absent from the applyRule switch silently no-ops."""
    src = _REWRITE_SRC.read_text(encoding="utf-8")
    assert f'case "{rule}":' in src, f"{rule} has no applyRule switch case"


def test_rule_lists_are_identical():
    """JAVA_RULES and ALL_RULES must agree exactly, in the same order."""
    src = _REWRITE_SRC.read_text(encoding="utf-8")
    all_rules_block = src.split("ALL_RULES", 1)[1].split(");", 1)[0]
    java_side = re.findall(r'"(Scos\w+)"', all_rules_block)
    assert java_side == list(JAVA_RULES), (
        "preprocess_javaparser.JAVA_RULES and ScosJavaRewrite.ALL_RULES diverged:\n"
        f"  python: {JAVA_RULES}\n  java:   {java_side}"
    )


@pytest.mark.parametrize("rule", NOT_PORTED_RULES)
def test_scala_only_rules_not_registered(rule: str):
    """Guard against someone 'completing' parity with a rule Java cannot express."""
    assert rule not in JAVA_RULES
    assert rule not in _REWRITE_SRC.read_text(encoding="utf-8").split("ALL_RULES", 1)[1] \
        .split(");", 1)[0]


def test_emitted_ewi_codes_are_mapped():
    """Every SPRKCNTSCL#### code emitted by the rules must exist in the Java CSV.

    ScosSparkIoDetectAnnotate introduced SPRKCNTSCL3200 / SPRKCNTSCL6000, which
    previously existed only in the Scala mapping — an unmapped code renders as a
    blank description in Issues.csv.
    """
    src = _REWRITE_SRC.read_text(encoding="utf-8")
    emitted = set(re.findall(r"SPRKCNTSCL(\d{4})", src))
    with _EWI_CSV.open(encoding="utf-8") as fh:
        mapped = {r["ewi_code"].replace("SPRKCNTSCL", "") for r in csv.DictReader(fh)}
    missing = sorted(emitted - mapped)
    assert not missing, f"EWI codes emitted but not in {_EWI_CSV.name}: {missing}"


def test_no_line_comment_replacement_for_statement_rules():
    """Statement-erasing rules must use block comments.

    Replacing a call expression with a ``//`` line comment swallows the trailing
    ``;`` and any same-line ``}``, producing uncompilable Java. The two rules that
    erase a call must emit ``/* ... */`` so the trailing ``;`` stays a valid empty
    statement.
    """
    src = _REWRITE_SRC.read_text(encoding="utf-8")
    for marker in ("ScosSparkContextNoopCommentOut", "dbutils.widgets."):
        idx = src.find(marker)
        assert idx != -1
    assert '"/* SCOS: [SPRKCNTSCL1500] ScosSparkContextNoopCommentOut:' in src
    assert '"/* SCOS: [SPRKCNTSCL1500] ScosDbUtilsWidgetsToProperty:' in src


# --- toolchain-gated behavioral integration ---------------------------------


_IT_ENABLED = os.environ.get("SCOS_RUN_JAVAPARSER_IT") == "1" and shutil.which("mvn")
_pytestmark_it = pytest.mark.skipif(
    not _IT_ENABLED, reason="set SCOS_RUN_JAVAPARSER_IT=1 and have mvn on PATH"
)


@pytest.fixture(scope="module")
def jar() -> str:
    if not _JAR.exists():
        subprocess.run(
            ["mvn", "-q", "-f", str(_POM), "package", "-DskipTests"],
            check=True, capture_output=True, text=True, timeout=900,
        )
    assert _JAR.exists(), f"fat-jar not built at {_JAR}"
    return str(_JAR)


def _run_rule(jar_path: str, source: str, rule: str, tmp_path: Path,
              name: str = "Fix.java") -> str:
    f = tmp_path / name
    f.write_text(source, encoding="utf-8")
    proc = subprocess.run(
        ["java", "-cp", jar_path, _MAIN_CLASS,
         "--source", str(f), "--rule", rule, "--stdout"],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


_FIXTURE = """import org.apache.spark.rdd.RDD;
import org.apache.spark.api.java.JavaRDD;
public class Fix {
  void m(SparkSession spark, Dataset<Row> df) {
    Dataset<Row> a = df.select(functions.approxCountDistinct(col("x"), 0.05));
    Dataset<Row> keep = df.select(functions.coalesce(col("a"), col("b")));
    Dataset<Row> b = df.repartition(10).coalesce(2);
    df.rdd().persist(StorageLevel.MEMORY_ONLY());
    df.unpersist(true);
    df.display();
    display(df);
    spark.conf().set("spark.executor.memory", "4g");
    spark.sparkContext().hadoopConfiguration().set("fs.s3a.access.key", "k");
    sc.textFile("/p/in.txt");
    sc.wholeTextFiles("/p/dir");
    sc.stop();
    df.groupByKey();
    df.write().format("delta").save("/out");
  }
}
"""


@_pytestmark_it
@pytest.mark.parametrize("rule,expected", [
    # (rule, concrete code that must appear after the rewrite)
    ("ScosApproxCountDistinctDropRsd", 'functions.approxCountDistinct(col("x"))'),
    ("ScosRddPersistToCache", "df.persist(StorageLevel.MEMORY_ONLY())"),
    ("ScosUnpersistDropBlockingArg", "df.unpersist()"),
    ("ScosDisplayMethodToShow", "df.show()"),
    ("ScosScTextfileToReadText", 'spark.read().text("/p/in.txt")'),
    ("ScosDeltaWriteToParquet", 'df.write().format("parquet").save("/out")'),
])
def test_rewrite_produces_expected_code(jar, tmp_path, rule, expected):
    out = _run_rule(jar, _FIXTURE, rule, tmp_path)
    code_only = "\n".join(
        ln for ln in out.splitlines() if not ln.strip().startswith("//")
    )
    assert expected in code_only, f"{rule} did not produce {expected!r}"


@_pytestmark_it
def test_partition_noop_strip_collapses_chain_and_keeps_column_fn(jar, tmp_path):
    """Chained no-ops must collapse in ONE edit, and functions.coalesce must survive.

    Emitting one edit per no-op call produced overlapping RangeEdits on
    df.repartition(10).coalesce(2) and corrupted the output.
    """
    out = _run_rule(jar, _FIXTURE, "ScosPartitionNoopStrip", tmp_path)
    code_only = "\n".join(
        ln for ln in out.splitlines() if not ln.strip().startswith("//")
    )
    assert 'functions.coalesce(col("a"), col("b"))' in code_only, \
        "column function functions.coalesce() must NOT be stripped"
    assert ".repartition(10)" not in code_only
    assert ".coalesce(2)" not in code_only
    # The chain must collapse to the bare receiver.
    assert re.search(r"^\s*df;\s*$", code_only, re.M), \
        "df.repartition(10).coalesce(2) should collapse to `df;`"
    # Overlapping RangeEdits used to splice the comment INTO the code, yielding
    # `df.repartition(10)PartitionNoopStrip: removed ...`. Assert that exact shape
    # is absent rather than the rule name (which legitimately appears in comments).
    assert ")PartitionNoopStrip" not in out, "overlapping edits corrupted the output"


@_pytestmark_it
def test_display_global_rewritten_method_form_untouched(jar, tmp_path):
    out = _run_rule(jar, _FIXTURE, "ScosDisplayToShow", tmp_path)
    code_only = "\n".join(
        ln for ln in out.splitlines() if not ln.strip().startswith("//")
    )
    assert "df.show()" in code_only, "bare display(df) should become df.show()"
    assert "df.display()" in code_only, \
        "the zero-arg METHOD form is owned by ScosDisplayMethodToShow, not this rule"


@_pytestmark_it
def test_statement_erasing_rules_stay_compilable(jar, tmp_path):
    """sc.stop(); and dbutils.widgets.remove(); must leave a valid empty statement."""
    out = _run_rule(jar, _FIXTURE, "ScosSparkContextNoopCommentOut", tmp_path)
    assert "*/;" in out, "block comment must be followed by the original ';'"
    assert "sc.stop()" not in out.replace("sc.stop() is a no-op", "")


@_pytestmark_it
def test_snowflake_connector_read_and_write_rewrite(jar, tmp_path):
    src = """public class Fix {
  void m(SparkSession spark, Dataset<Row> df) {
    Dataset<Row> r = spark.read().format("snowflake").option("query", "SELECT 1").load();
    df.write().format("snowflake").option("dbtable", "T").mode("append").save();
  }
}
"""
    out = _run_rule(jar, src, "ScosSnowflakeConnectorIO", tmp_path)
    code_only = "\n".join(
        ln for ln in out.splitlines() if not ln.strip().startswith("//")
    )
    assert 'new SnowflakeSession(spark).sql("SELECT 1")' in code_only
    # format/option dropped, .mode() preserved, table name carried over.
    assert 'df.write().mode("append").saveAsTable("T")' in code_only
    # Phase 3 needs the import marker to inject SnowflakeSession.
    assert "SCOS-RECIPE-INSERT-IMPORT: com.snowflake.snowpark_connect.client.SnowflakeSession" in out


@_pytestmark_it
def test_io_detect_codes_and_exclusions(jar, tmp_path):
    src = """public class Fix {
  void m(SparkSession spark, Dataset<Row> df) {
    Dataset<Row> j = spark.read().format("jdbc").option("url", "u").load();
    Dataset<Row> t = spark.read().table("db.sch.tbl");
    df.write().insertInto("db.sch.out");
    Dataset<Row> s = spark.read().format("snowflake").option("query", "Q").load();
  }
}
"""
    out = _run_rule(jar, src, "ScosSparkIoDetectAnnotate", tmp_path)
    assert "SPRKCNTSCL6000-Error" in out, "JDBC must be flagged 6000"
    assert out.count("SPRKCNTSCL3200-IO") == 2, "table read + insertInto → two 3200s"
    # .format("snowflake") is owned by ScosSnowflakeConnectorIO, not this rule.
    assert out.count("SPRKCNTSCL") == 3


@_pytestmark_it
def test_sparkcontext_getorcreate_rewrite(jar, tmp_path):
    src = """public class Fix {
  void m() {
    SparkContext c = SparkContext.getOrCreate();
  }
}
"""
    out = _run_rule(jar, src, "ScosSparkContextGetOrCreateRewrite", tmp_path)
    code_only = "\n".join(
        ln for ln in out.splitlines() if not ln.strip().startswith("//")
    )
    assert "SnowparkConnectSession.builder().getOrCreate()" in code_only


@_pytestmark_it
def test_rdd_import_annotate_covers_java_only_imports(jar, tmp_path):
    """Java's JavaRDD/JavaSparkContext imports must be flagged, not just spark.rdd."""
    out = _run_rule(jar, _FIXTURE, "ScosRddImportAnnotate", tmp_path)
    assert out.count("ScosRddImportAnnotate") == 2, \
        "both org.apache.spark.rdd.RDD and api.java.JavaRDD imports must be annotated"


@_pytestmark_it
@pytest.mark.parametrize("rule", PORTED_RULES)
def test_every_ported_rule_is_dispatched_at_runtime(jar, tmp_path, rule):
    """An unregistered rule prints 'unknown rule' to stderr — catch that for real."""
    f = tmp_path / "Empty.java"
    f.write_text("public class Empty {}\n", encoding="utf-8")
    proc = subprocess.run(
        ["java", "-cp", jar, _MAIN_CLASS, "--source", str(f), "--rule", rule],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0
    assert "unknown rule" not in proc.stderr, f"{rule} is not wired into applyRule"
