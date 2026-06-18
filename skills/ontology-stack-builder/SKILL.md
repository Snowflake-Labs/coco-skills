---
name: ontology-stack-builder
title: Build Ontology Stack
summary: Builds a 5-layer ontology stack on Snowflake from relational tables or an OWL file via a 7-phase gated workflow.
description: >-
  Use when you want to turn a Snowflake schema (or an OWL/RDF/Turtle file) into a full
  ontology-powered analytics layer — knowledge graph tables, abstract entity views,
  ontology metadata tables, semantic views, and a Cortex Agent that routes across
  them. Use when the user mentions building an ontology, schema-to-ontology mapping,
  knowledge graph on Snowflake, KG_NODE / KG_EDGE, or wants graph hierarchy traversal
  (descendants, ancestors, paths) over relational data. Do NOT use for simple
  semantic view creation (use `semantic-view` directly) or standalone agent creation
  (use `cortex-agent` directly). Triggers: build ontology, create ontology stack,
  schema-to-ontology, OWL import, knowledge graph on snowflake, ontology layer
  generation, KG_NODE, KG_EDGE, abstract views, ontology metadata.
tools:
  - snowflake_sql_execute
  - Bash
  - Read
  - Write
  - Edit
  - Grep
  - Glob
prompt: "$ontology-stack-builder Build an ontology stack on MY_DB.MY_SCHEMA using tables TABLE_A, TABLE_B, TABLE_C"
language: en
status: Published
author: Tianxia Jia
type: snowflake
demo-url: https://medium.com/snowflake/ontology-on-snowflake-from-architecture-to-deployment-with-a-cortex-code-skill-197866ce9c9f
---

# Ontology Stack Builder

## Overview

Generate a 5-layer ontology stack on Snowflake from existing tables or an OWL file:

- **L1 Physical** — existing tables, or `KG_NODE` / `KG_EDGE` if KG path
- **L2 Metadata** — ~22 `ONT_*` tables (classes, relations, properties, rules, RBAC)
- **L3 Abstract Views** — `VW_ONT_*` views generated via stored procedure
- **L4 Semantic Views** — base + ontology + metadata models
- **L5 Cortex Agent** — routes intent across all tools

The skill runs a 7-phase gated workflow. Each phase has an explicit stopping point — do not advance without user confirmation.

## Workflow

### Phase 1: Gather inputs

Collect `DATABASE.SCHEMA`, source tables (or OWL path), 3–10 business questions, ontology name. Ask path choice: **KG** (creates `KG_NODE` / `KG_EDGE`) or **direct-table**. Run `SHOW SEMANTIC VIEWS IN SCHEMA {DB}.{SCHEMA}` to detect models the user may want to reuse as the base.

⚠️ STOPPING POINT: Confirm inputs and path choice before introspection.

### Phase 2: Analyze schema

Run `DESCRIBE TABLE` + `SHOW PRIMARY KEYS`, or parse OWL with `scripts/parse_owl.py`. Propose classes (one per entity) and relations (from foreign keys). Write `classes.json` and `relations.json` to `/tmp/ontology_parsed/` using the exact field names: `is_abstract`, `parent_name`, `is_deprecated`, `is_hierarchical`. If an existing semantic view was chosen, enrich proposals with its curated descriptions.

⚠️ STOPPING POINT: Confirm proposed classes and relations.

### Phase 3: Visualize and edit

Launch `scripts/visualize_ontology.py` (Streamlit). User can add / delete / edit classes and relations. Re-read the JSON files after save to pick up edits.

⚠️ STOPPING POINT: Confirm structure before SQL generation.

### Phase 4: Generate and deploy SQL

Run `scripts/generate_ontology_sql.py` to produce numbered SQL files: physical layer (KG only), concrete views (`V_*`), metadata tables (`ONT_*`), abstract views (`VW_ONT_*`), view-generator stored procedure, optional inference engine, optional graph traversal UDFs (`EXPAND_DESCENDANTS_TOOL`, `GET_ANCESTORS_TOOL`, `GET_HIERARCHY_PATH_TOOL`, `GET_DIRECT_CHILDREN_TOOL`).

Cross-check generated `CREATE` statements against counts in `classes.json` / `relations.json` before showing the user. Don't ask for approval on incomplete SQL.

⚠️ STOPPING POINT: Show generated SQL. Wait for approval before executing any `CREATE TABLE / VIEW / PROCEDURE`.

