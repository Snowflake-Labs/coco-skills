#!/usr/bin/env python3
"""
SNOW-3385158: Deterministic external orchestrator for Phase 2 chunked dispatch.

Manages Phase 2 (Apply Fixes) dispatch by:
  1. Reading migration_state.json to get manifest, migrated_dir, skill_directory
  2. Splitting the manifest into token-balanced chunks sized for a fixer worker
     pool — at least min(max_parallel, n_files) chunks, each within the budget
  3. Writing chunk assignments to migration_state.json under phase2_chunks/chunks
  4. Printing a wave-based dispatch plan so the coordinator runs up to
     max_parallel fixer sub-agents concurrently per wave
  5. Verifying 100% file coverage (every manifest file is present in Output/)

The mechanical transform (imports, session-init, migration header) is NOT done
here. It runs once, deterministically, in Phase 3 (scripts/update_imports.py)
over every manifest file — so there is no separate pre-fixer "fallback"
transform pass to stamp a premature header. Files the LLM fixer skips are still
caught: Phase 2c (verify_migration.py) flags them as partial from evidence, and
Phase 3 applies the mechanical floor. ``fallback_transform.py`` remains as an
optional manual gap-filler but is no longer part of the automatic pipeline.

Parallelism & state safety: fixer workers run concurrently within a wave and
therefore MUST NOT write migration_state.json (they would race on one file).
Each worker returns a CHUNK_RESULT line and the coordinator is the single
writer of state after each wave completes.

Usage:
    python3 orchestrate_phases.py --state /path/to/migration_state.json --phase 2
    python3 orchestrate_phases.py --state /path/to/migration_state.json --phase 2 --budget 80000
    python3 orchestrate_phases.py --state /path/to/migration_state.json --phase 2 --max-parallel 6
    python3 orchestrate_phases.py --state /path/to/migration_state.json --phase 2 --language scala

Returns exit code 0 on success, 1 on configuration errors.
"""

import argparse
import functools
import json
import math
import os
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from notebook_io import detect_format, parse_notebook, scan_and_parse_notebooks

DEFAULT_BUDGET = 80_000
TOKENS_PER_FILE_OVERHEAD = 2_000
CHARS_PER_TOKEN = 4
# Size of the fixer worker pool: how many fixer sub-agents the coordinator may
# run concurrently. Chunks are dispatched in waves of this width, so the pool
# stays busy instead of processing files one agent at a time.
DEFAULT_MAX_PARALLEL = 6


def build_notebook_index(root: str) -> dict[str, dict]:
    """Walk ``root`` once via :func:`scan_and_parse_notebooks` and return a
    path→info index.

    Keys are the absolute paths of each discovered notebook; values carry
    the detected ``format`` and ``language`` plus a per-cell language count
    (``code_cells_by_language``) so downstream phases can size chunks and
    decide whether to run both analyzers on cross-language notebooks.

    Each notebook is parsed exactly once: ``scan_and_parse_notebooks``
    reuses the ``FormatInfo`` produced during the scan so we don't pay for
    detection twice per file. Persisted into ``migration_state.json`` under
    ``notebook_index`` so analyzers and fixers can skip redundant per-file
    I/O.
    """
    index: dict[str, dict] = {}
    for entry, nb in scan_and_parse_notebooks(root):
        abs_path = entry["abs_path"]
        per_cell: dict[str, int] = {}
        for cell in nb.cells:
            if cell.cell_type != "code":
                continue
            key = cell.cell_language or "unknown"
            per_cell[key] = per_cell.get(key, 0) + 1
        index[abs_path] = {
            "format": entry["format"],
            "language": entry["language"],
            "rel_path": entry["file"],
            "code_cells_by_language": per_cell,
        }
    return index


