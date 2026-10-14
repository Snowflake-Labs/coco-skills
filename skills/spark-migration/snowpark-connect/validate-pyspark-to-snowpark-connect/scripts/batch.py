"""batch.py — batch planning, report merging, and async worker-pool runner.

Subcommands (when run as __main__):
  merge-reports   Fold per-batch run_index.json files into a merged manifest.
  pool            Async worker-pool runner: one SDK session per validation batch.
  (default)       Break sections.json into balanced LPT batches.
                  Flags: --manifest, --sections, --out, [--max-entrypoints], [--max-weight]

Usage — batch planning:
    batch.py --manifest <schemas/manifest.json> \\
             --sections <sections.json> \\
             --out <batches.json> \\
             [--max-entrypoints 8] [--max-weight 40]

Usage — merge reports (existing mode):
    batch.py merge-reports --batches-dir <path/to/Validation/batches> \\
                           --out <path/to/Validation> \\
                           [--run-id <id>]

Usage — merge reports (prepared mode):
    batch.py merge-reports --prepared <path/to/batches_prepared.json> \\
                           --out <path/to/Validation> \\
                           [--run-id <id>]

Usage — pool runner:
    batch.py pool --prepared <batches_prepared.json>
                  --primary-conv-root <primary repo path>
                  --original-source <path>
                  --connection <snowflake connection name>
                  --skill-directory <this skill's dir>
                  [--pool-size 5] [--model auto] [--effort high]
                  [--max-turns 0] [--retries 1]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from cortex_code_agent_sdk import (
    CortexCodeAgentOptions,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    query,
)

try:
    from progress_ui_emit import (  # type: ignore
        emit_eps_from_summary,
        find_progress_run,
        map_batch_phase,
        milestone as _ui_milestone,
        phase_end as _ui_phase_end,
        phase_start as _ui_phase_start,
        report_ready as _ui_report_ready,
        validation_complete as _ui_validation_complete,
    )
except Exception:  # pragma: no cover — Progress UI is optional
    def find_progress_run(*_a, **_k):  # type: ignore
        return None
    def map_batch_phase(*_a, **_k):  # type: ignore
        return None
    def _ui_phase_start(*_a, **_k):  # type: ignore
        return None
    def _ui_phase_end(*_a, **_k):  # type: ignore
        return None
    def _ui_milestone(*_a, **_k):  # type: ignore
        return None
    def _ui_report_ready(*_a, **_k):  # type: ignore
        return None
    def _ui_validation_complete(*_a, **_k):  # type: ignore
        return None
    def emit_eps_from_summary(*_a, **_k):  # type: ignore
        return None

# Allowlist for IDs that appear in filesystem paths (section_id, batch_id).
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


# ===========================================================================
# batch — break sections.json into balanced LPT batches
# ===========================================================================

# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------


def _load_ep_weights(manifest: dict) -> dict[str, int]:
    """Build ep_id -> weight; null/missing weight defaults to 1."""
    result: dict[str, int] = {}
    for ep in manifest.get("entrypoints") or []:
        ep_id = ep.get("id")
        if ep_id is None:
            continue
        w = ep.get("weight")
        result[ep_id] = int(w) if w is not None else 1
    return result


# ---------------------------------------------------------------------------
# Coverage validation
# ---------------------------------------------------------------------------


def validate_coverage(manifest: dict, sections: list[dict]) -> list[str]:
    """Return a list of human-readable coverage error strings (empty == valid).

    Checks (in order): duplicates, missing, unknown.  Each category is sorted
    by ep_id for stable output.
    """
    ep_ids_manifest: set[str] = set(_load_ep_weights(manifest).keys())

    # Build ep_id -> [section_id, ...] mapping (preserves duplicates within / across sections)
    ep_occurrences: dict[str, list[str]] = {}
    for sec in sections:
        sid = sec.get("section_id") or "<missing>"
        for eid in (sec.get("ep_ids") or []):
            ep_occurrences.setdefault(eid, []).append(sid)

    errors: list[str] = []

    # 1. Duplicates: ep appears in more than one occurrence (cross-section or within same section)
    for ep_id in sorted(ep_occurrences):
        sids = ep_occurrences[ep_id]
        if len(sids) > 1:
            unique_sids = sorted(set(sids))
            if len(unique_sids) == 1:
                errors.append(
                    f"entrypoint {ep_id} appears {len(sids)} times in section {unique_sids[0]};"
                    f" each entrypoint must appear exactly once"
                )
            else:
                errors.append(
                    f"entrypoint {ep_id} appears in {len(unique_sids)} sections"
                    f" ({', '.join(unique_sids)});"
                    f" each entrypoint must appear in exactly one section"
                )

    # 2. Missing: manifest EP not assigned to any section
    sectioned_eps = set(ep_occurrences.keys())
    for ep_id in sorted(ep_ids_manifest - sectioned_eps):
        errors.append(
            f"entrypoint {ep_id} is in the manifest but not assigned to any section"
        )

    # 3. Unknown: section EP not present in manifest (sorted by ep_id then section_id)
    unknown_pairs: list[tuple[str, str]] = []
    for sec in sections:
        sid = sec.get("section_id") or "<missing>"
        for eid in (sec.get("ep_ids") or []):
            if eid not in ep_ids_manifest:
                unknown_pairs.append((eid, sid))
    for ep_id, sid in sorted(set(unknown_pairs), key=lambda x: (x[0], x[1])):
        errors.append(
            f"section {sid} references entrypoint {ep_id},"
            f" which is not in the manifest"
        )

    return errors


# ---------------------------------------------------------------------------
# LPT bin-packing
# ---------------------------------------------------------------------------


def _lpt_split(
    ep_weights: list[tuple[str, int]],
    max_entrypoints: int,
    max_weight: int,
) -> tuple[list[list[tuple[str, int]]], list[str]]:
    """Bin-pack (ep_id, weight) pairs using Longest-Processing-Time (LPT).

    Returns (bins, warnings) where each bin is a list of (ep_id, weight) in
    insertion order.  Bins are deterministic: sort EPs by weight desc then
    ep_id asc; tie-break bin selection by lowest bin index.

    Invariant: when every individual EP weight <= max_weight, every resulting
    bin also has total_weight <= max_weight.  Achieved by retrying with one
    extra bin whenever a multi-EP bin exceeds max_weight, up to n_bins==n_eps.
    """
    warnings: list[str] = []

    if not ep_weights:
        return [], warnings

    # Deterministic sort: weight desc, ep_id asc as tie-break
    sorted_eps = sorted(ep_weights, key=lambda x: (-x[1], x[0]))
    n_eps = len(sorted_eps)

    # Warn once (before packing) for EPs that individually exceed max_weight.
    for ep_id, w in sorted_eps:
        if w > max_weight:
            warnings.append(
                f"ep {ep_id} weight {w} exceeds max-weight {max_weight};"
                f" placed in its own batch"
            )

    total_weight = sum(w for _, w in sorted_eps)

    n_bins = max(
        math.ceil(total_weight / max_weight),
        math.ceil(n_eps / max_entrypoints),
        1,
    )

    while True:
        bins: list[list[tuple[str, int]]] = [[] for _ in range(n_bins)]
        bin_weights: list[int] = [0] * n_bins

        for ep_id, w in sorted_eps:
            # Find lightest bin that still has room; tie-break by lowest index
            best_idx: int | None = None
            best_w = math.inf
            for i, b in enumerate(bins):
                if len(b) < max_entrypoints and bin_weights[i] < best_w:
                    best_w = bin_weights[i]
                    best_idx = i

            if best_idx is None:
                # All bins at entrypoint cap — open a new bin
                bins.append([(ep_id, w)])
                bin_weights.append(w)
            else:
                bins[best_idx].append((ep_id, w))
                bin_weights[best_idx] += w

        # Floor: one bin per EP — cannot improve further by adding bins.
        if len(bins) >= n_eps:
            break

        # Check for "avoidable" violations: bins with >1 EP over max_weight.
        # Single-EP bins over max_weight are unavoidable (already warned).
        any_avoidable = any(
            bin_weights[i] > max_weight and len(bins[i]) > 1
            for i in range(len(bins))
        )
        if not any_avoidable:
            break

        n_bins += 1

    return [b for b in bins if b], warnings


# ---------------------------------------------------------------------------
# Coverage reconciliation + batch assembly
# ---------------------------------------------------------------------------


def batch_sections(
    manifest: dict,
    sections: list[dict],
    max_entrypoints: int,
    max_weight: int,
) -> tuple[list[dict], list[str]]:
    """Return (batches, warnings).

    Whole sections are the packing unit: a section's entrypoints always stay
    together. A section that fits within both caps is packed as one unit, and
    several whole sections may share a batch when they fit (first-fit
    decreasing by weight) — so small sections fill each other's spare capacity
    instead of each forcing its own batch. A section too large for either cap is
    split on its own via LPT; its chunks are standalone and never mixed with
    other sections.

    A single-section batch keeps the familiar ``<section_id>__NN`` id; a batch
    holding more than one section is ``mixed__NN``. ``section_ids`` /
    ``section_names`` list every section in the batch.

    Raises ValueError on malformed input (missing section_id or ep_ids, or a
    non-positive cap). Coverage is assumed valid (call validate_coverage first).
    """
    if max_entrypoints <= 0 or max_weight <= 0:
        raise ValueError(
            f"max_entrypoints and max_weight must be positive; "
            f"got max_entrypoints={max_entrypoints}, max_weight={max_weight}"
        )
    warnings: list[str] = []
    ep_weight: dict[str, int] = _load_ep_weights(manifest)

    # Normalize + validate sections; compute each section's weight and size.
    norm: list[dict] = []
    for sec in sections:
        sid = sec.get("section_id") or ""
        if not sid:
            raise ValueError(f"section missing section_id: {sec!r}")
        if not _SAFE_ID.match(sid):
            raise ValueError(
                f"section_id {sid!r} contains invalid characters;"
                f" only [A-Za-z0-9_-] are allowed"
            )
        ep_ids_raw = sec.get("ep_ids")
        if ep_ids_raw is None:
            raise ValueError(f"section {sid!r} missing ep_ids")
        if not isinstance(ep_ids_raw, list):
            raise ValueError(f"section {sid!r}: ep_ids must be a list")
        ep_ids = list(ep_ids_raw)
        if not ep_ids:
            continue
        pairs: list[tuple[str, int]] = []
        for eid in ep_ids:
            if eid not in ep_weight:
                raise ValueError(
                    f"ep {eid} in section {sid!r} not found in manifest"
                    f" (coverage gate should have caught this)"
                )
            pairs.append((eid, ep_weight[eid]))
        norm.append({
            "section_id": sid,
            "name": sec.get("name") or sid,
            "pairs": pairs,
            "weight": sum(w for _, w in pairs),
            "n_eps": len(pairs),
        })

    batches: list[dict] = []

    def _emit(batch_id: str, secs: list[dict]) -> None:
        sids = [s["section_id"] for s in secs]
        snames = [s["name"] for s in secs]
        batches.append({
            "batch_id": batch_id,
            "section_ids": sids,
            "section_names": snames,
            "ep_ids": [eid for s in secs for eid, _ in s["pairs"]],
            "n_eps": sum(s["n_eps"] for s in secs),
            "total_weight": sum(s["weight"] for s in secs),
        })

    # 1. Oversized sections (exceed either cap): split on their own via LPT.
    fitting: list[dict] = []
    for s in norm:
        if s["n_eps"] > max_entrypoints or s["weight"] > max_weight:
            bins, sec_warnings = _lpt_split(s["pairs"], max_entrypoints, max_weight)
            warnings.extend(sec_warnings)
            for i, bin_eps in enumerate(bins, start=1):
                chunk = {
                    "section_id": s["section_id"], "name": s["name"],
                    "pairs": bin_eps,
                    "weight": sum(w for _, w in bin_eps),
                    "n_eps": len(bin_eps),
                }
                _emit(f"{s['section_id']}__{i:02d}", [chunk])
        else:
            fitting.append(s)

    # 2. Fitting sections: first-fit-decreasing by weight into shared batches,
    #    respecting BOTH caps. Multiple whole sections may share a batch.
    fitting.sort(key=lambda s: (-s["weight"], s["section_id"]))
    packed: list[dict] = []  # each: {"secs": [...], "weight": int, "n_eps": int}
    for s in fitting:
        placed = False
        for b in packed:
            if (b["weight"] + s["weight"] <= max_weight
                    and b["n_eps"] + s["n_eps"] <= max_entrypoints):
                b["secs"].append(s)
                b["weight"] += s["weight"]
                b["n_eps"] += s["n_eps"]
                placed = True
                break
        if not placed:
            packed.append({"secs": [s], "weight": s["weight"], "n_eps": s["n_eps"]})

    # 3. Name fitting batches: single section -> <sid>__01; multi -> mixed__NN.
    mixed_n = 0
    for b in packed:
        if len(b["secs"]) == 1:
            _emit(f"{b['secs'][0]['section_id']}__01", b["secs"])
        else:
            mixed_n += 1
            _emit(f"mixed__{mixed_n:02d}", b["secs"])

    return batches, warnings


def _build_output(
    batches: list[dict],
    warnings: list[str],
    max_entrypoints: int,
    max_weight: int,
) -> dict:
    n_batches = len(batches)
    n_eps = sum(b["n_eps"] for b in batches)
    total_w = sum(b["total_weight"] for b in batches)
    weights = [b["total_weight"] for b in batches]
    return {
        "max_entrypoints": max_entrypoints,
        "max_weight": max_weight,
        "batches": batches,
        "summary": {
            "n_batches": n_batches,
            "n_entrypoints": n_eps,
            "total_weight": total_w,
            "weight_min": min(weights) if weights else 0,
            "weight_max": max(weights) if weights else 0,
            "weight_mean": total_w / n_batches if n_batches else 0.0,
        },
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# CLI — batch planning
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Break sections.json into balanced LPT batches.",
    )
    parser.add_argument("--manifest", required=True, help="Path to schemas/manifest.json.")
    parser.add_argument("--sections", required=True, help="Path to sections.json.")
    parser.add_argument("--out", required=True, help="Output path for batches.json.")
    parser.add_argument(
        "--max-entrypoints", type=int, default=10,
        help="Max entrypoints per batch (default: 10).",
    )
    parser.add_argument(
        "--max-weight", type=int, default=80,
        help="Max total weight per batch (default: 80).",
    )
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    sections_path = Path(args.sections)
    out_path = Path(args.out)

    if not manifest_path.exists():
        print(
            f"[batch] error: manifest not found: {manifest_path}",
            file=sys.stderr,
        )
        return 1
    if not sections_path.exists():
        print(
            f"[batch] error: sections not found: {sections_path}",
            file=sys.stderr,
        )
        return 1

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[batch] error: cannot parse manifest: {exc}", file=sys.stderr)
        return 1

    try:
        sections = json.loads(sections_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[batch] error: cannot parse sections: {exc}", file=sys.stderr)
        return 1

    if not isinstance(sections, list):
        print(
            "[batch] error: sections.json must be a JSON array",
            file=sys.stderr,
        )
        return 1

    for sec in sections:
        if not sec.get("section_id"):
            print(
                f"[batch] error: section missing section_id: {sec!r}",
                file=sys.stderr,
            )
            return 1
        if "ep_ids" not in sec:
            print(
                f"[batch] error: section {sec['section_id']!r} missing ep_ids",
                file=sys.stderr,
            )
            return 1

    cov_errors = validate_coverage(manifest, sections)
    if cov_errors:
        print(
            "[batch] error: sections.json coverage check failed:",
            file=sys.stderr,
        )
        for err in cov_errors:
            print(f"  - {err}", file=sys.stderr)
        return 3

    try:
        batches, warnings = batch_sections(
            manifest, sections, args.max_entrypoints, args.max_weight
        )
    except ValueError as exc:
        print(f"[batch] error: {exc}", file=sys.stderr)
        return 1

    output = _build_output(batches, warnings, args.max_entrypoints, args.max_weight)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    n_batches = output["summary"]["n_batches"]
    n_eps = output["summary"]["n_entrypoints"]
    print(f"wrote {out_path} ({n_batches} batches, {n_eps} entrypoints)")
    return 0


# ===========================================================================
# batch (merge-reports) — fold per-batch run_index.json files into a merged manifest
# ===========================================================================

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------



def _load_json_tolerant(path: Path) -> tuple[dict, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:
        return {}, str(exc)


def _ep_overall(ep: dict) -> str:
    """Read verdict.overall with fallback to phase_b.verdict."""
    v = ep.get("verdict")
    if isinstance(v, dict):
        overall = v.get("overall", "")
        if overall:
            return overall
    return ep.get("phase_b", {}).get("verdict", "unknown")


def _ep_comparison(ep: dict) -> str:
    return ep.get("comparison", {}).get("verdict", "no_baseline")


# ---------------------------------------------------------------------------
# Status rollup
# ---------------------------------------------------------------------------

_PASSED_STATUSES = {"passed", "passed_no_baseline"}


def _rollup_status(batches_meta: list[dict], all_eps: list[dict]) -> str:
    """Precedence: in_progress > partial > passed."""
    # in_progress wins
    for b in batches_meta:
        if b.get("status") == "in_progress":
            return "in_progress"

    # partial if any batch failed to merge (missing/corrupt artifacts)
    for b in batches_meta:
        if b.get("status") in ("error", "missing"):
            return "partial"

    # partial if any EP is hard_stuck or real_divergence
    for ep in all_eps:
        overall = _ep_overall(ep)
        comp = _ep_comparison(ep)
        if overall == "hard_stuck" or comp == "real_divergence":
            return "partial"

    return "passed"


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------


def _compute_totals(all_eps: list[dict]) -> dict:
    totals: dict[str, int] = {
        "entrypoints": len(all_eps),
        "passed": 0,
        "passed_no_baseline": 0,
        "hard_stuck": 0,
        "other": 0,
        "match": 0,
        "cosmetic_divergence": 0,
        "real_divergence": 0,
        "no_baseline": 0,
    }
    for ep in all_eps:
        overall = _ep_overall(ep)
        comp = _ep_comparison(ep)
        if overall == "passed":
            totals["passed"] += 1
        elif overall == "passed_no_baseline":
            totals["passed_no_baseline"] += 1
        elif overall == "hard_stuck":
            totals["hard_stuck"] += 1
        else:
            totals["other"] += 1

        if comp == "match":
            totals["match"] += 1
        elif comp == "cosmetic_divergence":
            totals["cosmetic_divergence"] += 1
        elif comp == "real_divergence":
            totals["real_divergence"] += 1
        else:
            totals["no_baseline"] += 1

    return totals


# ---------------------------------------------------------------------------
# REPORT.md renderer
# ---------------------------------------------------------------------------

_VERDICT_LABEL = {
    "passed": "PASS",
    "passed_no_baseline": "PASS (no baseline)",
    "hard_stuck": "HARD_STUCK",
    "in_progress": "IN_PROGRESS",
}

_COMP_LABEL = {
    "match": "match",
    "cosmetic_divergence": "cosmetic_divergence",
    "real_divergence": "REAL_DIVERGENCE",
    "no_baseline": "no_baseline",
}


def _batch_duration_s(b: dict) -> str:
    """Wall-clock duration (s) between run.started_at and run.completed_at. `""` when unknown."""
    start = b.get("started_at")
    end = b.get("completed_at")
    if not (start and end):
        return ""
    try:
        # ISO 8601; tolerate trailing Z.
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return f"{(e - s).total_seconds():.0f}"


def _batch_tokens(b: dict) -> str:
    """input+output+cache_read+cache_creation from pool_status metrics. `""` when unknown."""
    m = b.get("metrics") or {}
    if not m:
        return ""
    total = (
        int(m.get("input_tokens", 0) or 0)
        + int(m.get("output_tokens", 0) or 0)
        + int(m.get("cache_creation_input_tokens", 0) or 0)
        + int(m.get("cache_read_input_tokens", 0) or 0)
    )
    return f"{total:,}" if total else ""


def _render_report(merged: dict) -> str:
    run = merged["run"]
    totals = merged["totals"]
    batches = merged["batches"]
    all_eps = merged["entrypoints"]
    parse_errors = merged.get("parse_errors", [])

    lines: list[str] = []

    lines.append("# Merged Validation Report\n")
    lines.append(f"**Run ID:** {run['id']}  ")
    lines.append(f"**Status:** {run['status']}  ")
    lines.append(f"**Batches:** {run['n_batches']}  ")
    lines.append(f"**Generated:** {run['completed_at']}  ")
    lines.append("")

    # Totals table
    lines.append("## Totals\n")
    lines.append("| Metric | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| Entrypoints | {totals['entrypoints']} |")
    lines.append(f"| passed | {totals['passed']} |")
    lines.append(f"| passed_no_baseline | {totals['passed_no_baseline']} |")
    lines.append(f"| hard_stuck | {totals['hard_stuck']} |")
    lines.append(f"| other | {totals['other']} |")
    lines.append(f"| comparison: match | {totals['match']} |")
    lines.append(f"| comparison: cosmetic_divergence | {totals['cosmetic_divergence']} |")
    lines.append(f"| comparison: real_divergence | {totals['real_divergence']} |")
    lines.append(f"| comparison: no_baseline | {totals['no_baseline']} |")
    lines.append("")

    # Per-batch sections
    lines.append("## Per-Batch Results\n")
    # Build EP lookup by batch
    eps_by_batch: dict[str, list[dict]] = {}
    for ep in all_eps:
        bid = ep.get("batch_id", "unknown")
        eps_by_batch.setdefault(bid, []).append(ep)

    for b in batches:
        bid = b["batch_id"]
        run_id = b.get("run_id", "")
        status = b.get("status", "unknown")
        report_path = b.get("report_path")
        b_time = _batch_duration_s(b)
        b_tokens = _batch_tokens(b)
        lines.append(f"### Batch `{bid}`\n")
        lines.append(f"- Run ID: `{run_id}`")
        lines.append(f"- Status: `{status}`")
        if b_time:
            lines.append(f"- Time: {b_time} s")
        if b_tokens:
            lines.append(f"- Tokens: {b_tokens}")
        if report_path:
            lines.append(f"- Report: `{report_path}`")
        lines.append("")
        lines.append("| Entrypoint | Overall | Comparison | Time (s) | Tokens |")
        lines.append("|------------|---------|------------|----------|--------|")
        for ep in eps_by_batch.get(bid, []):
            ep_id = ep.get("id", "?")
            overall = _ep_overall(ep)
            comp = _ep_comparison(ep)
            overall_label = _VERDICT_LABEL.get(overall, overall)
            comp_label = _COMP_LABEL.get(comp, comp)
            lines.append(
                f"| `{ep_id}` | {overall_label} | {comp_label} | {b_time} | {b_tokens} |"
            )
        lines.append("")

    # Needs human review
    review_eps = [
        ep for ep in all_eps
        if _ep_overall(ep) == "hard_stuck"
        or _ep_comparison(ep) in ("real_divergence", "no_baseline")
    ]
    lines.append("## Needs Human Review\n")
    if review_eps:
        lines.append("| Batch | Entrypoint | Overall | Comparison | Reason |")
        lines.append("|-------|------------|---------|------------|--------|")
        for ep in review_eps:
            bid = ep.get("batch_id", "?")
            ep_id = ep.get("id", "?")
            overall = _ep_overall(ep)
            comp = _ep_comparison(ep)
            reason = ""
            v = ep.get("verdict")
            if isinstance(v, dict):
                reason = v.get("reason", "")
            # LLM-authored reasons can contain literal `|` (SQL fragments,
            # column lists) and newlines — both split the markdown cell.
            reason = (reason or "").replace("|", "\\|").replace("\n", " ")
            overall_label = _VERDICT_LABEL.get(overall, overall)
            comp_label = _COMP_LABEL.get(comp, comp)
            lines.append(f"| `{bid}` | `{ep_id}` | {overall_label} | {comp_label} | {reason} |")
    else:
        lines.append("_No entrypoints require human review._")
    lines.append("")

    # Parse errors
    if parse_errors:
        lines.append("## Parse Errors\n")
        for pe in parse_errors:
            path = pe.get("path", pe.get("batch_id", "unknown"))
            error = pe.get("error", "unknown error")
            lines.append(f"- `{path}`: {error}")
        lines.append("")

    merge_warnings = merged.get("warnings", [])
    if merge_warnings:
        lines.append("## Warnings\n")
        for w in merge_warnings:
            lines.append(f"- {w}")
        lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Core merge logic
# ---------------------------------------------------------------------------


def merge(batches_dir: Path, out_dir: Path, run_id: str) -> dict:
    """Discover, load and merge all per-batch run_index.json files."""
    parse_errors: list[dict] = []
    batches_meta: list[dict] = []
    all_eps: list[dict] = []
    all_documented_divergences: list[dict] = []
    all_warnings: list = []

    # Optional: per-batch metrics from pool_status.json (single-EP batches → per-EP metrics).
    # pool_status is the only pool-level artifact — the same file is updated
    # live during the run and holds the terminal state at pool exit.
    pool_metrics: dict[str, dict] = {}
    pool_status_path = out_dir / "pool_status.json"
    if pool_status_path.is_file():
        try:
            _ps = json.loads(pool_status_path.read_text(encoding="utf-8"))
            for b in _ps.get("batches", []) or []:
                bid = b.get("batch_id")
                m = b.get("metrics")
                if bid and isinstance(m, dict):
                    pool_metrics[bid] = m
        except (OSError, json.JSONDecodeError):
            pool_metrics = {}

    # Discover batch dirs (sorted for determinism)
    batch_dirs = sorted(
        d for d in batches_dir.iterdir() if d.is_dir()
    ) if batches_dir.is_dir() else []

    for batch_dir in batch_dirs:
        batch_id = batch_dir.name
        index_path = batch_dir / "run_index.json"

        if not index_path.exists():
            all_warnings.append(
                f"batch {batch_id}: missing run_index.json — no entrypoints merged"
            )
            batches_meta.append({
                "batch_id": batch_id,
                "run_id": None,
                "status": "missing",
                "n_entrypoints": 0,
                "entrypoint_ids": [],
                "run_index_path": f"batches/{batch_id}/run_index.json",
                "report_path": None,
            })
            continue

        data, err = _load_json_tolerant(index_path)
        if err:
            parse_errors.append({
                "path": str(index_path.relative_to(batches_dir.parent)),
                "batch_id": batch_id,
                "error": err,
            })
            # Still record batch with error status
            batches_meta.append({
                "batch_id": batch_id,
                "run_id": None,
                "status": "error",
                "n_entrypoints": 0,
                "entrypoint_ids": [],
                "run_index_path": f"batches/{batch_id}/run_index.json",
                "report_path": None,
            })
            continue

        # Extract batch-level fields
        batch_run = data.get("run", {})
        batch_run_id = batch_run.get("id")
        batch_status = batch_run.get("status", "unknown")
        batch_started_at = batch_run.get("started_at")
        batch_completed_at = batch_run.get("completed_at")
        eps = data.get("entrypoints", [])

        # Augment each EP with batch_id
        for ep in eps:
            ep_copy = dict(ep)
            ep_copy["batch_id"] = batch_id
            all_eps.append(ep_copy)

        # Concat documented_divergences and warnings
        all_documented_divergences.extend(data.get("documented_divergences", []))
        all_warnings.extend(data.get("warnings", []))

        # Per-batch parse errors
        for pe in data.get("parse_errors", []):
            pe_copy = dict(pe)
            pe_copy["batch_id"] = batch_id
            parse_errors.append(pe_copy)

        # Check if REPORT.md exists for this batch
        report_md = batch_dir / "results" / "REPORT.md"
        report_path_str = f"batches/{batch_id}/results/REPORT.md" if report_md.exists() else None

        batches_meta.append({
            "batch_id": batch_id,
            "run_id": batch_run_id,
            "status": batch_status,
            "started_at": batch_started_at,
            "completed_at": batch_completed_at,
            "n_entrypoints": len(eps),
            "entrypoint_ids": [ep.get("id", "") for ep in eps],
            "run_index_path": f"batches/{batch_id}/run_index.json",
            "report_path": report_path_str,
            "metrics": pool_metrics.get(batch_id),
        })

    # Rollup status
    status = _rollup_status(batches_meta, all_eps)

    merged = {
        "run": {
            "id": run_id,
            "status": status,
            "n_batches": len(batches_meta),
            "completed_at": _iso_now(),
        },
        "batches": batches_meta,
        "entrypoints": all_eps,
        "totals": _compute_totals(all_eps),
        "documented_divergences": all_documented_divergences,
        "warnings": all_warnings,
        "parse_errors": parse_errors,
    }
    return merged


# ---------------------------------------------------------------------------
# Assemble from batches_prepared.json
# ---------------------------------------------------------------------------


def assemble_from_prepared(prepared_path: Path, out_dir: Path) -> tuple[Path, list[str]]:
    """Copy per-batch Validation trees from worktrees into <out_dir>/batches/.

    For each batch in batches_prepared.json:
    - Skip (with warning) if batch.error is non-null.
    - Skip (with warning) if batch_id fails the [A-Za-z0-9_-]+ allowlist.
    - Skip (with warning) if <worktree>/Validation does not exist.
    - Otherwise: shutil.copytree(<worktree>/Validation, <out_dir>/batches/<batch_id>/).

    Returns (batches_dir, warnings).
    """
    try:
        data = json.loads(prepared_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"[batch] error: could not read prepared file {prepared_path}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        raise
    batches_dir = out_dir / "batches"
    batches_dir.mkdir(parents=True, exist_ok=True)

    pool_failed: dict[str, str] = {}
    pool_status_path = out_dir / "pool_status.json"
    if pool_status_path.is_file():
        try:
            _ps = json.loads(pool_status_path.read_text(encoding="utf-8"))
            for b in _ps.get("batches", []) or []:
                bid = b.get("batch_id")
                if bid and b.get("status") == "failed":
                    pool_failed[bid] = b.get("error") or "pool batch failed"
        except (OSError, json.JSONDecodeError):
            pool_failed = {}

    warnings: list[str] = []
    for batch in data.get("batches", []):
        batch_id = batch.get("batch_id", "")
        error = batch.get("error")
        worktree = batch.get("worktree", "")

        if batch_id in pool_failed:
            warnings.append(f"batch {batch_id} skipped: {pool_failed[batch_id]}")
            continue

        if error is not None:
            warnings.append(f"batch {batch_id} skipped: {error}")
            continue

        if not batch_id or not _SAFE_ID.match(batch_id):
            warnings.append(f"batch {batch_id!r} skipped: invalid batch_id")
            continue

        val_dir = Path(worktree) / "Validation"
        if not val_dir.is_dir():
            warnings.append(f"batch {batch_id} skipped: no Validation dir")
            continue

        dest = batches_dir / batch_id
        # Realpath confinement — belt-and-suspenders after the allowlist check above.
        dest_real = dest.resolve()
        if not str(dest_real).startswith(str(batches_dir.resolve()) + os.sep):
            warnings.append(f"batch {batch_id} skipped: path escapes batches dir")
            continue
        shutil.copytree(val_dir, dest, dirs_exist_ok=True)

    return batches_dir, warnings


# ---------------------------------------------------------------------------
# CLI — merge reports
# ---------------------------------------------------------------------------


def merge_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge per-batch run_index.json files into a single top-level manifest.",
    )
    parser.add_argument(
        "--batches-dir",
        default=None,
        help="Path to the Validation/batches directory.",
    )
    parser.add_argument(
        "--prepared",
        default=None,
        help="Path to batches_prepared.json (alternative to --batches-dir).",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Path to the Validation root (output directory).",
    )
    parser.add_argument(
        "--run-id",
        default="merged",
        help="Run ID to use in the merged manifest (default: merged).",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out)

    # Exactly one of --batches-dir / --prepared must be given
    if (args.batches_dir is None) == (args.prepared is None):
        print(
            "[batch] error: exactly one of --batches-dir or --prepared must be given",
            file=sys.stderr,
        )
        return 2

    assemble_warnings: list[str] = []

    if args.prepared:
        prepared_path = Path(args.prepared)
        if not prepared_path.exists():
            print(
                f"[batch] error: prepared file does not exist: {prepared_path}",
                file=sys.stderr,
            )
            return 1
        batches_dir, assemble_warnings = assemble_from_prepared(prepared_path, out_dir)
    else:
        batches_dir = Path(args.batches_dir)
        if not batches_dir.exists():
            print(
                f"[batch] error: batches-dir does not exist: {batches_dir}",
                file=sys.stderr,
            )
            return 1

    merged = merge(batches_dir, out_dir, args.run_id)

    if assemble_warnings:
        merged["warnings"] = assemble_warnings + merged.get("warnings", [])

    # Write run_index.json
    out_index = out_dir / "run_index.json"
    out_index.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    # Write REPORT.md
    results_dir = out_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_report = results_dir / "REPORT.md"
    out_report.write_text(_render_report(merged), encoding="utf-8")

    print(str(out_report))
    return 0


# ===========================================================================
# pool — async worker-pool runner for per-batch Cortex Code SDK sessions
# ===========================================================================

# ---------------------------------------------------------------------------
# PoolBatch — per-batch state across the lifetime of the pool run
# ---------------------------------------------------------------------------


@dataclass
class PoolBatch:
    # Static fields (required, no defaults)
    batch_id: str
    ep_ids: list[str]
    n_eps: int
    total_weight: float
    worktree: str
    run_id: str
    validation_branch: str

    # Mutable runtime fields
    status: str = "queued"  # queued | running | done | failed
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    current_phase: str = "starting"
    attempt: int = 0
    started_at: str | None = None
    updated_at: str | None = None
    error: str | None = None
    summary_json_path: str | None = None
    metrics: dict[str, Any] | None = None
    pending_metrics: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _phase_a_settled(t: dict) -> bool:
    """True when a trial is terminal *with respect to Phase A*: either Phase A was
    skipped, or its LATEST Phase A iter produced a clean baseline (passing >= 1 and
    failing == 0).

    Uses the last iter deliberately (not "any clean iter ever", unlike
    validate.py::_phase_a_baseline_produced): a trial that passed on an earlier
    iter but regressed on its latest iter is still being worked, so it is NOT
    settled. A trial with no iters and not skipped is likewise not settled.
    """
    if t.get("status") == "phase_a_skipped":
        return True
    iters = t.get("phase_a_iters") or []
    if not iters:
        return False
    last = iters[-1]
    if not isinstance(last, dict):
        return False
    return last.get("passing", 0) >= 1 and last.get("failing", 0) == 0


def _derive_phase(state: dict | None) -> str:
    """Compute a human-readable phase label from a parsed state.json dict.

    Reads the batch worker's own state.json — the canonical, file-based
    source of truth. The label describes *what the worker is currently
    doing*, keyed off the last-completed milestone (and, once trials
    start, off ``state["phase"]`` and trial statuses).

    Workflow order:
        entrypoints_selected -> data-synthesizer subagent
        synth_deep           -> patch-author subagent
        patches_authored     -> source-runner runs Phase A
        phase_a_complete     -> scos-runner runs Phase B
        phase_b_complete     -> done
    """
    if state is None:
        return "starting"

    label = "starting"

    # Each label = what the worker does *after* the milestone flips to True.
    milestones_obj = state.get("milestones") or {}
    milestones = [
        ("entrypoints_selected", "synthesizing"),
        ("synth_deep", "patching"),
        ("patches_authored", "Phase A"),
        ("phase_a_complete", "Phase B"),
        ("phase_b_complete", "Phase B complete"),
    ]
    for key, milestone_label in milestones:
        if milestones_obj.get(key):
            label = milestone_label

    phase = state.get("phase", "init")
    trials = state.get("trials")

    # Terminal phase transitions from _advance_phase override milestone labels.
    if phase == "phase_b_done" or milestones_obj.get("phase_b_complete"):
        return "Phase B complete"

    # Live trial-progress overlay once trials exist.
    if isinstance(trials, dict) and trials:
        # Must match validate.py::_TERMINAL_TRIAL_STATUSES. phase_a_skipped is NOT
        # terminal — Phase B still runs for it and resolves it to passed_no_baseline
        # or hard_stuck (see batch-runner.md); counting it here would hide trials
        # that still have Phase B work pending.
        terminal = {"passed", "passed_no_baseline", "hard_stuck"}
        n_total = len(trials)

        if milestones_obj.get("phase_a_complete") or phase == "phase_a_done":
            n_b_terminal = sum(
                1 for t in trials.values()
                if (t.get("status") if isinstance(t, dict) else t) in terminal
            )
            label = f"Phase B ({n_b_terminal}/{n_total} terminal)"
        elif milestones_obj.get("patches_authored"):
            # "done" = terminal w.r.t. Phase A: skipped, or the latest Phase A
            # iter produced a clean baseline. A trial merely *having* iters (esp.
            # a failing latest iter) is still in progress and must not count.
            n_a_done = sum(
                1 for t in trials.values()
                if isinstance(t, dict) and _phase_a_settled(t)
            )
            label = f"Phase A ({n_a_done}/{n_total} done)"

    return label


def _read_phase(worktree: str) -> str:
    """Read the worker's state.json and derive its current phase."""
    state_path = Path(worktree) / "Validation" / "state.json"
    try:
        state: dict | None = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        state = None
    return _derive_phase(state)


