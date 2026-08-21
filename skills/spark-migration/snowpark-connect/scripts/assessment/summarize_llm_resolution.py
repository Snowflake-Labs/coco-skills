"""Summarize the LLM data-edge enrichment result for the user-facing report.

Reads ``llm_resolved_data_edges`` from an ``AssessmentIR.json`` and prints a
compact JSON blob so the coordinator can report the enrichment outcome to the
user (SKILL.md Phase 1b) **without loading the whole IR into context**.

Grouping is by the ``severity`` the resolver LLM stamped on each confirmed
unresolvable edge/import (see ``UnresolvableSeverity`` in ``assess_ir.py``) —
``critical`` (a required input missing from the export, can block migration),
``informational`` (real I/O with a runtime-only target — a lineage blind spot),
or ``benign`` (a scanner misclassification / dead code). This script does not
judge severity itself: that call belongs to the LLM that read the code. It only
aggregates that judgment, which is deterministic and testable.

Usage
-----
::

    python summarize_llm_resolution.py path/to/Reports/AssessmentIR.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def summarize(ir: dict) -> dict:
    """Group the enrichment result by the LLM-assigned severity."""
    llm = ir.get("llm_resolved_data_edges") or {}
    edges = llm.get("edges") or []

    # Every confirmed-unresolvable edge, plus imports the LLM confirmed
    # unresolvable — both carry a severity the resolver assigned.
    unresolvable = list(llm.get("unresolvable_edges") or [])
    unresolvable += [
        i for i in (llm.get("resolved_imports") or [])
        if i.get("resolution_type") == "unresolvable"
    ]

    critical: list[dict] = []
    informational: list[dict] = []
    benign = 0
    for u in unresolvable:
        item = {"file": u.get("file"), "line": u.get("line"),
                "why": u.get("why_unresolvable")}
        sev = u.get("severity")
        if sev == "critical":
            critical.append(item)
        elif sev == "benign":
            benign += 1
        else:
            # "informational", or an import left unclassified — surface it
            # (never silently drop) without raising it as a blocker.
            informational.append(item)

    return {
        "resolved": sum(1 for e in edges if e.get("source") == "resolved_unresolved"),
        "newly_discovered": sum(1 for e in edges if e.get("source") == "newly_discovered"),
        "baseline_unresolved_edges": len(ir.get("unresolved_data_edges") or []),
        "baseline_unresolved_imports": len(ir.get("unresolved_dynamic_imports") or []),
        "clean": not critical,
        "critical": critical,
        "informational": informational,
        "benign_count": benign,
        "insights": llm.get("llm_insights") or [],
    }


def run(ir_path: Path) -> int:
    try:
        ir = json.loads(Path(ir_path).read_text())
    except (OSError, ValueError) as e:
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        return 3
    print(json.dumps(summarize(ir), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(
            json.dumps({
                "status": "error",
                "message": "Usage: summarize_llm_resolution.py <AssessmentIR.json>",
            }),
            file=sys.stderr,
        )
        return 3
    return run(Path(args[0]))


if __name__ == "__main__":
    raise SystemExit(main())
