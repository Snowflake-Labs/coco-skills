# SCOS SQL Fix Rules (for the LLM fixer) — PySpark

PySpark-specific application details. The full rule set is in the shared
reference — **read `../../references/sql/sql-fix-rules.md` for all rules.**

## How to apply

- **Embedded SQL** (a `# SCOS-TODO: spark_sql_mechanical_rewrite: …` marker, or
  an `analysis.json` row with `language:"sql"` pointing at a `.py` file): edit
  the SQL string literal in place and leave a `# SCOS:` comment above the
  statement describing the change.
- **Standalone `.sql` files** (a `-- SCOS: TODO -` marker, or an `analysis.json`
  row with `language:"sql"` pointing at a `.sql` file): these are **not** in the
  manifest — take the list from `migration_state.json :: sql_rewrite_edits` and
  the `language:"sql"` rows of `analysis.json`. Edit the `.sql` file in place and
  annotate with the **`--` SQL comment prefix**, not `#`:
  - `-- SCOS: <explanation>` — fix applied
  - `-- SCOS: TODO - <explanation>` — left for manual review
- Preserve the existing `-- SCOS:` audit block Phase 0.6 wrote; append your note.

## Python-side construct gaps

- `CREATE TEMPORARY FUNCTION` → `spark.udf.register('name', python_fn)`.
- `DESCRIBE TABLE` → `spark.catalog.getTable(...)` rather than parsing output by column name.