def _batch_status_dict(b: PoolBatch) -> dict[str, Any]:
    return {
        "batch_id": b.batch_id,
        "status": b.status,
        "current_phase": b.current_phase,
        "session_id": b.session_id,
        "started_at": b.started_at,
        "updated_at": b.updated_at,
        "attempt": b.attempt,
        "error": b.error,
        "metrics": b.metrics,
        "summary_json_path": b.summary_json_path,
    }


def _build_metrics_totals(batches: list[PoolBatch]) -> dict[str, Any]:
    """Aggregate token + timing metrics across all batches."""
    def _s(key: str) -> int:
        return sum(int(b.metrics.get(key, 0) or 0) for b in batches if b.metrics)

    inp = _s("input_tokens")
    out = _s("output_tokens")
    cre = _s("cache_creation_input_tokens")
    cre_r = _s("cache_read_input_tokens")
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_creation_input_tokens": cre,
        "cache_read_input_tokens": cre_r,
        "total_tokens": inp + out + cre + cre_r,
        "duration_ms": _s("duration_ms"),
        "num_turns": _s("num_turns"),
    }


def _write_status(
    batches: list[PoolBatch],
    pool_size: int,
    out_dir: Path,
    pool_state: dict[str, Any] | None = None,
) -> None:
    ps = pool_state or {}
    n_done = sum(1 for b in batches if b.status == "done")
    n_failed = sum(1 for b in batches if b.status == "failed")

    data = {
        "updated_at": _iso_now(),
        "run": {
            "pool_size": pool_size,
            "started_at": ps.get("started_at"),
            "finished_at": ps.get("finished_at"),
            "status": ps.get("status", "running"),
            "n_batches": len(batches),
            "n_done": n_done,
            "n_failed": n_failed,
            "metrics_totals": _build_metrics_totals(batches),
        },
        "merge_report_path": ps.get("merge_report_path"),
        "batches": [_batch_status_dict(b) for b in batches],
    }
    tmp = out_dir / "pool_status.json.tmp"
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, out_dir / "pool_status.json")


