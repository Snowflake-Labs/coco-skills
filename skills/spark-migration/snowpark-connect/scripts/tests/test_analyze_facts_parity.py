"""Parity tests for the AST-facts detection path (Job 2).

These guarantee the facts-backed structural detectors produce the SAME
``scos_issues`` as the regex detectors they replace — so swapping detection onto
Scalameta AST facts changes precision (no comment/string false positives) WITHOUT
changing the analyzer's verdicts on real code.

Two layers:
  * Always-run: hand-built facts (matching the extractor's output shape) vs the
    regex detectors on equivalent code — validates the mapping/rule-table reuse.
  * Toolchain-gated: the REAL extractor on fixtures vs the regex path — proves
    end-to-end parity. Skipped unless SCOS_RUN_SCALAFIX_IT=1 and sbt present.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import analyze_scala as A


def _norm(issue: dict) -> tuple:
    """Comparable identity of a structural issue (order-independent)."""
    return (
        issue.get("category"),
        issue.get("api") or issue.get("format"),
        round(float(issue.get("risk", 0)), 3),
        issue.get("ewi_code"),
        bool(issue.get("decidable")),
    )


def _regex_structural(code: str) -> set:
    """The regex detectors the facts path replaces (imports/formats/df-apis/noop/udf)."""
    issues = (
        A.check_unsupported_imports_scala(code)
        + A.check_unsupported_formats_scala(code)
        + A.check_noop_apis_scala(code)
        + A.check_udf_patterns_scala(code)
        + A.check_unsupported_df_apis_scala(code)
    )
    return {_norm(i) for i in issues}


def _facts_structural(facts: dict) -> set:
    return {_norm(i) for i in A.check_scos_issues_from_facts(facts)}


# --- always-run: hand-built facts vs regex (validates the mapping) ----------

# (label, scala code, facts as the extractor would emit them on that code)
_CASES = [
    ("unsupported import",
     "import org.apache.hadoop.fs.Path\n",
     {"imports": [{"ref": "org.apache.hadoop.fs.Path", "line": 1}]}),
    ("hive import",
     "import org.apache.spark.sql.hive.HiveContext\n",
     {"imports": [{"ref": "org.apache.spark.sql.hive.HiveContext", "line": 1}]}),
    ("unsupported format avro",
     'val d = spark.read.format("avro").load("/p")\n',
     {"calls": [{"method": "format", "recv_leaf": "read", "args": ["avro"], "line": 1},
                {"method": "load", "recv_leaf": "format", "args": ["/p"], "line": 1}]}),
    ("unsupported df api checkpoint",
     "val c = df.checkpoint()\n",
     {"calls": [{"method": "checkpoint", "recv_leaf": "df", "args": [], "line": 1}]}),
    ("no-op hint",
     'val r = df.hint("broadcast")\n',
     {"calls": [{"method": "hint", "recv_leaf": "df", "args": ["broadcast"], "line": 1}]}),
    ("udf register",
     'spark.udf.register("f", fn)\n',
     {"calls": [{"method": "register", "recv_leaf": "udf", "args": ["f"], "line": 1}]}),
    ("clean / no structural issue",
     'val r = df.select("a").filter(col("a") > 1)\n',
     {"calls": [{"method": "select", "recv_leaf": "df", "args": ["a"], "line": 1},
                {"method": "filter", "recv_leaf": "select", "args": [], "line": 1}]}),
]


@pytest.mark.parametrize("label,code,facts", _CASES, ids=[c[0] for c in _CASES])
def test_facts_match_regex(label, code, facts):
    # normalize facts to the full extractor shape (missing keys -> empty lists)
    full = {"imports": [], "calls": [], "selects": [], "new_types": [], "spark_sql": []}
    full.update(facts)
    assert _facts_structural(full) == _regex_structural(code), (
        f"[{label}] facts-path issues diverge from regex-path"
    )


def test_rdd_facts_match_regex():
    # .rdd via a select fact; regex detects the same on equivalent code.
    facts = {"imports": [], "calls": [], "new_types": [],
             "selects": [{"member": "rdd", "recv_leaf": "df", "line": 1}], "spark_sql": []}
    assert A.has_rdd_usage_from_facts(facts)[0] is True
    assert A.has_rdd_usage("val r = df.rdd.map(x => x)")[0] is True


def test_rdd_facts_new_sparkcontext():
    facts = {"imports": [], "calls": [], "selects": [], "spark_sql": [],
             "new_types": [{"type": "SparkContext", "line": 1}]}
    assert A.has_rdd_usage_from_facts(facts)[0] is True


def test_facts_drop_comment_false_positive():
    # The precision win: a `.checkpoint()` mentioned only in a comment yields NO
    # AST call fact, so the facts path (correctly) does not flag it — whereas the
    # regex path would. This asserts the IMPROVEMENT, not a regression.
    facts = {"imports": [], "calls": [], "selects": [], "new_types": [], "spark_sql": []}
    assert _facts_structural(facts) == set()
    assert _regex_structural("// TODO: replace df.checkpoint() later\n") != set()


# --- toolchain-gated: REAL extractor vs regex (end-to-end parity) -----------

_IT = os.environ.get("SCOS_RUN_SCALAFIX_IT") == "1" and shutil.which("sbt")


@pytest.mark.skipif(not _IT, reason="set SCOS_RUN_SCALAFIX_IT=1 and have sbt on PATH")
@pytest.mark.parametrize("code", [
    'import org.apache.hadoop.fs.Path\nobject J { val d = spark.read.format("avro").load("/p") }\n',
    "object J { val c = df.checkpoint(); val r = df.rdd.map(x => x) }\n",
    'object J { val r = df.hint("broadcast"); spark.udf.register("f", fn) }\n',
    'object J { val r = df.select("a").filter(col("a") > 1) }\n',
])
def test_real_extractor_parity(tmp_path, code):
    import scala_ast_facts
    f = tmp_path / "J.scala"
    f.write_text(code, encoding="utf-8")
    facts_by_path = scala_ast_facts.extract_facts(f)
    assert facts_by_path is not None, "extractor returned None despite toolchain"
    file_facts = next(iter(facts_by_path.values()))
    # Whole-file comparison (single-construct fixtures => no block-scoping needed).
    assert _facts_structural(file_facts) == _regex_structural(code)
    # RDD parity too.
    assert A.has_rdd_usage_from_facts(file_facts)[0] == A.has_rdd_usage(code)[0]


@pytest.mark.skipif(not _IT, reason="set SCOS_RUN_SCALAFIX_IT=1 and have sbt on PATH")
def test_real_analyze_file_parity(tmp_path):
    """End-to-end: analyze_file with AST facts must produce the SAME findings as
    analyze_file with the regex path (file_facts=None) on the same source."""
    import scala_ast_facts

    class _StubRAG:
        def predict_failure(self, code):
            return {"similar_patterns": []}

    src = (
        "package x\n"
        "import org.apache.hadoop.fs.Path\n"
        "object Job extends App {\n"
        '  val df = spark.read.format("avro").load("/p")\n'
        "  val c = df.checkpoint()\n"
        '  val h = df.hint("broadcast")\n'
        "}\n"
    )
    f = tmp_path / "Job.scala"
    f.write_text(src, encoding="utf-8")
    facts = scala_ast_facts.extract_facts(f)
    assert facts is not None
    file_facts = next(iter(facts.values()))

    def summary(rows):
        return sorted((r.get("category"), round(float(r.get("final_risk", 0)), 2),
                       r.get("source")) for r in rows)

    facts_rows = A.analyze_file(_StubRAG(), f, risk_threshold=0.1, file_facts=file_facts)
    regex_rows = A.analyze_file(_StubRAG(), f, risk_threshold=0.1, file_facts=None)
    assert summary(facts_rows) == summary(regex_rows), (
        f"analyze_file diverges: facts={summary(facts_rows)} regex={summary(regex_rows)}"
    )


# --- behavioral-difference parity (Tier 1: call/member patterns) -------------
#
# Each migrated behavioral pattern must fire from AST call/member facts (or the
# SQL-string regex) for EXACTLY the same EWI set the regex path produces on the
# equivalent code. Hand-built facts mirror what ScosMigrateFacts emits; the
# gated cases below prove the real extractor agrees.

def _facts_behavioral(facts: dict, code: str) -> set:
    full = {"imports": [], "calls": [], "selects": [], "new_types": [], "spark_sql": []}
    full.update(facts)
    return {i["ewi_code"] for i in A.check_behavioral_from_facts(full, code)}


def _regex_behavioral(code: str) -> set:
    return {i["ewi_code"] for i in A.check_behavioral_differences_scala(code)}


def _calls(*methods, args=None):
    return [{"method": m, "recv_leaf": "x", "args": args or [], "line": 1} for m in methods]


# (label, scala code, facts) — one per migrated Tier-1 EWI, call form.
_BD_CASES = [
    ("5001 cast", 'val r = c.cast("int")', {"calls": _calls("cast", args=["int"])}),
    ("5002 datediff", "val r = datediff(a, b)", {"calls": _calls("datediff")}),
    ("5003 union", "val r = (df1).union(df2)", {"calls": _calls("union")}),
    ("5004 element_at", "val r = element_at(c, 1)", {"calls": _calls("element_at")}),
    ("5005 concat_ws", 'val r = concat_ws("-", a, b)', {"calls": _calls("concat_ws", args=["-"])}),
    ("5007 isnan", "val r = isnan(c)", {"calls": _calls("isnan")}),
    ("5008 regexp_replace", 'val r = regexp_replace(c, "a", "b")', {"calls": _calls("regexp_replace", args=["a", "b"])}),
    ("5009 greatest", "val r = greatest(a, b)", {"calls": _calls("greatest")}),
    ("5010 concat", "val r = concat(a, b)", {"calls": _calls("concat")}),
    ("5011 regexp_extract", 'val r = regexp_extract(c, "p", 1)', {"calls": _calls("regexp_extract", args=["p"])}),
    ("5012 first", "val r = first(c)", {"calls": _calls("first")}),
    ("5013 round", "val r = round(c, 2)", {"calls": _calls("round")}),
    ("5014 explode", "val r = explode(c)", {"calls": _calls("explode")}),
    ("5016 months_between", "val r = months_between(a, b)", {"calls": _calls("months_between")}),
    ("5019 split", 'val r = split(c, ",")', {"calls": _calls("split", args=[","])}),
    ("5025 approx_count_distinct", "val r = approx_count_distinct(c)", {"calls": _calls("approx_count_distinct")}),
    ("5026 date_format", 'val r = date_format(c, "yyyy")', {"calls": _calls("date_format", args=["yyyy"])}),
    ("5027 collect_list", "val r = collect_list(c)", {"calls": _calls("collect_list")}),
    ("5028 repartition", "val r = df.repartition(8)", {"calls": _calls("repartition")}),
    ("5023 groupBy", 'val r = df.groupBy("a")', {"calls": _calls("groupBy", args=["a"])}),
    ("5006 desc", 'val r = df.orderBy(col("x").desc)',
     {"calls": _calls("orderBy", "col"), "selects": [{"member": "desc", "recv_leaf": "col", "line": 1}]}),
]


@pytest.mark.parametrize("label,code,facts", _BD_CASES, ids=[c[0] for c in _BD_CASES])
def test_behavioral_facts_match_regex_call_form(label, code, facts):
    assert _facts_behavioral(facts, code) == _regex_behavioral(code), (
        f"[{label}] behavioral facts-path EWIs diverge from regex-path"
    )


# SQL-string form: the function lives inside spark.sql / selectExpr text, which
# the facts path scans with the same regex (parity with regex-over-code).
_BD_SQL_CASES = [
    ("datediff in spark.sql", 'val r = spark.sql("SELECT datediff(a,b) FROM t")',
     {"calls": _calls("sql", args=["SELECT datediff(a,b) FROM t"]),
      "spark_sql": [{"text": "SELECT datediff(a,b) FROM t", "line": 1}]}),
    ("regexp_extract in selectExpr", 'val r = df.selectExpr("regexp_extract(s, p, 1)")',
     {"calls": _calls("selectExpr", args=["regexp_extract(s, p, 1)"])}),
]


@pytest.mark.parametrize("label,code,facts", _BD_SQL_CASES, ids=[c[0] for c in _BD_SQL_CASES])
def test_behavioral_facts_match_regex_sql_form(label, code, facts):
    assert _facts_behavioral(facts, code) == _regex_behavioral(code), (
        f"[{label}] behavioral SQL-string facts-path EWIs diverge from regex-path"
    )


def _infix(op, lhs, rhs):
    return [{"op": op, "lhs": lhs, "rhs": rhs, "line": 1}]


# (label, code, facts) — one per migrated Tier-2 operator EWI.
_BD_INFIX_CASES = [
    ("5000 col/lit div", 'val r = col("a") / lit(0)',
     {"infix": _infix("/", 'col("a")', "lit(0)")}),
    ("5000 .divide", "val r = c.divide(lit(0))", {"calls": _calls("divide")}),
    ("5020 col/col int-div", 'val r = col("a") / col("b")',
     {"infix": _infix("/", 'col("a")', 'col("b")')}),
    ("5017 null-safe op", "val r = a <=> b", {"infix": _infix("<=>", "a", "b")}),
    ("5017 eqNullSafe", "val r = a.eqNullSafe(b)", {"calls": _calls("eqNullSafe")}),
    ("5015 === lit str", 'val r = col("a") === lit("x")',
     {"infix": _infix("===", 'col("a")', 'lit("x")')}),
]


@pytest.mark.parametrize("label,code,facts", _BD_INFIX_CASES, ids=[c[0] for c in _BD_INFIX_CASES])
def test_behavioral_facts_match_regex_infix_form(label, code, facts):
    assert _facts_behavioral(facts, code) == _regex_behavioral(code), (
        f"[{label}] behavioral infix facts-path EWIs diverge from regex-path"
    )


def _callx(method, arg_exprs):
    return [{"method": method, "recv_leaf": "x", "args": [], "arg_exprs": arg_exprs, "line": 1}]


# (label, code, facts) — arg-discriminated patterns reconstructed from arg facts.
_BD_SYNTH_CASES = [
    ("5021 cast \"boolean\"", 'val r = c.cast("boolean")', {"calls": _callx("cast", ['"boolean"'])}),
    ("5021 cast BooleanType", "val r = c.cast(BooleanType)", {"calls": _callx("cast", ["BooleanType"])}),
    ("5022 substring ,0", "val r = substring(s, 0, 3)", {"calls": _callx("substring", ["s", "0", "3"])}),
    ("5022 substr ,0", "val r = substr(s, 0)", {"calls": _callx("substr", ["s", "0"])}),
]


@pytest.mark.parametrize("label,code,facts", _BD_SYNTH_CASES, ids=[c[0] for c in _BD_SYNTH_CASES])
def test_behavioral_facts_match_regex_synth_form(label, code, facts):
    assert _facts_behavioral(facts, code) == _regex_behavioral(code), (
        f"[{label}] arg-discriminated facts-path EWIs diverge from regex-path"
    )


def test_behavioral_residual_patterns_match_regex():
    # Residual = bare type-name ref (5024 TimestampType) + negative-lookahead
    # chain context (5018 agg-without-alias). Both run via regex over code on
    # BOTH paths => identical by construction, matching how PySpark handles these
    # non-call patterns. Facts supplied as the extractor would emit them so any
    # co-firing migrated pattern (e.g. 5001 cast alongside 5024) is matched too.
    cases = [
        ("val r = c.cast(TimestampType)", {"calls": _callx("cast", ["TimestampType"])}),  # 5024 (+5001)
        ("val r = to_timestamp(c)", {"calls": _callx("to_timestamp", ["c"])}),            # 5024 call form
        ('val r = df.agg(sum(col("a")))',
         {"calls": _calls("agg", "sum", "col")}),                                          # 5018 agg-no-alias
    ]
    for code, facts in cases:
        assert _facts_behavioral(facts, code) == _regex_behavioral(code), code


def test_behavioral_facts_union_precision_gain():
    # The regex `(?<!\\w)\\.union\\(` MISSES `a.union(b)` (word char before dot);
    # the AST call fact catches it. Documented improvement, not a regression.
    assert "SPRKCNTSCL5003" not in _regex_behavioral("val r = a.union(b)")
    assert "SPRKCNTSCL5003" in _facts_behavioral({"calls": _calls("union")}, "val r = a.union(b)")


def test_behavioral_facts_drop_comment_false_positive():
    # `datediff(` only in a comment => no call fact, no spark.sql text => facts
    # path does NOT flag it, while the regex path (wrongly) would. Precision win.
    code = "// TODO: switch to datediff(a, b) later\nval r = df.select(col(\"a\"))\n"
    facts = {"calls": _calls("select", "col")}
    assert _facts_behavioral(facts, code) == set()
    assert "SPRKCNTSCL5002" in _regex_behavioral(code)


# --- Hive-DDL parity ---------------------------------------------------------

def _facts_hive(facts: dict, code: str) -> set:
    full = {"imports": [], "calls": [], "selects": [], "new_types": [], "spark_sql": []}
    full.update(facts)
    return {i["reason"] for i in A.check_hive_from_facts(full, code)}


def _regex_hive(code: str) -> set:
    return {i["reason"] for i in A.check_hive_ddl_patterns_scala(code)}


_HIVE_CASES = [
    ("msck repair", 'spark.sql("MSCK REPAIR TABLE t")',
     {"calls": [{"method": "sql", "recv_leaf": "spark", "args": ["MSCK REPAIR TABLE t"], "arg_exprs": ['"MSCK REPAIR TABLE t"'], "line": 1}],
      "spark_sql": [{"text": "MSCK REPAIR TABLE t", "line": 1}]}),
    ("alter recover", 'spark.sql("ALTER TABLE t RECOVER PARTITIONS")',
     {"spark_sql": [{"text": "ALTER TABLE t RECOVER PARTITIONS", "line": 1}]}),
    ("create table", 'spark.sql("CREATE EXTERNAL TABLE t (a int)")',
     {"spark_sql": [{"text": "CREATE EXTERNAL TABLE t (a int)", "line": 1}]}),
    ("use database", 'spark.sql("USE DATABASE db")',
     {"spark_sql": [{"text": "USE DATABASE db", "line": 1}]}),
    ("enableHiveSupport", "val s = SparkSession.builder.enableHiveSupport().getOrCreate()",
     {"calls": _calls("enableHiveSupport", "getOrCreate")}),
    ("hadoopConfiguration", "val c = sc.hadoopConfiguration",
     {"selects": [{"member": "hadoopConfiguration", "recv_leaf": "sc", "line": 1}]}),
    ("HiveContext", "val h = new HiveContext(sc)",
     {"new_types": [{"type": "HiveContext", "line": 1}]}),
]


@pytest.mark.parametrize("label,code,facts", _HIVE_CASES, ids=[c[0] for c in _HIVE_CASES])
def test_hive_facts_match_regex(label, code, facts):
    assert _facts_hive(facts, code) == _regex_hive(code), (
        f"[{label}] Hive-DDL facts-path reasons diverge from regex-path"
    )


def test_hive_facts_drop_comment_false_positive():
    # MSCK only in a comment / no spark.sql fact => facts path does NOT flag it.
    code = "// spark.sql(\"MSCK REPAIR TABLE t\") -- removed\nval r = df\n"
    assert _facts_hive({}, code) == set()
    assert _regex_hive(code) != set()


@pytest.mark.skipif(not _IT, reason="set SCOS_RUN_SCALAFIX_IT=1 and have sbt on PATH")
@pytest.mark.parametrize("code", [
    "object J { val r = datediff(a, b) }\n",
    'object J { val r = df.repartition(8); val s = concat_ws("-", a, b) }\n',
    'object J { val r = spark.sql("SELECT datediff(a,b) FROM t") }\n',
    'object J { val r = df.orderBy(col("x").desc) }\n',
    'object J { val r = col("a") / lit(0) }\n',
    'object J { val r = col("a") / col("b") }\n',
    'object J { val r = col("a") === lit("x") }\n',
    'object J { val r = c.cast("boolean") }\n',
    'object J { val r = c.cast(BooleanType) }\n',
    "object J { val r = substring(s, 0, 3) }\n",
    "object J { val r = to_timestamp(c) }\n",
    "object J { val r = c.cast(TimestampType) }\n",
])
def test_real_extractor_behavioral_parity(tmp_path, code):
    import scala_ast_facts
    f = tmp_path / "J.scala"
    f.write_text(code, encoding="utf-8")
    facts_by_path = scala_ast_facts.extract_facts(f)
    assert facts_by_path is not None, "extractor returned None despite toolchain"
    file_facts = next(iter(facts_by_path.values()))
    assert _facts_behavioral(file_facts, code) == _regex_behavioral(code)


@pytest.mark.skipif(not _IT, reason="set SCOS_RUN_SCALAFIX_IT=1 and have sbt on PATH")
@pytest.mark.parametrize("code", [
    'object J { val r = spark.sql("MSCK REPAIR TABLE t") }\n',
    'object J { val r = spark.sql("CREATE EXTERNAL TABLE t (a int)") }\n',
    'object J { val r = spark.sql("USE DATABASE db") }\n',
    "object J { val s = SparkSession.builder.enableHiveSupport().getOrCreate() }\n",
    "object J { val h = new HiveContext(sc) }\n",
])
def test_real_extractor_hive_parity(tmp_path, code):
    import scala_ast_facts
    f = tmp_path / "J.scala"
    f.write_text(code, encoding="utf-8")
    facts_by_path = scala_ast_facts.extract_facts(f)
    assert facts_by_path is not None, "extractor returned None despite toolchain"
    file_facts = next(iter(facts_by_path.values()))
    assert _facts_hive(file_facts, code) == _regex_hive(code)

