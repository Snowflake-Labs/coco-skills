# Phase 0.5: Deterministic Pre-Processing (MUST RUN)

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

**This phase MUST run as the first deterministic step of every migration**,
after Phase 0 has populated `<MIGRATED>` with the source copy and before
the analyzer in Phase 1 sees the code. It applies every registered
LibCST recipe under `<SKILL_DIRECTORY>/scripts/recipes/` to every Python
file in the manifest.

**Why this exists:** the LLM fixer in Phase 2 is good at judgment-heavy
rewrites (UDFs, custom logic, ambiguous SQL) but historically dropped
mechanical details — the canonical example is silently losing
`SparkSession.builder.config("spark.sql.session.timeZone", "UTC")` when
collapsing the builder chain, which shifts every timestamp in the
migrated workload by 8h on US laptops. Recipes solve those mechanical
patterns byte-for-byte once, so the LLM can spend its tokens on the
genuinely hard stuff.

The driver script is pure Python + LibCST (already in `pyproject.toml`),
so invoke it via `uv run`:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/preprocess_recipes.py \
  --state <CONVERSION>/migration_state.json
echo "preprocess_exit=$?"
```

**Built-in pre-flight (runs automatically first):** before applying any
recipe, `preprocess_recipes.py` runs `scripts/precompile_check.py`, which
`compile()`s every Python unit (whole `.py` file, or each python code cell of
a notebook). This exists because LibCST recipes call `cst.parse_module` and
**silently skip un-parseable input** — so a *pre-existing* syntax error in the
customer's source (the canonical case: an entire notebook cell stray-indented
at module level → `IndentationError: unexpected indent`) would otherwise
survive untouched into Phase 2, where the fixer's compile guard reverts the
whole file on every pass without ever fixing anything. The pre-flight:

- attempts **guarded, whitespace-only** auto-fixes (uniform dedent;
  module-scope logical-line dedent) — a transform is accepted only if the unit
  then compiles, so it can never change semantics beyond indentation; and
- records every unit that started broken in
  `migration_state.json["preexisting_syntax"]` as
  `{file, cell_id, error, auto_fixed}`.

Residual entries with `auto_fixed: false` are genuine source bugs the
pre-flight could not safely repair. Downstream phases consume this record: the
fixer (Phase 2) does **not** revert a whole file for a pre-existing broken cell
it did not touch, and the fixer gate (`scos_gates.py`) downgrades such a
compile failure from a blocking `CRITICAL` to an advisory `preexisting_syntax`
WARN. You can run the pre-flight standalone (`precompile_check.py --state ...`,
`--dry-run` supported), but you normally do not need to — Phase 0.5 runs it.

**Hard gate (all must be true):**

1. The script exits 0.
2. The printed `PHASE 0.5 SUMMARY` block reports `Files processed` >= 1, **or** the manifest contains no `.py` files (notebook-only workload) — in that case `Files processed: 0` is expected and the phase is still `passed`.
3. `migration_state.json["phases_completed"]["0_5_preprocess"]["status"] == "passed"`.

If exit code is non-zero, do NOT advance to Phase 1. Re-read the error,
fix the underlying issue (most likely: an un-parseable Python file in
`<MIGRATED>/`), and re-run the driver. The driver is idempotent — running
it again on already-rewritten files is a safe no-op.

**Write contract** (the driver records this for you; do not touch it
manually unless overriding):

```json
"phases_completed": {
  "0_5_preprocess": {
    "status": "passed",
    "ran_at": "<ISO-8601 UTC>",
    "files_processed": <int>,
    "files_modified": <int>,
    "total_edits": <int>,
    "recipes_run": ["<recipe_id>", ...]
  }
}
```

Plus a top-level `recipe_edits` block keyed by relative path:

```json
"recipe_edits": {
  "<rel_path>.py": [
    {
      "recipe_id": "<id>",
      "src_line": <int>,
      "output_line_anchor": "<id>:<src_line>:<8-hex>"
    }
  ]
}
```

The analyzer (Phase 1) and fixer (Phase 2) MAY read `recipe_edits` to
recognise recipe-managed regions and avoid re-flagging or undoing them.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 0.5: deterministic pre-processing"`

# Phase 0.6: Standalone SQL Rewrite (MUST RUN)

Runs immediately after Phase 0.5 and before Phase 1, so the analyzer and fixer
see already-rewritten SQL. Standalone `.sql` workloads are otherwise only
*analyzed* (no phase rewrites them); embedded `spark.sql("...")` SQL is handled
by the Phase 0.5 `spark_sql_mechanical_rewrite` recipe. This step is the
standalone-`.sql` counterpart: it deterministically rewrites the SCOS SQL gaps
that have a safe, semantics-preserving syntactic fix (EXPLAIN drops, GROUPING
SETS folding, CACHE/UNCACHE removal, …) via sqlglot and annotates the residual
judgment-heavy gaps — including window-missing-ORDER-BY and multi-column NOT IN,
which are detected but NOT auto-rewritten — with `-- SCOS: TODO -` for the fixer.

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/rewrite_sql_files.py \
  --state <CONVERSION>/migration_state.json
echo "sql_rewrite_exit=$?"
```

The script discovers `.sql` files under `migrated_dir` (excluding Databricks
native-JSON `.sql` notebooks), rewrites in place, prepends a `-- SCOS:` audit
block per file, and records `sql_rewrite_edits` + `phases_completed["0_6_sql_rewrite"]`.
It is idempotent (a file carrying the sentinel is skipped) and leaves
unparseable SQL byte-identical.

**Hard gate (all must be true):**

1. The script exits 0.
2. `migration_state.json["phases_completed"]["0_6_sql_rewrite"]["status"] == "passed"`.

A workload with no `.sql` files still records the phase with `files_processed: 0`
— that is a valid pass, not a skip. Phase 0.6 is **optional only for SQL-free
workloads**: when standalone `.sql` files are present, `validate_migration_state.py`
(Phase 4a) marks a missing/failed `0_6_sql_rewrite` as a hard failure. As a
reliability backstop, `orchestrate_phases.py --phase 2` also runs the standalone
SQL rewrite itself if this phase was not recorded — so even if the coordinator
skips this step, standalone `.sql` files are still rewritten before fixer
dispatch.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 0.6: standalone SQL rewrite"`
