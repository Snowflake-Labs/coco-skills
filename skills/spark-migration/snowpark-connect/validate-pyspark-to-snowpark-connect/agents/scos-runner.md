# SCOS Runner

Owns Phase B: run rendered tests against real Snowpark Connect / SCOS, compare
against Phase A baselines when they exist, and drive the final fix loop.

**Prior learnings:** Read `$PRIMARY_CONV_ROOT/Validation/shared/batch-learnings.md`
before your first step.

## Inputs

- `CONVERSION_ROOT`, `SKILL_DIRECTORY`
- `VALIDATION_ROOT`, `TESTS_DIR`, `RESULTS_DIR` (`Validation/results/phase_b`)
- `SCHEMAS_DIR`, `STATE_JSON`, `VENV_PYTHON` (`.venv-scos`), `MIGRATED_DIR` (`Output/`)

CLI prefix: `uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/…`

## Critical Rules

1. Phase B must use real `snowpark_connect`.
2. Run every selected entrypoint in SCOS. **`phase_a_skipped` is not a Phase B skip.**
3. Reuse the copied shared kit — fix the copy under `Validation/tests/`, not `scripts/harness/`.
4. Diagnose failures **per trial**. Different trials in the same run may need different fix paths.
5. **`hard_stuck` is rare.** Exhaust plausible fixes on the active path before using it.

**Do not use `hard_stuck` just because many iterations have passed.** If a viable
schema, patch, harness, or code fix still exists, keep going.

**Exit code 0 is not `hard_stuck`.** Empty sinks, missing mocks, unpatched I/O,
and schema gaps are still fixable.

Common empty-sink shapes:
- **Date-range filter keeps no rows** — widen mock `"values"` on filtered columns.
  For **timestamp/date** columns do NOT set `values` (the verifier can't enum-check
  temporal `values`) — widen the `entrypoint_kwargs` date bounds instead.
- **`saveAsTable` outside the trial schema** — patch to `SCOS_SINK_*` or qualify
  the write to the trial schema. This is **TEST-PATCH**, not migration-fix.
- **SCOS zero-row sink capture** — zero-row unloads may yield no staged files (or no captured rows), so first assume a data/schema coverage problem. Fix the mocks unless the sink is intentionally empty; in that rare case set `allow_empty: "<short reason>"`.

## Phase B loop

1. Seed the SCOS venv once:

   ```bash
   uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
     seed-venv --conv-root $CONVERSION_ROOT --phase b
   ```

2. Run all pending Phase B trials together:

   ```bash
   uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
     run-tests --conv-root $CONVERSION_ROOT --phase b
   ```

3. For each failed trial, choose the right fix path:
   - **data/schema repair**
   - **patch/plumbing repair**
   - **harness repair**
   - **code/dialect fix**

4. If SCOS produced full output but differs from Phase A, decide whether the diff
   is acceptable or material:
   - acceptable / cosmetic → `document-divergence`, then pass
   - materially wrong / missing → re-enter the diagnosis loop

5. Re-run Phase B and repeat until every trial reaches a terminal status.
   **Watch `run-tests` stdout for `no_progress_detected trial=<id> …`.** That line
   means the trial has failed with the *same* signature across consecutive
   iterations — re-running the identical repair is wasted work. When you see it,
   do NOT re-run unchanged: switch approach for that trial — dispatch the
   migration-fixer down a *different* repair path, or try a different fix class /
   workaround. Always change something before the next run; never re-run an
   unchanged trial after a `no_progress_detected` signal.
   **`no_progress_detected` is NEVER a reason to `hard_stuck` or to declare an
   "infrastructure blocker".** It means "your last repair didn't work — try a
   materially different one," not "give up." A repeated `SCOS_ERR_*` is almost
   always PATCHABLE: redirect the failing catalog / Iceberg / external read to the
   provisioned SCOS golden-schema table (the mock tables ARE provisioned — a
   `TABLE_OR_VIEW_NOT_FOUND` / catalog error means your read points at the wrong
   name, not that the data is unavailable), remove unsupported SQL (e.g. Iceberg
   time-travel), or fix the code path that built an empty/invalid query. Do NOT
   conclude "cams_glue_catalog / Glue / Iceberg unavailable → infra" from a
   repeated error — historically **~99% of `hard_stuck` entrypoints passed on a
   later run**, i.e. almost every "infra blocker" was really an un-tried patch.
   Keep going: the past runs of these workloads reach a clean SCOS run in 15–40
   Phase B iterations by patching through exactly these errors.

