# Phase 1: Analysis

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

**Run mode (size-aware)**: if `coordinator_mode == false` (single-file / small), run this phase **inline** by reading `../agents/analyzer.md` and following its steps yourself; if `coordinator_mode == true` (multi-file), **spawn a `task()` sub-agent** with the content of `../agents/analyzer.md` as prompt context (pass the `migration_state.json` path), so its many source reads and the growing `analysis.json` stay out of your window. The procedure is identical either way: run `analyze_pyspark.py` with `--recipe-edits <CONVERSION>/migration_state.json` so **the Phase 0.5 `recipe_edits` block is injected as per-block grounding** (issues become tiered by `kind`: `recipe_validated` | `recipe_incomplete` | `recipe_adjacent` | `standard`). The analyzer makes **no `CORTEX.COMPLETE` calls** — fully-decidable triggers are emitted directly, every non-decidable block is deferred as `needs_adjudication` for Phase 1.1b, and API calls not covered by any detection source are emitted as `needs_classification` for the lightweight Phase 1.1a classifier. Then perform the supplementary blind-spot scan from `../agents/analyzer.md` Step 2 (UDF / `pandas_udf` / `applyInPandas` / `checkpoint` / map-subscript patterns the script may miss) and append any genuinely-missing entries. When running inline, prefer `grep`/`Bash` over `Read` for that scan. Produces `analysis.json`.

**Cross-language notebooks**: inspect `migration_state.json :: notebook_index`. Any entry whose `code_cells_by_language` has more than one of `{python, scala}` is cross-language. For those workloads, ALSO run `analyze_scala.py` on the same inputs (with the same `--notebook-index` flag) and merge its output into the same `analysis.json` — each row carries a `language` field so the fixer and CELL_MODE pre-filter can distinguish Python-cell issues from Scala-cell issues. If no notebook is cross-language, skip the Scala analyzer.

**Quality gate**: run the analyzer gate (a deterministic script):

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/scos_gates.py analyzer \
  --state <CONVERSION>/migration_state.json --json
```

The gate reports its outcome on stdout (`verdict` + `exit_code` in `--json`, or a `PASS`/`FAIL` line in human mode); read that directly rather than relying on a shell `$?` capture, which is not portable to Windows `cmd.exe` / PowerShell.

> Invoke through `uv run` (not a bare `python3`/`python`) so the gate runs on a guaranteed interpreter on macOS / Linux / Windows. `scos_gates.py` itself is stdlib-only, so it adds no dependencies.

The gate validates `analysis.json` (valid JSON array + risk-distribution sanity) and scans every manifest `.py` file for the analyzer's known blind spots (UDF / pandas_udf / udtf decorators, `applyInPandas`, `checkpoint`, JVM `._jdf`/`._jvm` access, `sparkContext`, map-subscript, Hadoop/HDFS, Delta, ML pipelines), flagging any match not covered by an `analysis.json` entry. Comment-only and already-`# SCOS:`-annotated lines are skipped.

**Gate**:
- Exit `0` (`PASS` or `PASS_WITH_GAPS`) → advance. `PASS_WITH_GAPS` carries advisory `WARN` findings only; record them but do not block.
- Exit `2` (`FAIL`) → re-run the analyzer step the same way you ran it (inline, or by re-dispatching the `../agents/analyzer.md` sub-agent in multi-file mode) using the gate's `gaps` array as targeted feedback (it names each uncovered `file:line` + blind-spot code) — usually this just means appending the missing supplementary entries to `analysis.json` — then re-run the gate. Retry at most **2 times**; if it still fails, escalate to the user.
- Exit `3` (IO / usage error) → the gate could not read `migration_state.json` or `analysis.json`. Re-running the analyzer will NOT fix this. STOP and escalate to the user immediately; do not retry.

Record the result and set `migration_state.json` phase to 1:
```json
"phases_completed": {"1_analysis": {"status": "passed", "gate": "scos_gates.analyzer", "verdict": "<PASS|PASS_WITH_GAPS>", "attempts": <n>}}
```

# Phase 1.1a: Unknown API Classification (skip if no needs_classification rows)

```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/scripts/classify_unknown_modules.py \
  --analysis <CONVERSION>/analysis.json --list-modules
```
If the output is `[]`, skip to Phase 1.1b.

Otherwise, classify each module name using your own knowledge: is it part of the
Spark/PySpark/Delta ecosystem, or a Spark-adjacent library (Azure Synapse utilities,
Databricks Connect, GraphFrames, Koalas, MLlib wrappers, etc.)? Then apply:

```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/scripts/classify_unknown_modules.py \
  --analysis <CONVERSION>/analysis.json \
  --classifications '{"<module>": "spark_related"|"not_spark_related", ...}'
```

