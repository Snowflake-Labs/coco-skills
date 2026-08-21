# Phase 0.5: Deterministic AST Pre-Processing (MUST RUN)

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

### Phase 0.5: Deterministic AST Pre-Processing (MUST RUN)

**This phase MUST run as the first deterministic step of every migration**,
after Phase 0 has populated `<MIGRATED>` with the source copy and before
the LLM analyzer in Phase 1 sees the code. It runs the AST-grade Scalafix
rules (`scripts/scalafix_rules/`, Scalameta `SyntacticRule`s) on every
`.scala` file in the manifest. This is the **sole** deterministic
pre-processing tier — the analogue of libcst for PySpark — and the regex
recipe tier (`recipes_scala/`) has been removed entirely.

**Databricks Scala notebooks.** Databricks `.scala` notebooks in the manifest
(both native JSON format and exported-text format with `// Databricks notebook source`)
are also processed. Each Scala code cell is extracted, wrapped in a minimal synthetic
`object` body so Scalafix can parse it as a valid compilation unit, processed by the
same rules (see `references/scala/recipes.md` for the full list), and the transformed content is written back into the notebook. The wrapper
is stripped from the output; only the cell content changes. Cell-level Scalafix failures
are non-fatal — that cell is left unchanged and the notebook's other cells continue.
This ensures all rules apply to notebook cells identically to plain source files.

**Why this exists:** the LLM fixer in Phase 2 is good at judgment-heavy
rewrites (UDFs, custom logic, ambiguous SQL) but historically dropped
mechanical details — the canonical example is silently losing
`SparkSession.builder().config("spark.sql.session.timeZone", "UTC")` when
collapsing the builder chain, which shifts every timestamp in the
migrated workload on machines with non-UTC JVM defaults. The Scalafix
rules solve those mechanical patterns byte-for-byte once, so the LLM can
spend its tokens on the genuinely hard stuff. Because they are
Scalameta-AST-aware they handle multi-line chains, string interpolation,
computed expressions, and chained-receiver forms that a regex pass cannot
match — with no comment/string false positives.

**Session-init rewriting (Phase 0.5, non-test files).** The `ScosSparkSessionBuilderRewrite` rule performs the full builder rename at the AST level: `SparkSession.builder` → `SnowparkConnectSession.builder()`, dropping `.master(...)`, `.enableHiveSupport()`, and `.remote(...)` from the chain. It also emits `SCOS-RECIPE-PRESERVED-CONFIG: k=v` markers for every `.config(k, v)` call so Phase 3 can re-materialize them after the Phase 2 LLM fixer has run. Test files (names ending in `Test/Spec/Suite.scala`) are left on `SparkSession` so local harnesses keep `master("local[*]")`.

**I/O detection (Phase 0.5).** `ScosSparkIoDetectAnnotate` annotates all Spark I/O call chains that require attention in SCOS: JDBC (`.format("jdbc")`/`.jdbc(...)`) → `[SPRKCNTSCL6000-Error]`; Iceberg (`.format("iceberg").load/save`) → `[SPRKCNTSCL3200-IO]`; table reads/writes (`.read.table(name)`/`.insertInto(name)`) → `[SPRKCNTSCL3200-IO]`. These are annotation-only (never rewrites) — the LLM fixer in Phase 2 resolves the concrete target. Cloud URI reads (`s3://`, `gs://`, …) and wildcard paths are handled by their own dedicated rules (`ScosExternalCloudReadAnnotate`, `ScosWildcardReadAnnotate`). The full rule set is documented in `references/scala/recipes.md`.

**Hard prerequisite (SBT + JVM):** Scala migrations are SBT/JVM projects,
so the AST runner is **mandatory, not best-effort**. You need `uv` (always)
plus **one of**: `sbt` + a JVM (preferred — every Scala project has this),
`scalafix-cli` on PATH, or Coursier (auto-bootstrapped). The runner is
resolved in that order. Pinned, verified versions: scala 2.12.20,
scalafix-cli 0.14.3. The first sbt resolve (or first `cs launch`) downloads
the Scala toolchain + scalafix once; subsequent runs are cached.

The driver is Python (already in `pyproject.toml`), so invoke it via `uv run`:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/preprocess_scalafix.py \
  --state <CONVERSION>/migration_state.json
