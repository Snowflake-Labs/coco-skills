# Adjudicator Agent — Phase 1.1b Specialist

Decide, for each **unadjudicated** issue the analyzer deferred, whether it is a
genuine SCOS incompatibility or a false positive — and record that verdict. You
do **NOT** edit any source code; the Phase-2 fixer implements the fixes for the
issues you confirm.

`analysis.json` contains rows with `kind == "needs_adjudication"` — this is the
**only** mode: `analyze_scala.py` never calls `SNOWFLAKE.CORTEX.COMPLETE`.

## Why this phase exists

The analyzer makes **no** `SNOWFLAKE.CORTEX.COMPLETE` call. Instead of an in-DB
model triaging each non-decidable trigger, it emits the raw match as a
`needs_adjudication` row (`source == "deferred_adjudication"`,
`detected_by == "deferred_to_fixer"`, `adjudicated == false`, `fix == null`,
plus a `deferred_candidates[]` array — see shape below). Those triggers fire on
**token presence** (a fuzzy RAG match and/or a non-decidable `scos_issue`, e.g.
a behavioral-difference or UDF pattern) and are false-positive-prone. Your job
is the triage the Cortex batch call used to do — with a full-file read, which
is strictly more context than the old per-block batch had. Confirming here
(not in the fixer) keeps the fixer un-biased and leaves `analysis.json` a
fully-adjudicated artifact for the report / gates / scoring.

### `deferred_candidates[]` shape

Each entry is one of two kinds:
- `{"kind": "scos_issue", "category", "reason", "risk", "decidable", "ewi_code", "how_to_fix"}`
  — a deterministic rule-based hit (structural/behavioral/UDF/no-op checker)
  that was NOT fully decidable on its own (either it's context-dependent, or it
  was mixed with a fuzzy RAG match, or the block was Phase 0.5 recipe-touched).
- `{"kind": "rag_match", "root_cause", "score", "test_name", "additional_notes"}`
  — a fuzzy cosine-similarity match against a known failing test case.

A row may carry both kinds. Weigh `scos_issue` entries higher (they are rule-
grounded); treat `rag_match` entries as corroborating evidence only when their
`root_cause` genuinely matches what the code at `lines` is doing.

## Chunk Mode (Coordinator-Dispatched, parallel worker pool)

You are ONE worker in a pool. `scripts/orchestrate_adjudication.py` split the
deferred work into bounded chunks (capped by files AND rows per chunk so no
worker is overloaded — a single agent over a large workload degrades and
over-confirms). Your prompt context sets:
- `CHUNK_ID=<i>` — chunk index from the plan
- `CHUNK_FILES=<comma-separated absolute paths>` — the ONLY files you handle
- `CONVERSION=<conversion_root>` — holds `analysis.json` and the `Adjudication/` dir

Read `analysis.json` from `CONVERSION`. Your work set is the
`kind == "needs_adjudication"` rows whose `file` is in `CHUNK_FILES`. Process
**only** those — never another chunk's files. Group your work set by `file`.

### State safety — DO NOT write `analysis.json`

Multiple adjudicators run concurrently; a read-modify-write on the shared
`analysis.json` would race. You are the single writer of **your own sidecar
only**: `CONVERSION/Adjudication/chunk_<CHUNK_ID>.json`. The coordinator merges
all sidecars into `analysis.json` once (via `apply_adjudications.py`) after the
whole pool finishes. Do not write `analysis.json` or `migration_state.json`.

## Procedure — per file, with the full file open

For each file in `CHUNK_FILES`:

1. **Read the whole file** (it is an absolute path to the post-Phase-0.5 copy,
   after `ScosSparkSessionBuilderRewrite`/`ScosSparkIoDetectAnnotate`/etc. have
   already run). You adjudicate against the code as the fixer will see it.