def persist_notebook_index(state_path: str, root: str) -> dict[str, dict]:
    """Build the notebook index for ``root`` and save it under
    ``notebook_index`` in ``state_path``. Returns the index.

    Safe to call multiple times — overwrites any existing ``notebook_index``
    with a fresh scan so re-running Phase 0 picks up newly-added notebooks.

    Also merges notebook ``rel_path`` entries into ``state["manifest"]``,
    ``state["file_order"]``, and ``state["notebook_files"]`` so callers do
    not need a separate step to build the manifest for notebook-only workloads.
    Any existing ``.py`` entries already in the manifest are preserved.
    """
    idx = build_notebook_index(root)
    state = load_state(state_path)
    state["notebook_index"] = idx

    # Merge notebook rel_paths into manifest / notebook_files.
    existing = set(state.get("manifest", []))
    nb_files: dict = {k: [] for k in (
        "ipynb", "native_python", "native_scala", "native_sql",
        "exported_python", "exported_scala",
    )}
    for entry in idx.values():
        rel = entry.get("rel_path", "")
        fmt = entry.get("format", "")
        if rel:
            existing.add(rel)
        if fmt in nb_files and rel:
            nb_files[fmt].append(rel)

    # Preserve any existing notebook_files entries (may have been hand-set).
    for key, vals in nb_files.items():
        if vals:
            state.setdefault("notebook_files", {})[key] = vals

    state["manifest"] = sorted(existing)
    state["file_order"] = sorted(existing)

    save_state(state_path, state)
    return idx


def load_state(state_path: str) -> dict:
    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state_path: str, state: dict) -> None:
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


@functools.lru_cache(maxsize=None)
def estimate_file_tokens(file_path: str) -> int:
    """Estimate token count for a source file or notebook.

    For plain files: ``file_chars // 4 + 2000`` overhead.
    For notebooks: sum of cell source lengths (code cells only) // 4 + overhead.
    This avoids overcharging for the JSON wrapper of Databricks native notebooks,
    which can be large due to per-cell metadata that sub-agents don't re-emit.

    Memoized with :func:`functools.lru_cache` so the three call sites inside
    a single orchestration (``build_chunks``, total-tokens sum, and
    ``print_dispatch_plan``) share results instead of reparsing each
    notebook three times. The cache is process-scoped which is exactly
    the lifetime of a single orchestration run.
    """
    # Capture detection once and thread it through to parse_notebook so the
    # notebook's 4 KiB head isn't re-read inside parse_notebook.
    info = detect_format(file_path)
    if info.get("format") != "not_notebook":
        try:
            nb = parse_notebook(file_path, info=info)
        except (ValueError, OSError):
            # Fall through to raw byte estimate
            pass
        else:
            total_chars = sum(
                len(cell.source) for cell in nb.cells if cell.cell_type == "code"
            )
            return total_chars // CHARS_PER_TOKEN + TOKENS_PER_FILE_OVERHEAD

    try:
        file_chars = os.path.getsize(file_path)
    except OSError:
        file_chars = 0
    return file_chars // CHARS_PER_TOKEN + TOKENS_PER_FILE_OVERHEAD


def build_chunks(manifest: list, migrated_dir: str, budget: int) -> list:
    """Split manifest into budget-aware chunks.

    Each chunk's estimated token cost stays within ``budget``. A single file
    that exceeds the budget on its own is placed in a dedicated chunk so it is
    never silently skipped.
    """
    chunks = []
    current_chunk = []
    current_tokens = 0

    for f in manifest:
        path = f if os.path.isabs(f) else os.path.join(migrated_dir, f)
        file_tokens = estimate_file_tokens(path)

        if current_tokens + file_tokens > budget and current_chunk:
            chunks.append(current_chunk)
            current_chunk = [f]
            current_tokens = file_tokens
        else:
            current_chunk.append(f)
            current_tokens += file_tokens

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def build_balanced_chunks(
    manifest: list, migrated_dir: str, budget: int, max_parallel: int
) -> list:
    """Split the manifest into token-balanced chunks sized for a worker pool.

    Unlike :func:`build_chunks` (which greedily packs files up to ``budget`` and
    can yield a single chunk for a small workload — forcing one sequential fixer),
    this produces **at least** ``min(max_parallel, n_files)`` chunks so the
    coordinator always has parallel work to dispatch. ``budget`` is still a hard
    per-chunk cap: if balancing across the target bins would exceed it, more bins
    are opened (down to one file per chunk for an oversized file).

    Balancing uses Longest-Processing-Time-first (LPT): files are placed
    heaviest-first into whichever bin is currently lightest, which keeps wave
    wall-clock even across workers.
    """
    weighted = sorted(
        (
            (f, estimate_file_tokens(resolve_file_path(f, migrated_dir)))
            for f in manifest
        ),
        key=lambda fw: (-fw[1], fw[0]),
    )
    n_files = len(weighted)
    if n_files == 0:
        return []

    total = sum(w for _, w in weighted)
    # Enough bins to respect the budget, and at least the worker-pool width so a
    # small workload still fans out across the pool.
    bins_for_budget = max(1, math.ceil(total / budget))
    target = min(n_files, max(bins_for_budget, min(max(1, max_parallel), n_files)))

    def _lpt(num_bins: int) -> list:
        bins = [{"files": [], "tokens": 0} for _ in range(num_bins)]
        for f, w in weighted:
            lightest = min(bins, key=lambda b: b["tokens"])
            lightest["files"].append(f)
            lightest["tokens"] += w
        return bins

    bins = _lpt(target)
    # Honour the per-chunk budget: a single file larger than the budget is
    # unavoidable (it gets its own chunk), but otherwise add bins until every
    # chunk fits.
    while any(b["tokens"] > budget for b in bins) and target < n_files:
        target += 1
        bins = _lpt(target)

    # Sort each chunk's files alphabetically for deterministic, readable output
    # and drop any empty bins.
    return [sorted(b["files"]) for b in bins if b["files"]]