```json
"phases_completed": {"1_5a_classification": {"status": "passed", "classify_spark": <n>, "classify_not_spark": <n>}}
```
**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 1.1a: unknown APIs classified"`

# Phase 1.1b: Adjudication (default; skipped only when there are no needs_adjudication rows)

**Run this phase by default.** The analyzer never calls `CORTEX.COMPLETE`; it
emits every non-decidable block as a `kind == "needs_adjudication"` row, so this
phase confirms-or-dismisses them. After Phase 1.1a runs, any Spark-relevant
unknown APIs are also present as `needs_adjudication` rows. **Skip this phase
only** when a workload has no `needs_adjudication` rows.

Quick check — if this prints `0`, there is nothing to adjudicate, so skip Phase 1.1b:
```bash
python3 -c "import json;print(sum(1 for r in json.load(open('<CONVERSION>/analysis.json')) if r.get('kind')=='needs_adjudication'))"
```

**What it does:** a pool of adjudicators confirms-or-dismisses each deferred
trigger match *before* the fixer runs, so (a) the fixer only ever implements
issues already judged real (no edit bias), and (b) `analysis.json` becomes a
fully-adjudicated artifact for Phase 1.2 / the gates / scoring. Adjudicators edit
**only** verdict sidecars — never source code, never `analysis.json` directly.

**Run mode — chunked worker pool (like Phase 2).** A single adjudicator over a
large workload overloads one context window and over-confirms, so fan the work
out across bounded workers:

1. **Plan** the chunks (bounded by files AND rows so no worker is overloaded):
   ```bash
   uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/scripts/orchestrate_adjudication.py \
     --analysis <CONVERSION>/analysis.json --max-parallel 4
   ```
   Read the printed `WAVE`/`CHUNK_ID`/`CHUNK_FILES` plan. If it prints
   `ADJUDICATION_PLAN chunks=0`, there is nothing to adjudicate — skip to the
   git checkpoint.
2. **Dispatch adjudicator workers wave by wave.** For each wave, spawn **all of
   that wave's chunks' `../agents/adjudicator.md` sub-agents concurrently** — issue
   the `task()` calls in a single turn. Pass each worker its `CHUNK_ID`,
   `CHUNK_FILES`, and `CONVERSION`. Each worker writes
   `<CONVERSION>/Adjudication/chunk_<CHUNK_ID>.json` and returns an
   `ADJUDICATION_RESULT` line. Workers never write `analysis.json`.
3. **Merge once (you are the single writer).** After all waves complete, fold
   every sidecar into `analysis.json`:
   ```bash
   uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/scripts/apply_adjudications.py \
     --analysis <CONVERSION>/analysis.json --verdicts-dir <CONVERSION>/Adjudication
   ```
   It applies each verdict — false positive → `resolution:safe` +
   `resolution_reason` + `adjudicated:true`; real → `kind:standard` +
   `adjudicated:true` + `final_risk` + `fix` — and prints
   `ADJUDICATION_RESULT confirmed=<n> dismissed=<n>` plus a
   `submitted=<n> applied=<n>` accounting line and a `matched_by` breakdown.

   **Exit `1` means verdicts were dropped — do not advance.** Every submitted
   verdict must be applied; if `applied < submitted`, adjudicator reasoning was
   discarded and the Phase-2 fixer would end up judging those rows itself, which is
   exactly what Phase 1.1b exists to prevent. Re-dispatch the chunk whose verdicts
   went unapplied, then re-run the merge. Use `--allow-unapplied` only to
   deliberately proceed after recording why.

   `UNRESOLVED_left=<k>` with exit `0` is different and less serious: those rows
   received no verdict from any worker (a sidecar never covered them). The Phase-2
   fixer fallback handles them, but they were never independently adjudicated —
   prefer re-dispatching the missing chunk.

   Verdicts are matched to rows by a content fingerprint (`file` + `cell_id` +
   `lines` + `ewi_code` + `code`), because `lines` alone is **cell-relative** for
   notebooks and therefore not unique — several unrelated issues routinely share a
   label like `"1-7"`. Sidecars that omit `cell_id`/`ewi_code`/`code` still apply via
   a positional fallback within the collision group, which `matched_by` will report
   as `positional`; seeing that means the adjudicator is not emitting the full key.

After the merge, every former `needs_adjudication` row is either `resolution:
safe` (the fixer leaves it) or `kind: standard` (the fixer implements it via its
normal per-issue path). Record the merge script's counts:
```json
"phases_completed": {"1_5_adjudication": {"status": "passed", "confirmed": <n>, "dismissed": <n>, "chunks": <c>}}
```
**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 1.1b: deferred issues adjudicated"`
