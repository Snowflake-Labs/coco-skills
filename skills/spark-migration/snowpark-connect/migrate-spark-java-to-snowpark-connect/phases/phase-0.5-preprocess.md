# Phase 0.5: Deterministic AST Pre-Processing (MUST RUN)

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

### Phase 0.5: Deterministic AST Pre-Processing (MUST RUN)

**This phase MUST run as the first deterministic step of every migration**, after Phase 0 has populated `<MIGRATED>` with the source copy and before the LLM analyzer in Phase 1.

It runs the JavaParser-AST rules (`scripts/javaparser_rules/ScosJavaRewrite.java`) on every `.java` file in the manifest using `LexicalPreservingPrinter` for byte-accurate output. This is the sole deterministic pre-processing tier — the direct analogue of Scalafix for the Java path.

**Why this exists:** The LLM fixer historically drops mechanical details — the canonical example is silently losing `SparkSession.builder().config("spark.sql.session.timeZone", "UTC")` when collapsing the builder chain. The JavaParser rules solve those mechanical patterns byte-for-byte once, so the LLM can spend tokens on the genuinely hard stuff.

**Session-init rewriting (Phase 0.5, non-test files).** The `ScosSparkSessionBuilderRewrite` rule performs the full builder rename at the AST level: `SparkSession.builder()` → `SnowparkConnectSession.builder()`, dropping `.master(...)`, `.enableHiveSupport()`, and `.remote(...)`. It emits `// SCOS-RECIPE-PRESERVED-CONFIG: k=v` markers for every `.config(k, v)` call. Test files (`*Test.java`, `src/test/`) are left on `SparkSession`.

**Hard prerequisite (JDK + Maven):** Java projects always have a JVM. You need `uv` plus a **JDK 11 or 17** (Java 8 and Java 21 are not supported) and **Maven** (or `./mvnw`). The fat-jar is built once at `scripts/javaparser_maven/target/scos-javaparser-*.jar` and cached. If no JDK/Maven is found, the driver exits 1 and records `status: "failed"` — **HARD failure, do NOT advance to Phase 1.**

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/preprocess_javaparser.py \
  --state <CONVERSION>/migration_state.json
echo "preprocess_exit=$?"
```

**Hard gate (all must be true):**
1. The script exits 0.
2. `migration_state.json["phases_completed"]["0_5c_javaparser"]["status"] == "passed"`.
3. A JDK+Maven runner was resolved.

If exit code is non-zero, do NOT advance to Phase 1. Fix the underlying issue (missing JDK/Maven or un-parseable Java file) and re-run. The driver is idempotent.

**Write contract:**
```json
"phases_completed": {
  "0_5c_javaparser": {
    "status": "passed",
    "ran_at": "<ISO-8601 UTC>",
    "files_processed": <int>,
    "files_modified": <int>,
    "total_edits": <int>,
    "rules_run": ["ScosSparkSessionBuilderRewrite", "ScosCheckpointToCache", "..."]
  }
}
```

Plus a top-level `recipe_edits` block keyed by relative path. The `recipe_id` uses the `javaparser:<RuleName>` namespace:

```json
"recipe_edits": {
  "<rel_path>.java": [
    {
      "recipe_id": "javaparser:ScosSparkSessionBuilderRewrite",
      "src_line": <int>,
      "output_line_anchor": "javaparser:<RuleName>:<src_line>:<8-hex>"
    }
  ]
}
```

The analyzer (Phase 1) and fixer (Phase 2) **MUST** read `recipe_edits` to recognise AST-managed regions and must NOT re-flag, re-rewrite, or revert them.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 0.5: deterministic AST pre-processing (javaparser)"`

### Phase 0.6: Standalone SQL Rewrite (conditional, MUST RUN when `.sql` files present)

Run the deterministic SQL rewriter over standalone `.sql` files before the
analyzer sees them. This is the **same** rewriter the PySpark and Scala paths use
— it is language-agnostic (it rewrites SQL text, not host code). It applies only
to standalone `.sql` files in the workload; embedded `spark.sql("...")` strings
inside `.java` files are handled by the LLM fixer in Phase 2 (see
`<SKILL_DIRECTORY>/references/sql/sql-fix-rules.md`).

Skip this phase entirely when the workload has **no** `.sql` files (the common
case for pure Java projects). Record `status: "not_applicable"` with
`reason: "no .sql files"`.

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/rewrite_sql_files.py \
  --state <CONVERSION>/migration_state.json
```

This writes `sql_rewrite_edits` into `migration_state.json` and annotates each
`.sql` file with a `-- SCOS Migration Output` header. The `.sql` files are
intentionally NOT added to the manifest (they do not feed back into the `.java`
analysis loop); they are tracked only via `sql_rewrite_edits` and the
`language:"sql"` rows of `analysis.json`.

Record in `migration_state.json`:
```json
"phases_completed": {"0_6_sql_rewrite": {"status": "passed", "sql_files": <N>}}
```
(or `"not_applicable"` with a reason when skipped.)

⛔ **Do not skip this when `.sql` files are present.** `scripts/validate_migration_state.py`
promotes `0_6_sql_rewrite` from optional to **hard-required** whenever the workload
contains standalone `.sql` files — otherwise their SCOS gaps are detected in Phase 1
and never rewritten. Missing it fails Phase 4a. `agents/fixer.md` also assumes this
phase owns `.sql` files end to end and instructs the fixer not to touch them.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 0.6: standalone SQL rewrite"`