def get_processed_files(state: dict) -> set:
    """Return all files already processed according to migration_state.json."""
    phase2 = state.get("2_fixes", {})
    files_done = set(phase2.get("files_done", []))
    processed = set(state.get("processed_files", []))
    return files_done | processed


def resolve_file_path(f: str, migrated_dir: str) -> str:
    return f if os.path.isabs(f) else os.path.join(migrated_dir, f)


def print_dispatch_plan(
    chunks: list, migrated_dir: str, language: str, max_parallel: int
) -> None:
    """Print structured dispatch instructions for the LLM coordinator.

    Chunks are grouped into *waves* of ``max_parallel``. The coordinator spawns
    every chunk in a wave concurrently (parallel ``task()`` calls in one turn),
    waits for the whole wave, then updates state once and starts the next wave.
    """
    total_files = sum(len(c) for c in chunks)
    n_chunks = len(chunks)
    width = max(1, max_parallel)
    num_waves = math.ceil(n_chunks / width) if n_chunks else 0
    print()
    print("=" * 60)
    print("PHASE 2 DISPATCH PLAN")
    print("=" * 60)
    print(f"Total files  : {total_files}")
    print(f"Total chunks : {n_chunks}")
    print(f"Worker pool  : {width} (MAX_PARALLEL)")
    print(f"Waves        : {num_waves}")
    print(f"Language     : {language}")
    print(f"MAX_PARALLEL={width}")
    print()

    for w in range(num_waves):
        wave_chunks = chunks[w * width : (w + 1) * width]
        print(f"========== WAVE {w + 1}/{num_waves} "
              f"(dispatch these {len(wave_chunks)} chunk(s) IN PARALLEL) ==========")
        for offset, chunk in enumerate(wave_chunks):
            chunk_id = w * width + offset + 1
            chunk_tokens = sum(
                estimate_file_tokens(resolve_file_path(f, migrated_dir))
                for f in chunk
            )
            print(f"--- CHUNK {chunk_id}/{n_chunks} ---")
            print(f"Files: {len(chunk)} | Estimated tokens: ~{chunk_tokens:,}")
            print(f"CHUNK_MODE=chunked")
            print(f"CHUNK_ID={chunk_id}")
            print(f"CHUNK_FILES={','.join(chunk)}")
            print()

    print("COORDINATOR INSTRUCTIONS (parallel worker pool):")
    print(f"  1. Process waves in order. For each wave, spawn ALL its chunks'")
    print(f"     agents/fixer.md sub-agents IN PARALLEL — issue the {width}")
    print("     task() calls in a single turn, passing CHUNK_MODE=chunked,")
    print("     CHUNK_ID, CHUNK_FILES, and PARALLEL_MODE=true to each.")
    print("  2. Workers do NOT write migration_state.json (they would race).")
    print("     Each worker returns a CHUNK_RESULT line; YOU (coordinator) are")
    print("     the single writer of state.")
    print("  3. When the whole wave returns, update migration_state.json ONCE:")
    print("     append each reported file to processed_files[], remove from")
    print("     pending_files[], and set chunks[i].status='done'. Then git")
    print("     checkpoint the wave.")
    print("  4. After the last wave, if pending_files is non-empty, re-run this")
    print("     script — it recomputes chunks from the remaining files.")
    print("  5. Repeat until all chunks are processed.")
    print()



