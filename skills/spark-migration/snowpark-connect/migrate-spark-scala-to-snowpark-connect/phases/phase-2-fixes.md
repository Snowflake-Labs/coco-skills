# Phase 2: Apply Fixes

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

### Phase 2: Apply Fixes

<!-- SNOW-3385158: Orchestration moved to external script for deterministic dispatch -->
<!-- SNOW-3383536: Budget reduced to 80k tokens/chunk (aggressive mode) for guaranteed completion -->
**Pre-dispatch: Run External Orchestrator (PLAN ONLY)** — Compute budget-aware chunks and write the dispatch plan to `migration_state.json`. This step is **side-effect-free**: it does NOT modify any source files. (The deterministic fallback runs later, in Phase 2a, only after the fixers complete — running it now would generically transform the whole manifest before the fixer ever sees it.)

```bash
python3 <SKILL_DIRECTORY>/scripts/orchestrate_phases.py \
  --state <CONVERSION>/migration_state.json \
  --phase 2 \
  --budget 80000 \
  --max-parallel 6 \
  --language scala
```

`--max-parallel` is the fixer worker-pool width (default 6). The orchestrator
splits the manifest into at least `min(max_parallel, n_files)` budget-aware
chunks and groups them into **waves** of `max_parallel`. Lower it (e.g. `1`)
for fully sequential dispatch; raise it for more concurrency.

The script prints a structured **PHASE 2 DISPATCH PLAN**: a `MAX_PARALLEL=<n>` line, a `Waves` count, and for each wave a `========== WAVE k/N (dispatch these C chunk(s) IN PARALLEL) ==========` header followed by its chunks (`CHUNK_MODE`, `CHUNK_ID`, `CHUNK_FILES`). It ends with `PLANNING ONLY — no files were modified.` Read the plan and act on it wave-by-wave. Fallback is **not** run here.

Token formula: `file_tokens = file_chars // 4 + 2000` (characters ÷ 4 plus 2000 overhead per file). A single file that exceeds the budget on its own gets a dedicated chunk so it is never silently skipped.

**Context-overflow prevention (REQUIRED before dispatch)**: Before spawning fixer agents, pre-filter `analysis.json` to only the issues for each chunk's files. Loading the full `analysis.json` (100+ issues) + `fix-rules.md` (300+ lines) + 20+ source files in a single agent context causes API 400 errors. Write a per-chunk slice:

```python
import json
with open(f"{CONVERSION}/analysis.json") as f:
    all_issues = json.load(f)
chunk_files_set = set(chunk_files)  # relative paths for this chunk
chunk_issues = [i for i in all_issues if any(f in i.get("file","") for f in chunk_files_set)]
with open(f"{CONVERSION}/analysis_chunk_{chunk_id}.json", "w") as f:
    json.dump(chunk_issues, f)
```

Pass `--analysis-json <CONVERSION>/analysis_chunk_<n>.json` in each agent prompt instead of the full `analysis.json`. Delete the per-chunk slices after the wave completes.

**Dispatch fixers wave-by-wave (parallel worker pool)**: process waves in order. For **each wave**, spawn ALL of that wave's chunks' `agents/fixer.md` sub-agents **concurrently** — issue the `task()` calls in a **single turn** (one message with multiple tool calls), passing `CHUNK_MODE=chunked`, `CHUNK_ID=<n>`, `CHUNK_FILES=<files>`, and `PARALLEL_MODE=true` to each. The fixer reads the pre-filtered analysis slice (not the full `analysis.json`), loads `references/fix-rules.md` for the detailed Scala-specific rules, and applies fixes to its assigned file list.

> **You (the coordinator) are the single writer of `migration_state.json`.** In `PARALLEL_MODE=true`, workers must NOT write state (they would race) — each returns a `CHUNK_RESULT` line listing the files it completed. When the **whole wave** returns, update `migration_state.json` ONCE: append each reported file to `phases_completed["2_fixes"].files_done`, remove it from `pending_files`, and mark `chunks[i].status="done"`. Then git-checkpoint that wave before starting the next.

**Checkpoint detection**: after the last wave, read `migration_state.json`. If `phases_completed["2_fixes"]["pending_files"]` is non-empty, re-run `orchestrate_phases.py` — it recomputes chunks (and re-waves them) from the remaining files. Dispatch the new waves the same way. Repeat until `pending_files` is empty.

**Verify (deterministic)**: run `verify_phase.py --phase 2` — covers phase-2 orchestration (a multi-file workload must carry the orchestrator plan `max_parallel_fixers`/`phase2_chunks` in state, else the coordinator fixed inline and bypassed the worker pool), syntax artifacts, high-risk coverage, no-op over-annotation, stale cross-file refs, file count, no empty files, **preserved-config survival** (fixer must not undo any Phase 0.5 `SCOS-RECIPE-PRESERVED-CONFIG`), and **notebook coverage** (Scala notebooks get the same validity + artifact + high-risk-marker checks as `.scala` files). Compilation is **not** re-checked here — Phase 2b owns the authoritative compile gate:

```bash
python3 <SKILL_DIRECTORY>/scripts/verify_phase.py \
  --phase 2 --language scala --strict \
  --state <CONVERSION>/migration_state.json
echo "verify_phase2_exit=$?"
```

**Gate**: exit 0 ⇒ `PASS` or `PASS_WITH_GAPS`. On exit 1 (`FAIL`), re-run the fixer on the listed files with that feedback, then re-run the verifier. Update `migration_state.json` phase to 2. (Compilation correctness is enforced separately by the Phase 2b hard gate below.)

> **Line-number shift pitfall.** The `high-risk coverage` check compares
> `analysis.json` line numbers against the *current* file content.  Every
> `// SCOS:` marker line you insert shifts all subsequent line numbers, so
> repeatedly patching line-by-line causes the verifier to report new failures
> on each re-run.  **Prefer the `resolution` field over inline markers for
> bulk coverage:** after the fixer wave completes, set `resolution: "todo"` on
> every remaining high-risk issue that lacks a nearby `// SCOS:` marker:
>
> ```python
> for issue in issues:
>     if float(issue.get("final_risk") or issue.get("risk") or 0) >= 0.7:
>         if issue.get("resolution") not in ("safe","fixed","todo","perf"):
>             issue["resolution"] = "todo"
>             issue["resolution_reason"] = "…"
> ```
>
> `verify_phase.py` accepts either a nearby `// SCOS:` comment **or** a
> recognised `resolution` verdict — both satisfy the gate, but the
> `resolution` approach never causes line-number drift.  Reserve inline
> markers for concrete fixes; use `resolution: "todo"` as the sweep for
> anything the fixer did not address in-code.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 2: fixes applied"`