def _build_prompt(batch: PoolBatch, args: argparse.Namespace, base_sha: str) -> str:
    friction_log = getattr(args, "friction_log", None)
    friction_env = f"  FRICTION_LOG={friction_log}\n" if friction_log else ""
    friction_block = ""
    if friction_log:
        friction_block = (
            "\n"
            "FRICTION LOG (shared across batches):\n"
            f"  Path: {friction_log}\n"
            "  Append short bullet points to this file for every papercut you\n"
            "  hit — confusing skill docs, unclear error messages, tool\n"
            "  friction, wasted iterations, retries caused by bad instructions,\n"
            "  anything that slowed you down or that a future run should not\n"
            "  have to rediscover. One bullet per issue; include batch_id and a\n"
            "  brief description; keep entries short and factual.\n"
            "  Concurrent-safe append: use `printf '%s\\n' \"- ...\" >> $FRICTION_LOG`\n"
            "  (single line writes on Linux are atomic up to PIPE_BUF); create\n"
            "  the file if it doesn't exist.\n"
            "  Pass this path and the same instruction down to every subagent\n"
            "  you dispatch (data-synthesizer, patch-author, source-runner, scos-runner,\n"
            "  harvester) so they can record their own friction directly.\n"
        )

    return (
        f"You are the per-batch validation worker for batch {batch.batch_id}.\n"
        "A git worktree has already been prepared for this batch.\n"
        "Environment (also set as env vars):\n"
        f"  CONVERSION_ROOT={batch.worktree}\n"
        f"  PRIMARY_CONV_ROOT={args.primary_conv_root}\n"
        f"  BASE_SHA={base_sha}\n"
        f"  ORIGINAL_SOURCE={args.original_source}\n"
        f"  CONNECTION_NAME={args.connection}\n"
        f"  SKILL_DIRECTORY={args.skill_directory}\n"
        f"  batch_id={batch.batch_id}\n"
        f"{friction_env}"
        "\n"
        f"Read {args.skill_directory}/agents/batch-runner.md and follow it end-to-end"
        " for THIS batch:\n"
        "synthesize -> patch -> Phase A -> Phase B -> summary -> harvest -> write batch"
        " learnings.\n"
        "Do NOT ask the user anything; everything is in Validation/state.json and the"
        " env vars.\n"
        f"Finish only after `{getattr(args, 'control_script', 'validate.py')} summary` exits 0 AND the harvester has"
        " cherry-picked\n"
        "this batch's [MIGRATION-FIX] commits onto the deliverable branch.\n"
        "In your final message, include the per-EP terminal-status table.\n"
        f"{friction_block}"
    )