def run_fallback(state_path: str, skill_directory: str, language: str) -> int:
    """Run the Phase 2a coverage/fallback hard gate.

    For Python and Scala: runs fallback_transform.py, which fills any files
    missed by fixer sub-agents with deterministic transforms and writes
    ``state["fallback_processed"]``.

    For Java: fallback_transform.py is Python/Scala-only and cannot process
    Java files.  Java's deterministic transforms are handled exclusively by
    Phase 0.5c (JavaParser rules) and Phase 3 (update_imports_java.py), so
    there is no residual work for a Java fallback pass.  This branch records
    ``phases_completed["2a_fallback"]`` with ``status: "skipped"`` and
    verifies that all manifest ``.java`` files are present in ``Output/``
    (the coverage contract).
    """
    if language == "java":
        return _run_java_fallback(state_path)

    fallback_script = os.path.join(skill_directory, "scripts", "fallback_transform.py")
    if not os.path.exists(fallback_script):
        print(
            f"WARNING: fallback_transform.py not found at {fallback_script}",
            file=sys.stderr,
        )
        return 1

    cmd = [sys.executable, fallback_script, "--state", state_path]
    if language == "scala":
        cmd += ["--language", language]

    print("=" * 60)
    print("MANDATORY FALLBACK HARD GATE")
    print("=" * 60)
    print(f"Running: {' '.join(cmd)}")
    print("(Always runs — fills any files missed by sub-agents)")
    print()

    result = subprocess.run(cmd)
    return result.returncode


def _run_java_fallback(state_path: str) -> int:
    """Java-specific Phase 2a: coverage check + skip record.

    Java's deterministic transforms (Phase 0.5c JavaParser + Phase 3
    update_imports_java.py) subsume what fallback_transform.py does for
    Python/Scala, so there is no residual fallback work for Java.  This
    function records the phase as skipped with an explanatory reason and
    validates that all manifest .java files are present in Output/.

    Writes ``phases_completed["2a_fallback"]`` to migration_state.json.
    Returns 0 when coverage is 100%; 1 when manifest files are missing.
    """
    import datetime as _dt
    import json as _json
    import pathlib as _pl

    with open(state_path, "r", encoding="utf-8") as fh:
        state = _json.load(fh)

    migrated_dir = state.get("migrated_dir", "")
    manifest: list[str] = state.get("manifest", [])
    missing: list[str] = []

    if migrated_dir and manifest:
        migrated = _pl.Path(migrated_dir)
        for rel in manifest:
            if not rel.endswith(".java"):
                continue
            if not (migrated / rel).is_file():
                missing.append(rel)

    coverage_ok = len(missing) == 0

    # When coverage is false (missing manifest files), write status="failed" so
    # validate_migration_state --strict rejects the state — "skipped" is only
    # correct when coverage is 100% and there is genuinely no work to do.
    phase_entry: dict = {
        "status": "skipped" if coverage_ok else "failed",
        "reason": (
            "Java deterministic transforms are fully handled by Phase 0.5c "
            "(JavaParser rules) and Phase 3 (update_imports_java.py). "
            "fallback_transform.py is Python/Scala-only."
        ),
        "ran_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "coverage_ok": coverage_ok,
    }
    if missing:
        phase_entry["missing_files"] = missing

    state.setdefault("phases_completed", {})["2a_fallback"] = phase_entry

    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        _json.dump(state, fh, indent=2)
    import os as _os
    _os.replace(tmp, state_path)

    print("=" * 60)
    print("JAVA PHASE 2A — COVERAGE CHECK (fallback skipped)")
    print("=" * 60)
    if coverage_ok:
        print(f"Coverage: 100% ({len(manifest)} manifest .java file(s) present)")
        print("phases_completed[\"2a_fallback\"] written with status=skipped")
        return 0
    else:
        print(f"MISSING: {len(missing)} manifest .java file(s) absent from Output/")
        for m in missing[:10]:
            print(f"  - {m}")
        return 1


