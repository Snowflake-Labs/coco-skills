#!/usr/bin/env python3
"""Evidence-based verification of per-file migration status.

The fixer agent self-reports completion in ``migration_state.json``
(``processed_files`` / ``files_done``), and ``fallback_transform.py`` stamps a
``SPRKCNTPY0099`` "Partial Migration" finding on whatever it deems
unprocessed. Neither signal is verified against the migrated output, so a
file can be recorded "done" without a real migration, or flagged "partial"
after a successful one (the SNOW-3383532 fallback key-mismatch bug).

This module cross-checks the self-reported state against objective,
deterministic evidence read from the migrated files themselves. No LLM, no
Snowflake — pure file + state inspection, so results are reproducible and
diffable.

Evidence collected per file:
  * recorded_done    -- fixer/coordinator listed the file as processed
                        (any of the keys ``_collect_done_files`` unions).
  * fixer_annotated  -- a genuine ``# SCOS:`` fix comment that is NOT a
                        fallback (…0099) import annotation. Deterministic
                        proof the LLM actually engaged with the file.
  * fallback_only    -- carries the fallback "not processed by the LLM fixer
                        agent" signature with NO real fixer edit. Deterministic
                        proof the LLM never processed it (mechanical-only).
  * residual_high_risk -- advisory: a high-risk analyzer snippet that still
                        appears verbatim in the migrated file. Surfaced as a
                        ``needs_review`` hint, NOT a status trigger — the
                        snippet match is fuzzy and can't override the hard
                        marker evidence above (e.g. the LLM may remove a call
                        and leave an explanatory comment).

Status precedence:
  not_attempted -> no completion record and the file isn't on disk.
  migrated      -> a genuine fixer marker is present (LLM engaged), or the
                   file is recorded done with no fallback-only signature.
  partial       -> fallback-only signature with real Spark work to do.
  trivial       -> no Spark surface and no findings (nothing to migrate).

The ``disagreements`` list is the headline output: files where the
self-reported state and the on-disk evidence conflict — most importantly
"state says done but the LLM provably never processed it".

Files classified as ``not_attempted`` are treated as a hard gate failure. In a
healthy run, Phase 2's coverage check should already have caught any missing
output file. If one still reaches Phase 2c, the workflow must escalate instead
of silently passing with ``disagreements == 0``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from fallback_transform import (
    FALLBACK_EWI_CODE_PY,
    FALLBACK_EWI_CODE_SCALA,
    _collect_done_files,
)

# Distinctive line the fallback header injects (see fallback_transform.py).
FALLBACK_SIGNATURE = "not processed by the LLM fixer agent"
# EWI codes shared with fallback_transform.py — not invented here.
FALLBACK_CODES = {FALLBACK_EWI_CODE_PY, FALLBACK_EWI_CODE_SCALA}
HIGH_RISK_THRESHOLD = 0.7
# Minimum length for a snippet line to be a distinctive "still present" probe;
# mirrors the reporter's resolution heuristic so the two stay consistent.
_PROBE_MIN_LEN = 12

STATUS_MIGRATED = "migrated"
STATUS_PARTIAL = "partial"
STATUS_TRIVIAL = "trivial"
STATUS_NOT_ATTEMPTED = "not_attempted"

# Reuse the same EWI codes fallback_transform.py already defines for "file not
# fully processed by the LLM". verify_migration.py is the authoritative writer
# now, but the code values themselves are inherited from the existing SCOS
# vocabulary rather than invented here.
HUMAN_ACTION_CODE_PY = FALLBACK_EWI_CODE_PY
HUMAN_ACTION_CODE_SCALA = FALLBACK_EWI_CODE_SCALA
HUMAN_ACTION_CODE = HUMAN_ACTION_CODE_PY  # default; see human_action_code()
HUMAN_ACTION_ROOT_CAUSE = (
    "Verified by evidence check: the LLM fixer did not process this file — "
    "only the mechanical fallback ran (no migration edits are present in the "
    "output)."
)
HUMAN_ACTION_FIX = (
    "Human action required. Re-run the migration fixer on this file, or migrate "
    "it manually: replace the Spark session/context initialization with the "
    "Snowpark Connect equivalent, convert the unsupported APIs flagged by the "
    "analyzer, then validate the output against sample data."
)


def human_action_code(language: str) -> str:
    return HUMAN_ACTION_CODE_SCALA if language in {"scala", "java"} else HUMAN_ACTION_CODE_PY

# A genuine fixer comment is "# SCOS:" / "// SCOS:" that does NOT carry a
# fallback (…0099) code. Fallback annotations look like
# "# SCOS: [SPRKCNTPY0099] PySpark import — review…".
_SCOS_COMMENT_RE = re.compile(r"(?:#|//)\s*SCOS(?:-WARN|-TODO)?:")
_SPARK_SURFACE_RE = re.compile(r"\b(pyspark|sparksession|sparkcontext|\bspark\.)", re.IGNORECASE)


def _has_fixer_edit(content: str) -> bool:
    for line in content.splitlines():
        if _SCOS_COMMENT_RE.search(line) and "0099" not in line:
            return True
    return False


def _has_spark_surface(content: str) -> bool:
    return bool(_SPARK_SURFACE_RE.search(content))


def _snippet_present(code_snippet: str, content_norm: str) -> bool:
    """True only when we can positively confirm the snippet survives.

    Inverse of the reporter's resolution heuristic, but with the opposite
    (low-noise) bias: if we can't find a distinctive line we do NOT claim the
    snippet is present. Used only for the advisory ``needs_review`` hint.
    """
    distinctive = [
        ln.strip()
        for ln in code_snippet.splitlines()
        if len(ln.strip()) >= _PROBE_MIN_LEN
    ]
    if not distinctive:
        return False
    probe = max(distinctive, key=len)
    return re.sub(r"\s+", " ", probe) in content_norm


def _resolve_migrated_path(rel_path: str, migrated_dir: str) -> str:
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.join(migrated_dir, rel_path)


def _normalize_rel_path(path: str) -> str:
    """Normalize a repo-relative path for stable comparisons."""
    norm = os.path.normpath(path).replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm


def _resolve_to_manifest(
    path: str,
    *,
    manifest_set: set[str],
    basename_map: dict[str, list[str]],
    migrated_dir: str,
) -> str | None:
    """Resolve an arbitrary path shape to an exact manifest entry.

    Prefer exact normalized relative-path matches. Only fall back to basename
    matching when the basename is unique in the manifest; otherwise the path is
    ambiguous and must not be attributed to any single file.
    """
    if not path:
        return None

    candidate = path
    if os.path.isabs(path):
        try:
            rel = os.path.relpath(path, migrated_dir)
        except ValueError:
            rel = path
        if not rel.startswith(".."):
            candidate = rel

    candidate = _normalize_rel_path(candidate)
    if candidate in manifest_set:
        return candidate

    matches = basename_map.get(os.path.basename(candidate), [])
    if len(matches) == 1:
        return matches[0]
    return None


def _real_findings(findings: list[dict]) -> list[dict]:
    """Drop the fallback's own SPRKCNTPY0099 meta-findings."""
    out = []
    for f in findings:
        if f.get("code") in FALLBACK_CODES:
            continue
        if f.get("snowpark_connect_category") == "Partial Migration":
            continue
        out.append(f)
    return out


