# Phase 1: Analysis

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

### Phase 1: Analysis

**Run the analyzer directly (no specialist agent)** — the coordinator runs `analyze_java.py` itself. The analyzer makes **no** `CORTEX.COMPLETE` calls: pattern detection is deterministic (AST facts when available, else regex fallback) plus RAG retrieval (`predict_failure`, similarity only — no generation). Blocks whose findings are **structurally-decidable failures** (exact unsupported import/format/module/Dataset API, or the `.rdd` gateway) are emitted deterministically (`source="trigger_decidable"`); blocks that are **fully result-identical** (every method call on the `data/java/safe_apis.json` allowlist, no deterministic issue) are dropped without a RAG round-trip; every other flagged block is deferred as a `kind == "needs_adjudication"` row (`source="deferred_adjudication"`) for **Phase 1.1b** below to confirm-or-dismiss, and Java API calls not covered by any detection source are emitted as `needs_classification` rows for the lightweight Phase 1.1a classifier.

1. **Run the analyzer**:
```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/analyze_java.py \
  --path <MIGRATED> \
  --recipe-edits <CONVERSION>/migration_state.json \
  --rag-backend trigger \
  --output <CONVERSION>/analysis.json
```

2. **Record the phase:**
```json
"phases_completed": { "1_analysis": {"status": "passed", "issues_found": <N>} }
```

> `agents/analyzer.md` is retained as human-readable reference for the analyzer flow.

**Verify (deterministic)**: run `verify_phase.py --phase 1 --language java`:
```bash
python3 <SKILL_DIRECTORY>/scripts/verify_phase.py \
  --phase 1 --language java --strict \
  --state <CONVERSION>/migration_state.json
echo "verify_phase1_exit=$?"
```

**Gate**: exit 0 ⇒ `PASS` or `PASS_WITH_GAPS`. On exit 1 (`FAIL`), re-run the analyzer with feedback (max 2 retries). Update `migration_state.json` phase to 1.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 1: analysis complete" && git tag -f phase-1-complete`

> The `phase-1-complete` tag is **required**: the Phase 2b compile gate reverts any file that fails to compile back to this tag.

### Phase 1.1a: Unknown API Classification (skip if no needs_classification rows)

```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/scripts/classify_unknown_modules.py \
  --analysis <CONVERSION>/analysis.json --list-modules
```
If the output is `[]`, skip to Phase 1.1b.

Otherwise, classify each module name using your own knowledge: is it part of the
Spark/Java/Delta ecosystem, or a Spark-adjacent library (Azure Synapse utilities,
Databricks Connect, GraphFrames, MLlib wrappers, etc.)? Then apply:

```bash
uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/scripts/classify_unknown_modules.py \
  --analysis <CONVERSION>/analysis.json \
  --classifications '{"<module>": "spark_related"|"not_spark_related", ...}'
```

```json
"phases_completed": {"1_5a_classification": {"status": "passed", "classify_spark": <n>, "classify_not_spark": <n>}}
```
**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 1.1a: unknown APIs classified"`

### Phase 1.1b: Adjudication (default; skipped only when there are no needs_adjudication rows)

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
fully-adjudicated artifact for Phase 1a / the gates / scoring. Adjudicators edit
**only** verdict sidecars — never source code, never `analysis.json` directly.

**Run mode — chunked worker pool (like Phase 2), same scripts PySpark and Scala use**
(`orchestrate_adjudication.py` / `apply_adjudications.py` are language-agnostic —
they operate on content fingerprints (`file` + `cell_id` + `lines` + `ewi_code`
+ `code`) in `analysis.json`, not on source code):

1. **Plan** the chunks (bounded by files AND rows so no worker is overloaded):
   ```bash
   uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/scripts/orchestrate_adjudication.py \
     --analysis <CONVERSION>/analysis.json --max-parallel 4
   ```
   Read the printed `WAVE`/`CHUNK_ID`/`CHUNK_FILES` plan. If it prints
   `ADJUDICATION_PLAN chunks=0`, there is nothing to adjudicate — skip to the
   git checkpoint.
2. **Dispatch adjudicator workers wave by wave.** For each wave, spawn **all of
   that wave's chunks' `agents/adjudicator.md` sub-agents concurrently** — issue
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