def run_verification(state_path: str, skill_directory: str, language: str) -> int:
    """Run verify_migration.py --write as the Phase 2c barrier.

    MUST be invoked ONCE, only after Phase 2b (compilation gate) and all
    fixer re-dispatching have fully completed — never inside the per-chunk
    orchestration loop, or it will persist partial labels before the async
    fixer finishes (stale/false partials). It cross-checks the self-reported
    state against on-disk evidence and reconciles both artifacts to the
    truth: genuinely-partial files (LLM never processed them) get a single
    authoritative SPRKCNTPY0099 finding in analysis.json plus an entry in
    ``needs_human_action``; falsely-flagged migrations are cleared. This is
    the sole writer of Partial Migration findings — the fallback no longer
    writes them — so the report reads a reconciled, evidence-based view.
    """
    verify_script = os.path.join(skill_directory, "scripts", "verify_migration.py")
    if not os.path.exists(verify_script):
        print(
            f"WARNING: verify_migration.py not found at {verify_script}",
            file=sys.stderr,
        )
        return 1

    cmd = [sys.executable, verify_script, "--state", state_path, "--write",
           "--language", language]

    print("=" * 60)
    print("EVIDENCE-BASED VERIFICATION GATE")
    print("=" * 60)
    print(f"Running: {' '.join(cmd)}")
    print("(Reconciles state + analysis.json to the verified truth)")
    print()

    result = subprocess.run(cmd)
    return result.returncode


def verify_coverage(state: dict, state_path: str, manifest: list, migrated_dir: str) -> list:
    """Check all manifest files exist in migrated_dir. Returns list of missing files."""
    missing = []
    for f in manifest:
        path = resolve_file_path(f, migrated_dir)
        if not os.path.exists(path):
            missing.append(f)

    print("=" * 60)
    print("COVERAGE VERIFICATION")
    print("=" * 60)
    if missing:
        print(f"MISSING ({len(missing)} file(s)):")
        for m in missing:
            print(f"  - {m}")
        print()
        print("ACTION REQUIRED: Escalate to user — manifest files are missing from Output/.")
    else:
        print(f"Coverage: 100% ({len(manifest)}/{len(manifest)} files present)")

    # Persist coverage result back to state
    state["pending_files"] = missing
    state["orchestrator_coverage_verified"] = len(missing) == 0
    save_state(state_path, state)

    return missing


def _ensure_phase_0_6(state_path: str) -> None:
    """Reliability backstop: run the standalone-`.sql` rewrite (Phase 0.6) here if
    the coordinator skipped it.

    ``orchestrate_phases.py --phase 2`` is a step the coordinator *always* invokes
    before dispatch, whereas the standalone Phase 0.6 step is frequently skipped —
    leaving `.sql` files detected (Phase 1) but never rewritten. Running
    ``rewrite_sql_files.main`` here closes that gap deterministically. It is
    idempotent: a file already carrying the migration-header sentinel is skipped,
    and a state that already records ``0_6_sql_rewrite`` short-circuits. Best-effort
    — never blocks Phase 2 dispatch."""
    try:
        state = load_state(state_path)
    except Exception:
        return
    if "0_6_sql_rewrite" in (state.get("phases_completed") or {}):
        return  # the standalone phase already ran
    try:
        import rewrite_sql_files
    except Exception as exc:  # noqa: BLE001 — rag/sqlglot unavailable: skip cleanly
        print(f"  [phase-0.6 backstop] skipped (import failed: {exc})")
        return
    print("  [phase-0.6 backstop] standalone SQL rewrite was not recorded — "
          "running rewrite_sql_files.py now")
    try:
        rewrite_sql_files.main(["--state", state_path])
    except SystemExit:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"  [phase-0.6 backstop] rewrite failed (non-fatal): {exc}")