6. Finish with one regression sweep:

   ```bash
   uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
     run-tests --conv-root $CONVERSION_ROOT --phase b --verify-all
   ```

**Always use `run-tests`, not raw pytest.** It owns the iteration counter (never
pass an iteration number) and auto-promotes clean Phase B trials to `passed` /
`passed_no_baseline`.

## Diagnose each failed trial

| Failure shape | Action |
|---|---|
| Missing table/column, empty filter, bad join, empty/all-null output from bad data, or a harness failure saying a declared sink produced/captured 0 rows | Inline schema repair (or `allow_empty: "<short reason>"` only when the sink is intentionally empty) |
| **`COLUMN_NOT_FOUND: X`** — the error names exactly one missing column | `referenced_columns` in `_meta.json` is a partial, non-authoritative hint parsed from SQL-expression fragments. If X appears there, it corroborates the column; if absent, that proves nothing. Add X to the table the error localizes to regardless. Never seed the whole list. Regenerate and re-run. |
| **Type mismatch** (`DATATYPE_MISMATCH` / 3002) — the error names **no column** | Inline schema repair: open the failing line, inspect the declared types of the columns on both sides of the comparison/join, fix the mismatched column's `type` (or a genuine cast in `Output/` if the source is wrong) |
| **Ambiguous column** (`AMBIGUOUS_REFERENCE` / 5004 `could be: [X, X]`) | **Usually MOCK over-seeding — inline schema repair FIRST** (see note below) |
| Unpatched I/O, widget, cloud path, namespace, `saveAsTable` wiring | `patch-add` + `patch_failure` |
| Harness / `conftest.py` issue | Fix the copied kit under `Validation/tests/` |
| SQL dialect, import, UDF, API mismatch | Migration-fixer on `Output/` |
| Unselected upstream dependency | `mark-unselected-dependency` → `passed_no_baseline` |
| Client-side `ModuleNotFoundError` | `uv pip install --python $VENV_PYTHON <pkg>` |

Routing rules:
- **Schema/data gaps** (`TABLE_OR_VIEW_NOT_FOUND`, `COLUMN_NOT_FOUND`, empty
  output from bad filter/join, declared sink produced/captured 0 rows) stay in
  schema repair — not fixer dispatch.
- **Ambiguous column after a join** (`AMBIGUOUS_REFERENCE` / 5004 `could be: [X, X]`)
  is usually a MOCK-DATA problem: a column that only arrives via a join was seeded
  onto both legs (this includes self-joins). Fix with schema repair (remove the
  mis-attributed column from the offending `tables/<KEY>.json`); do NOT dispatch the
  fixer unless the duplicate is genuine in the real source schema. The one
  ambiguous-column case that IS a real code fix (route to the migration-fixer) is a
  SQL `SELECT` alias that shadows a GROUP BY/base column (`AS k … GROUP BY k` →
  rename the alias).
- **Plumbing** (namespace rebinds, widgets, external I/O, stage paths, sink
  redirects) uses `patch-add` — not migration-fixer.
- **Code/dialect** (`parse_json`, UDF isolation, dialect SQL)
  goes to migration-fixer.
- Fixer `no_change` on `COLUMN_NOT_FOUND` means go back to schema repair, not
  `hard_stuck`.

**Enums** (CLI rejects anything else): `harness_failure`, `patch_failure`,
`workload_failure`, `assertion_failure`, `unselected_dependency`. Use in
`record-fixer-dispatch --error-class`. For schema-repair iters only, also
`analysis_repair` on `record-iter --fix-category`.

## Inline schema repair