def classify_file(
    rel_path: str,
    *,
    recorded_done: bool,
    migrated_dir: str,
    findings: list[dict],
) -> dict:
    """Classify one manifest file from state + on-disk evidence."""
    path = _resolve_migrated_path(rel_path, migrated_dir)
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            content = fh.read()
        on_disk = True
    except OSError:
        content = ""
        on_disk = False

    fixer_annotated = _has_fixer_edit(content)
    fallback_sig = FALLBACK_SIGNATURE in content
    fallback_only = fallback_sig and not fixer_annotated

    real = _real_findings(findings)
    high_risk = [f for f in real if (f.get("final_risk") or 0) >= HIGH_RISK_THRESHOLD]
    has_work = bool(real) or _has_spark_surface(content)

    # The fixer may record its verdict in analysis.json (the ``resolution``
    # field) rather than leaving an inline ``# SCOS:`` comment — notably for
    # findings it judged contextually safe. That write-back is just as much
    # proof of engagement as an inline marker, so treat it as such; otherwise a
    # fully-reviewed file with only "safe" outcomes looks un-processed.
    resolved_in_analysis = any((f.get("resolution") or "").strip() for f in real)
    fixer_engaged = fixer_annotated or resolved_in_analysis

    # Advisory only: high-risk snippets we can still positively spot in the file.
    content_norm = re.sub(r"\s+", " ", content)
    residual_high_risk = [
        f for f in high_risk if _snippet_present(f.get("code") or "", content_norm)
    ]

    # Hard, deterministic evidence drives status. The fuzzy snippet check above
    # is never allowed to override it (see main.py: real SPRKCNTPY4002 edits but
    # the snippet probe still matches an explanatory comment).
    if not on_disk:
        status = STATUS_NOT_ATTEMPTED
    elif fixer_engaged:
        status = STATUS_MIGRATED
    elif fallback_only:
        status = STATUS_PARTIAL if has_work else STATUS_TRIVIAL
    elif not has_work:
        status = STATUS_TRIVIAL
    elif recorded_done:
        status = STATUS_MIGRATED
    else:
        status = STATUS_PARTIAL

    needs_review = status == STATUS_MIGRATED and bool(residual_high_risk)

    # Self-report vs evidence conflict — the headline false-alarm detector.
    disagreement = None
    if not on_disk and recorded_done:
        disagreement = "state=done but output file is missing"
    elif recorded_done and status == STATUS_PARTIAL:
        disagreement = "state=done but LLM never processed the file (fallback-only)"
    elif not recorded_done and status == STATUS_MIGRATED:
        disagreement = "state=not-done but the file carries real LLM edits"

    return {
        "file": rel_path,
        "status": status,
        "recorded_done": recorded_done,
        "fixer_annotated": fixer_annotated,
        "resolved_in_analysis": resolved_in_analysis,
        "fallback_only": fallback_only,
        "on_disk": on_disk,
        "residual_high_risk": len(residual_high_risk),
        "needs_review": needs_review,
        "disagreement": disagreement,
    }


