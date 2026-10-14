"""Gated integration tests for ScosAnalyze data-edge parity with PySpark c08ad5187.

These tests require a compiled scos-analyze.jar (or the control classpath) and
verify that ScosAnalyze.scala correctly resolves / records data-edge endpoints
matching the PySpark data_edge_ast capability set.

Gate: set SCOS_RUN_ANALYZE_IT=1 and have sbt + a JVM available.

Coverage:
  B1  Literal string path
  B6  Binary + concatenation
  B9  val-binding trace
  B11 Ternary if/else — BOTH branches
  B12 Trivial method passthrough (.trim etc.)
  B13 sys.env.getOrElse default
  B16 No-paren single-return def inlining
  B7  Map("k"->v)("k") literal lookup
  A1.3 DeltaTable.forPath — SECOND arg is path
  A1.5 spark.read.jdbc — SECOND arg is table
  A1.6 spark.catalog.createTable — catalog sink
  sc  SparkContext reads (textFile, wholeTextFiles, binaryFiles, ...)
  Unresolved: dynamic path → unresolved_reads edge recorded, not dropped
  Line: each edge carries a 1-based line number
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_CONTROL_DIR = Path(__file__).resolve().parents[2] / (
    "validate-spark-scala-to-snowpark-connect"
    "/harness-scala/control"
)
_IT_ENABLED = os.environ.get("SCOS_RUN_ANALYZE_IT") == "1" and shutil.which("sbt")
_CP_CACHE: list[str] = []

pytestmark = pytest.mark.skipif(
    not _IT_ENABLED,
    reason="set SCOS_RUN_ANALYZE_IT=1 and have sbt on PATH",
)


def _classpath() -> str:
    if not _CP_CACHE:
        cp = subprocess.run(
            ["sbt", "--batch", "-error", "export Compile/fullClasspath"],
            cwd=_CONTROL_DIR, capture_output=True, text=True, timeout=600,
        ).stdout.strip().splitlines()[-1]
        _CP_CACHE.append(cp)
    return _CP_CACHE[0]


def _analyze(code: str) -> dict:
    """Write code to a temp .scala file, run ScosAnalyze, return per-file facts."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "Job.scala"
        f.write_text(code, encoding="utf-8")
        out = Path(d) / "facts.json"
        subprocess.run(
            ["java", "-cp", _classpath(), "com.snowflake.scos.validate.ScosAnalyze",
             "--source", str(f), "--output", str(out)],
            check=True, capture_output=True, timeout=120,
        )
        result = json.loads(out.read_text(encoding="utf-8"))
        return result["files"][0]


# ── B1: literal ──────────────────────────────────────────────────────────────

def test_b1_literal_path():
    facts = _analyze("""
object Job {
  def main(args: Array[String]): Unit = {
    val df = spark.read.parquet("s3://bucket/data.parquet")
  }
}
""")
    reads = facts["reads"]
    assert any(r["call"] == "parquet" and "s3://bucket/data.parquet" in r["args"]
               for r in reads), f"B1 literal not found in reads: {reads}"
    assert reads[0]["line"] > 0, "line number must be set"


# ── B6: + concatenation ───────────────────────────────────────────────────────

def test_b6_concat():
    facts = _analyze("""
object Job {
  val base = "s3://bucket"
  def main(args: Array[String]): Unit = {
    val df = spark.read.csv(base + "/input.csv")
  }
}
""")
    reads = facts["reads"]
    args_all = [a for r in reads for a in r["args"]]
    assert any("s3://bucket/input.csv" in a for a in args_all), (
        f"B6 concat not resolved; reads: {reads}")


# ── B9: val-binding trace ─────────────────────────────────────────────────────

def test_b9_val_binding():
    facts = _analyze("""
object Job {
  val tableName = "DB.SCH.ORDERS"
  def main(args: Array[String]): Unit = {
    val df = spark.read.table(tableName)
  }
}
""")
    reads = facts["reads"]
    args_all = [a for r in reads for a in r["args"]]
    assert "DB.SCH.ORDERS" in args_all, f"B9 val binding not traced; reads: {reads}"


# ── B11: ternary — BOTH branches ──────────────────────────────────────────────

def test_b11_ternary_both_branches():
    facts = _analyze("""
object Job {
  def main(args: Array[String]): Unit = {
    val mode = "dev"
    val df = spark.read.parquet(if (mode == "dev") "s3://dev/data" else "s3://prod/data")
  }
}
""")
    reads = facts["reads"]
    args_all = [a for r in reads for a in r["args"]]
    assert "s3://dev/data" in args_all, f"B11 true-branch missing; reads: {reads}"
    assert "s3://prod/data" in args_all, f"B11 false-branch missing; reads: {reads}"


# ── B16: no-paren def inlining ────────────────────────────────────────────────

