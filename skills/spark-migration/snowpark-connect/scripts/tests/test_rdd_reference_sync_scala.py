"""Guard against drift between RDD *detection* and RDD *conversion guidance*
on the Scala side (mirror of ``test_rdd_reference_sync.py`` for PySpark).

``analyze_scala.py`` flags RDD usage via ``RDD_METHODS`` (operation names,
surfaced when gated by a ``.rdd`` / SparkContext token) and ``RDD_PATTERNS``
(entry-point regexes). The Scalafix rule ``ScosRddExclusiveMethodAnnotate``
annotates the RDD-exclusive subset via its ``EXCLUSIVE`` set. The LLM fixer
rewrites those sites using ``references/scala/rdd-conversion.md`` as its only
RDD-specific guidance — so if a name is added to a detector but not the
reference, the fixer is told "this is an RDD issue" with no idea how to fix it.

This suite asserts:
  * every detected ``RDD_METHODS`` / ``RDD_PATTERNS`` token is documented;
  * every Scalafix ``EXCLUSIVE`` token is documented;
  * the Scalafix ``EXCLUSIVE`` set AGREES with the analyzer — the frozen
    §10/aggregate exclusive tokens live in both, and DataFrame homonyms live in
    neither exclusive path.

The analyzer constants are parsed with ``ast`` (no import) so the test stays
free of that module's heavy runtime dependencies. Run from ``snowpark-connect/``:

    pytest scripts/tests/test_rdd_reference_sync_scala.py
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_SNOWPARK_CONNECT = Path(__file__).resolve().parents[2]
_ANALYZER = _SNOWPARK_CONNECT / "scripts" / "analyze_scala.py"
_REFERENCE = _SNOWPARK_CONNECT / "references" / "scala" / "rdd-conversion.md"
_SCOS_RULES = _SNOWPARK_CONNECT / "scripts" / "scalafix_rules" / "SCOSRules.scala"

# Frozen L3 §10/aggregate exclusive tokens — must appear in BOTH the analyzer's
# RDD_METHODS and the Scalafix EXCLUSIVE set (drift guard for the integration).
_FROZEN_EXCLUSIVE_TOKENS = frozenset({
    "treeAggregate", "treeReduce", "collectAsMap", "countApprox",
    "countApproxDistinct", "meanApprox", "sumApprox",
    "repartitionAndSortWithinPartitions", "toDebugString",
})

# DataFrame homonyms (also methods/attributes on DataFrame/Dataset/Writer). They
# are kept ONLY in the gated RDD_METHODS set and must NEVER enter an exclusive /
# no-equivalent path — otherwise they false-fire on ordinary DataFrame code.
_HOMONYM_TOKENS = frozenset({
    "groupBy", "partitionBy", "toDF", "context", "reduce", "fold",
    "aggregate", "groupByKey",
})

# Regex-pattern junk tokens to drop when reducing an RDD_PATTERNS entry (a raw
# regex string) to the identifier it searches for.
_PATTERN_JUNK = frozenset({"b", "s", "import", "org", "apache", "spark", "new"})


def _extract_collection(source: str, name: str):
    """Return the literal set/list assigned to ``name`` at module top level.

    Parses with ``ast`` (no import) so the test stays free of the analyzer's
    runtime dependencies.
    """
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    value = node.value
                    if isinstance(value, ast.Call):  # frozenset({...}) / set({...})
                        value = value.args[0]
                    return ast.literal_eval(value)
    raise AssertionError(f"{name} not found as a top-level assignment")


def _normalize_pattern(pattern: str) -> str:
    """Reduce an ``RDD_PATTERNS`` regex entry to the identifier to search for.

    ``r"\\.rdd\\b"`` -> ``rdd``; ``r"\\bsc\\.textFile\\b"`` -> ``textFile``;
    ``r"import\\s+org\\.apache\\.spark\\.SparkContext"`` -> ``SparkContext``.
    """
    idents = [i for i in re.findall(r"[A-Za-z][A-Za-z0-9]*", pattern)
              if i not in _PATTERN_JUNK]
    assert idents, f"no identifier extractable from pattern {pattern!r}"
    return idents[-1]


def _exclusive_set(scos_rules_source: str) -> set[str]:
    """Parse the string literals from ScosRddExclusiveMethodAnnotate's
    ``EXCLUSIVE`` Set literal, ignoring ``//`` comment lines (prose with quotes).
    """
    m = re.search(
        r"val EXCLUSIVE: Set\[String\] = Set\((.*?)\)\s*\n",
        scos_rules_source, re.DOTALL,
    )
    assert m, "EXCLUSIVE set literal not found in SCOSRules.scala"
    body = "\n".join(line.split("//", 1)[0] for line in m.group(1).splitlines())
    return set(re.findall(r'"([^"]+)"', body))


def _documented(token: str, reference_text: str) -> bool:
    """True if ``token`` appears in the reference on a word boundary."""
    return re.search(rf"\b{re.escape(token)}\b", reference_text) is not None


@pytest.fixture(scope="module")
def reference_text() -> str:
    return _REFERENCE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def analyzer_source() -> str:
    return _ANALYZER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def scos_rules_source() -> str:
    return _SCOS_RULES.read_text(encoding="utf-8")


def test_every_rdd_method_is_documented(reference_text, analyzer_source):
    methods = _extract_collection(analyzer_source, "RDD_METHODS")
    assert methods, "RDD_METHODS came back empty"
    missing = sorted(m for m in methods if not _documented(m, reference_text))
    assert not missing, (
        "RDD_METHODS detected by analyze_scala.py but NOT documented in "
        f"references/scala/rdd-conversion.md: {missing}. Add a row/example for "
        "each, or remove it from the detector."
    )


def test_every_rdd_pattern_is_documented(reference_text, analyzer_source):
    patterns = _extract_collection(analyzer_source, "RDD_PATTERNS")
    assert patterns, "RDD_PATTERNS came back empty"
    missing = sorted(
        p for p in patterns if not _documented(_normalize_pattern(p), reference_text)
    )
    assert not missing, (
        "RDD_PATTERNS detected by analyze_scala.py but NOT documented in "
        f"references/scala/rdd-conversion.md: {missing}."
    )


def test_every_exclusive_token_is_documented(reference_text, scos_rules_source):
    """Every Scalafix-annotated RDD-exclusive name must be documented so the
    fixer knows the DataFrame rewrite for the site it just flagged."""
    exclusive = _exclusive_set(scos_rules_source)
    assert exclusive, "EXCLUSIVE set came back empty"
    missing = sorted(t for t in exclusive if not _documented(t, reference_text))
    assert not missing, (
        "ScosRddExclusiveMethodAnnotate EXCLUSIVE tokens NOT documented in "
        f"references/scala/rdd-conversion.md: {missing}."
    )


def test_frozen_exclusive_tokens_in_both_detectors(analyzer_source, scos_rules_source):
    """The §10/aggregate exclusive additions must be present in BOTH the
    analyzer's RDD_METHODS and the Scalafix EXCLUSIVE set — remove one without
    the other and this drift guard fires."""
    methods = set(_extract_collection(analyzer_source, "RDD_METHODS"))
    exclusive = _exclusive_set(scos_rules_source)
    missing_methods = sorted(_FROZEN_EXCLUSIVE_TOKENS - methods)
    missing_exclusive = sorted(_FROZEN_EXCLUSIVE_TOKENS - exclusive)
    assert not missing_methods, (
        f"frozen exclusive tokens missing from analyze_scala.RDD_METHODS: {missing_methods}"
    )
    assert not missing_exclusive, (
        "frozen exclusive tokens missing from SCOSRules EXCLUSIVE set: "
        f"{missing_exclusive}"
    )


def test_scalafix_exclusive_agrees_with_analyzer(analyzer_source, scos_rules_source):
    """Every RDD-exclusive name the Scalafix rule annotates must be a real RDD
    method the analyzer knows about (RDD_METHODS) — no stray/typo token that the
    detector would never surface guidance for."""
    methods = set(_extract_collection(analyzer_source, "RDD_METHODS"))
    exclusive = _exclusive_set(scos_rules_source)
    # `reduceByKeyLocally` is an RDD-exclusive alias annotated by Scalafix but not
    # a separate analyzer detection token (its base `reduceByKey` is) — allow it.
    unknown = sorted(exclusive - methods - {"reduceByKeyLocally"})
    assert not unknown, (
        "ScosRddExclusiveMethodAnnotate EXCLUSIVE tokens that are not in "
        f"analyze_scala.RDD_METHODS (typo / drift): {unknown}."
    )


def test_new_exclusive_tokens_are_not_homonyms():
    """The §10/aggregate names added by this integration must be genuinely
    RDD-exclusive — the drift guard must not admit a DataFrame homonym into the
    newly-annotated set. (Pre-existing EXCLUSIVE entries are out of scope: this
    checks only what the RDD-migration integration introduced.)"""
    leaked = sorted(_FROZEN_EXCLUSIVE_TOKENS & _HOMONYM_TOKENS)
    assert not leaked, (
        f"newly-added exclusive tokens that are DataFrame homonyms: {leaked}."
    )


def test_accumulator_rule_defined_registered_and_named(scos_rules_source):
    """No-sbt static coverage for the new ScosAccumulatorAnnotate rule.

    The behavioral tests in ``test_scalafix_ported_recipes_scala.py`` are gated
    behind the sbt IT mark, so this unconditionally-run guard is the only CI
    coverage (without sbt) that the rule is defined, registered, keys off the
    real Scala accumulator names — never the PySpark ``sc.accumulator`` /
    ``AccumulatorParam`` spelling — and carries the SCL1500 EWI.
    """
    assert "class ScosAccumulatorAnnotate" in scos_rules_source, (
        "ScosAccumulatorAnnotate rule not defined in SCOSRules.scala"
    )
    conf = (_SCOS_RULES.parent / "scos.scalafix.conf").read_text(encoding="utf-8")
    assert "ScosAccumulatorAnnotate" in conf, (
        "ScosAccumulatorAnnotate not registered in scos.scalafix.conf"
    )
    body = scos_rules_source.split("class ScosAccumulatorAnnotate", 1)[1].split(
        "\nclass ", 1)[0]
    for name in ("longAccumulator", "doubleAccumulator", "collectionAccumulator",
                 "LongAccumulator", "DoubleAccumulator", "CollectionAccumulator",
                 "AccumulatorV2"):
        assert name in body, f"{name} not targeted by ScosAccumulatorAnnotate"
    assert "AccumulatorParam" not in body, (
        "PySpark-only AccumulatorParam must not be targeted"
    )
    assert "SPRKCNTSCL1500" in body, (
        "ScosAccumulatorAnnotate must carry EWI SPRKCNTSCL1500"
    )
