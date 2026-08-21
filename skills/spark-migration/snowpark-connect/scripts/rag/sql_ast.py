# flake8: noqa: T201
"""sqlglot-backed structural SQL analysis for plain ``.sql`` workloads.

The token KB (``kb_rules.json``) and the regex matchers in ``detectors.py``
fire on *keyword presence* or *bounded text spans*. For a class of
high-severity SCOS gaps that is too coarse — the precise signal lives in the
parse tree, and the text-only heuristics false-positive:

  * **Window function missing ``ORDER BY``** — Spark raises
    ``AnalysisException`` for an unordered ``ROW_NUMBER``/``LEAD``/… window;
    Snowflake silently returns a nondeterministic result. The token rules for
    ``row_number`` / ``lead`` / ``first_value`` carry exactly this note but
    fire on the bare function name, so a correctly-ordered window
    (``ROW_NUMBER() OVER (PARTITION BY x ORDER BY y)``) is flagged anyway.
  * **``IN (SELECT …)`` inside a LEFT JOIN ``ON`` clause** — SCOS collapses the
    join to INNER semantics. The regex detector uses a bounded look-ahead that
    leaps from a ``LEFT JOIN … ON`` across into a later ``WHERE`` and matches an
    unrelated ``IN (SELECT``.
  * **SELECT alias colliding with a GROUP BY column** (LCA disambiguation) —
    the regex collects aliases and GROUP BY idents across the *whole file*,
    mixing one query's aliases with another query's GROUP BY.
  * **Multi-column ``NOT IN``** (a tuple LHS, ``(a, b) NOT IN (...)``) — only the
    multi-column form diverges between SCOS and Spark; single-column ``NOT IN``
    shares the same three-valued NULL semantics in both engines and is *not* a
    gap. The token rule fires on every ``NOT IN`` regardless of arity (so it
    both false-positives on scalar ``NOT IN`` and, because ``_scan_sql`` only
    matches the first occurrence per file, misses later multi-column ones). The
    AST walks every ``NOT IN`` and flags exactly the tuple-LHS occurrences.

It also carries the structural SCOS §9 SQL-behavioral-difference checks whose
keyword token rules over-fire:

  * **§9.6 ``INSERT OVERWRITE ... PARTITION``** — the gap needs both OVERWRITE
    and a PARTITION spec; the token rule fires on ``INSERT OVERWRITE`` alone.
  * **§9.12 ``GROUPING SETS`` with a non-empty ``GROUP BY``** (and multi-expr
    ROLLUP/CUBE) — the empty-GROUP-BY form is supported; the token rule fires on
    bare ``GROUPING SETS``.
  * **§9.4 ``LATERAL VIEW`` with an unsupported generator** — only FLATTEN /
    SPLIT_TO_TABLE are supported; the token rule fires on every ``LATERAL VIEW``.
  * **§9.3 multiple table-valued generators in one ``SELECT``** — new coverage;
    inherently structural (count generators in the projection).
  * **§9.10 ``TABLESAMPLE``**, **§9.11 ``TRANSFORM … USING``** (Hive script
    transform), **§9.2 ``EXPLAIN <DDL>``** (rejected — Snowflake EXPLAIN is
    DML-only), **§9.15 ``EXPLAIN <mode>``** (mode silently ignored) — new
    coverage. EXPLAIN can't be fully parsed by sqlglot, so it lands as a
    ``Command`` whose payload's leading keyword separates DDL / mode / plain DML.

When sqlglot can parse the text (``dialect="spark"``), these AST detectors are
authoritative: ``analyze_sql`` returns an :class:`AstResult` whose findings the
caller appends, and whose supersession flags tell the caller to skip the
token/regex matchers the AST has adjudicated. When the parse fails (exotic
vendor syntax), ``analyze_sql`` returns ``None`` and the caller falls back to
the regex path unchanged — unparseable SQL is never dropped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Window functions whose result is meaningless without an ORDER BY: Spark
# rejects them (``requires window to be ordered``); Snowflake permits them and
# returns a nondeterministic ordering. Matched by sqlglot's canonical
# ``sql_name()`` (uppercase).
_ORDER_SENSITIVE_WINDOW = frozenset({
    "ROW_NUMBER", "RANK", "DENSE_RANK", "NTILE", "PERCENT_RANK", "CUME_DIST",
    "LEAD", "LAG", "FIRST_VALUE", "LAST_VALUE", "NTH_VALUE",
})

# Token-rule note signature for the window-ORDER-BY concern. When the AST has
# run, the caller suppresses any token rule whose note contains this phrase
# (the row_number / lead / first_value rules all share it).
WINDOW_ORDER_NOTE_MARK = "window to be ordered"

# Regex detectors (detectors.py) the AST supersedes when the parse succeeds.
_SUPERSEDED_DETECTORS = frozenset({
    "detector:lca_alias_collision",
    "detector:in_subquery_in_on_clause",
    "detector:window_case_aggregate",
    "detector:cast_to_interval",
})

# Token rules (kb_rules.json) the AST supersedes when the parse succeeds: any
# sql_construct rule anchored on the ``NOT IN`` keyword. The AST is arity-aware
# (flags only the multi-column tuple form), so deferring to it removes the
# token rule's scalar-NOT-IN false positives.
NOT_IN_API_TOKEN = "NOT IN"

# SCOS §9 LATERAL VIEW generators that ARE supported (everything else is the
# gap — §9.4). Matched by sqlglot canonical ``sql_name()`` (uppercase).
_SUPPORTED_LATERAL_GENERATORS = frozenset({"FLATTEN", "SPLIT_TO_TABLE"})

# Table-valued generator functions (§9.3): more than one in a single SELECT
# projection raises in SCOS. ``STACK`` parses as an Anonymous func, so it is
# matched by name rather than node type.
_GENERATOR_NODE_NAMES = frozenset({"EXPLODE", "POSEXPLODE", "INLINE", "STACK"})

# §9.2 — EXPLAIN over DDL is rejected (Snowflake EXPLAIN covers DML only).
# Matched on the leading keyword of an EXPLAIN command's payload.
_EXPLAIN_DDL_LEADERS = frozenset({"CREATE", "DROP", "ALTER", "TRUNCATE", "REPLACE"})

# §9.15 — every EXPLAIN <mode> emits a plain Snowflake EXPLAIN; the mode is
# silently ignored.
_EXPLAIN_MODE_LEADERS = frozenset({"FORMATTED", "EXTENDED", "CODEGEN", "COST", "LOGICAL"})

# behavioral:sql.* token rules (kb_rules.json) the §9 AST detectors supersede
# when the parse succeeds. The token rules fire on bare keyword presence; the
# AST checks the surrounding structure (PARTITION present, non-empty GROUP BY,
# unsupported generator) and so eliminates their false positives.
_SUPERSEDED_BEHAVIORAL_RULES = frozenset({
    "behavioral:sql.insert-overwrite-partition",  # §9.6 — needs PARTITION too
    "behavioral:sql.grouping-sets",                # §9.12 — needs non-empty GROUP BY
    "behavioral:sql.lateral-view",                 # §9.4 — needs unsupported generator
})

# Rule-id partitions are now derived from the catalog (``data/sql_rules.json``)
# so detection, rewrite, and the gate share one source of truth. Re-exported
# here for existing importers (e.g. ``scos_gates``). MECHANICAL = a SAFE,
# semantics-preserving deterministic rewrite exists (gate-enforced); JUDGMENT =
# everything else detected (annotate / LLM fixer). Note: ``window_without_order_by``
# and ``multicolumn_not_in`` carry a best-effort transform but are JUDGMENT (their
# rewrites are not truly deterministic / NULL-equivalent).
from rag.sql_catalog import (  # noqa: E402,F401
    JUDGMENT_RULE_IDS,
    MECHANICAL_RULE_IDS,
)

# Drop ``${VAR}.`` / ``{var}.`` qualifiers and turn lone placeholders into bare
# identifiers so templated workload SQL parses. Substitutions never add or
# remove newlines, so reported line numbers stay aligned with the source file.
_PH_QUALIFIER = re.compile(r"\$?\{[^}]+\}\.")
_PH_LONE = re.compile(r"\$?\{[^}]+\}")


def _normalize_placeholders(sql: str) -> str:
    sql = _PH_QUALIFIER.sub("", sql)
    sql = _PH_LONE.sub("_ph_", sql)
    return sql


@dataclass
class AstFinding:
    rule_id: str
    anchor: str
    severity: str
    disposition: str
    note: str
    jira: str | None
    line: int
    snippet: str


@dataclass
class AstResult:
    """Outcome of a successful sqlglot parse.

    ``analyze_sql`` returns ``None`` (not an empty ``AstResult``) when the text
    could not be parsed, so the caller can distinguish "parsed, found nothing"
    from "could not parse, fall back to regex".
    """
    findings: list[AstFinding] = field(default_factory=list)
    # AST adjudicated window-ordering: suppress token rules carrying the
    # WINDOW_ORDER_NOTE_MARK note.
    handled_window_order: bool = False
    # AST adjudicated NOT IN arity: suppress token rules anchored on NOT IN.
    handled_not_in: bool = False
    # Regex detector rule_ids the AST supersedes.
    handled_detectors: frozenset[str] = _SUPERSEDED_DETECTORS
    # behavioral:sql.* token rule_ids the §9 AST detectors supersede.
    handled_token_rule_ids: frozenset[str] = _SUPERSEDED_BEHAVIORAL_RULES


def _node_line(node, base_line: int) -> int:
    """Best-effort source line for an sqlglot node: the minimum ``line`` over
    the node and its descendants' ``.meta`` (leaf tokens carry positions).
    ``base_line`` offsets into the enclosing document (1 for a standalone
    file)."""
    best: int | None = None
    candidates = [node]
    try:
        candidates.extend(node.walk())
    except Exception:
        pass
    for c in candidates:
        nn = c[0] if isinstance(c, tuple) else c
        meta = getattr(nn, "meta", None)
        if meta:
            ln = meta.get("line")
            if ln is not None and (best is None or ln < best):
                best = ln
    return base_line + (best - 1) if best else base_line


def analyze_sql(text: str, base_line: int = 1):
    """Parse ``text`` as Spark SQL and run AST structural detectors.

    Returns an :class:`AstResult` on a clean parse, or ``None`` if sqlglot
    cannot parse the text (caller falls back to the regex path). A single
    unparseable statement disables the AST pass for the whole text — a
    deliberately conservative contract that never silences the regex fallback.
    """
    from rag.sql_engine import detect  # lazy import: sql_engine imports sql_ast

    findings = detect(text, base_line)
    if findings is None:
        return None  # parse failed — caller falls back to the regex/token path
    result = AstResult(handled_window_order=True, handled_not_in=True)
    result.findings = findings
    return result