After approval, execute each file via `snowflake_sql_execute`. Verify with `SHOW VIEWS LIKE 'VW_ONT_%'` and `SHOW TABLES LIKE 'ONT_%'`. Build `deployed_objects.json` mapping classes to deployed artifacts. Re-launch the visualizer with `--deployed-objects` so coverage colors render correctly.

⚠️ STOPPING POINT: Confirm deployment before semantic view creation.

### Phase 4.5: Base semantic view

If the user chose an existing semantic view in Phase 1, skip. Otherwise invoke the `semantic-view` skill (FastGen) over the **original source tables** to build `{ONTOLOGY_NAME}_BASE`. Test 2–3 business questions via the skill's audit mode.

⚠️ STOPPING POINT: Confirm base semantic view is functional.

### Phase 5: Ontology semantic views

Ask which ontology-layer models to create: KG (over `V_*`), Ontology (over `VW_ONT_*`), Metadata (over `ONT_*`). For each, invoke the `semantic-view` skill, then audit. Run `cortex semantic-views describe` on each and record metadata for Phase 6.

⚠️ STOPPING POINT: Confirm ontology semantic views deployed and tested.

### Phase 6: Cortex Agent

Run discovery first — do not rely on session memory:

- `cortex semantic-views list --in database {DB} schema {SCHEMA}`
- `cortex semantic-views describe {FQN}` for each
- `SHOW USER FUNCTIONS LIKE '%_TOOL' IN SCHEMA {DB}.{SCHEMA}`

Classify each semantic view by its table references (base / kg / ontology / metadata). Build `TOOL_INVENTORY` from discovered assets only. Invoke the `cortex-agent` skill in create mode and pass the inventory plus discovery metadata — let the skill generate orchestration. Test routing via the skill's debug mode. If routing is wrong, invoke `cortex-agent` in refine mode (do not hand-edit the prompt).

⚠️ STOPPING POINT: Confirm agent routes correctly across all tools.

### Phase 7: End-to-end validation

Verify row counts on `KG_NODE`, `KG_EDGE`, `ONT_*` tables; sample queries against `VW_ONT_*` views; agent returns a non-empty answer for one Phase 1 question.

## Sub-flows

This skill delegates two phases to other skills loaded at runtime:

- `semantic-view` — Phases 4.5 and 5
- `cortex-agent` — Phase 6

## Common Mistakes

- Skipping the deploy gate and executing SQL before user review
- Using `abstract` instead of `is_abstract` in `classes.json`
- Hand-writing agent orchestration instead of delegating to `cortex-agent`
- Re-launching the visualizer in Phase 4 without `--deployed-objects` (everything shows as unmapped)
- Building `TOOL_INVENTORY` from session memory instead of running discovery
- Creating the base semantic view over `VW_ONT_*` views instead of source tables
- Adding a routing block for a tool that is not in `TOOL_INVENTORY`

## Red Flags

Refuse these rationalizations:

- "User said 'do everything at once' — I can skip the gates." No. Run the current phase only.
- "The `semantic-view` skill just finished — I can roll into the next phase without asking." No. Always present the gate.
- "I remember the FQNs from earlier in the session — no need to run discovery in Phase 6." No. Always run `cortex semantic-views list / describe` and `SHOW USER FUNCTIONS`.
- "I'll hand-edit the agent orchestration prompt to fix routing." No. Use `cortex-agent` refine mode.
- "The completeness check is optional if the SQL looks right." No. Cross-check `CREATE` counts against `classes.json` / `relations.json` first.
- "Empty results in audit mode are probably fine." No. Treat as failure and refine.

## Stopping Points

- Phase 1 — confirm inputs and KG vs direct-table path
- Phase 2 — confirm proposed classes and relations
- Phase 3 — confirm structure before SQL generation
- Phase 4a — show generated SQL, wait for approval before executing
- Phase 4b — confirm Snowflake deployment
- Phase 4.5 — confirm base semantic view tested and functional
- Phase 5 — confirm ontology semantic views tested and functional
- Phase 6 — confirm agent routes correctly
- Phase 7 — confirm end-to-end validation

## Output

```
/tmp/generated/
├── 01_physical_layer.sql        (KG path only)
├── 02_concrete_views.sql
├── 03_metadata_tables.sql
├── 04_abstract_views.sql
├── 05_view_generator_sp.sql
├── 06_inference_engine.sql      (optional)
├── 07_graph_traversal_tools.sql (optional)
├── spcs_graph_service.py        (optional)
└── spcs_setup.sql               (optional)
```

Semantic views and the Cortex Agent are deployed directly to Snowflake by the delegated skills.