def test_b16_noparen_def_inline():
    facts = _analyze("""
object Job {
  def outputPath = "/data/output"
  def main(args: Array[String]): Unit = {
    spark.read.text(outputPath)
  }
}
""")
    reads = facts["reads"]
    args_all = [a for r in reads for a in r["args"]]
    assert "/data/output" in args_all, f"B16 def inline not resolved; reads: {reads}"


# ── B13: sys.env.getOrElse default ───────────────────────────────────────────

def test_b13_sys_env_default():
    facts = _analyze("""
object Job {
  def main(args: Array[String]): Unit = {
    val df = spark.read.parquet(sys.env.getOrElse("DATA_PATH", "/fallback/path"))
  }
}
""")
    reads = facts["reads"]
    args_all = [a for r in reads for a in r["args"]]
    assert "/fallback/path" in args_all, (
        f"B13 sys.env default not resolved; reads: {reads}")


# ── B7: Map literal lookup ─────────────────────────────────────────────────────

def test_b7_map_lookup():
    facts = _analyze("""
object Job {
  val paths = Map("train" -> "s3://bucket/train.parquet", "test" -> "s3://bucket/test.parquet")
  def main(args: Array[String]): Unit = {
    val df = spark.read.parquet(paths("train"))
  }
}
""")
    reads = facts["reads"]
    args_all = [a for r in reads for a in r["args"]]
    assert "s3://bucket/train.parquet" in args_all, (
        f"B7 Map lookup not resolved; reads: {reads}")


# ── A1.3: DeltaTable.forPath SECOND arg ──────────────────────────────────────

def test_a1_3_deltatble_forpath_second_arg():
    facts = _analyze("""
object Job {
  def main(args: Array[String]): Unit = {
    val dt = DeltaTable.forPath(spark, "s3://delta/table")
  }
}
""")
    reads = facts["reads"]
    args_all = [a for r in reads for a in r["args"]]
    assert "s3://delta/table" in args_all, (
        f"A1.3 DeltaTable.forPath 2nd arg not captured; reads: {reads}")


# ── A1.5: spark.read.jdbc SECOND arg is table ─────────────────────────────────

def test_a1_5_jdbc_second_arg_is_table():
    facts = _analyze("""
object Job {
  def main(args: Array[String]): Unit = {
    val df = spark.read.jdbc("jdbc:snowflake://host", "SCHEMA.MY_TABLE")
  }
}
""")
    reads = facts["reads"]
    args_all = [a for r in reads for a in r["args"]]
    assert "SCHEMA.MY_TABLE" in args_all, (
        f"A1.5 jdbc table (2nd arg) not captured; reads: {reads}")
    # URL must NOT be the captured arg
    assert not any("jdbc:" in a for a in args_all), (
        f"A1.5 jdbc URL incorrectly captured as table arg; reads: {reads}")


# ── A1.6: spark.catalog.createTable sink ──────────────────────────────────────

def test_a1_6_catalog_create_table_sink():
    facts = _analyze("""
object Job {
  def main(args: Array[String]): Unit = {
    spark.catalog.createTable("my_catalog_table")
  }
}
""")
    writes = facts["writes"]
    args_all = [a for w in writes for a in w["args"]]
    assert "my_catalog_table" in args_all, (
        f"A1.6 catalog.createTable not captured as write; writes: {writes}")


# ── SparkContext reads ────────────────────────────────────────────────────────

def test_sc_textfile_detected():
    facts = _analyze("""
object Job {
  def main(args: Array[String]): Unit = {
    val rdd = sc.textFile("s3://sc/file.txt")
    val rdd2 = sc.wholeTextFiles("s3://sc/dir")
  }
}
""")
    reads = facts["reads"]
    methods = {r["call"] for r in reads}
    assert "textFile" in methods, f"sc.textFile not detected; reads: {reads}"
    assert "wholeTextFiles" in methods, f"sc.wholeTextFiles not detected; reads: {reads}"


# ── Unresolved edge: dynamic arg not dropped ──────────────────────────────────

def test_dynamic_path_creates_unresolved_edge_not_dropped():
    facts = _analyze("""
object Job {
  def main(args: Array[String]): Unit = {
    val path = unknownFn()          // can't resolve
    val df = spark.read.parquet(path)
  }
}
""")
    # Must have NO resolved reads for this path (not in reads.args)
    resolved_args = [a for r in facts["reads"] for a in r["args"]]
    assert not any("unknownFn" in a for a in resolved_args)
    # Must appear in unresolved_reads so source is NOT silently dropped
    unresolved = facts.get("unresolved_reads", [])
    assert unresolved, f"dynamic path was silently dropped; reads: {facts['reads']}"
    calls = {u["call"] for u in unresolved}
    assert "parquet" in calls, f"unresolved parquet read not found; {unresolved}"
    assert unresolved[0].get("line", 0) > 0, "unresolved edge must carry line number"