def verify_migration(state: dict, analysis: list[dict], migrated_dir: str) -> dict:
    """Verify every manifest file. Returns per-file rows plus a summary."""
    manifest: list[str] = [_normalize_rel_path(p) for p in state.get("manifest", [])]
    manifest_set = set(manifest)
    basename_map: dict[str, list[str]] = {}
    for rel in manifest:
        basename_map.setdefault(os.path.basename(rel), []).append(rel)

    done_set = _collect_done_files(state)
    resolved_done = {
        rel
        for p in done_set
        if (rel := _resolve_to_manifest(
            p, manifest_set=manifest_set, basename_map=basename_map, migrated_dir=migrated_dir
        )) is not None
    }

    # Group findings by exact normalized manifest entry when possible.
    by_rel: dict[str, list[dict]] = {}
    for f in analysis:
        rel = _resolve_to_manifest(
            f.get("file", ""),
            manifest_set=manifest_set,
            basename_map=basename_map,
            migrated_dir=migrated_dir,
        )
        if rel is not None:
            by_rel.setdefault(rel, []).append(f)

    rows = [
        classify_file(
            rel,
            recorded_done=rel in resolved_done,
            migrated_dir=migrated_dir,
            findings=by_rel.get(rel, []),
        )
        for rel in manifest
    ]

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    disagreements = [r for r in rows if r["disagreement"]]
    not_attempted = [r for r in rows if r["status"] == STATUS_NOT_ATTEMPTED]
    needs_review = [r for r in rows if r["needs_review"]]

    return {
        "files": rows,
        "summary": {
            "total": len(rows),
            "by_status": counts,
            "disagreements": len(disagreements),
            "not_attempted": len(not_attempted),
            "migrated_needs_review": len(needs_review),
            # Compilation is only tracked workload-wide, not per file.
            "compilation_reverted_count": state.get("compilation_reverted_count", 0),
        },
        "disagreements": disagreements,
        "not_attempted": not_attempted,
    }


