---
name: semantic-view-patterns
title: Apply Semantic View Patterns
summary: Tutorials and apply-mode for 25 Snowflake Semantic View modeling patterns spanning joins, metrics, and dimensions.
description: "Use when learning Snowflake Semantic View patterns, teaching SV concepts, applying patterns to existing SVs, or building new SVs with best practices. Triggers: semantic view patterns, sv patterns, walk me through, time intelligence, range join, ASOF join, semi-additive, window metrics, derived metrics, role playing dimensions, accumulating snapshot, sv diagnostics, fan trap, USING clause, NON ADDITIVE BY."
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Read
  - Write
  - Edit
  - Glob
  - Grep
prompt: walk me through time intelligence in semantic views
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Semantic View Patterns

## Overview

Interactive, end-to-end tutorials for 25 Snowflake Semantic View (SV) modeling patterns. Each pattern ships with annotated DDL and YAML, seed data, and live `SEMANTIC_VIEW()` queries. Two modes:

- **Tutorial mode** — deploy a working example into your account, walk through the DDL/YAML, run live queries, then clean up. Triggers: "walk me through", "teach me", "what patterns are available".
- **Apply mode** — read your existing SV (or table list), map the pattern's structural roles to your columns, and generate adapted DDL/YAML. Triggers: "apply X to my SV", "my tables are…", "help me implement".

If ambiguous, ask which mode the user wants.

## Available Patterns

`range_join`, `asof_join`, `multi_path_metrics`, `shared_degenerate_dimension`, `semi_additive_metric`, `window_metrics`, `derived_metrics`, `time_intelligence`, `entity_facts`, `variables`, `multi_fact_table`, `ai_metadata`, `tags`, `introspection`, `fact_as_relationship_key`, `system_explain_semantic_query`, `caller_rights` ⚠️ ACCOUNTADMIN, `standard_sql`, `inline_sv` ⚠️ PrPr, `materialization` ⚠️ PrPr, `scoped_dataset` ⚠️ PrPr, `row_access_policies` ⚠️ ACCOUNTADMIN, `role_playing_dimensions`, `accumulating_snapshot`, `sv_diagnostics`.

Each pattern lives at `<skill_dir>/snippets/<name>/` with `README.md`, `schema.sql`, `seed_data.sql`, `semantic_view.sql`, `semantic_view.yaml`, `queries.sql`.

## Authoring Format

Ask whether to use DDL (`CREATE SEMANTIC VIEW`) or YAML (`SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML`). Skip if the user already specified.

```sql
-- Verify (dry-run):
CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('DB.SCHEMA', $$ <yaml> $$, TRUE);
-- Deploy:
CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('DB.SCHEMA', $$ <yaml> $$);
-- Export:
SELECT SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW('DB.SCHEMA.SV');
```

DDL-only features (no YAML equivalent): `AI_QUESTION_CATEGORIZATION`, `WITH TAG`, `MAX_STALENESS`, `ADD MATERIALIZATION`, `VARIABLES`, `ASOF` joins, inline subqueries in `TABLES`. Apply these post-deploy via `ALTER SEMANTIC VIEW`.

## Tutorial Workflow

1. **Pick the pattern** from the user's request, or list patterns and ask.
2. **Pre-flight**: probe `SHOW DATABASES LIKE 'SNOWFLAKE_LEARNING_DB'`. If present, offer it as default; otherwise ask for `DATABASE.SCHEMA`, role, and warehouse. Track every object you create for cleanup. Access-control snippets use hardcoded DBs (`SV_CALLER_TEST`, `RAP_TEST`) and need ACCOUNTADMIN.
3. **Read** the snippet files for the chosen pattern.
4. **Act 1 — Problem**: synthesize what the pattern solves in 2–3 sentences. Hint: "Ask 'tell me about other approaches' to see how Power BI / Tableau / dbt handle this."
5. **Act 2 — Data Model**: walk through `schema.sql`, deploy schema + seed via `snowflake_sql_execute` (substitute `SNIPPETS.PUBLIC` → `TARGET_DB.TARGET_SCHEMA`), then `SELECT * LIMIT 5` from each table.
6. **Act 3 — SV Pattern**: excerpt and annotate TABLES/RELATIONSHIPS/FACTS/DIMENSIONS/METRICS sections, then deploy.
7. **Act 4 — Live Queries**: run each numbered query in `queries.sql`, narrate the actual output values.
8. **Act 5 — Gotchas**: read `## What Doesn't Work` and present each trap plainly.
9. **Cleanup**: list every object created, offer to drop them via the `-- CLEANUP` block.

## Apply Workflow

1. Match the request to the closest pattern; confirm with the user.
2. Read `README.md` and the chosen format file (DDL or YAML). Skip schema/seed/queries.
3. Get the user's existing SV: pasted text, file path, or `GET_DDL('semantic_view', 'DB.SCHEMA.SV')`. If from scratch, take table descriptions.
4. Show a mapping table (snippet role → user column) and have them fill it in. Ask only for what the pattern requires.
5. Generate adapted output: a diff for existing SVs, a complete definition for new ones. Use the user's exact table/column names. For YAML, include the dry-run + deploy snippet.
6. Flag schema-specific gotchas (composite keys, non-standard date grain).
7. Offer to deploy, run test queries, or layer another pattern.

## Common Mistakes

- **Not confirming mode or format first** — a Tutorial-mode answer to an Apply-mode user wastes both your time. Ask once at the start.
- **Reverting to snippet names in adapted output** — use the user's `FACT_ORDERS.ORDER_DATE`, not the snippet's `FACT_SALES.SALE_MONTH`.
- **Pasting the README verbatim** — synthesize, don't dump.
- **Skipping cleanup** — always list created objects and offer to drop them.
- **Generating YAML for DDL-only features silently** — call out `ASOF`, `VARIABLES`, `WITH TAG`, `MATERIALIZATION` and emit the post-deploy DDL.
- **Cardinality wrong on a relationship** — silently inflates metrics; verify with `SHOW METRICS` and a row-count sanity query.
- **Fan trap from two facts joined through a shared dim without `USING`** — disambiguate with the `USING (relationship)` clause on each metric.
- **Forgetting to switch roles** in access-control snippets before running query blocks.
