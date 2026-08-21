"""Tests for the ScosSaveAsTableDropStorageOpts Scalafix rule (Row E parity).

The rule drops unsupported ``.format(...)`` / ``.option("path", …)`` calls from
a ``.write…saveAsTable(...)`` chain — the Scala method-chain analog of the
PySpark recipe ``saveastable_drop_format_path_kwargs_rewrite``.

Two layers of coverage:
  * Static guards (always run): the rule is defined in SCOSRules.scala and
    registered in scos.scalafix.conf, and the conf/source stay in sync.
  * A toolchain-gated integration test that actually compiles + runs scalafix
    on a fixture. It is skipped unless ``SCOS_RUN_SCALAFIX_IT=1`` (and ``sbt`` is
    on PATH), because compiling Scala in CI is slow / network-dependent.
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
_RULE = "ScosSaveAsTableDropStorageOpts"
_FQCN = f"com.snowflake.scos.scalafix.{_RULE}"


# --- static guards ----------------------------------------------------------


def test_rule_defined_in_sources():
    src = _RULES_SRC.read_text(encoding="utf-8")
    assert f"class {_RULE} extends SyntacticRule" in src


def test_rule_registered_in_conf():
    conf = _CONF.read_text(encoding="utf-8")
    assert f"class:{_FQCN}" in conf


def test_conf_and_sources_in_sync():
    # Every rule listed in the conf must be defined in SCOSRules.scala.
    src = _RULES_SRC.read_text(encoding="utf-8")
    conf = _CONF.read_text(encoding="utf-8")
    for line in conf.splitlines():
        line = line.strip()
        if not line.startswith('"class:com.snowflake.scos.scalafix.'):
            continue
        cls = line.split(".")[-1].rstrip('"')
        # Whitespace-robust: long rule names wrap before ``extends``
        # (``class Foo\n    extends SyntacticRule("Foo")``), so match the class
        # declaration and its SyntacticRule registration independently.
        assert f"class {cls}" in src, f"{cls} in conf but not in SCOSRules.scala"
        assert f'SyntacticRule("{cls}")' in src, \
            f"{cls} in conf but not registered via SyntacticRule(\"{cls}\") in SCOSRules.scala"


# --- toolchain-gated integration --------------------------------------------


_IT_ENABLED = os.environ.get("SCOS_RUN_SCALAFIX_IT") == "1" and shutil.which("sbt")


@pytest.mark.skipif(not _IT_ENABLED, reason="set SCOS_RUN_SCALAFIX_IT=1 and have sbt on PATH")
def test_saveastable_rewrite_integration():
    cp = subprocess.run(
        ["sbt", "--batch", "-error", "export Compile/fullClasspath"],
        cwd=_SBT_DIR, capture_output=True, text=True, timeout=600,
    ).stdout.strip().splitlines()[-1]
    fixture = (
        "object F {\n"
        "  def a(df: org.apache.spark.sql.DataFrame): Unit = {\n"
        '    df.write.format("parquet").option("path", "s3://b/x").mode("overwrite").saveAsTable("db.t")\n'
        "  }\n"
        "  def c(df: org.apache.spark.sql.DataFrame): Unit = {\n"
        '    df.write.option("mergeSchema", "true").saveAsTable("db.t3")\n'
        "  }\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "F.scala"
        f.write_text(fixture, encoding="utf-8")
        out = subprocess.run(
            ["java", "-cp", cp, "scalafix.cli.Cli", "--rules", f"class:{_FQCN}", "--stdout", str(f)],
            capture_output=True, text=True, timeout=180,
        ).stdout
    # The original storage-opt calls are dropped from the CODE (the explanatory
    # comment legitimately mentions ``.format()``/``.option("path", …)``, so we
    # assert against the concrete original call arguments instead).
    assert '.format("parquet")' not in out
    assert '.option("path", "s3://b/x")' not in out
    assert 'df.write.mode("overwrite").saveAsTable("db.t")' in out
    # non-path option on a chain with no storage opts is left untouched.
    assert '.option("mergeSchema", "true")' in out
