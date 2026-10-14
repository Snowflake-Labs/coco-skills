# Phase 2: Apply Fixes

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

> **RDD chains are converted holistically by the fixer.** An RDD expression is
> its entry point (`sc.parallelize`/`sc.range`/`sc.textFile`) **plus** every
> downstream RDD method on the result. The Phase-0.5 entry-point recipes now
> deliberately **skip** any `sc.*` call whose value flows into an RDD-only op
> (inline chain or assign-then-use), leaving the intact RDD block for the
> analyzer to classify and the fixer to convert as a unit per
> `<SKILL_DIRECTORY>/references/python/rdd-conversion.md`. A mechanical entry-point rewrite is
> therefore **never** a complete fix when RDD-only methods follow — the fixer
> must finish the chain, and must never stamp `-Fixed` on a DataFrame that still
> has RDD methods (`.map`/`.sum`/`.zip`/…) dangling on it.

<!-- SNOW-3385158: Orchestration moved to external script for deterministic dispatch -->
<!-- SNOW-3383531: Budget reduced to 80k tokens/chunk (aggressive mode) for guaranteed completion -->
**Pre-dispatch: Run External Orchestrator** — Compute budget-aware chunks and write the dispatch plan to `migration_state.json`:

```bash
python3 <SKILL_DIRECTORY>/scripts/orchestrate_phases.py \
  --state <CONVERSION>/migration_state.json \
  --phase 2 \
  --budget 80000 \
  --max-parallel 6 \
  --language python
```

The script splits the manifest into **token-balanced chunks sized for the worker pool** — at least `min(max_parallel, n_files)` chunks so even a small workload fans out — and prints a **wave-based dispatch plan**. It groups chunks into waves of `MAX_PARALLEL`, and for each chunk outputs `CHUNK_MODE`, `CHUNK_ID`, and `CHUNK_FILES`. It also initialises `chunks[]`/`pending_files`/`processed_files` and prints a final coverage report (every manifest file must be present in `Output/`). Read the output and act on it. (No `fallback_transform.py` runs here — the mechanical floor is owned by Phase 3.)

Token formula: `file_tokens = file_chars // 4 + 2000` (characters ÷ 4 plus 2000 overhead per file). `--budget` is a hard per-chunk cap; a single file that exceeds it gets a dedicated chunk so it is never silently skipped. `--max-parallel` (default 4, or `max_parallel_fixers` from state) sets the worker-pool width.

**Spawn specialists IN PARALLEL (worker pool)**: process the plan **wave by wave**. For each wave, spawn **all of that wave's chunks' `../agents/fixer.md` sub-agents concurrently** — issue the `task()` calls in a **single turn** (do NOT await one before starting the next). Pass `CHUNK_MODE=chunked`, `PARALLEL_MODE=true`, `CHUNK_ID=<n>`, and `CHUNK_FILES=<files>` to each. Every fixer processes **only** the files in its `CHUNK_FILES`.

> **State-write ownership (critical):** parallel fixers **MUST NOT** write `migration_state.json` — concurrent read-modify-write on one file loses updates. Each fixer instead returns a single line:
> `CHUNK_RESULT id=<CHUNK_ID> processed=<f1,f2,...> skipped=<f,...> issues_fixed=<int> todos=<int>`
> **You (the coordinator) are the single writer.** After the whole wave's sub-agents return, update `migration_state.json` **once**: append each `processed` file to `processed_files[]`, remove it from `pending_files[]`, append `skipped` files to `2_fixes_skipped[]`, and set `chunks[i].status = "done"` for every chunk in the wave.

After each wave's state update, git checkpoint:
```bash
cd <CONVERSION> && git add -A && git commit -m "Phase 2: wave <w>/<total> complete (<k> chunks)"
```

**Checkpoint detection**: After a wave's state update, if `pending_files` is non-empty (e.g. a worker crashed), re-run `orchestrate_phases.py` — it recomputes balanced chunks from the remaining files — and dispatch the next plan. Repeat until `pending_files` is empty and every `chunks[i].status == "done"`.

**Quality gate**: after all chunks complete, run the fixer gate on the full `Output/` directory (a deterministic script):

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/scos_gates.py fixer \
  --state <CONVERSION>/migration_state.json --json
```

The gate compiles every manifest `.py` with `py_compile`, validates each `.ipynb` as well-formed notebook JSON, confirms no migrated file is empty or missing from `Output/`, and checks that every high-risk `analysis.json` issue (`final_risk >= 0.7`) has a fix or `# SCOS:` marker near its line. Read the verdict from stdout.

The gate emits findings as `gaps[].code`. **On any FAIL you must read
`../references/gate-findings.md` and look up each code before acting** — it gives the
code's meaning and, critically, its **Owner**. Several codes (`phase2_not_orchestrated`,
`manifest_file_missing`, `sql_mechanical_not_rewritten`, `preexisting_syntax`) are
**not fixable by a fixer**; re-dispatching one wastes a full pass and can regress
correct work.

**Gate**:
- Exit `0` (`PASS` / `PASS_WITH_GAPS`) → advance and update `migration_state.json` phase to 2. `PASS_WITH_GAPS` carries advisory `WARN` findings only (e.g. no-op over-annotation); record them but do not block.
- Exit `2` (`FAIL`) → **gap-scoped re-dispatch (do NOT re-run the whole file).** Re-dispatch `../agents/fixer.md` on the files named in the gaps, passing the gate's `gaps` array verbatim as **`TARGET_ISSUES`** (each entry is `file:line + code + reason`). The fixer must fix **only** those `TARGET_ISSUES` and **leave every already-resolved line untouched** (see `../agents/fixer.md` → "Targeted re-fix mode"). This is far cheaper than re-reasoning over the whole file and — critically — prevents retries from *regressing* code that already converted correctly (a full stateless re-do has been observed to break prior good work). Then re-run the gate. Retry at most **2 times**; if it still fails, escalate to the user.
- Exit `3` (IO / usage error) → STOP and escalate; re-running the fixer will not fix a missing state/path.

Files the LLM fixer skipped are still handled downstream: Phase 2c (`verify_migration.py`) classifies them as `partial` from on-disk evidence, and Phase 3 (`scripts/update_imports.py`) applies the mechanical floor (imports, session-init replacement, migration header) to *every* manifest file regardless.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 2: all chunks complete, fixes applied"`
