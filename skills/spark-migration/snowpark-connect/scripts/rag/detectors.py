# flake8: noqa: T201
"""Structural (pattern) detectors for SCOS gaps that no token trigger can express.

Some of the highest-severity SCOS gaps are not "API X is used" — they are
*shapes* in the query: an output alias that collides with a column name, a
``CASE`` expression used as a window aggregate, an ``IN (SELECT ...)`` inside a
LEFT JOIN ``ON`` clause, or a cast to an INTERVAL type. The token KB
(``kb_rules.json``) cannot encode these (their anchors degrade to junk tokens
like ``avg`` / ``MIN`` / ``int`` and get demoted to reference-only ``manual``).

These detectors run small, bounded regex/structural matchers over SQL text and
emit the same curated notes those manual rules carry. They are deliberately
framed as *candidates*: precise confirmation of LCA collisions needs the table
schema, so the LLM still adjudicates. The point is to make these gaps visible
instead of silently missed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

# --------------------------------------------------------------------------- #
# Individual structural matchers. Each returns [(char_offset, snippet), ...].
# --------------------------------------------------------------------------- #

_AS_ALIAS = re.compile(r"\bAS\s+([A-Za-z_]\w*)", re.IGNORECASE)
_GROUP_BY = re.compile(
    r"\bGROUP\s+BY\b(.*?)(?:\bHAVING\b|\bORDER\s+BY\b|\bQUALIFY\b|\bLIMIT\b|\)|$)",
    re.IGNORECASE | re.DOTALL,
)


def _detect_lca(text: str) -> list[tuple[int, str]]:
    """Output alias whose name also appears as a GROUP BY key (Lateral Column
    Alias collision candidate)."""
    aliases = {m.group(1).lower() for m in _AS_ALIAS.finditer(text)}
    if not aliases:
        return []
    hits: list[tuple[int, str]] = []
    for g in _GROUP_BY.finditer(text):
        gb_idents = {w.lower() for w in re.findall(r"[A-Za-z_]\w*", g.group(1))}
        common = sorted(aliases & gb_idents)
        if common:
            hits.append((g.start(), f"GROUP BY key collides with SELECT alias: {common}"))
    return hits


# A windowed aggregate whose argument is a CASE expression: ``SUM(CASE ... END) OVER``
_WINDOW_CASE = re.compile(
    r"\b\w+\s*\(\s*CASE\b[\s\S]{0,600}?\bEND\s*\)\s*OVER\b", re.IGNORECASE
)


def _detect_window_case(text: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(0)[:120]) for m in _WINDOW_CASE.finditer(text)]


# IN (SELECT ...) inside the ON clause of a LEFT [OUTER] JOIN (bounded lookahead
# so it doesn't span unrelated clauses).
_IN_IN_ON = re.compile(
    r"\bLEFT\s+(?:OUTER\s+)?JOIN\b[\s\S]{0,300}?\bON\b[\s\S]{0,200}?\bIN\s*\(\s*SELECT\b",
    re.IGNORECASE,
)


def _detect_in_subquery_on(text: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(0)[:120]) for m in _IN_IN_ON.finditer(text)]


# Cast to an INTERVAL type: SQL ``CAST(x AS INTERVAL ...)`` / ``x :: INTERVAL`` or
# PySpark ``.cast("interval ...")``.
_CAST_INTERVAL = re.compile(
    r"\bCAST\s*\([^()]*\bAS\s+INTERVAL\b"
    r"|::\s*INTERVAL\b"
    r"|\.cast\s*\(\s*['\"]\s*interval\b",
    re.IGNORECASE,
)


def _detect_cast_interval(text: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(0)[:120]) for m in _CAST_INTERVAL.finditer(text)]


# ``CORR(DISTINCT x, y)`` — Snowflake silently drops the DISTINCT, returning a
# different value than Spark. This MUST be a lexical detector (not an AST rule):
# sqlglot's spark dialect cannot parse ``CORR(DISTINCT ...)`` (the aggregate
# requires a second positional expression), so the AST pass returns None and the
# whole file falls back to this regex path. Matches CORR with a leading DISTINCT
# argument; the comma confirms the two-arg aggregate form (not a window OVER).
_CORR_DISTINCT = re.compile(
    r"\bCORR\s*\(\s*DISTINCT\b",
    re.IGNORECASE,
)


def _detect_corr_distinct(text: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(0)[:120]) for m in _CORR_DISTINCT.finditer(text)]


# Snowflake stage path (`@DB.SCHEMA.STAGE/...`) embedded in a path-typed argument
# to a Spark file reader. SCOS's read-format dispatchers reject the stage syntax
# with "UNSUPPORTED FORMAT <fmt> WITH NO PATH" (or schema-inference failures
# where the path can't be resolved). A customer porting from a Snowflake-stage-
# backed pipeline naturally writes ``spark.read.csv("@DB.SCHEMA.STAGE/file")``
# but SCOS file readers do not understand stage paths — they expect S3, ADLS,
# GCS, or DBFS-style paths.
_SF_STAGE_IN_READ = re.compile(
    r"""\.read\s*(?:\.\w+\s*)?\(\s*['"]\s*@\w+\.\w+\.\w+/""",
    re.IGNORECASE,
)


def _detect_sf_stage_in_read(text: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(0)[:120]) for m in _SF_STAGE_IN_READ.finditer(text)]