# ---------------------------------------------------------------------------
# Message processing
# ---------------------------------------------------------------------------


def _process_message(
    batch: PoolBatch,
    msg: Any,
    write_status_fn: Callable[[], None],
) -> None:
    """Consume an SDK message: capture cumulative ``ResultMessage`` metrics.

    Phase tracking is handled independently by `_phase_watcher`, which polls
    each worktree's ``Validation/state.json`` — the file-based source of
    truth updated by every `validate.py record-milestone` call the worker makes.

    Non-``ResultMessage`` messages update ``batch.updated_at`` but do not call
    ``write_status_fn()``; the phase watcher flushes ``pool_status.json`` on its
    periodic tick (default every 10 s), which is intentional to avoid excessive I/O.
    """
    ts = _iso_now()

    if isinstance(msg, ResultMessage):
        # Last ResultMessage seen is cumulative — stage authoritative metrics;
        # they are committed to batch.metrics only on successful completion.
        usage = getattr(msg, "usage", None) or {}
        if isinstance(usage, dict):
            batch.pending_metrics = {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                "duration_ms": int(getattr(msg, "duration_ms", 0) or 0),
                "num_turns": int(getattr(msg, "num_turns", 0) or 0),
                "total_cost_usd": getattr(msg, "total_cost_usd", None),
            }
        batch.updated_at = ts
        write_status_fn()
        return

    batch.updated_at = ts


