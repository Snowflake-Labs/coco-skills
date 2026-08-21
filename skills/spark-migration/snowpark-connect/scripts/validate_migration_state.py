#!/usr/bin/env python3
"""
SNOW-XXXXXXX: Post-run validator for migration_state.json.

Asserts that every required phase recorded evidence in `migration_state.json`,
either via the canonical `phases_completed[<key>]` block or via a documented
legacy top-level field. Designed to be the LAST step of every migration run,
so silent skips become loud failures.

Required phases (per migrate-pyspark-to-snowpark-connect/SKILL.md):
    0_5_preprocess - Phase 0.5 deterministic recipe pre-processing
    1_analysis     - Analyzer agent
    1a_assessment_report - Assessment-report render (Phase 1a)
    2_fixes        - Fixer agent
    2a_coverage    - Coverage verification (orchestrator)   -- legacy field:
                     `orchestrator_coverage_verified: true`
    2b_compilation - Compilation gate                       -- legacy field:
                     `compilation_reverted_count: N`
    2c_verification - Evidence-based verification gate
    3_imports      - Deterministic import/header updater (update_imports.py)
    4_reports      - Reporter agent

NOT required:
    4a_validation  - Self-attested post-run state validation (see OPTIONAL_PHASES).
                     Older runs recorded this under `4b_validation`; that key is
                     still accepted as a back-compat alias.

Usage:
    python3 validate_migration_state.py --state /path/to/migration_state.json
    python3 validate_migration_state.py --state ... --strict   # exit 1 on any miss
    python3 validate_migration_state.py --state ... --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------- Schema spec ---------------------------------------------------- #

# Each entry: (canonical_key, legacy_top_level_key_or_None, human_label)
REQUIRED_PHASES_PYTHON: list[tuple[str, str | None, str]] = [
    ("0_5_preprocess", None, "Phase 0.5 — Deterministic pre-processing"),
    ("1_analysis", None, "Phase 1 — Analyzer"),
    ("1a_assessment_report", None, "Phase 1a — Assessment report"),
    ("2_fixes", None, "Phase 2 — Fixer"),
    ("2a_coverage", "orchestrator_coverage_verified", "Phase 2a — Coverage verification"),
    ("2b_compilation", "compilation_reverted_count", "Phase 2b — Compilation gate"),
    ("2c_verification", None, "Phase 2c — Evidence-based verification"),
    ("3_imports", None, "Phase 3 — Imports & headers"),
    ("4_reports", None, "Phase 4 — Reports"),
]

# Scala uses ``2a_fallback`` (not ``2a_coverage``). Its sole deterministic
# pre-processing tier is AST-level (Scalafix), recorded under ``0_5b_scalafix``
# (the regex recipe tier and its ``0_5_preprocess`` key were removed). Like
# Python it renders the HTML readiness report early (Phase 1a, pre-fix), so
# ``1a_assessment_report`` is required and ``4_reports`` covers the CSVs only.
# Phase 2c is required (same as Python and Java): it generates SPRKCNTSCL0099
# partial-migration findings; without it those codes never appear in Issues.csv.
REQUIRED_PHASES_SCALA: list[tuple[str, str | None, str]] = [
    ("0_5b_scalafix", None, "Phase 0.5 — AST pre-processing (Scalafix)"),
    ("1_analysis", None, "Phase 1 — Analyzer"),
    ("1a_assessment_report", None, "Phase 1a — Assessment report"),
    ("2_fixes", None, "Phase 2 — Fixer"),
    ("2a_fallback", None, "Phase 2a — Fallback / coverage"),
    ("2b_compilation", "compilation_reverted_count", "Phase 2b — Compilation gate"),
    # Phase 2c generates SPRKCNTSCL0099 partial-migration findings and prevents
    # stale noise from polluting Issues.csv. The Scala SKILL explicitly notes this
    # is the sole writer of those codes. Java has it; Scala must too.
    ("2c_verification", None, "Phase 2c — Evidence-based verification"),
    ("3_imports", None, "Phase 3 — Imports & headers"),
    ("4_reports", None, "Phase 4 — Reports"),
]

# Java mirrors Scala's phase structure: sole deterministic pre-processing tier is
# AST-level (JavaParser), recorded under 0_5c_javaparser. Like Scala it renders
# the HTML readiness report early (Phase 1a, pre-fix) and uses 2a_fallback.
REQUIRED_PHASES_JAVA: list[tuple[str, str | None, str]] = [
    ("0_5c_javaparser", None, "Phase 0.5 — AST pre-processing (JavaParser)"),
    ("1_analysis", None, "Phase 1 — Analyzer"),
    ("1a_assessment_report", None, "Phase 1a — Assessment report"),
    ("2_fixes", None, "Phase 2 — Fixer"),
    ("2a_fallback", None, "Phase 2a — Fallback / coverage"),
    ("2b_compilation", "compilation_reverted_count", "Phase 2b — Compilation gate"),
    ("2c_verification", None, "Phase 2c — Evidence-based verification"),
    ("3_imports", None, "Phase 3 — Imports & headers"),
    ("4_reports", None, "Phase 4 — Reports"),
]

# Back-compat alias: REQUIRED_PHASES points to Python phases so existing callers
# that import this symbol directly are unaffected.
REQUIRED_PHASES = REQUIRED_PHASES_PYTHON

# Phases that are explicitly NOT required (skipping them is fine, surfacing
# their absence as a notice keeps the report honest).
#
# `4a_validation` lives here on purpose: the validator itself writes (or asks
# the agent to write) this entry as a self-attestation, so requiring it would
# create a chicken-and-egg problem on the first run. Treating it as optional
# makes its presence visible in the human report without failing strict mode.
OPTIONAL_PHASES: list[tuple[str, str | None, str]] = [
    ("0_6_sql_rewrite", None, "Phase 0.6 — Standalone SQL rewrite"),
    # Phase 1.1 (python / scala / java): the analyzer defers every non-decidable
    # block as a ``needs_adjudication`` row and this phase confirms-or-dismisses
    # them. Optional because a workload with nothing deferred legitimately skips
    # it — but it must be a RECOGNIZED key, otherwise every run that does
    # adjudicate reports ``1_5_adjudication`` as an unrecognized extra key.
    ("1_5_adjudication", None, "Phase 1.1 — Adjudication of deferred findings"),
    ("4a_validation", None, "Phase 4a — State validation (self-attestation)"),
    # Scala-only, conditional: emitted by Phase 3b only when the workload has
    # Scala notebooks (flattened to source entrypoints). Recognized as
    # informational so it is not reported as an unrecognized key.
    ("3b_notebook_source", None, "Phase 3b — Scala notebooks → source entrypoints"),
]

# Back-compat: phases_completed keys that older runs wrote under a different
# name. The first present alias satisfies the canonical key, and the alias is
# not reported as an "unrecognized" extra key.
PHASE_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "4a_validation": ("4b_validation",),
}


def _required_phases_for_language(language: str) -> list[tuple[str, str | None, str]]:
    """Return the correct required-phase list for *language*."""
    if language == "scala":
        return REQUIRED_PHASES_SCALA
    if language == "java":
        return REQUIRED_PHASES_JAVA
    return REQUIRED_PHASES_PYTHON

ALLOWED_SKIP_STATUSES = {"skipped", "not_applicable"}
PASS_STATUSES = {"passed", "complete", "ok", "done"}


# ---------- Result types --------------------------------------------------- #


@dataclass
class PhaseResult:
    key: str
    label: str
    status: str  # "ok" | "ok_legacy" | "ok_skipped_with_reason" | "missing" | "skipped_no_reason"
    detail: str

    @property
    def is_failure(self) -> bool:
        return self.status in {"missing", "skipped_no_reason"}


@dataclass
class ValidationReport:
    state_path: str
    results: list[PhaseResult]
    optional_results: list[PhaseResult]
    extra_keys: list[str]

    @property
    def has_failures(self) -> bool:
        return any(r.is_failure for r in self.results)


# ---------- Validation logic ---------------------------------------------- #


def _classify_phase_entry(entry: Any) -> tuple[str, str]:
    """Return (status, detail) for a value found at phases_completed[key]."""
    if isinstance(entry, dict):
        status = str(entry.get("status", "")).lower()
        if status in ALLOWED_SKIP_STATUSES:
            reason = entry.get("skip_reason") or entry.get("reason")
            if reason:
                return ("ok_skipped_with_reason", f"skipped: {reason}")
            return ("skipped_no_reason", f"status={status!r} but no skip_reason")
        if status in PASS_STATUSES or "status" not in entry:
            # Pre-existing trials commonly record subfields without a status key
            # (e.g. `2_fixes: {processed_files: [...]}`); treat that as success.
            return ("ok", f"status={status or 'implicit'}")
        return ("missing", f"unknown status={status!r}")
    if entry in (True, "true", 1):
        return ("ok", "true")
    return ("missing", f"unrecognized value: {entry!r}")


def _phase_present_via_legacy(state: dict, legacy_key: str | None) -> tuple[bool, str]:
    if legacy_key is None:
        return (False, "")
    if legacy_key not in state:
        return (False, "")
    val = state[legacy_key]
    if val is True or (isinstance(val, int) and val >= 0) or (isinstance(val, str) and val):
        return (True, f"legacy field {legacy_key}={val!r}")
    return (False, "")


def _has_standalone_sql(state: dict) -> bool:
    """True if ``migrated_dir`` holds a plain (non-Databricks-JSON) ``.sql`` file.

    Standalone ``.sql`` files are not in the manifest, so their presence is the
    signal that Phase 0.6 (the only step that rewrites them) was actually needed.
    Databricks native-JSON ``.sql`` notebooks start with ``{`` and are excluded."""
    migrated = state.get("migrated_dir") or ""
    if not migrated:
        return False
    root = Path(migrated)
    if not root.is_dir():
        return False
    for p in root.rglob("*.sql"):
        try:
            head = p.read_text(encoding="utf-8", errors="ignore")[:64].lstrip()
        except OSError:
            continue
        if not head.startswith("{"):
            return True
    return False


def validate(state: dict, state_path: str, language: str = "python") -> ValidationReport:
    pc = state.get("phases_completed", {}) or {}
    results: list[PhaseResult] = []
    optional_results: list[PhaseResult] = []

    canonical_seen: set[str] = set()
    required_phases = _required_phases_for_language(language)

    for key, legacy, label in required_phases:
        if key in pc:
            status, detail = _classify_phase_entry(pc[key])
            results.append(PhaseResult(key, label, status, detail))
            canonical_seen.add(key)
            continue

        legacy_ok, legacy_detail = _phase_present_via_legacy(state, legacy)
        if legacy_ok:
            results.append(
                PhaseResult(
                    key,
                    label,
                    "ok_legacy",
                    legacy_detail + " (canonical phases_completed key missing)",
                )
            )
            continue

        results.append(
            PhaseResult(
                key,
                label,
                "missing",
                "no canonical key, no legacy field",
            )
        )

    for key, legacy, label in OPTIONAL_PHASES:
        found_key = key if key in pc else next(
            (alias for alias in PHASE_KEY_ALIASES.get(key, ()) if alias in pc),
            None,
        )
        if found_key is not None:
            status, detail = _classify_phase_entry(pc[found_key])
            if found_key != key:
                detail = f"{detail} (via legacy key {found_key!r})"
            optional_results.append(PhaseResult(key, label, status, detail))

    # Phase 0.6 is optional for SQL-free workloads, but REQUIRED when standalone
    # .sql files are present: otherwise their SCOS gaps are detected (Phase 1)
    # and never rewritten. Promote a miss to a hard failure in that case.
    if _has_standalone_sql(state):
        ok = False
        if "0_6_sql_rewrite" in pc:
            status, _ = _classify_phase_entry(pc["0_6_sql_rewrite"])
            ok = status in {"ok", "ok_legacy", "ok_skipped_with_reason"}
        if not ok:
            results.append(PhaseResult(
                "0_6_sql_rewrite",
                "Phase 0.6 — Standalone SQL rewrite (required: .sql present)",
                "missing",
                "standalone .sql files are present but Phase 0.6 did not run — "
                "their SCOS gaps were detected but never rewritten",
            ))

    alias_keys = {alias for aliases in PHASE_KEY_ALIASES.values() for alias in aliases}
    expected_keys = {k for k, _, _ in required_phases + OPTIONAL_PHASES} | alias_keys
    extra = sorted(k for k in pc.keys() if k not in expected_keys)

    return ValidationReport(
        state_path=state_path,
        results=results,
        optional_results=optional_results,
        extra_keys=extra,
    )


# ---------- Output -------------------------------------------------------- #


_STATUS_ICON = {
    "ok": "[OK]    ",
    "ok_legacy": "[LEGACY]",
    "ok_skipped_with_reason": "[SKIP*] ",
    "missing": "[MISS]  ",
    "skipped_no_reason": "[BAD]   ",
}


def render_text(report: ValidationReport) -> str:
    lines: list[str] = []
    lines.append(f"Validating: {report.state_path}")
    lines.append("")
    lines.append("Required phases:")
    for r in report.results:
        icon = _STATUS_ICON.get(r.status, "[??]    ")
        lines.append(f"  {icon} {r.key:<18} {r.label:<42} {r.detail}")
    if report.optional_results:
        lines.append("")
        lines.append("Optional phases (informational):")
        for r in report.optional_results:
            icon = _STATUS_ICON.get(r.status, "[??]    ")
            lines.append(f"  {icon} {r.key:<18} {r.label:<42} {r.detail}")
    if report.extra_keys:
        lines.append("")
        lines.append("Unrecognized phases_completed keys (ignored):")
        for k in report.extra_keys:
            lines.append(f"  - {k}")
    lines.append("")
    failed = [r for r in report.results if r.is_failure]
    if failed:
        lines.append(f"FAIL: {len(failed)} required phase(s) missing or skipped without reason:")
        for r in failed:
            lines.append(f"  - {r.key}: {r.detail}")
    else:
        lines.append("PASS: all required phases present.")
    return "\n".join(lines)


def render_json(report: ValidationReport) -> str:
    return json.dumps(
        {
            "state_path": report.state_path,
            "verdict": "FAIL" if report.has_failures else "PASS",
            "required": [
                {"key": r.key, "label": r.label, "status": r.status, "detail": r.detail}
                for r in report.results
            ],
            "optional": [
                {"key": r.key, "label": r.label, "status": r.status, "detail": r.detail}
                for r in report.optional_results
            ],
            "extra_phases_completed_keys": report.extra_keys,
        },
        indent=2,
    )


# ---------- CLI ----------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--state", required=True, help="Path to migration_state.json"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on any required-phase miss. Without --strict, missing phases "
        "are still reported but the script exits 0 (advisory mode for adoption).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human-readable text.",
    )
    parser.add_argument(
        "--language",
        default="python",
        choices=["python", "scala", "java"],
        help=(
            "Migration language. Controls which required-phase set is checked. "
            "Default: python (back-compat). Use 'scala' for Scala migrations, 'java' for Java migrations."
        ),
    )
    args = parser.parse_args(argv)

    state_path = Path(args.state)
    if not state_path.exists():
        print(f"ERROR: state file not found: {state_path}", file=sys.stderr)
        return 2
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: state file is not valid JSON: {e}", file=sys.stderr)
        return 2

    report = validate(state, str(state_path), language=args.language)
    print(render_json(report) if args.json else render_text(report))

    if args.strict and report.has_failures:
        return 1
    return 0


if __name__ == "__main__":
    # Unit-style assertions to verify the language gate switches correctly.
    _py = _required_phases_for_language("python")
    _sc = _required_phases_for_language("scala")
    _jv = _required_phases_for_language("java")
    assert any(k == "2a_coverage" for k, _, _ in _py), "Python phases must include 2a_coverage"
    assert not any(k == "2a_coverage" for k, _, _ in _sc), "Scala phases must NOT include 2a_coverage"
    assert any(k == "2a_fallback" for k, _, _ in _sc), "Scala phases must include 2a_fallback"
    assert not any(k == "2a_fallback" for k, _, _ in _py), "Python phases must NOT include 2a_fallback"
    assert any(k == "1a_assessment_report" for k, _, _ in _sc), \
        "Scala phases must include 1a_assessment_report (HTML rendered pre-fix)"
    # Scala's sole pre-processing tier is AST (Scalafix) under 0_5b_scalafix; the
    # regex 0_5_preprocess key was removed. Python keeps its libcst 0_5_preprocess.
    assert any(k == "0_5b_scalafix" for k, _, _ in _sc), \
        "Scala phases must require 0_5b_scalafix (sole AST pre-processing tier)"
    assert not any(k == "0_5_preprocess" for k, _, _ in _sc), \
        "Scala phases must NOT include 0_5_preprocess (regex tier removed)"
    assert all(k in {p[0] for p in _py} for k in ("0_5_preprocess", "1_analysis", "4_reports")), \
        "Python phases must include core phases"
    assert all(k in {p[0] for p in _sc} for k in ("0_5b_scalafix", "1_analysis", "4_reports")), \
        "Scala phases must include core phases"
    # Java assertions: mirrors Scala structure with javaparser pre-processing tier.
    assert any(k == "0_5c_javaparser" for k, _, _ in _jv), \
        "Java phases must require 0_5c_javaparser (AST pre-processing tier)"
    assert any(k == "2a_fallback" for k, _, _ in _jv), \
        "Java phases must include 2a_fallback (like Scala)"
    assert not any(k == "0_5_preprocess" for k, _, _ in _jv), \
        "Java phases must NOT include 0_5_preprocess"
    assert not any(k == "0_5b_scalafix" for k, _, _ in _jv), \
        "Java phases must NOT include 0_5b_scalafix"
    assert all(k in {p[0] for p in _jv} for k in ("0_5c_javaparser", "1_analysis", "4_reports")), \
        "Java phases must include core phases"
    assert any(k == "2c_verification" for k, _, _ in _jv), \
        "Java phases must include 2c_verification (evidence-based verification gate)"
    print("validate_migration_state self-checks passed.")
    raise SystemExit(main())