@dataclass(frozen=True)
class Detector:
    rule_id: str
    anchor: str
    severity: str
    disposition: str
    note: str
    jira: str | None
    fn: Callable[[str], list[tuple[int, str]]]


DETECTORS: list[Detector] = [
    Detector(
        rule_id="detector:lca_alias_collision",
        anchor="SELECT alias collides with column name (LCA)",
        severity="high",
        disposition="annotate",
        jira="SNOW-3595425",
        note=(
            "When a SELECT alias shares a name with an actual table column, "
            "Snowflake rejects the query with `ambiguous column name`. Spark "
            "resolves the ambiguity by context (GROUP BY refers to the column, "
            "not the alias). This is the Lateral Column Alias (LCA) "
            "disambiguation gap. Candidate: an output alias name is reused as a "
            "GROUP BY key — verify it does not collide with a real column."
        ),
        fn=_detect_lca,
    ),
    Detector(
        rule_id="detector:window_case_aggregate",
        anchor="CASE expression as window aggregate",
        severity="high",
        disposition="annotate",
        jira=None,
        note=(
            "Snowflake's window function resolution does not support `CASE`/"
            "`WHEN` expressions as the aggregate function selection mechanism. "
            "A windowed aggregate whose argument is a CASE expression "
            "(e.g. `SUM(CASE WHEN ... END) OVER (...)`) fails; Spark handles it "
            "via its plan-based optimizer. Rewrite the CASE outside the window."
        ),
        fn=_detect_window_case,
    ),
    Detector(
        rule_id="detector:in_subquery_in_on_clause",
        anchor="IN (SELECT ...) in LEFT JOIN ON clause",
        severity="high",
        disposition="annotate",
        jira=None,
        note=(
            "When an `IN (SELECT ...)` subquery appears in the `ON` clause of a "
            "LEFT OUTER JOIN, Snowflake incorrectly drops rows that don't match "
            "the subquery — producing INNER JOIN semantics instead of LEFT "
            "OUTER JOIN semantics. Spark correctly keeps all left-side rows. "
            "Move the IN predicate into a WHERE/derived table."
        ),
        fn=_detect_in_subquery_on,
    ),
    Detector(
        rule_id="detector:cast_to_interval",
        anchor="CAST to INTERVAL type",
        severity="high",
        disposition="annotate",
        jira="SNOW-3595418",
        note=(
            "Casting integer or decimal values to interval types fails in "
            "Snowflake. Spark supports this implicitly. SCOS has no workaround "
            "translation for these cast patterns; construct the interval "
            "explicitly (e.g. via make_interval / numtodsinterval-style logic)."
        ),
        fn=_detect_cast_interval,
    ),
    Detector(
        rule_id="detector:corr_distinct",
        anchor="CORR(DISTINCT ...)",
        severity="medium",
        disposition="annotate",
        jira=None,
        note=(
            "`CORR(DISTINCT x, y)` silently drops the DISTINCT in Snowflake, "
            "returning a slightly different correlation than Spark (which "
            "deduplicates the pairs first). Lexical detector: sqlglot cannot "
            "parse `CORR(DISTINCT ...)`, so the AST pass is unavailable. "
            "Deduplicate explicitly: `SELECT corr(x, y) FROM (SELECT DISTINCT "
            "x, y FROM t)`."
        ),
        fn=_detect_corr_distinct,
    ),
    Detector(
        rule_id="detector:snowflake_stage_path_in_read",
        anchor="Snowflake stage path in spark.read.<format>(...)",
        severity="high",
        disposition="annotate",
        jira=None,
        note=(
            "SCOS's file readers do not accept Snowflake stage paths "
            "(`@DB.SCHEMA.STAGE/...`) as the path argument — schema inference "
            "fails (`Given path: '@DB.SCHEMA.STAGE/...'`) or the format "
            "rejects the call (`UNSUPPORTED FORMAT <fmt> WITH NO PATH`). "
            "Customers porting from a Snowflake-stage-backed pipeline naturally "
            "write `spark.read.csv(\"@DB.SCHEMA.STAGE/file\")`; rewrite to a "
            "supported scheme — `spark.read.table(\"DB.SCHEMA.TABLE\")` for "
            "Snowflake tables, or stage the data via `COPY INTO` first. "
            "Production telemetry: 176 errors / 1 customer in week 1."
        ),
        fn=_detect_sf_stage_in_read,
    ),
]


def run_detectors(text: str) -> list[tuple[Detector, int, str]]:
    """Run every structural detector over ``text``.

    Returns ``(detector, char_offset, snippet)`` tuples so the caller can
    translate the offset into a line number relative to its own base line.
    """
    out: list[tuple[Detector, int, str]] = []
    for det in DETECTORS:
        for pos, snippet in det.fn(text):
            out.append((det, pos, snippet))
    return out
