"""Guard against drift between RDD *detection* and RDD *conversion guidance*.

``analyze_pyspark.py`` flags RDD usage via ``RDD_METHODS`` (operation names) and
``RDD_PATTERNS`` (entry-point substrings). The LLM fixer then rewrites those
sites using ``references/python/rdd-conversion.md`` as its only RDD-specific
guidance. If a name is added to the detector but not the reference, the fixer is
told "this is an RDD issue" with no idea how to fix it — exactly the gap this
suite exists to prevent.

This test asserts every detected name is documented in the reference. It parses
the constants out of ``analyze_pyspark.py`` with ``ast`` (no import) so it stays
free of that module's runtime dependencies.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_rdd_reference_sync.py
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_SNOWPARK_CONNECT = Path(__file__).resolve().parents[2]
_ANALYZER = _SNOWPARK_CONNECT / "scripts" / "analyze_pyspark.py"
_REFERENCE = _SNOWPARK_CONNECT / "references" / "python" / "rdd-conversion.md"
_EXCLUSIVE_ANNOTATE_RECIPE = (
    _SNOWPARK_CONNECT
    / "scripts"
    / "recipes"
    / "rdd_exclusive_method_todo_annotate"
    / "recipe.py"
)


def _extract_collection(source: str, name: str):
    """Return the literal set/list assigned to ``name`` at module top level.

    Parses with ``ast`` (no import) so the test stays free of the analyzer's
    runtime dependencies. Handles a bare literal (``{...}`` / ``[...]``) as well
    as a ``frozenset({...})`` / ``set({...})`` wrapper (used by the recipes,
    which cannot import the analyzer's heavy stack and re-declare their token
    lists standalone).
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
    """Reduce an ``RDD_PATTERNS`` entry to the token to search for.

    ``".parallelize("`` -> ``"parallelize"``; ``".rdd"`` -> ``"rdd"``. Multi-word
    phrases (the import patterns) are searched verbatim as substrings.
    """
    return pattern.strip().lstrip(".").rstrip("(")


def _documented(token: str, reference_text: str) -> bool:
    """True if ``token`` appears in the reference.

    Phrases (with spaces) are matched as substrings; single identifiers are
    matched on word boundaries so e.g. ``map`` matches ``rdd.map(`` but not the
    interior of ``mapValues``.
    """
    if " " in token:
        return token in reference_text
    return re.search(rf"\b{re.escape(token)}\b", reference_text) is not None


@pytest.fixture(scope="module")
def reference_text() -> str:
    return _REFERENCE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def analyzer_source() -> str:
    return _ANALYZER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def exclusive_annotate_recipe_source() -> str:
    return _EXCLUSIVE_ANNOTATE_RECIPE.read_text(encoding="utf-8")


def test_every_rdd_method_is_documented(reference_text, analyzer_source):
    methods = _extract_collection(analyzer_source, "RDD_METHODS")
    assert methods, "RDD_METHODS came back empty"
    missing = sorted(m for m in methods if not _documented(m, reference_text))
    assert not missing, (
        "RDD_METHODS detected by analyze_pyspark.py but NOT documented in "
        f"references/python/rdd-conversion.md: {missing}. Add a row/example for "
        "each, or remove it from the detector."
    )


def test_every_rdd_pattern_is_documented(reference_text, analyzer_source):
    patterns = _extract_collection(analyzer_source, "RDD_PATTERNS")
    assert patterns, "RDD_PATTERNS came back empty"
    missing = sorted(
        p for p in patterns if not _documented(_normalize_pattern(p), reference_text)
    )
    assert not missing, (
        "RDD_PATTERNS detected by analyze_pyspark.py but NOT documented in "
        f"references/python/rdd-conversion.md: {missing}."
    )


# --------------------------------------------------------------------------- #
# Classification integrity: the RDD fix payload NAMES the detected op(s) and
# points the fixer at the reference for the rewrite (no duplicated mapping in
# code). The only thing classified in code is RDD_NO_EQUIVALENT — the handful of
# ops with genuinely no DataFrame equivalent. Assert that set stays honest:
# every token is a real detected RDD name, and the doc marks it as such.
# --------------------------------------------------------------------------- #
def test_no_equivalent_tokens_are_known_rdd_names(analyzer_source):
    no_equiv = _extract_collection(analyzer_source, "RDD_NO_EQUIVALENT")
    methods = {m.lower() for m in _extract_collection(analyzer_source, "RDD_METHODS")}
    patterns = {
        _normalize_pattern(p).lower()
        for p in _extract_collection(analyzer_source, "RDD_PATTERNS")
    }
    known = methods | patterns
    unknown = sorted(t for t in no_equiv if t not in known)
    assert not unknown, (
        "RDD_NO_EQUIVALENT tokens that are not detected RDD methods/patterns "
        f"(typo or stale entry): {unknown}."
    )


def test_no_equivalent_ops_are_marked_todo_in_reference(reference_text, analyzer_source):
    """Each no-equivalent token must be flagged TODO / 'no equivalent' in the doc,
    so the code classification cannot silently diverge from the reference."""
    no_equiv = _extract_collection(analyzer_source, "RDD_NO_EQUIVALENT")
    lines = reference_text.splitlines()
    undocumented = []
    for token in sorted(no_equiv):
        hit = [ln.lower() for ln in lines if token in ln.lower()]
        if not any(("todo" in ln or "no equivalent" in ln) for ln in hit):
            undocumented.append(token)
    assert not undocumented, (
        "RDD_NO_EQUIVALENT tokens not marked as TODO / 'no equivalent' in "
        f"references/python/rdd-conversion.md: {undocumented}."
    )


# --------------------------------------------------------------------------- #
# Recipe/analyzer parity: ``rdd_exclusive_method_todo_annotate`` re-declares the
# RDD-exclusive token list in its own ``_TARGET_METHODS`` because recipes run
# standalone on libcst and must NOT import ``analyze_pyspark`` (its heavy rag /
# snowpark runtime stack). That deliberate copy can silently drift from the
# analyzer's ``RDD_EXCLUSIVE_METHODS`` — this guard keeps the two identical.
# --------------------------------------------------------------------------- #
def test_exclusive_annotate_recipe_targets_match_analyzer(
    analyzer_source, exclusive_annotate_recipe_source
):
    exclusive = set(_extract_collection(analyzer_source, "RDD_EXCLUSIVE_METHODS"))
    targets = set(
        _extract_collection(exclusive_annotate_recipe_source, "_TARGET_METHODS")
    )
    assert exclusive, "RDD_EXCLUSIVE_METHODS came back empty"
    assert exclusive == targets, (
        "rdd_exclusive_method_todo_annotate._TARGET_METHODS has drifted from "
        "analyze_pyspark.RDD_EXCLUSIVE_METHODS — the recipe annotates exactly the "
        "analyzer's RDD-exclusive names. Only in RDD_EXCLUSIVE_METHODS: "
        f"{sorted(exclusive - targets)}; only in _TARGET_METHODS: "
        f"{sorted(targets - exclusive)}."
    )