echo "preprocess_exit=$?"
```

**Hard gate (all must be true):**

1. The script exits 0.
2. `migration_state.json["phases_completed"]["0_5b_scalafix"]["status"] == "passed"`.
3. A runner was resolved. **If no runner is available (no `sbt`+JVM, no
   `scalafix-cli`, no Coursier), the script exits 1 and records
   `status: "failed"` — this is a HARD failure, not a skip.** Do NOT advance
   to Phase 1: install `sbt` + a JVM and re-run.

If exit code is non-zero, do NOT advance to Phase 1. Re-read the error,
fix the underlying issue (a missing JVM/sbt runner, or an un-parseable
Scala file in `<MIGRATED>/`), and re-run the driver. The driver is
idempotent — running it again on already-rewritten files is a safe no-op.
If scalafix runs but fails on an individual plain file or notebook cell,
that item is logged in `failures` and processing continues — one bad file
does not abort the run, but a missing runner does.

Opt-outs (tune the runner; they do NOT make the phase optional):
- `--no-sbt` / `SCOS_SCALAFIX_USE_SBT=0` — disable the sbt runner.
- `--no-bootstrap-coursier` / `SCOS_BOOTSTRAP_COURSIER=0` — disable Coursier bootstrap.
- `--no-auto-launch` / `SCOS_SCALAFIX_AUTO_LAUNCH=0` — disable Coursier launch entirely.

**Write contract** (the driver records this for you; do not touch it
manually unless overriding):

```json
"phases_completed": {
  "0_5b_scalafix": {
    "status": "passed",
    "ran_at": "<ISO-8601 UTC>",
    "files_processed": <int>,        // plain .scala + notebooks with Scala cells
    "files_modified": <int>,
    "total_edits": <int>,
    "rules_run": ["ScosSparkSessionBuilderRewrite", "ScosCheckpointToCache", "..."],
    "notebooks_processed": <int>,    // present only when notebooks were in manifest
    "notebooks_modified": <int>
  }
}
```

Plus a top-level `recipe_edits` block keyed by relative path. For notebooks,
the `output_line_anchor` includes a `cell<N>` segment to identify the cell:

```json
"recipe_edits": {
  "<rel_path>.scala": [
    {
      "recipe_id": "scalafix:ScosSparkSessionBuilderRewrite",
      "src_line": <int>,
      "output_line_anchor": "scalafix:<RuleName>:<src_line>:<8-hex>"
    }
  ],
  "<notebook_path>.scala": [
    {
      "recipe_id": "scalafix:ScosCheckpointToCache",
      "src_line": <int>,
      "output_line_anchor": "scalafix:ScosCheckpointToCache:cell<N>:<src_line>:<8-hex>"
    }
  ]
}
```

The analyzer (Phase 1) and fixer (Phase 2) **MUST** read `recipe_edits` to
recognise AST-managed regions. These regions are already handled
deterministically: the analyzer MUST NOT re-flag them and the fixer MUST NOT
re-rewrite, collapse, or undo them (binding — see `agents/fixer.md`). The Phase 2
verifier enforces this by asserting every `SCOS-RECIPE-PRESERVED-CONFIG` pair is
still materialized.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 0.5: deterministic AST pre-processing (scalafix)"`

### Phase 0.6: Standalone SQL Rewrite (conditional, MUST RUN when `.sql` files present)

Run the deterministic SQL rewriter over standalone `.sql` files before the
analyzer sees them. This is the **same** rewriter the PySpark path uses — it is
language-agnostic (it rewrites SQL text, not host code). It only applies to
standalone `.sql` files in the workload; embedded `spark.sql("...")` strings
inside `.scala` files are handled by the Phase 0.5 `ScosSparkSqlMechanicalRewrite`
Scalafix rule and the LLM fixer (see `references/sql-fix-rules.md`).

Skip this phase entirely when the workload has **no** `.sql` files (the common
case for pure Scala projects). Record `status: "not_applicable"` with
`reason: "no .sql files"`.

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/rewrite_sql_files.py \
  --state <CONVERSION>/migration_state.json
```

This writes `sql_rewrite_edits` into `migration_state.json` and annotates each
`.sql` file with a `-- SCOS Migration Output` header. The `.sql` files are
intentionally NOT added to the manifest (they do not feed back into the
`.scala` analysis loop); they are tracked only via `sql_rewrite_edits` and the
`language:"sql"` rows of `analysis.json`.

Record in `migration_state.json`:
```json
"phases_completed": {"0_6_sql_rewrite": {"status": "passed", "sql_files": <N>}}
```
(or `"not_applicable"` with a reason when skipped.)

`0_6_sql_rewrite` is an **optional** phase key for Scala
(`scripts/validate_migration_state.py` → `OPTIONAL_PHASES`) — it does not fail
strict mode when absent, but recording it keeps the run report honest.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 0.6: standalone SQL rewrite"`
