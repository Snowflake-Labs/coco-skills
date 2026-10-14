#!/usr/bin/env python3
"""Deterministic EWI severity classifier — the single source of truth.

Severity (``Error`` / ``Warning`` / ``IO`` / ``Fixed``) is NOT guessed from the
human-readable marker prose. It is a pure function of three structured facts
that every detection already carries:

    support      — does the construct exist on SCOS?
                   {unsupported, partial, behavioral, supported}
                   (``unsupported`` == a real call is a guaranteed runtime
                    failure; from the api-catalog ``status`` field.)

    category     — what KIND of construct is it?
                   {code_logic, data_io, performance, config}

    disposition  — what did the migrator DO to the emitted code?
                   {left_in_place, stubbed, rewritten, dropped, annotated}
                   (recipes know this by their action; the fixer knows if it
                    fixed; a bare kb-rule detection is ``left_in_place``.)

The decision table (see ``classify_status``):

  * rewritten / dropped and equivalent .......... Fixed   (code now runs correctly)
  * category == data_io ......................... IO      (repoint to stage/table/connector)
  * category == performance ..................... Warning (perf advice; runs)
  * disposition == stubbed ...................... Warning (runs, semantically incomplete)
  * support == unsupported & left_in_place ...... Error   (WILL fail at runtime)
  * otherwise (behavioral / partial) ............ Warning (runs, differs)

This eliminates the prose-regex classifier: the message is descriptive only,
never load-bearing. Adding a rule/recipe means declaring (support, category,
disposition) once; the severity follows and is exhaustively testable.
"""
from __future__ import annotations

import re
from typing import Optional

# Canonical value sets ------------------------------------------------------
SUPPORTS = ("unsupported", "partial", "behavioral", "supported")
CATEGORIES = ("code_logic", "data_io", "performance", "config")
DISPOSITIONS = ("left_in_place", "stubbed", "rewritten", "dropped", "annotated")

STATUSES = ("Error", "Warning", "IO", "Fixed")


def classify_status(
    support: Optional[str],
    category: Optional[str],
    disposition: str = "left_in_place",
    *,
    fix_is_equivalent: bool = True,
) -> str:
    """Return the EWI status from structured facts. Pure and total.

    ``support``/``category`` may be None (LLM-only finding with no rule); the
    conservative fallback keeps a genuine-looking finding actionable without
    over-Erroring: unknown category + unknown support -> Warning (advisory),
    NOT Error. Only a declared ``unsupported`` + ``code_logic`` left in the code
    yields ``Error``.
    """
    support = (support or "").strip().lower() or None
    category = (category or "").strip().lower() or None
    disposition = (disposition or "left_in_place").strip().lower()

    # A real rewrite/removal that leaves equivalent code is a completed fix.
    if disposition in ("rewritten", "dropped") and fix_is_equivalent:
        return "Fixed"
    # A stubbed construct runs (returns a placeholder) but needs manual work —
    # checked before support so a stubbed *unsupported* API is still Warning.
    if disposition == "stubbed":
        return "Warning"
    # Data input/output plumbing is a repoint (stage/table/connector), never a
    # code-conversion Error — checked before support so an *unsupported* format
    # or connector routes to IO, not Error.
    if category == "data_io":
        return "IO"
    # The one path to Error: a guaranteed runtime failure LEFT IN the code. This
    # beats the performance bucket so an unsupported perf/lifecycle API that
    # actually raises (e.g. DataFrame.checkpoint) is Error, while a mere perf
    # ADVISORY (count() may hang) is not `unsupported` and falls through to
    # Warning below.
    if support == "unsupported" and disposition in ("left_in_place", "annotated"):
        return "Error"
    # Performance advice / everything behavioral / partial / supported — runs.
    return "Warning"


# ---------------------------------------------------------------------------
# Deterministic category inference (used to backfill kb_rules and to bucket
# LLM-only findings). Identity-based on the api anchor / rule_id — NEVER on the
# free-text note, which can mention example paths/keys and mislead.
# ---------------------------------------------------------------------------

# data_io: external storage, unsupported file formats, DB/source connectors,
# filesystem. Deliberately does NOT include supported formats (csv/json/parquet)
# or generic verbs (save/load) — those work on SCOS and would over-match.
_CAT_DATA_IO = re.compile(
    r"\borc\b|\bavro\b|\.text\b|\bdelta\b|deltatable|"
    r"\bjdbc\b|redshift|hivewarehouse|hive_warehouse|pyodbc|\bodbc\b|\bkafka\b|\bkinesis\b|"
    r"dbutils\.fs\b|createexternaltable|external.?table|"
    r"s3[an]?://|gs://|abfss?://|dbfs:|hdfs://",
    re.I,
)
# performance: lifecycle / materialization / perf-only knobs (the code runs).
_CAT_PERF = re.compile(
    r"\bcache\b|\bpersist\b|unpersist|checkpoint|\.count\b|materiali|repartition|"
    r"broadcast.?join|hotpath|\bskew\b|\bspill\b|localiterator|prefetch|\bobserve\b",
    re.I,
)
# config: session/runtime/parameterization knobs (not data, not logic).
_CAT_CONFIG = re.compile(
    r"spark\.conf|spark\.sql\.[a-z]|dbutils\.widgets|dbutils\.secrets|dbutils\.notebook|"
    r"\.config\(|enablehivesupport|sparksession\.builder|setloglevel|"
    r"spark\.driver|spark\.executor|hadoopconfiguration",
    re.I,
)


def _rule_id_tail(rule_id: str) -> str:
    """The meaningful tail of a rule_id, dropping the provenance prefix.

    rule_ids look like ``csv:sql_test:unpivot.sql`` or ``parity:jdbc#redshift``
    or ``apicat:session_io.pyspark.sql.DataFrameReader.orc#unsupported``. The
    leading ``<source>:`` token is provenance (``csv``/``parity``/``apicat``/…)
    and must NOT drive category — else every csv-sourced test rule reads as the
    CSV data format. Keep only the segment after the last ``:``.
    """
    rid = rule_id or ""
    return rid.rsplit(":", 1)[-1]


def infer_category(api: object, rule_id: str = "") -> str:
    """Category from the rule's ANCHOR identity: the ``api`` list plus the
    rule_id TAIL (provenance prefix stripped). data_io and config beat the
    code_logic default; performance is checked last (a perf knob on an I/O op is
    still fundamentally I/O)."""
    parts = list(api) if isinstance(api, (list, tuple)) else [api]
    ident = " ".join(map(str, parts)) + " " + _rule_id_tail(rule_id)
    if _CAT_DATA_IO.search(ident):
        return "data_io"
    if _CAT_CONFIG.search(ident):
        return "config"
    if _CAT_PERF.search(ident):
        return "performance"
    return "code_logic"


# api-catalog ``status`` -> support axis.
def support_from_status(status: Optional[str]) -> Optional[str]:
    s = (status or "").strip().lower()
    if s == "unsupported":
        return "unsupported"
    if s == "partial":
        return "partial"
    if s == "behavioral":
        return "behavioral"
    return None  # absent -> behavioral/context-dependent (not a guaranteed failure)