async def _phase_watcher(
    batches: list[PoolBatch],
    write_status_fn: Callable[[], None],
    stop_event: asyncio.Event,
    interval: float = 10.0,
    progress_run: Path | None = None,
) -> None:
    """Refresh every running batch's ``current_phase`` from its worktree's
    ``Validation/state.json`` on a fixed cadence. ``state.json`` is the
    file-based source of truth — every phase transition is a
    ``validate.py record-milestone`` write. During long tool calls (Phase A/B test
    iterations) the SDK stream can be quiet for many minutes; without this
    watcher, ``pool_status.json`` would freeze on the last message seen. On
    every tick we also flush ``pool_status.json`` so ``updated_at`` stays
    fresh. Exits promptly when ``stop_event`` is set.
    """
    last_ui_phase: str | None = None
    while not stop_event.is_set():
        changed = False
        for b in batches:
            if b.status != "running":
                continue
            new_phase = _read_phase(b.worktree)
            if new_phase != b.current_phase:
                b.current_phase = new_phase
                changed = True
            b.updated_at = _iso_now()

            # Mirror the highest-priority running phase into the Progress UI.
            mapped = map_batch_phase(b.current_phase)
            if progress_run and mapped and mapped != last_ui_phase:
                if last_ui_phase:
                    _ui_phase_end(progress_run, last_ui_phase)
                _ui_phase_start(
                    progress_run, mapped,
                    message=f"batch {b.batch_id}: {b.current_phase}",
                )
                last_ui_phase = mapped

        # Always write on a tick when anything is running, so consumers see
        # a fresh `updated_at` even when milestones haven't advanced.
        if changed or any(b.status == "running" for b in batches):
            write_status_fn()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    if progress_run and last_ui_phase:
        _ui_phase_end(progress_run, last_ui_phase)


