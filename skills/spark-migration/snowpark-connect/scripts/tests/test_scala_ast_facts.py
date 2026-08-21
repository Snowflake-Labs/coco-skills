"""Tests for the Scala AST fact extractor (Job 2 foundation): ScosMigrateFacts.scala
+ scala_ast_facts.py runner.

Two layers (mirroring test_scalafix_saveastable_scala.py):
  * Static guards (always run): the extractor source + circe dep + runner API exist.
  * A toolchain-gated integration test that compiles + runs the extractor and
    asserts the emitted facts. Skipped unless SCOS_RUN_SCALAFIX_IT=1 and sbt is
    on PATH (compiling Scala in CI is slow / network-dependent).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import scala_ast_facts

_SCRIPTS = Path(__file__).resolve().parent.parent
_FACTS_SRC = _SCRIPTS / "scalafix_rules" / "ScosMigrateFacts.scala"
_SBT_BUILD = _SCRIPTS / "scalafix_sbt" / "build.sbt"


# --- static guards ----------------------------------------------------------


def test_extractor_source_present():
    src = _FACTS_SRC.read_text(encoding="utf-8")
    assert "object ScosMigrateFacts" in src
    assert "import scala.meta._" in src
    # emits the fact categories the analyzer's detectors consume
    for key in ('"imports"', '"calls"', '"selects"', '"new_types"', '"spark_sql"', '"session_created"'):
        assert key in src, f"extractor missing fact key {key}"


def test_build_has_circe():
    assert "circe-core" in _SBT_BUILD.read_text(encoding="utf-8")


def test_runner_api():
    assert hasattr(scala_ast_facts, "extract_facts")
    assert hasattr(scala_ast_facts, "facts_available")


def test_runner_returns_none_without_source(tmp_path):
    # A non-existent path returns None (graceful), regardless of toolchain.
    assert scala_ast_facts.extract_facts(tmp_path / "nope.scala") is None


# --- toolchain-gated integration --------------------------------------------


_IT = os.environ.get("SCOS_RUN_SCALAFIX_IT") == "1" and shutil.which("sbt")


@pytest.mark.skipif(not _IT, reason="set SCOS_RUN_SCALAFIX_IT=1 and have sbt on PATH")
def test_extract_facts_integration(tmp_path):
    f = tmp_path / "Job.scala"
    f.write_text(
        "package demo\n"
        "import org.apache.hadoop.fs.Path\n"
        "object Job extends App {\n"
        '  val df = spark.read.format("avro").load("/p")\n'
        "  val r = df.rdd.map(x => x)\n"
        '  spark.sql("MSCK REPAIR TABLE t")\n'
        "}\n",
        encoding="utf-8",
    )
    facts = scala_ast_facts.extract_facts(f)
    assert facts is not None, "extractor returned None despite toolchain present"
    fobj = next(iter(facts.values()))
    assert fobj["parse_ok"]
    assert any(i["ref"] == "org.apache.hadoop.fs.Path" for i in fobj["imports"])
    assert any(c["method"] == "format" and "avro" in c["args"] for c in fobj["calls"])
    assert any(s["member"] == "rdd" for s in fobj["selects"])
    assert any("MSCK REPAIR" in s["text"] for s in fobj["spark_sql"])
    # line numbers present (the whole point — findings map back to source)
    assert all("line" in c for c in fobj["calls"])