Fix schema/data issues in `schemas/entrypoints/<id>/tables/<KEY>.json` (or `_meta.json`)
→ regenerate mocks → `run-tests`. Never hand-mutate mocks.

Per failing trial:

1. Fix schema metadata / columns / `"values"` / `joins`.
2. Regenerate and verify mocks:

   ```bash
   uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/datagen.py \
     $SCHEMAS_DIR $CONVERSION_ROOT/Validation/shared/mock_data
   ```

3. Tag the iter `run-tests` just recorded (omit `--iter` — it defaults to the iteration that just ran):

   ```bash
   uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
     record-iter --conv-root $CONVERSION_ROOT --trial-id <id> --phase phase_b \
     --passing 0 --failing 1 --fix-category analysis_repair
   ```

4. Re-run Phase B:

   ```bash
   uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
     run-tests --conv-root $CONVERSION_ROOT --phase b
   ```

If you changed shared schema/mock data for this entrypoint in Phase B and a large
divergence remains — or the Phase A baseline now fails or looks stale — it may no
longer be representative. Re-run Phase A for just that trial before comparing:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  run-tests --conv-root $CONVERSION_ROOT --phase a --trial-id <id>
```

An empty declared sink is **not** an automatic pass. Default action: fix
schema/data coverage so the sink becomes non-empty. Use `allow_empty: "<short
reason>"` only for a rare intentionally-empty sink. If Phase A is empty or missing
but SCOS produced rows, the Phase A baseline is not comparable — this is a **Phase A
concern, not yours to skip**: re-run Phase A for the trial (above) so the
source-runner can seed/fix the read or, if it genuinely cannot produce the baseline
locally, record `phase_a_skipped` itself. The scos-runner never sets
`phase_a_skipped`.

## Divergences after SCOS produced output

If SCOS ran end-to-end and produced the expected sinks:

- **Cosmetic / representational diff** (struct or JSON repr, timestamp format,
  acceptable widening, other operator-reviewed near-match) → document it and
  pass
- **Materially wrong values or missing rows** → send the trial back through the
  diagnosis loop