def _load_json_file(path: str, *, label: str) -> object:
    """Load JSON with a clear operator-facing error on failure."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise ValueError(f"{label} not found: {path}") from None
    except json.JSONDecodeError as e:
        raise ValueError(f"{label} is not valid JSON at {path}: {e}") from None
    except OSError as e:
        raise ValueError(f"Could not read {label} at {path}: {e}") from None


def _human_action_entry(rel_path: str, code: str = HUMAN_ACTION_CODE_PY) -> dict:
    return {
        "file": rel_path,
        "code": code,
        "lines": "1",
        "root_cause": HUMAN_ACTION_ROOT_CAUSE,
        "category": "Partial Migration",
        "fix": HUMAN_ACTION_FIX,
        "final_risk": 0.9,
        "snowpark_connect_category": "Partial Migration",
        # Distinguishes an evidence-backed entry from the old blind auto-append.
        "verified_by": "verify_migration",
    }


def _phase_2c_completion_entry(*, partial: list[str], migrated_unrecorded: list[str]) -> dict:
    """Build the canonical phases_completed payload for a successful Phase 2c."""
    return {
        "status": "passed",
        "disagreements": 0,
        "not_attempted": 0,
        "needs_human_action": partial,
        "verified_human_action_count": len(partial),
        "recorded_migrated_count": len(migrated_unrecorded),
    }


def _strip_files(
    lst: list[str],
    *,
    drop_files: set[str],
    manifest_set: set[str],
    basename_map: dict[str, list[str]],
    migrated_dir: str,
) -> list[str]:
    out = []
    for x in lst:
        rel = _resolve_to_manifest(
            x, manifest_set=manifest_set, basename_map=basename_map, migrated_dir=migrated_dir
        )
        if rel in drop_files:
            continue
        out.append(x)
    return out


def reconcile(
    state: dict,
    analysis: list[dict],
    verification: dict,
    *,
    language: str = "python",
    migrated_dir: str | None = None,
) -> dict:
    """Rewrite state + analysis so they tell the verified truth.

    Closes the loop on every disagreement so a re-verify returns
    ``disagreements == 0``:

    * ``state=done but fallback-only`` -> the file is genuinely partial. Remove
      it from every completion list, add it to ``needs_human_action``, and emit
      ONE verified ``SPRKCNTPY0099`` human-action finding in ``analysis.json``.
    * ``state=not-done but real edits`` -> reconcile by recording it done.

    Also strips the old blindly-appended ``Partial Migration`` findings, so the
    only partial entries left are the evidence-backed ones. Pure: returns new
    ``state``, ``analysis``, and a change log; the caller persists them.
    """
    rows = verification["files"]
    partial = sorted(r["file"] for r in rows if r["status"] == STATUS_PARTIAL)
    migrated_unrecorded = sorted(
        r["file"]
        for r in rows
        if r["status"] == STATUS_MIGRATED and not r["recorded_done"]
    )

    new_state = json.loads(json.dumps(state))
    migrated_dir = migrated_dir or new_state.get("migrated_dir", "")
    manifest_set = {_normalize_rel_path(p) for p in new_state.get("manifest", [])}
    basename_map: dict[str, list[str]] = {}
    for rel in manifest_set:
        basename_map.setdefault(os.path.basename(rel), []).append(rel)
    partial_set = set(partial)

    # Demote genuinely-partial files out of every completion list.
    if "processed_files" in new_state:
        new_state["processed_files"] = _strip_files(
            new_state["processed_files"],
            drop_files=partial_set,
            manifest_set=manifest_set,
            basename_map=basename_map,
            migrated_dir=migrated_dir,
        )
    if isinstance(new_state.get("2_fixes"), dict) and "files_done" in new_state["2_fixes"]:
        new_state["2_fixes"]["files_done"] = _strip_files(
            new_state["2_fixes"]["files_done"],
            drop_files=partial_set,
            manifest_set=manifest_set,
            basename_map=basename_map,
            migrated_dir=migrated_dir,
        )
    pc = new_state.get("phases_completed", {}).get("2_fixes")
    if isinstance(pc, dict) and "files_done" in pc:
        pc["files_done"] = _strip_files(
            pc["files_done"],
            drop_files=partial_set,
            manifest_set=manifest_set,
            basename_map=basename_map,
            migrated_dir=migrated_dir,
        )

    new_state["needs_human_action"] = partial

    # Reconcile the under-counted side: record verified migrations as done.
    if migrated_unrecorded:
        proc = new_state.setdefault("processed_files", [])
        for f in migrated_unrecorded:
            if f not in proc:
                proc.append(f)

    # Rebuild analysis: drop every old Partial-Migration / 0099 entry, then add
    # exactly one verified human-action finding per genuinely-partial file.
    kept = [
        f
        for f in analysis
        if f.get("code") not in FALLBACK_CODES
        and f.get("snowpark_connect_category") != "Partial Migration"
        and f.get("category") != "Partial Migration"
    ]
    code = human_action_code(language)
    new_analysis = kept + [_human_action_entry(p, code) for p in partial]

    return {
        "state": new_state,
        "analysis": new_analysis,
        "changes": {
            "marked_human_action": partial,
            "recorded_migrated": migrated_unrecorded,
            "partial_findings_dropped": len(analysis) - len(kept),
            "human_action_findings_written": len(partial),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evidence-based verification of per-file migration status"
    )
    parser.add_argument("--state", required=True, help="Path to migration_state.json")
    parser.add_argument(
        "--analysis", help="Path to analysis.json (defaults to alongside state)"
    )
    parser.add_argument(
        "--migrated-dir", help="Path to Output/ (defaults to state.migrated_dir)"
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Reconcile: rewrite migration_state.json and analysis.json so they "
        "match the verified evidence (closes the loop; disagreements -> 0). "
        "Default is a dry run that only reports.",
    )
    parser.add_argument(
        "--language",
        default="python",
        choices=["python", "scala", "java"],
        help="Selects the human-action EWI code (SPRKCNTPY0099 / SPRKCNTSCL0099); java maps to the SCL code.",
    )
    args = parser.parse_args()

    try:
        state = _load_json_file(args.state, label="migration_state.json")
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not isinstance(state, dict):
        print(
            f"ERROR: migration_state.json must be a JSON object, got {type(state).__name__}",
            file=sys.stderr,
        )
        return 2

    conversion_root = state.get("conversion_root", os.path.dirname(os.path.abspath(args.state)))
    migrated_dir = args.migrated_dir or state.get(
        "migrated_dir", os.path.join(conversion_root, "Output")
    )
    analysis_path = args.analysis or os.path.join(conversion_root, "analysis.json")

    analysis: list[dict] = []
    if os.path.exists(analysis_path):
        try:
            raw_analysis = _load_json_file(analysis_path, label="analysis.json")
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        if not isinstance(raw_analysis, list):
            print(
                f"ERROR: analysis.json must be a JSON array, got {type(raw_analysis).__name__}",
                file=sys.stderr,
            )
            return 2
        analysis = raw_analysis

    result = verify_migration(state, analysis, migrated_dir)
    print(json.dumps(result["summary"], indent=2))
    if result["disagreements"]:
        print("\nSelf-report vs evidence disagreements:")
        for d in result["disagreements"]:
            print(f"  {d['file']}: {d['disagreement']}")
    if result["not_attempted"]:
        print("\nNot attempted (missing output file):")
        for row in result["not_attempted"]:
            print(f"  {row['file']}")
        print(
            "ERROR: verify_migration found file(s) missing from Output/. "
            "Phase 2 coverage should have caught this — escalate to the user.",
            file=sys.stderr,
        )
        return 1

    if not args.write:
        if result["disagreements"]:
            print("\nRun again with --write to reconcile (mark human-action files).")
        return 0

    # Close the loop: persist the verified truth, then re-verify to prove it.
    rec = reconcile(state, analysis, result, language=args.language, migrated_dir=migrated_dir)
    with open(args.state, "w", encoding="utf-8") as fh:
        json.dump(rec["state"], fh, indent=2)
    with open(analysis_path, "w", encoding="utf-8") as fh:
        json.dump(rec["analysis"], fh, indent=2)

    print("\nReconciled:")
    print(f"  marked human-action: {rec['changes']['marked_human_action']}")
    print(f"  recorded migrated (was unrecorded): {rec['changes']['recorded_migrated']}")
    print(f"  stale partial findings dropped: {rec['changes']['partial_findings_dropped']}")
    print(f"  verified human-action findings written: {rec['changes']['human_action_findings_written']}")

    after = verify_migration(rec["state"], rec["analysis"], migrated_dir)
    n = after["summary"]["disagreements"]
    if after["not_attempted"]:
        print("\nNot attempted after reconcile:")
        for row in after["not_attempted"]:
            print(f"  {row['file']}")
        print(
            "ERROR: verify_migration found file(s) missing from Output/. "
            "Phase 2 coverage should have caught this — escalate to the user.",
            file=sys.stderr,
        )
        return 1
    if n == 0:
        rec["state"].setdefault("phases_completed", {})["2c_verification"] = (
            _phase_2c_completion_entry(
                partial=rec["changes"]["marked_human_action"],
                migrated_unrecorded=rec["changes"]["recorded_migrated"],
            )
        )
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(rec["state"], fh, indent=2)
    print(f"\nRe-verify after reconcile: disagreements = {n}")
    return 0 if n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