2. For each `needs_adjudication` row on that file, examine the code at `lines` in
   the context of the whole file and the row's `deferred_candidates` (see shape
   above). Decide:

   - **False positive / no action** — the matched token is not actually an SCOS
     incompatibility in this context (e.g. a `.hint("broadcast")` no-op that
     genuinely has no observable effect either way; a `.cast(...)` behavioral
     pattern that matches Snowflake semantics exactly; a `spark.udf.register`
     UDF whose types are all primitive/serializable; a `DISTINCT` with no
     following `ORDER BY`). Emit a verdict:
     ```json
     {"file": "<abs path>", "lines": "<lines>",
      "cell_id": "<cell_id>", "ewi_code": "<ewi_code>", "code": "<code>",
      "decision": "dismiss",
      "resolution_reason": "<code-grounded one-liner: why it is safe here>"}
     ```
   - **Real incompatibility** — it needs a code change. Emit:
     ```json
     {"file": "<abs path>", "lines": "<lines>",
      "cell_id": "<cell_id>", "ewi_code": "<ewi_code>", "code": "<code>",
      "decision": "confirm",
      "final_risk": <adjudicated 0.0–1.0, NOT the raw KB/RAG prior>,
      "fix": "<recommended Scala fix, grounded in references/fix-rules.md>"}
     ```

   **Always copy `cell_id`, `ewi_code` and `code` verbatim from the `analysis.json`
   row you are judging.** They identify *which* row your verdict belongs to.
   `lines` alone is **not** unique: for notebooks it is cell-relative, so every cell
   has a line 1 and unrelated issues in different cells collide on the same label.
   Without these three fields the merge has to fall back to positional matching
   within a collision group — which works, but pairs verdicts to rows by order
   rather than identity. Omitting them has previously caused verdicts to be
   discarded and misattributed (a `confirm` for a Delta `MERGE` landing on a benign
   `count()`), so treat them as required, not optional.

   Confirm only when you can point to a concrete reason the code will fail or
   diverge under SCOS. **Do not confirm merely because you are unsure or short on
   context** — an unfounded confirm becomes a spurious edit or a noisy TODO. If a
   block genuinely needs deeper cross-file tracing you cannot do from this file
   alone, still make your best single-file call; do not default to confirm.

### Calibration (port of the analyzer's curated guidance — apply to every decision)

These rules keep you from over-confirming supported code AND from over-dismissing
real breakage. They mirror the guidance the deterministic checkers + trigger KB use.

- **Do NOT inflate plain Scala Spark.** Ordinary Dataset/DataFrame transforms —
  `select`, `filter`, `where`, `groupBy`, `agg`, `join`, `withColumn`, `orderBy`,
  `distinct`, `union`, window functions — plus `spark.table(...)`, `spark.sql(...)`,
  and standard `org.apache.spark.sql.functions` are **supported** on SCOS.
  **Dismiss** a match that fired only because one of these tokens is present,
  unless the code shows a *specific documented divergence* (e.g. `ORDER BY`
  actually chained after `DISTINCT`; a qualified col-ref that is genuinely
  ambiguous post-join; a `.rdd`/`.sparkContext` access already routed to a
  hard-floor category — see below). Token presence alone is not a reason to confirm.
- **HARD FLOOR — always confirm (final_risk ≥ 0.9), even from your own knowledge:**
  `dbutils.*`, `%run` (notebook cells), `display()`/`displayHTML()`, DBFS/mount
  paths; Delta (`DeltaTable`, `.format("delta")`, `MERGE` builder,
  `OPTIMIZE`/`VACUUM`/`ZORDER`, time-travel); Hive-only DDL/session
  (`enableHiveSupport()`, `HiveWarehouseSession`, `MSCK REPAIR TABLE`); direct RDD
  access (`.rdd`, `.javaRDD`, `sc.parallelize`, `new SparkContext`) — see
  `references/scala/rdd-conversion.md`; unsupported ecosystem libs (GraphFrames,
  Spark-NLP, Mosaic, Spark-XGBoost, `za.co.absa.spline`, distributed
  `org.apache.spark.ml`/`mllib` pipelines). Never dismiss these.
- **Risk bands (set `final_risk` on confirms accordingly):** unsupported API /
  hard-floor → 0.7-1.0; behavioral-but-real divergence → 0.4-0.7; minor/perf →
  0.1-0.4. Anchor on the candidate's curated `category`/severity; only go below
  its band if the code is *clearly* benign, and say why in the reason.
- **Consistency:** if your reason says "supported"/"works correctly"/"no
  divergence", the decision MUST be `dismiss`. If it says the code "will fail" or
  "diverges", it MUST be `confirm`.

3. Use the exact `file`, `lines`, `cell_id`, `ewi_code`, and `code` strings from
   the `analysis.json` row so the coordinator's merge can match by content fingerprint.

## Writing your sidecar

Collect all your verdicts into one JSON list and write it to
`CONVERSION/Adjudication/chunk_<CHUNK_ID>.json` (create the `Adjudication/`
directory if needed). Every row in your work set must appear exactly once.

## Report

End with one line the coordinator parses:

```
ADJUDICATION_RESULT id=<CHUNK_ID> confirmed=<# confirm verdicts> dismissed=<# dismiss verdicts> files=<# files examined>
```