# ---------------------------------------------------------------------------
# Per-batch SDK session runner
# ---------------------------------------------------------------------------


def _verify_batch_completion(worktree: str) -> tuple[bool, str | None]:
    """Return (ok, error_message).

    Requires the same artifacts ``validate.py summary`` gates on before exit 0:
    a schema-valid ``summary.json`` (with ``decision.overall``), ``run_index.json``
    (with ``entrypoints``), ``results/REPORT.md``, and ``events.jsonl``.
    """
    val_dir = Path(worktree) / "Validation"
    results_dir = val_dir / "results"
    summary_path = results_dir / "summary.json"

    if not summary_path.is_file():
        return False, f"missing required artifact: Validation/results/summary.json"

    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"invalid summary.json: {exc}"

    decision = summary.get("decision")
    if not isinstance(decision, dict) or not decision.get("overall"):
        return False, (
            "summary.json missing decision.overall"
            " (validate.py summary may not have completed)"
        )

    index_path = val_dir / "run_index.json"
    if not index_path.is_file():
        return False, "missing required artifact: Validation/run_index.json"

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"invalid run_index.json: {exc}"

    if not isinstance(index.get("entrypoints"), list):
        return False, "run_index.json missing entrypoints list"

    report_path = results_dir / "REPORT.md"
    if not report_path.is_file():
        return False, "missing required artifact: Validation/results/REPORT.md"

    events_path = val_dir / "events.jsonl"
    if not events_path.is_file():
        return False, "missing required artifact: Validation/events.jsonl"

    return True, None