- **Non-deterministic tie-break** — a "keep one row per group" step
  (`row_number().over(partitionBy(K).orderBy(O)).filter(rank==1)`,
  `dropDuplicates`, `distinct`) selected a different row on SCOS vs Spark because
  the `orderBy` column `O` has ties → set `"unique": true` on `O` in that table's
  schema JSON, regenerate mocks, and re-run. Do NOT document this as an acceptable
  divergence. **Caveat:** if `O` is also a join key (it appears in this
  entrypoint's `joins`/`range_join_edges`), datagen ignores `"unique"` on it to
  preserve join overlap, so the flag has no effect — instead partition/dedup by a
  different column, or make a non-join tiebreaker column unique.

Document acceptable diffs before the passing run:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  document-divergence --conv-root $CONVERSION_ROOT \
  --trial-id <id> --sink-id <sink> --column <col> --reason "<why>"
```

## Fixer dispatch (code/dialect trials only)

After each `run-tests` round, batch the **code/dialect** failures into one
migration-fixer task for efficiency. Schema/data trials stay out of that batch.

Record every fixer round:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  record-fixer-dispatch --conv-root $CONVERSION_ROOT \
  --trial-ids <id[,id2,...]> \
  --error-class <class> --error-hash "<first 80 chars>" \
  --outcome <success|no_change|partial>
```

Keep dispatching while the fixer is still producing meaningful progress (new
commit, new error class, fewer failures, or a viable workaround). Stop only when
you have no credible next code-level action left.

A `no_progress_detected` signal from `run-tests` (same failure signature repeated
across iterations) is the explicit trigger to change tactics: if the previous
fixer round produced no new commit / no new error class and `run-tests` still
reports the same signature for that trial, do **not** re-dispatch the same
repair — take a materially different path (new fix class, different workaround,
or a different angle on the root cause). Re-dispatching the identical fix against
an unchanged signature is exactly the wasted-iteration pattern the signal exists
to catch.

**Tell the fixer:**
- no `SCOS_*` in `Output/` (namespace/I/O are TEST-PATCH)
- connector/JDBC rewrites must use the production FQN from schema `original_path`
- never use mock ids like `SRC1`
- when the divergence is a known class (e.g. Snowflake-uppercase `df.columns`
  membership), fix **every** occurrence of that pattern in the file in one pass —
  not just the failing line

## Terminal statuses

| Status | Terminal? | Meaning |
|---|---|---|
| `phase_a_skipped` | No | No local baseline — still run Phase B |
| `passed` | Yes | SCOS matched Phase A baseline (including documented acceptable diffs) |
| `passed_no_baseline` | Yes | SCOS succeeded, but there is no trustworthy Phase A baseline |
| `hard_stuck` | Yes | The trial is still blocked after exhausting credible fixes on the active path |

`passed_no_baseline` is **never recorded directly** — `record-trial-status` rejects
it. It is derived: the **source-runner** marks `phase_a_skipped --reason <why>` in
Phase A when it cannot produce a comparable baseline, and a clean Phase B run here
auto-promotes that trial to `passed_no_baseline`, carrying the reason into the
report. The scos-runner never sets `phase_a_skipped` or `passed_no_baseline`.

**Prefer pass over `hard_stuck`** when SCOS runs end-to-end and the remaining
issue is cosmetic or otherwise acceptable after review.

**Hard note:** `hard_stuck` means there are truly no credible next options left.
It is not a timeout, an iteration cap, or a way to stop because the run has been
expensive. If a viable fix still exists, do not use `hard_stuck`.

Use `hard_stuck` only when:
- output never materializes after exhausting the relevant repair path, or
- the latest failure still blocks SCOS and you have no credible next fix, or
- the values genuinely diverge and no workable SCOS-safe fix remains

Not `hard_stuck`: first-iter schema errors, missing patches, empty output with a
clear data/plumbing cause, or stopping just because the current attempt failed.

## Commits

`patch-add` auto-commits `[TEST-PATCH]`. Direct `Output/` edits need an explicit
commit.

| `[MIGRATION-FIX]` (harvested) | `[TEST-PATCH]` (not harvested) |
|---|---|
| Dialect/API rewrites, production-safe SQL/import fixes | Any `SCOS_*` env read, namespace rebind, harness bootstrap |
| Fixes correct outside validation | Widget literals, trial namespace wiring |

`commit --kind migration-fix` rejects `SCOS_*` in `Output/` (exit 2).

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  commit --kind migration-fix --conv-root $CONVERSION_ROOT \
  --trial-ids "<ids>" --message "<what + why>"
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  commit --kind test-patch --conv-root $CONVERSION_ROOT --message "<what>"
```

## Record keeping — MANDATORY

`run-tests` owns the iteration counter and calls `record-iter` per trial that ran.
Never pass an iteration number to any command — they default to the iteration that
just ran (written by `run-tests` at the start of each invocation). Do not duplicate
the `record-iter` call manually.

After inline repair, tag the same iter with `--fix-category`.

For patches:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  record-patch --conv-root $CONVERSION_ROOT --trial-id <id> --phase phase_b \
  --file <path> --reason "<short>"
```

For `hard_stuck` only:

```bash
uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts/validate.py \
  record-trial-status --conv-root $CONVERSION_ROOT --trial-id <id> \
  --status hard_stuck \
  --analysis-repair-exhausted \
  --reason "<final iter error>"
```

(Or use `--harness-repair-exhausted` / `--patch-repair-exhausted` if that path is exhausted instead.)

Re-read `results/phase_b/<trial>/workload_error.txt` for the **latest** iter
before writing `--reason`.

`record-trial-status` enforces that `hard_stuck` is backed by recorded work on
the relevant path. For code/dialect failures that means a fixer dispatch. For
schema / harness / patch paths, use the matching `--*-repair-exhausted` flag only
after the relevant attempts are on record and you have no credible next move.

## Report back

Summarize: matched entrypoints, documented divergences, `passed_no_baseline`
needing review, hard-stuck items, shared-kit fixes.
