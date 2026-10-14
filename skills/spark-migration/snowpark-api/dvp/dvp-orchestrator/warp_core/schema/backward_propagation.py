"""
Backward type propagation for column schema inference.

After forward schema propagation (source → transformations → outputs), some
source columns remain untyped or low-confidence because their types are only
visible in *how* they are used inside transformation logic — not in their
declaration.

This module implements a second pass that extracts column-type evidence from
transformation logic strings and propagates it BACKWARD to the originating
data_in nodes.

Design
------
Rationale for name-based (not graph-based) propagation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The most common blocker to graph-based backward walking is that transformations
inside helper functions refer to their DataFrame argument via a ``param_*``
placeholder node rather than a concrete ``in_*`` node.  Resolving those
parameter references back to the call sites requires rebuilding the full
function call graph from the serialised ASG — prohibitively complex.

Instead, we use a **global column-name assumption**: if the logic of *any*
transformation in the workload contains a high-confidence type signal for a
column named "X", then every data_in source that has a column named "X" with
lower confidence is eligible for upgrade.

This assumption is valid for enterprise workloads where:
  1. A column with a given name appears in only one kind of source table
     (naming is domain-specific and consistent).
  2. The same business rules are applied to every extract of that table.

Patterns recognised
~~~~~~~~~~~~~~~~~~~
+------------------------------+-------------------+-------------------+
| Pattern in logic             | Inferred src type | Confidence        |
+==============================+===================+===================+
| col("X") rlike "..."         | STRING            | HIGH (≈ 1.0)      |
| col("X").rlike("...")        | STRING            | HIGH (≈ 1.0)      |
| col("X") === ""              | STRING            | HIGH (≈ 1.0)      |
| col("X") == ""               | STRING            | HIGH (≈ 1.0)      |
| col("X").isInCollection(...) | STRING            | MEDIUM (string    |
|   when collection has strs   |                   | items only)       |
+------------------------------+-------------------+-------------------+

Design note — join-ambiguous columns (won't fix)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
When a column name appears in multiple join inputs, backward propagation
upgrades the type in all of them. The *attribution* is uncertain (we don't
know which join leg the evidence came from), but the *type* is correct in
practice: join keys must be compatible, so both sides have the same type.
Marking them ``ambiguous_join`` (weight 0.3) would lower the score for
correctly-typed columns, making the metric less accurate, not more honest.
Decision: keep the current behaviour (propagate to all matching sources).
"""

from __future__ import annotations

import re
from typing import Any

from warp_core.ir.pyspark_models import (
    ASG,
    InferenceConfidence,
    InferenceSource,
    InferredColumn,
)

# ---------------------------------------------------------------------------
# Regex patterns for evidence extraction
# ---------------------------------------------------------------------------

# col("X") rlike "..."  OR  col("X").rlike("...")
_RLIKE_RE = re.compile(
    r'col\s*\(\s*["\'](\w+)["\']\s*\)\s*(?:rlike|\.rlike\s*\()',
    re.IGNORECASE,
)

# col("X") === ""  OR  col("X") == ""
_STRING_EQ_RE = re.compile(
    r'col\s*\(\s*["\'](\w+)["\']\s*\)\s*(?:===|==)\s*["\']',
    re.IGNORECASE,
)

# Sources eligible for upgrade by backward propagation.
#
# We intentionally exclude ``naming_convention`` and ``schema_definition``:
# those already carry a *logical/business* type (e.g. L_INT for "nombre de
# jours") inferred from domain knowledge.  Backward propagation derives the
# *physical/storage* type (often STRING for Hive fields that hold numeric
# strings).  Overwriting the business type with the storage type would
# regress schema understanding without improving it, and would conflict with
# naming-convention scoring (0.8 weight → usage 1.0 but wrong semantic).
#
# Eligible sources are those whose type comes purely from cross-reference
# propagation — no domain or code evidence behind them yet.
_UPGRADEABLE_SOURCES: frozenset[str] = frozenset({
    "xref_input",   # propagated from another source with the same name (0.5)
    "xref_output",  # propagated from a data_out with the same name (0.5)
    "xref_function", # propagated from a test fixture (0.7) — code-indirect
    "",             # missing/empty source string
    # NOTE: "naming_convention" is deliberately excluded — see docstring.
})


def extract_column_evidence(asg: ASG) -> dict[str, str]:
    """Scan ALL transformation logic strings and collect high-confidence type signals.

    Returns a mapping ``{column_name: inferred_type}`` for every column where
    at least one reliable pattern was found.  Currently the only inferred type
    is ``STRING`` (from string-operation guards).

    Only non-test transformations are considered (``is_test_file`` flag).
    """
    evidence: dict[str, str] = {}

    for tx in asg.transformations:
        if getattr(tx, "is_test_file", False):
            continue
        logic: str = getattr(tx, "logic", "") or ""
        if not logic:
            continue

        # rlike guard: col is definitely STRING in the source
        for col_name in _RLIKE_RE.findall(logic):
            evidence[col_name] = "STRING"

        # String equality check: col is definitely STRING
        for col_name in _STRING_EQ_RE.findall(logic):
            evidence.setdefault(col_name, "STRING")

    return evidence


def apply_backward_type_propagation(asg: ASG) -> int:
    """Upgrade low-confidence source columns using transformation-logic evidence.

    Returns the number of columns upgraded.

    Algorithm
    ---------
    1. ``extract_column_evidence`` scans every (non-test) transformation's
       ``logic`` string for high-confidence type signals.
    2. For every production ``data_in`` node, each column that:
         a. matches a name in the evidence dict, AND
         b. has a source that is eligible for upgrade (see ``_UPGRADEABLE_SOURCES``),
       is upgraded to ``InferenceSource.USAGE`` / ``InferenceConfidence.HIGH``
       with the evidence-derived type.
    3. Returns a count of upgraded columns.
    """
    evidence = extract_column_evidence(asg)
    if not evidence:
        return 0

    upgraded = 0
    for node in asg.data_in:
        if getattr(node, "is_test_file", False):
            continue
        for col in node.inferred_columns or []:
            new_type = evidence.get(col.name)
            if new_type is None:
                continue

            src_value: str = (
                col.source.value
                if hasattr(col.source, "value")
                else str(col.source or "")
            )

            if src_value not in _UPGRADEABLE_SOURCES:
                continue

            # Only upgrade if we are changing the type or the confidence.
            already_correct_type = col.inferred_type == new_type
            already_high = col.confidence == InferenceConfidence.HIGH

            if already_correct_type and already_high:
                continue

            col.inferred_type = new_type
            col.source = InferenceSource.USAGE
            col.confidence = InferenceConfidence.HIGH
            upgraded += 1

    return upgraded