def orchestrate_phase2(
    state_path: str,
    budget: int,
    language: str,
    max_parallel: int,
    run_fallback_flag: bool = False,
) -> int:
    """Phase 2 orchestration.

    Default (planning) mode: chunk the manifest, persist the dispatch plan, and
    print it for the coordinator. It does **not** mutate any files. The
    deterministic fallback MUST run only AFTER the fixer sub-agents complete —
    invoke this script again with ``--run-fallback`` for that (Phase 2a).

    Running fallback during planning would, on a fresh state, generically
    transform every file before the fixer ever runs, because
    ``find_unprocessed_files`` returns the full manifest when no progress has
    been recorded yet. Keeping planning side-effect-free prevents that.

    With ``run_fallback_flag=True`` (Phase 2a): run ``fallback_transform.py``
    over the files the fixer did not record as done, then verify coverage.
    """
    # Backstop: ensure standalone .sql files were rewritten (Phase 0.6) before we
    # dispatch — runs only if the coordinator skipped the standalone phase. Done
    # before load_state so the 0_6_sql_rewrite / sql_rewrite_edits this writes are
    # picked up by the state we then read (and not clobbered by our save).
    _ensure_phase_0_6(state_path)
    state = load_state(state_path)
    manifest: list = state.get("manifest", [])
    migrated_dir: str = state.get("migrated_dir", "")
    skill_directory: str = state.get("skill_directory", "")

    if not manifest:
        print("ERROR: manifest is empty in migration_state.json", file=sys.stderr)
        return 1

    if not migrated_dir:
        print("ERROR: migrated_dir not set in migration_state.json", file=sys.stderr)
        return 1

    # ---- Phase 2a: post-fixer fallback hard gate + coverage (explicit opt-in) ----
    if run_fallback_flag:
        print("SCOS Phase 2a — Fallback Hard Gate")
        print("==================================")
        print(f"  State      : {state_path}")
        print(f"  Language   : {language}")
        print()
        if skill_directory:
            fallback_rc = run_fallback(state_path, skill_directory, language)
            if fallback_rc != 0:
                print(
                    f"ERROR: fallback/coverage gate exited {fallback_rc} — "
                    "manifest files are missing from Output/. Escalate to user.",
                    file=sys.stderr,
                )
                return fallback_rc  # propagate: do NOT swallow missing-file failures
        else:
            print(
                "WARNING: skill_directory not set in migration_state.json — "
                "skipping fallback_transform.py",
                file=sys.stderr,
            )
        state = load_state(state_path)
        migrated_dir = state.get("migrated_dir", migrated_dir)
        manifest = state.get("manifest", manifest)
        print()
        verify_coverage(state, state_path, manifest, migrated_dir)
        return 0

    # ---- Planning mode (default): chunk + dispatch plan ONLY (no mutation) ----
    print("SCOS Phase 2 Orchestrator (planning)")
    print("====================================")
    print(f"  State       : {state_path}")
    print(f"  Manifest    : {len(manifest)} file(s)")
    print(f"  Budget      : {budget:,} tokens/chunk")
    print(f"  Worker pool : {max_parallel} parallel fixer(s)")
    print(f"  Language    : {language}")
    print(f"  Output dir  : {migrated_dir}")
    print()

    # Balance the manifest across the worker pool (at least min(max_parallel,
    # n_files) chunks) while keeping each chunk within the token budget.
    chunks = build_balanced_chunks(manifest, migrated_dir, budget, max_parallel)
    total_tokens = sum(
        estimate_file_tokens(resolve_file_path(f, migrated_dir)) for f in manifest
    )
    print(
        f"Chunking: {len(manifest)} files → {len(chunks)} chunk(s) "
        f"(~{total_tokens:,} total tokens, {budget:,} budget/chunk, "
        f"pool={max_parallel})"
    )

    # Write chunks and updated budget to state before printing dispatch plan.
    # The persisted chunks carry an `id` + `status` so the coordinator can mark
    # each done after its wave completes (the coordinator is the state writer).
    state["phase2_chunks"] = chunks
    state["chunks"] = [
        {"id": i + 1, "files": c, "status": "pending"} for i, c in enumerate(chunks)
    ]
    state["context_budget_tokens"] = budget
    state["max_parallel_fixers"] = max_parallel
    save_state(state_path, state)

    # Print the dispatch plan for the LLM coordinator to act on
    print_dispatch_plan(chunks, migrated_dir, language, max_parallel)


    print()
    print("PLANNING ONLY — no files were modified.")
    print(
        "Next: spawn the fixer sub-agents per the plan above; AFTER they "
        "complete, run this script again with --run-fallback (Phase 2a)."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SNOW-3385158: External orchestrator for deterministic Phase 2 dispatch"
    )
    parser.add_argument(
        "--state",
        required=True,
        help="Path to migration_state.json",
    )
    parser.add_argument(
        "--phase",
        type=int,
        default=2,
        help="Migration phase to orchestrate (default: 2; only phase 2 is supported)",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=DEFAULT_BUDGET,
        help=f"Token budget per chunk (default: {DEFAULT_BUDGET:,})",
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=DEFAULT_MAX_PARALLEL,
        help=(
            "Fixer worker-pool width: how many fixer sub-agents the coordinator "
            f"runs concurrently (default: {DEFAULT_MAX_PARALLEL}). The manifest is "
            "split into at least min(max_parallel, n_files) balanced chunks and "
            "dispatched in waves of this width. Set to 1 for sequential dispatch."
        ),
    )
    parser.add_argument(
        "--language",
        choices=["python", "scala", "java"],
        default="python",
        help="Source language of the workload (default: python)",
    )
    parser.add_argument(
        "--build-notebook-index",
        metavar="ROOT",
        default=None,
        help=(
            "Phase 0 helper: scan ROOT for notebooks, build a notebook_index, "
            "and persist it into migration_state.json. Exits immediately after "
            "writing the index (does not run Phase 2 orchestration)."
        ),
    )
    parser.add_argument(
        "--run-fallback",
        action="store_true",
        help=(
            "Phase 2a (scala only; disabled for pyspark): run the deterministic "
            "fallback hard gate over files the fixer did not complete, then "
            "verify coverage. Run this ONLY after the fixer sub-agents finish — "
            "never during initial planning, or it will generically transform the "
            "whole manifest on a fresh state."
        ),
    )
    parser.add_argument(
        "--run-verification",
        action="store_true",
        help=(
            "Phase 2c barrier: run the evidence-based verification gate "
            "(verify_migration.py --write) ONCE and exit. Call this only after "
            "Phase 2b and all fixer re-dispatching have completed — never in "
            "the per-chunk loop."
        ),
    )
    args = parser.parse_args()

    state_path = os.path.abspath(args.state)
    if not os.path.exists(state_path):
        print(f"ERROR: migration_state.json not found: {state_path}", file=sys.stderr)
        return 1

    # --run-fallback (Phase 2a fallback transform) is a Scala/Java gate; it is
    # disabled for pyspark, where every file is handled by the fixer sub-agents.
    # Java (like Scala) is a JVM project and uses the fallback gate.
    # Treat it as a no-op (success) rather than an error so the flow continues.
    if args.run_fallback and args.language not in {"scala", "java"}:
        print(
            "--run-fallback is disabled for pyspark (Scala/Java gate); skipping.",
        )
        return 0

    if args.run_verification:
        state = load_state(state_path)
        skill_directory = state.get("skill_directory", "")
        if not skill_directory:
            print(
                "ERROR: skill_directory not set in migration_state.json — "
                "cannot locate verify_migration.py",
                file=sys.stderr,
            )
            return 1
        return run_verification(state_path, skill_directory, args.language)

    if args.build_notebook_index:
        root = os.path.abspath(args.build_notebook_index)
        if not os.path.isdir(root):
            print(f"ERROR: --build-notebook-index root is not a directory: {root}", file=sys.stderr)
            return 1
        idx = persist_notebook_index(state_path, root)
        print(f"notebook_index: {len(idx)} entries written to {state_path}")
        return 0

    if args.phase != 2:
        print(
            f"ERROR: Phase {args.phase} is not supported. Only --phase 2 is implemented.",
            file=sys.stderr,
        )
        return 1

    return orchestrate_phase2(
        state_path,
        args.budget,
        args.language,
        max(1, args.max_parallel),
        run_fallback_flag=args.run_fallback,
    )


if __name__ == "__main__":
    sys.exit(main())