async def run_batch(
    batch: PoolBatch,
    args: argparse.Namespace,
    out_dir: Path,
    base_sha: str,
    write_status_fn: Callable[[], None],
) -> bool:
    batch.started_at = _iso_now()

    env: dict[str, str] = {
        "CONVERSION_ROOT": batch.worktree,
        "PRIMARY_CONV_ROOT": args.primary_conv_root,
        "BASE_SHA": base_sha,
        "ORIGINAL_SOURCE": args.original_source,
        "CONNECTION_NAME": args.connection,
        "SKILL_DIRECTORY": args.skill_directory,
        "batch_id": batch.batch_id,
    }
    friction_log = getattr(args, "friction_log", None)
    if friction_log:
        env["FRICTION_LOG"] = friction_log

    opts = CortexCodeAgentOptions(
        cwd=batch.worktree,
        connection=args.connection,
        model=args.model,
        effort=args.effort,
        permission_mode="bypassPermissions",
        allow_dangerously_skip_permissions=True,
        session_id=batch.session_id,
        add_dirs=[args.skill_directory, args.primary_conv_root],
        env=env,
        max_turns=None if args.max_turns == 0 else args.max_turns,
        max_buffer_size=100_000_000,
    )

    prompt = _build_prompt(batch, args, base_sha)
    result_ok = False

    # Use an explicit generator reference so we can call aclose() in THIS task
    # on any error.  Without this, Python schedules aclose() as a background
    # asyncio task; anyio's cancel scopes are task-scoped, so closing them in a
    # different task raises RuntimeError and corrupts async state for every
    # other running batch (CancelledError cascade).
    gen = query(prompt=prompt, options=opts)
    try:
        async for msg in gen:
            # Capture real session_id as early as possible
            if isinstance(msg, SystemMessage):
                sid = (getattr(msg, "data", {}) or {}).get("session_id")
                if sid:
                    batch.session_id = str(sid)
            elif isinstance(msg, (StreamEvent, ResultMessage)):
                sid = getattr(msg, "session_id", None)
                if sid:
                    batch.session_id = str(sid)

            _process_message(batch, msg, write_status_fn)

            if isinstance(msg, ResultMessage):
                result_ok = not bool(getattr(msg, "is_error", True))
    except BaseException:
        # Close the generator in this task before re-raising, so anyio cancel
        # scopes are torn down in the correct task context.
        try:
            await gen.aclose()
        except BaseException:
            pass
        raise

    if not result_ok:
        batch.error = "SDK session ended with error"
        return False

    ok, err = _verify_batch_completion(batch.worktree)
    if not ok:
        batch.error = err
        return False

    summary_path = Path(batch.worktree) / "Validation" / "results" / "summary.json"
    batch.summary_json_path = str(summary_path)
    batch.metrics = batch.pending_metrics
    return True


# ---------------------------------------------------------------------------
# Worker coroutine — one per concurrent slot
# ---------------------------------------------------------------------------


async def _worker(
    worker_id: int,
    queue: asyncio.Queue[PoolBatch],
    pending: set[str],
    batches: list[PoolBatch],
    args: argparse.Namespace,
    out_dir: Path,
    base_sha: str,
    retries: int,
    write_status_fn: Callable[[], None],
) -> None:
    while pending:
        try:
            batch = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

        batch.status = "running"
        batch.updated_at = _iso_now()
        write_status_fn()

        try:
            ok = await run_batch(batch, args, out_dir, base_sha, write_status_fn)
        except asyncio.CancelledError:
            # External cancellation (e.g. Ctrl+C → asyncio.run cancels gather)
            # must propagate so the pool exits promptly instead of draining the
            # queue. run_batch already cleaned up its query generator.
            raise
        except BaseException as exc:
            ok = False
            batch.error = repr(exc)
            print(
                f"[pool] {batch.batch_id} ERROR: {repr(exc)[:200]}",
                flush=True,
            )

        if ok:
            batch.status = "done"
            pending.discard(batch.batch_id)
        else:
            if batch.error is None:
                batch.error = "batch did not produce required validation artifacts"
            if batch.attempt < retries:
                batch.attempt += 1
                batch.status = "queued"
                # Fresh session for the retry — reusing a session_id with a new
                # query() can collide with the crashed session's transcript.
                batch.session_id = str(uuid.uuid4())
                batch.current_phase = "starting"
                batch.error = None
                batch.pending_metrics = {}
                # Brief delay so anyio cancel scopes from the failed session
                # fully unwind before we attempt a new SDK connection.
                await asyncio.sleep(1.0)
                queue.put_nowait(batch)
            else:
                batch.status = "failed"
                pending.discard(batch.batch_id)

        batch.updated_at = _iso_now()
        write_status_fn()

        queue.task_done()


# ---------------------------------------------------------------------------
# Merge-reports subprocess
# ---------------------------------------------------------------------------


def _run_merge_reports(args: argparse.Namespace, out_dir: Path) -> str | None:
    skill_path = Path(args.skill_directory)
    project_dir = skill_path.parent
    batch_script = skill_path / "scripts" / "batch.py"

    cmd = [
        "uv",
        "run",
        "--project",
        str(project_dir),
        "python",
        str(batch_script),
        "merge-reports",
        "--prepared",
        args.prepared,
        "--out",
        str(out_dir),
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            print(
                f"[pool] WARNING: merge-reports exited {proc.returncode}: "
                f"{proc.stderr[:300]}",
                flush=True,
            )
            return None
        # merge-reports prints the REPORT.md path; take the last non-empty line
        for line in reversed(proc.stdout.splitlines()):
            line = line.strip()
            if line:
                return line
        return None
    except Exception as exc:
        print(f"[pool] WARNING: merge-reports failed to launch: {exc}", flush=True)
        return None


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


async def _run(args: argparse.Namespace) -> int:
    if args.pool_size <= 0:
        print(
            f"[pool] error: --pool-size must be >= 1 (got {args.pool_size})",
            file=sys.stderr,
            flush=True,
        )
        return 2

    prepared_path = Path(args.prepared)
    if not prepared_path.exists():
        print(
            f"[pool] error: --prepared file does not exist: {prepared_path}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    try:
        prepared = json.loads(prepared_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"[pool] error: could not read --prepared file {prepared_path}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    base_sha: str = str(prepared.get("base_sha", ""))

    eligible = [b for b in prepared.get("batches", []) if b.get("error") is None]

    out_dir = Path(args.primary_conv_root) / "Validation"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not eligible:
        # No batches to validate (every prepared batch was already errored). Still
        # write pool_status.json so downstream tooling (orchestrator, Step 5
        # report consumer) finds the file it expects.
        print(
            "[pool] No eligible batches (all have errors). Attempting merge-reports...",
            flush=True,
        )
        now = _iso_now()
        _write_status(
            [],
            args.pool_size,
            out_dir,
            {"started_at": now, "finished_at": now, "status": "done", "merge_report_path": None},
        )
        merge_report_path = _run_merge_reports(args, out_dir)
        # Back-fill merge_report_path now that we have it
        _write_status(
            [],
            args.pool_size,
            out_dir,
            {"started_at": now, "finished_at": now, "status": "done", "merge_report_path": merge_report_path},
        )
        if merge_report_path:
            print(f"Merge report: {merge_report_path}", flush=True)
        return 0

    batches = [
        PoolBatch(
            batch_id=b["batch_id"],
            ep_ids=list(b.get("ep_ids") or []),
            n_eps=int(b.get("n_eps", 0)),
            total_weight=float(b.get("total_weight", 0)),
            worktree=str(b.get("worktree", "")),
            run_id=str(b.get("run_id", "")),
            validation_branch=str(b.get("validation_branch", "")),
        )
        for b in eligible
    ]

    _ps: dict[str, Any] = {
        "started_at": _iso_now(),
        "finished_at": None,
        "status": "running",
        "merge_report_path": None,
    }

    write_status_fn: Callable[[], None] = lambda: _write_status(
        batches, args.pool_size, out_dir, _ps
    )
    write_status_fn()

    # Progress UI (optional) — reuse the migrate skill's .migration-ui/ run dir.
    progress_run = find_progress_run(args.primary_conv_root)
    total_eps = sum(b.n_eps for b in batches)
    if progress_run:
        _ui_phase_start(
            progress_run, "survey",
            message=f"Validation pool starting · {len(batches)} batches · {total_eps} entrypoints",
        )
        _ui_milestone(
            progress_run, "validation",
            f"Validating {total_eps} entrypoints across {len(batches)} batches",
        )

    queue: asyncio.Queue[PoolBatch] = asyncio.Queue()
    for b in batches:
        queue.put_nowait(b)

    pending: set[str] = {b.batch_id for b in batches}
    n_workers = min(args.pool_size, len(batches))

    workers = [
        asyncio.create_task(
            _worker(
                i, queue, pending, batches, args, out_dir, base_sha,
                args.retries, write_status_fn,
            )
        )
        for i in range(n_workers)
    ]
    # pool_status.json is updated by (a) _phase_watcher every 10s (polls each
    # worktree's state.json for milestone advancement), (b) _process_message
    # on every ResultMessage (metrics), and (c) every batch status transition
    # in _worker via write_status_fn.
    phase_stop = asyncio.Event()
    watcher = asyncio.create_task(
        _phase_watcher(batches, write_status_fn, phase_stop, progress_run=progress_run)
    )
    try:
        await asyncio.gather(*workers)
    finally:
        phase_stop.set()
        await watcher

    finished_at = _iso_now()
    if progress_run:
        _ui_phase_end(progress_run, "survey")
        _ui_phase_start(progress_run, "harvest", message="Merging validation reports")

    merge_report_path = _run_merge_reports(args, out_dir)

    n_done = sum(1 for b in batches if b.status == "done")
    n_failed = sum(1 for b in batches if b.status == "failed")

    # Emit per-entrypoint results from each finished batch's summary.json
    if progress_run:
        for b in batches:
            if b.status != "done":
                continue
            summary_path = Path(b.worktree) / "Validation" / "results" / "summary.json"
            emit_eps_from_summary(progress_run, summary_path, total=total_eps or None)
        if merge_report_path:
            _ui_report_ready(progress_run, merge_report_path, phase="harvest")
        _ui_phase_end(progress_run, "harvest")
        _ui_validation_complete(progress_run, {
            "batches_done": n_done,
            "batches_failed": n_failed,
            "entrypoints": total_eps,
        })

    # Finalize pool_status.json with completion state and merge report path
    _ps["finished_at"] = finished_at
    _ps["status"] = "done" if n_failed == 0 else "partial"
    _ps["merge_report_path"] = merge_report_path
    write_status_fn()

    # Emit a single completion line — full detail is in pool_status.json
    print(
        f"[pool] done: {n_done}/{len(batches)} batches passed, {n_failed} failed. "
        f"Status: {out_dir / 'pool_status.json'}",
        flush=True,
    )

    if n_failed > 0:
        failed_ids = [b.batch_id for b in batches if b.status == "failed"]
        print(f"[pool] FAILED batches: {failed_ids}", flush=True)
        return 1

    return 0


def _parse_pool_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Worker-pool runner: one Cortex Code SDK session per validation batch, "
            "N concurrent."
        ),
    )
    p.add_argument(
        "--prepared", required=True, metavar="PATH", help="Path to batches_prepared.json"
    )
    p.add_argument(
        "--primary-conv-root",
        required=True,
        metavar="PATH",
        help="Primary conversion repo root",
    )
    p.add_argument(
        "--original-source", required=True, metavar="PATH", help="Original source path"
    )
    p.add_argument(
        "--connection", required=True, metavar="NAME", help="Snowflake connection name"
    )
    p.add_argument(
        "--skill-directory", required=True, metavar="PATH", help="This skill's directory"
    )
    p.add_argument(
        "--pool-size",
        type=int,
        default=5,
        metavar="N",
        help="Max concurrent sessions (default: 5)",
    )
    p.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        metavar="NAME",
        help="Model name (default: claude-sonnet-4-6); e.g. 'auto' or 'claude-opus-4-6'",
    )
    p.add_argument(
        "--effort", default=None, metavar="LEVEL", help="Effort level, e.g. 'high'"
    )
    p.add_argument(
        "--max-turns",
        type=int,
        default=0,
        metavar="N",
        help="Max turns per session; 0 = unlimited (default: 0)",
    )
    p.add_argument(
        "--retries",
        type=int,
        default=1,
        metavar="N",
        help="Re-enqueue attempts per batch on transient failure (default: 1)",
    )
    p.add_argument(
        "--friction-log",
        default=None,
        metavar="PATH",
        help=(
            "Optional path to a shared friction log. When set, every batch's "
            "prompt is augmented with an instruction to append papercuts, "
            "confusing docs, unclear errors, wasted iterations, and other "
            "friction encountered during the run to this file — and to pass "
            "the same path + instruction down to every subagent it dispatches."
        ),
    )
    p.add_argument(
        "--control-script",
        default="validate.py",
        metavar="SCRIPT",
        help=(
            "Base name of the control-plane script used for the summary finish-condition "
            "hint in the worker prompt (default: validate.py). Set to scos_state.py when "
            "running the Scala validator pool."
        ),
    )
    return p.parse_args()


def pool_main() -> int:
    args = _parse_pool_args()
    return asyncio.run(_run(args))


# ===========================================================================
# pool-status — human-readable status report over pool_status.json
# ===========================================================================


def _fmt_duration_short(started_at: str | None, end_at: str | None) -> str:
    if not (started_at and end_at):
        return ""
    try:
        s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    m = int((e - s).total_seconds()) // 60
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h {m % 60}m"


def _sum_tokens(m: dict | None) -> int:
    if not isinstance(m, dict):
        return 0
    return sum(int(m.get(k, 0) or 0) for k in (
        "input_tokens", "output_tokens",
        "cache_creation_input_tokens", "cache_read_input_tokens",
    ))


def _render_pool_status(pool_status: dict) -> str:
    """Header line + one line per batch (done, running, queued, failed)."""
    run = pool_status.get("run") or {}
    status = run.get("status", "unknown")
    n_batches = int(run.get("n_batches", 0) or 0)
    n_done = int(run.get("n_done", 0) or 0)
    n_failed = int(run.get("n_failed", 0) or 0)
    batches = pool_status.get("batches") or []
    n_running = sum(1 for b in batches if b.get("status") == "running")
    n_queued = sum(1 for b in batches if b.get("status") == "queued")
    dur = _fmt_duration_short(
        run.get("started_at"),
        run.get("finished_at") or pool_status.get("updated_at"),
    )
    tokens = int((run.get("metrics_totals") or {}).get("total_tokens", 0) or 0)

    header = f"[pool] {status}"
    if status in ("done", "partial") and dur:
        header += f" in {dur}"
    elif dur:
        header += f" {dur}"
    header += (
        f": {n_done}/{n_batches} done, {n_failed} failed, "
        f"{n_running} running, {n_queued} queued | pool tokens: {tokens:,}"
    )
    if status in ("done", "partial"):
        report = pool_status.get("merge_report_path")
        if report:
            header += f" | report: {report}"

    order = {"done": 0, "running": 1, "queued": 2, "failed": 3}
    rows = []
    for b in sorted(batches, key=lambda x: (order.get(x.get("status"), 4),
                                            x.get("batch_id") or "")):
        bs = (b.get("status") or "?").upper()
        bid = b.get("batch_id", "?")
        phase = b.get("current_phase") or "—"
        tok = _sum_tokens(b.get("metrics"))
        tok_part = f" — {tok:,} tokens" if tok else ""
        err = b.get("error")
        err_part = f" (error: {err})" if err else ""
        rows.append(f"  {bs:8s} {bid}: {phase}{tok_part}{err_part}")

    return "\n".join([header, *rows])


def _pool_status_main(argv: list[str] | None = None) -> int:
    """Read pool_status.json and print a human-readable status report."""
    parser = argparse.ArgumentParser(
        description="Print a status report for a validation pool.",
    )
    parser.add_argument(
        "--root", required=True, metavar="PATH",
        help="Path to the Validation dir containing pool_status.json.",
    )
    args = parser.parse_args(argv)

    path = Path(args.root) / "pool_status.json"
    if not path.is_file():
        print(f"[pool] pool_status.json not yet present at {path}")
        return 0
    try:
        pool_status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[pool] could not read pool_status.json: {exc!r}")
        return 0
    if not isinstance(pool_status.get("run"), dict):
        print(f"[pool] pool_status.json has no `run` block yet at {path}")
        return 0
    print(_render_pool_status(pool_status))
    return 0


# ===========================================================================
# Entry point — subcommand dispatcher
# ===========================================================================

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "merge-reports":
        sys.argv.pop(1)
        sys.exit(merge_main())
    elif len(sys.argv) > 1 and sys.argv[1] == "pool":
        sys.argv.pop(1)
        sys.exit(pool_main())
    elif len(sys.argv) > 1 and sys.argv[1] == "pool-status":
        sys.argv.pop(1)
        sys.exit(_pool_status_main())
    else:
        sys.exit(main())
