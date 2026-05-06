---
name: ontology-stack-builder
title: Build Ontology Stack on Snowflake
summary: Generates the full Ontology-on-Snowflake stack from any relational schema through a 7-phase gated workflow.
description: >-
  Use for ALL requests that mention: ontology, knowledge graph, semantic layer from schema,
  ontology-on-snowflake, abstract views, ontology metadata tables, KG_NODE, KG_EDGE,
  build ontology stack, generate ontology from tables, ontology layer generation,
  map tables to ontology, generate cortex agent config.
  Triggers: build ontology, create ontology stack, schema-to-ontology, OWL import,
  knowledge graph on snowflake, create semantic models from schema.
  Do NOT use for: simple semantic view creation (use semantic-view skill),
  standalone Cortex Agent creation (use cortex-agent skill).
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Bash
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - ask_user_question
  - skill
prompt: "$ontology-stack-builder Build an ontology stack on MY_DB.MY_SCHEMA using tables TABLE_A, TABLE_B, TABLE_C"
language: en
status: Published
author: Tianxia Jia
type: community
demo-url: https://medium.com/snowflake/ontology-on-snowflake-from-architecture-to-deployment-with-a-cortex-code-skill-197866ce9c9f
---

# Ontology Stack Builder

Generate the complete Ontology-on-Snowflake stack from any relational schema and set of business questions.

## When to Use

- You have Snowflake tables and want to build an ontology-powered analytics layer
- You want Cortex Agent to intelligently route questions across semantic models
- You need abstract ontology views that unify entity types across tables
- You have an OWL ontology file to map onto existing Snowflake tables
- You want graph analytics (centrality, community detection, shortest path) on your data

## Architecture Overview

The skill generates a 5-layer stack:

```
Layer 5: Cortex Agent (intent routing across semantic models + graph tools)
Layer 4: Semantic Models (KG concrete, Ontology abstract, Metadata governance)
Layer 3: Generated Abstract Views (VW_ONT_* via stored procedure)
Layer 2: Ontology Metadata (~22 ONT_* tables: classes, properties, rules, roles, etc.)
Layer 1: Physical Storage (existing tables, or KG_NODE/KG_EDGE if KG path)
       + Concrete Views (V_{CLASS} entity views, V_{REL} relationship views)
```

## Workflow Overview

This skill follows a 7-phase gated workflow. Each phase requires user confirmation before proceeding.

```
Phase 1: GATHER INPUTS
  ├── Collect schema, business questions, optional OWL
  ├── Ask: KG path or direct-table path?
  ├── Discover existing semantic views (Step 1b)
  ├── Ask: use existing semantic as base, or create from scratch?
  └── ⚠️ GATE: Inputs confirmed
      │
Phase 2: ANALYZE & RECOMMEND ONTOLOGY
  ├── Introspect schema OR parse OWL
  ├── If existing semantic: enrich proposals with curated metadata
  ├── Propose classes, relations, mappings
  └── ⚠️ GATE: Ontology design confirmed
      │
Phase 3: VISUALIZE, MODIFY & CONFIRM
  ├── Show ontology graph (Streamlit agraph)
  ├── Show coverage matrix
  ├── Sidebar editor: add/delete classes & relations
  ├── Click node → inline edit form (label, parent, abstract)
  ├── Visual diff: gold border = new, orange = modified
  ├── Save changes back to JSON / Undo all
  └── ⚠️ GATE: Visual confirmation
      │
Phase 4: GENERATE & DEPLOY ONTOLOGY LAYER (Layers 1-3)
  ├── Generate physical tables SQL (if KG path)
  ├── Generate concrete entity/relationship views SQL
  ├── Generate metadata tables SQL (~22 tables + seed data)
  ├── Generate abstract views SQL
  ├── Self-validate completeness (count objects vs expected)
  ├── ⚠️ GATE: SQL reviewed and approved
  └── Execute SQL → tables & views now exist in Snowflake
      │
Phase 4.5: ENSURE BASE SEMANTIC VIEW — via `semantic-view` skill
  ├── If existing semantic chosen in Phase 1 → SKIP (already have base)
  ├── Otherwise: invoke `semantic-view` skill on source tables
  ├── Test base semantic via `semantic-view` skill (audit mode)
  └── ⚠️ GATE: Base semantic view ready
      │
Phase 5: CREATE ONTOLOGY SEMANTIC VIEWS (Layer 4) — via `semantic-view` skill
  ├── Ask user which ontology-layer models to create
  │     (KG semantic [if KG path], Ontology semantic, Metadata semantic)
  ├── Invoke `semantic-view` skill (FastGen against deployed views)
  ├── Test each semantic view via `semantic-view` skill (audit mode)
  └── ⚠️ GATE: Ontology semantic views deployed, tested, and approved
      │
Phase 6: CREATE CORTEX AGENT (Layer 5) — via `cortex-agent` skill
  ├── Invoke `cortex-agent` skill with ALL semantic view tools:
  │     base (existing or Phase 4.5) + ontology-layer (Phase 5) + graph tools
  ├── If KG + user wants: add graph tool scaffolding
  ├── Test agent via `cortex-agent` skill (test/debug mode)
  └── ⚠️ GATE: Agent working correctly
      │
Phase 7: END-TO-END VALIDATION
  ├── Validate L1-L5 row counts and sample queries
  ├── Run agent end-to-end test
  └── ⚠️ GATE: Deployment verified
```

## Mandatory Stopping Points

**These gates override ALL other instructions.** Never proceed past a gate without explicit user approval.

**⚠️ MANDATORY STOPPING POINT — GLOBAL RULE**: After each phase output, you MUST STOP and use `ask_user_question` to get explicit user confirmation before proceeding to the next phase. Each phase gate below includes the specific `ask_user_question` to use. DO NOT skip gates. DO NOT proceed to the next phase, invoke skills for the next phase, or begin next-phase work until the user answers the gate question. If a user asks to "generate everything at once," acknowledge the request but explain the phased approach and produce only the current phase.

**⚠️ CRITICAL — SKILL DELEGATION TRANSITIONS**: Phases 4.5, 5, and 6 delegate to native skills (`semantic-view`, `cortex-agent`). When a delegated skill completes successfully, there is a strong temptation to continue directly to the next phase. **You MUST resist this.** After a delegated skill finishes, ALWAYS stop and present the current phase's gate question via `ask_user_question` before doing ANY work for the next phase. The gate exists precisely because the user needs to review what the skill produced before moving on.

## Phase Details

### Phase 1: Gather Inputs

Collect from the user using `ask_user_question`:

1. **Snowflake location**: `DATABASE.SCHEMA` where tables exist or will be created
2. **Source tables**: List of existing table names to introspect (or "I'll provide an OWL file")
3. **Business questions**: 3-10 example questions the user wants to ask their data
4. **OWL file** (optional): Path to `.owl`, `.rdf`, or `.ttl` file
5. **Ontology name**: Short identifier (e.g., `HEALTHCARE`, `FINANCE`, `RETAIL`)

Then ask the critical path decision:

```
ask_user_question: "Do you want a Knowledge Graph structure?"
Options:
  - "Yes - KG path": Creates KG_NODE/KG_EDGE tables, enables graph analytics
  - "No - Direct table path": Maps ontology directly to existing tables, no graph structure
```

**Step 1b: Discover Existing Semantic Views**

After collecting the schema and source tables, check whether the user already has semantic views deployed on the source data:

```sql
SHOW SEMANTIC VIEWS IN SCHEMA {DATABASE}.{SCHEMA};
```

Also use the `semantic-view` skill (discover/describe mode) to understand what tables each existing semantic view covers:
```
cortex semantic-views discover
cortex semantic-views describe {DATABASE}.{SCHEMA}.{SEMANTIC_VIEW_NAME}
```

Then prompt the user:

```
ask_user_question: "I found N existing semantic view(s) in {DATABASE}.{SCHEMA}: [list names].
  Do any of these already cover your source tables?"
Options:
  - "Yes — use my existing semantic view as the base": Reuses the existing semantic view as the
    base data layer tool in the final agent. No duplicate base semantic will be created.
  - "No — create everything from scratch": A new base semantic view will be created over the
    source tables (Phase 4.5) before building the ontology layer.
```

If no semantic views are found, skip the question and default to "create from scratch."

If user selects "Yes," record:
- `existing_base_semantic`: The FQN of the existing semantic view (e.g., `DB.SCHEMA.MY_MODEL`)
- `existing_semantic_tables`: The list of tables covered by the existing semantic (from describe output)

This information is used in:
- **Phase 2**: The existing semantic's curated column descriptions and relationships enrich ontology proposals
- **Phase 4.5**: Skipped entirely if an existing base semantic is available
- **Phase 6**: The existing semantic becomes the `base_query_tool` in the agent

Store all inputs as session variables. If user provides only some inputs, ask for the rest.

**Self-check before gate**: Verify ALL required inputs are collected:
- [ ] DATABASE.SCHEMA specified
- [ ] Source tables listed OR OWL file path provided
- [ ] At least 3 business questions captured
- [ ] Ontology name defined
- [ ] KG vs direct-table path chosen
- [ ] Checked for existing semantic views (`SHOW SEMANTIC VIEWS`)
- [ ] User confirmed starting point (existing semantic vs. tables only)

If any are missing, ask the user for them. Do NOT present the gate with incomplete inputs.

**⚠️ MANDATORY STOPPING POINT**: Present a summary of all collected inputs and the chosen path. You MUST use `ask_user_question`:

```
ask_user_question: "Here are the collected inputs. Is everything correct?"
Options:
  - "Yes, proceed to Phase 2"
  - "No, I need to change something"
```

### Phase 2: Analyze & Recommend Ontology

**If OWL file provided:**

Run the OWL parser:
```bash
uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/parse_owl.py \
  --owl-file "<owl_path>" \
  --output-dir "/tmp/ontology_parsed"
```

Then map parsed classes to existing tables using `DESCRIBE TABLE` on each source table.

**If no OWL file (schema-first discovery):**

Run schema introspection:
```bash
uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/introspect_schema.py \
  --database "<DATABASE>" \
  --schema "<SCHEMA>" \
  --tables "<TABLE1>,<TABLE2>,..." \
  --questions "<Q1>|<Q2>|..." \
  --output-dir "/tmp/ontology_parsed"
```

This script:
1. Calls `DESCRIBE TABLE` and `SHOW PRIMARY KEYS` for each table via Snowflake SQL
2. Analyzes foreign key relationships and column patterns
3. Proposes ontology classes (one per table or entity type)
4. Proposes relations from FK relationships and naming conventions
5. Outputs `classes.json`, `relations.json`, `mappings.json`

**If existing semantic view available (from Step 1b):**

When the user has an existing semantic view, use the `semantic-view` skill (describe mode) to extract its curated metadata and enrich the ontology proposals:

```
cortex semantic-views describe {DATABASE}.{SCHEMA}.{EXISTING_SEMANTIC_VIEW}
```

The existing semantic model provides:
- **Column descriptions** → Use as class property descriptions instead of auto-generated ones
- **Relationships** → Map directly to ontology relation proposals (validates FK-based proposals)
- **Dimensions/Facts/Metrics** → Inform which columns are important for each class
- **Table references** → Confirm which source tables are in scope

After running schema introspection (or OWL parsing), merge the existing semantic metadata:
1. For each proposed class, check if the semantic model has a matching table — if so, import its column descriptions
2. For each semantic model relationship, verify a corresponding ontology relation exists — if not, add one
3. Use the semantic model's metric definitions to annotate key properties in `classes.json`

This enrichment is additive — the introspection still runs, but produces better-documented results when a semantic model already describes the schema.

**`classes.json` schema** — each entry MUST use these exact keys:
```json
[
  {
    "name": "ClassName",
    "label": "Display Name",
    "description": "What this class represents",
    "parent_name": null,
    "is_abstract": false,
    "is_deprecated": false,
    "namespace": "DB.SCHEMA",
    "uri": "urn:db:schema:ClassName",
    "_source_table": "TABLE_NAME",
    "_id_column": "ID_COL",
    "_name_column": "NAME_COL"
  }
]
```

**`relations.json` schema** — each entry MUST use these exact keys:
```json
[
  {
    "name": "relation_name",
    "label": "Relation Name",
    "description": "What this relation represents",
    "domain_class": "SourceClass",
    "domain_classes": ["SourceClass"],
    "range_class": "TargetClass",
    "range_classes": ["TargetClass"],
    "is_transitive": false,
    "is_symmetric": false,
    "is_functional": true,
    "is_abstract": false,
    "is_hierarchical": false,
    "parent_name": null,
    "inverse_name": null,
    "cardinality": "N:1",
    "uri": "urn:rel:relation_name",
    "_source_table": "TABLE_NAME",
    "_src_column": "SRC_COL",
    "_dst_column": "DST_COL"
  }
]
```

**IMPORTANT**: When editing `classes.json` or `relations.json` directly (e.g., adding a class via prompt), use the exact field names above. In particular: `is_abstract` (not `abstract`), `parent_name` (not `parent`), `is_deprecated` (not `deprecated`), `is_hierarchical` (not `hierarchical`). For abstract relations, set `is_abstract: true` and leave `_source_table`/`_src_column`/`_dst_column` as `null`. Concrete child relations reference their abstract parent via `parent_name`.

Present the proposed ontology to the user as a table:

| Class Name | Source Table | Type | ID Column | Name Column | Parent Class |
|------------|-------------|------|-----------|-------------|--------------|

And proposed relations:

| Relation | Domain Class | Range Class | Source | Cardinality |
|----------|-------------|-------------|--------|-------------|

**Self-check before gate**: Verify the ontology design is complete:
- [ ] Every source table maps to at least one class
- [ ] Every class has a `_source_table`, `_id_column`, and `_name_column` (unless abstract)
- [ ] Every foreign key relationship has a corresponding relation entry
- [ ] No duplicate class names or relation names
- [ ] `classes.json` and `relations.json` use exact field names from the schema above
- [ ] At least one concrete class exists

If any check fails, fix the issue before presenting to the user. Do NOT ask the user to approve an incomplete ontology.

**⚠️ MANDATORY STOPPING POINT**: You MUST use `ask_user_question`:

```
ask_user_question: "Please review the proposed ontology above. Want to proceed, or make changes?"
Options:
  - "Looks good, proceed to Phase 3"
  - "I want to add/remove/change classes or relations"
```

### Phase 3: Visualize, Modify & Confirm

Launch the visualization & editing app:
```bash
uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/visualize_ontology.py -- \
  --classes-json "/tmp/ontology_parsed/classes.json" \
  --relations-json "/tmp/ontology_parsed/relations.json" \
  --port 8501
```

This shows:
- **Hierarchy** tab with two expandable sections: **Class Hierarchy** (interactive/text toggle, search, depth slider) and **Relation Hierarchy** (interactive/text toggle, abstract/concrete grouping)
- Ontology graph with coverage coloring and **Layer toggle** (All / Concrete / Abstract)
- Coverage tab showing the ontology design structure (implementation mapping becomes available after Phase 4 deployment)

The **Layer toggle** on the Ontology Graph tab controls visibility:
- **All**: shows both abstract and concrete classes/relations (abstract items rendered distinctly)
- **Concrete**: hides abstract classes and abstract relations
- **Abstract**: shows only abstract classes and abstract relations (the schema blueprint)

The sidebar **Ontology Editor** allows the user to modify the ontology before generation:
- **Add Class**: name, label, description, parent (dropdown), abstract toggle
- **Add Relation**: name, source/target class dropdowns, cardinality, description
- **Delete Class**: re-parents children to root, removes involved relations
- **Delete Relation**: removes the selected relation
- **Save Changes**: writes modified `classes.json` and `relations.json` back to disk
- **Undo All**: resets to the original loaded state

When a node is clicked in the Ontology Graph tab, an **Edit Class** form appears below the
detail panel with label, description, parent, and abstract fields.

**Visual diff markers** show what changed this session:
- Gold border (thick) = newly added class or relation
- Orange border (thick) = modified class
- Tooltips show NEW / MODIFIED tags

**Edge styling** distinguishes relation types:
- Solid blue = subClassOf (class hierarchy)
- Dashed purple = concrete relation
- Solid deep purple (thick) = abstract relation
- Dotted light purple = specializes (child relation → parent relation)

If Streamlit is not available or user prefers text, render an ASCII tree instead:
```
Entity
├── Person
│   ├── Employee
│   └── Customer
├── Organization
│   ├── Department
│   └── Supplier
└── Event
    ├── Order
    └── Meeting
```

**Self-check before gate**: If user made edits in the visualizer, verify:
- [ ] Modified `classes.json` and `relations.json` were saved (check file timestamps or diff)
- [ ] Any added classes still follow the schema (all required fields present)
- [ ] Any added relations reference valid domain/range classes that exist in `classes.json`
- [ ] No orphaned relations pointing to deleted classes

If the visualizer was used, re-read the JSON files to pick up user edits.

**⚠️ MANDATORY STOPPING POINT**: You MUST use `ask_user_question`:

```
ask_user_question: "The visualizer is running. Please review the ontology structure. Ready to proceed to SQL generation (Phase 4)?"
Options:
  - "Yes, proceed to Phase 4"
  - "No, I need to make changes first"
```

### Phase 4: Generate & Deploy Ontology Layer (Layers 1-3)

**If KG path:**
Generate `01_physical_layer.sql`:
- `CREATE TABLE KG_NODE` with NODE_ID, NODE_TYPE, NAME, PROPS (VARIANT), TS_INGESTED
- `CREATE TABLE KG_EDGE` with EDGE_ID, SRC_ID, DST_ID, EDGE_TYPE, WEIGHT, PROPS, EFFECTIVE_START/END
- Both clustered by type columns
- INSERT statements to load data from source tables into KG_NODE/KG_EDGE

Generate `02_concrete_views.sql`:
- Per-class entity views: `V_{CLASS}` — typed projections from KG_NODE using `PROPS:field::TYPE`
- Per-relation relationship views: `V_{REL}` — typed projections from KG_EDGE with SRC_ID, DST_ID, WEIGHT, EFFECTIVE_START/END
- Direct-table path: thin SELECT wrappers over source tables

**For both paths:**
Generate `03_metadata_tables.sql` (~22 tables):
- Core: ONT_ONTOLOGY, ONT_CLASS, ONT_RELATION_DEF, ONT_CLASS_MAP, ONT_REL_MAP
- Source mappings: ONT_OBJECT_SOURCE, ONT_LINK_SOURCE
- Properties: ONT_SHARED_PROPERTY, ONT_PROPERTY, ONT_DERIVED_PROPERTY
- Interfaces: ONT_INTERFACE, ONT_INTERFACE_PROPERTY, ONT_INTERFACE_IMPL
- Inference: ONT_RULE, REL_EDGE_INFERRED
- Data quality: ONT_CONSTRAINT_VIOLATION
- Actions: ACT_TYPE, ACT_DEF
- Functions: ONT_FUNCTION, ONT_FUNCTION_BINDING
- Views: OBJ_VIEW_DEF, OBJ_VIEW_FIELD
- RBAC: ONT_ROLE, ONT_ROLE_BINDING, ONT_PERMISSION
- Auto-populated INSERT statements derived from class/relation metadata

Generate `04_abstract_views.sql`:
- Per-class views: `VW_ONT_{CLASS_NAME}` — UNION ALL of all source tables for that class
- Hierarchy views: `VW_ONT_SUBCLASS_OF`, `VW_DESCENDANTS`, `VW_ANCESTORS`
- Unified entity view: `VW_ONT_ALL_ENTITIES`
- Stats view: `VW_ONT_HIERARCHY_STATS`
- Resolved relationships view: `REL_RESOLVED` (joins edges with node names)

Generate `05_view_generator_sp.sql`:
- `SP_GENERATE_ONTOLOGY_VIEWS()` stored procedure that reads ONT_CLASS_MAP and regenerates views dynamically

**Optional — KG path only:**
Ask the user:
```
ask_user_question: "Generate inference engine stored procedures?"
Options:
  - "Yes": Generates SP_INFER_TRANSITIVE, SP_INFER_INVERSE, SP_RUN_ONTOLOGY_INFERENCE, 
    SP_CHECK_CARDINALITY_SINGLE, SP_CHECK_REFERENTIAL — enables rule-based inference and data quality checks
  - "No": Skip inference engine (can be added later)
```

If yes, generate `06_inference_engine.sql`:
- `SP_INFER_TRANSITIVE(TARGET_REL, RULE_ID)` — recursive transitive closure (depth ≤ 5)
- `SP_INFER_INVERSE(RULE_ID)` — materialise inverse relationships from ONT_RELATION_DEF
- `SP_RUN_ONTOLOGY_INFERENCE()` — master runner for all enabled ONT_RULE entries
- `SP_CHECK_CARDINALITY_SINGLE(REL, CHECK_NAME)` — validate 1:1/N:1 constraints
- `SP_CHECK_REFERENTIAL(REL, CHECK_NAME)` — validate edge endpoint integrity
- Results written to `REL_EDGE_INFERRED` and `ONT_CONSTRAINT_VIOLATION`

**KG path only**: Ask the user about SQL graph traversal tools:
```
ask_user_question: "Generate SQL graph traversal tools for the agent?"
Options:
  - "Yes": Generates 4 SQL UDF tools (EXPAND_DESCENDANTS_TOOL, GET_ANCESTORS_TOOL,
    GET_HIERARCHY_PATH_TOOL, GET_DIRECT_CHILDREN_TOOL) — zero infrastructure, pure SQL
    against KG_NODE/KG_EDGE. These are registered as generic tools in the Cortex Agent.
  - "No": Skip graph traversal tools (agent uses only semantic view tools)
```

If yes, generate `07_graph_traversal_tools.sql`:
- `EXPAND_DESCENDANTS_TOOL(ROOT_CONCEPT)` — recursive downward traversal, returns all descendants with depth and path
- `GET_ANCESTORS_TOOL(CONCEPT)` — recursive upward traversal, returns all ancestors with shortest depth
- `GET_HIERARCHY_PATH_TOOL(START_CONCEPT, END_CONCEPT)` — finds path between two concepts via subClassOf edges
- `GET_DIRECT_CHILDREN_TOOL(PARENT_CONCEPT)` — single-hop children lookup

These are complementary to SPCS graph tools (Phase 6b). SQL UDF tools handle hierarchy traversal with zero infrastructure. SPCS tools handle advanced graph algorithms (centrality, community detection) but require a container service.

Use the generator script:
```bash
uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/generate_ontology_sql.py \
  --classes-json "/tmp/ontology_parsed/classes.json" \
  --relations-json "/tmp/ontology_parsed/relations.json" \
  --mappings-json "/tmp/ontology_parsed/mappings.json" \
  --database "<DATABASE>" \
  --schema "<SCHEMA>" \
  --ontology-name "<NAME>" \
  --kg-path <true|false> \
  --include-inference <true|false> \
  --include-graph-tools <true|false> \
  --output-dir "/tmp/generated"
```

Present all generated SQL files to the user for review.

**Step 4a-verify: Completeness check (MANDATORY before asking for approval)**

Before presenting the gate, self-validate that ALL expected objects are present in the generated SQL:

| File | Expected objects |
|------|-----------------|
| `01_physical_layer.sql` (KG only) | `KG_NODE`, `KG_EDGE` tables + INSERT statements |
| `02_concrete_views.sql` | One `V_{CLASS}` view per concrete class, one `V_{REL}` view per relation |
| `03_metadata_tables.sql` | All ~22 `ONT_*` tables + INSERT seed data |
| `04_abstract_views.sql` | One `VW_ONT_{CLASS}` per class, plus `VW_ONT_SUBCLASS_OF`, `VW_DESCENDANTS`, `VW_ANCESTORS`, `VW_ONT_ALL_ENTITIES`, `VW_ONT_HIERARCHY_STATS`, `REL_RESOLVED` |
| `05_view_generator_sp.sql` | `SP_GENERATE_ONTOLOGY_VIEWS` stored procedure |
| `06_inference_engine.sql` (if selected) | `SP_INFER_TRANSITIVE`, `SP_INFER_INVERSE`, `SP_RUN_ONTOLOGY_INFERENCE`, `SP_CHECK_CARDINALITY_SINGLE`, `SP_CHECK_REFERENTIAL` |
| `07_graph_traversal_tools.sql` (if selected) | `EXPAND_DESCENDANTS_TOOL`, `GET_ANCESTORS_TOOL`, `GET_HIERARCHY_PATH_TOOL`, `GET_DIRECT_CHILDREN_TOOL` |

Cross-check: count the CREATE VIEW/TABLE/PROCEDURE statements in each file against the expected counts derived from `classes.json` and `relations.json`. If anything is missing, fix the generation before presenting to the user. Do NOT ask the user to approve incomplete SQL.

**⚠️ MANDATORY STOPPING POINT — DO NOT EXECUTE SQL**: Present the completeness summary and all generated SQL files. You MUST use `ask_user_question` to get explicit approval before executing any SQL:

```
ask_user_question: "Phase 4 SQL generation complete. I've generated the SQL files listed above. Ready to deploy to Snowflake?"
Options:
  - "Yes, deploy all SQL"
  - "No, I need to review/change something first"
```

DO NOT execute any SQL statements, DO NOT proceed to Step 4b, until the user explicitly approves via the question above.

**Step 4b: Deploy SQL artifacts (Layers 1-3)**

After approval, execute all SQL scripts in order so that tables and views exist for Phases 5-6:

1. **Physical layer** (if KG path): Execute `01_physical_layer.sql`
2. **Concrete views**: Execute `02_concrete_views.sql` (V_{CLASS} entity views, V_{REL} relationship views)
3. **Metadata tables**: Execute `03_metadata_tables.sql` (~22 tables + auto-populated seed data)
4. **Abstract views**: Execute `04_abstract_views.sql`
5. **View generator SP**: Execute `05_view_generator_sp.sql`
6. **Inference engine** (if selected): Execute `06_inference_engine.sql`
7. **SPCS graph service** (if selected): Execute `spcs_setup.sql`
8. **Graph traversal tools** (if selected): Execute `07_graph_traversal_tools.sql`

Quick-validate that key objects exist:
```sql
SHOW VIEWS LIKE 'VW_ONT_%' IN SCHEMA {DATABASE}.{SCHEMA};
SHOW TABLES LIKE 'ONT_%' IN SCHEMA {DATABASE}.{SCHEMA};
-- If graph traversal tools selected:
SHOW USER FUNCTIONS LIKE '%_TOOL' IN SCHEMA {DATABASE}.{SCHEMA};
```

Present a deployment summary to the user:
- Number of tables created (KG tables + ONT_* metadata tables)
- Number of views created (V_* concrete views + VW_ONT_* abstract views)
- Number of stored procedures created
- Number of UDFs created (if graph traversal tools selected)
- Any warnings or errors encountered during execution

**⚠️ MANDATORY STOPPING POINT — DO NOT PROCEED TO PHASE 5**

Before you can present the gate question, you MUST complete ALL of the following steps in order. Do NOT skip any step. Do NOT present the gate question until all 3 steps are done.

**Required Step A: Generate deployed-objects manifest**

Build a `deployed_objects.json` manifest from the SHOW commands output. The manifest maps each ontology concept to its generated Snowflake artifact(s), enabling the visualizer to show the "Original Design → Snowflake Implementation" mapping.

Write the manifest to `/tmp/ontology_parsed/deployed_objects.json`:
```json
{
  "source": "<original input — e.g. 'healthcare_ontology.owl' or 'User-described ontology'>",
  "database": "{DATABASE}",
  "schema": "{SCHEMA}",
  "views": ["V_DOCTOR", "V_PATIENT", "VW_ONT_PERSON", "VW_ONT_ALL_ENTITIES", ...],
  "tables": ["KG_NODE", "KG_EDGE", "ONT_CLASS_DEF", "ONT_RELATION_DEF", ...],
  "procedures": ["SP_GENERATE_ONTOLOGY_VIEWS", ...],
  "udfs": ["EXPAND_DESCENDANTS_TOOL", ...],
  "class_to_objects": {
    "Doctor": {"views": ["V_DOCTOR", "VW_ONT_DOCTOR"], "metadata_row": "ONT_CLASS_DEF"},
    "Patient": {"views": ["V_PATIENT", "VW_ONT_PATIENT"], "metadata_row": "ONT_CLASS_DEF"},
    "Person": {"views": ["VW_ONT_PERSON"], "metadata_row": "ONT_CLASS_DEF"}
  },
  "relation_to_objects": {
    "treats": {"view": "V_TREATS", "metadata_row": "ONT_RELATION_DEF"},
    "subClassOf": {"view": "VW_ONT_SUBCLASS_OF", "metadata_row": "ONT_RELATION_DEF"}
  }
}
```

Build the `class_to_objects` mapping by iterating `classes.json`:
- For each concrete class: look for `V_{CLASS_NAME}` in the views list → add to views array
- For each class (abstract or concrete): look for `VW_ONT_{CLASS_NAME}` in the views list → add to views array
- Every class gets `"metadata_row": "ONT_CLASS_DEF"` since all classes are seeded into that table

Build the `relation_to_objects` mapping by iterating `relations.json`:
- For each relation: look for `V_{RELATION_NAME}` in the views list → set as view
- Every relation gets `"metadata_row": "ONT_RELATION_DEF"`

**Required Step B: Re-launch the visualizer with coverage mapping**

Stop the Phase 3 visualizer if still running, then re-launch with the `--deployed-objects` flag:
```bash
uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/visualize_ontology.py -- \
  --classes-json "/tmp/ontology_parsed/classes.json" \
  --relations-json "/tmp/ontology_parsed/relations.json" \
  --deployed-objects "/tmp/ontology_parsed/deployed_objects.json" \
  --port 8501
```

Without re-launching, the graph will show all nodes as unmapped (red). After re-launch with `--deployed-objects`, nodes will be colored by implementation status:
- **Coverage tab**: "Original Design → Snowflake Implementation" with progress bar, 3-column breakdown (Mapped / Covered by Ancestor / Unmapped), and full artifact inventory
- **Graph tab**: Nodes colored green (mapped), blue (covered by ancestor), red (unmapped), gray (abstract)
- **Node detail**: Click any node to see its deployed Snowflake objects

**Required Step C: Verify the visualizer shows coverage (NOT all red/unmapped)**

Open the visualizer and confirm that concrete classes show as green (mapped) or blue (covered), NOT red (unmapped). If everything is still red, the manifest was not loaded — check the `--deployed-objects` path and re-launch.

**Only after Steps A, B, C are done**, present the gate question using `ask_user_question`:

```
ask_user_question: "Phase 4 deployment complete. All Layer 1-3 objects are deployed. The visualizer is showing the coverage mapping. Ready to proceed to Phase 4.5 (Base Semantic View)?"
Options:
  - "Yes, proceed to Phase 4.5"
  - "No, I need to fix something first"
```

DO NOT invoke the `semantic-view` skill, DO NOT start Phase 4.5 or Phase 5, and DO NOT continue until the user explicitly confirms via the question above.

### Phase 4.5: Ensure Base Semantic View — via `semantic-view` skill

The final delivery always includes a **base semantic view** that covers the source tables directly (for concrete data queries). This phase ensures one exists — either by reusing an existing semantic view or creating a new one.

**If existing semantic view selected in Phase 1 (Step 1b):**

Skip this phase entirely — including the Phase 4.5 gate below. The user's existing semantic view becomes the base. Record its FQN:
```
base_semantic_view = "{DATABASE}.{SCHEMA}.{EXISTING_SEMANTIC_VIEW_NAME}"
```

Proceed directly to Phase 5 (do NOT present the Phase 4.5 gate question).

**If no existing semantic view (create from scratch):**

Invoke the native **`semantic-view` skill** (creation mode) to build a base semantic view over the **original source tables** (not the VW_ONT_* views — those are covered in Phase 5):

```
skill: semantic-view

Context to provide when the skill asks:
  - Semantic view name: {ONTOLOGY_NAME}_BASE
  - Target database: {DATABASE}
  - Target schema: {SCHEMA}
  - Table references: The original source tables from Phase 1
    (e.g., COMPANIES_ENRICHED, SECURITIES, EXCHANGES, SECTORS, etc.)
  - SQL queries: Convert the business questions from Phase 1 into SELECT statements
    against the source tables (these become VQRs in the semantic model)
  - Business context: "Base data layer semantic view for direct queries against source tables.
    Use for specific entity lookups, aggregations, filtering by attributes, and concrete
    data questions that don't require ontology reasoning."
```

The `semantic-view` skill will:
1. Call FastGen to auto-generate the semantic model YAML from the source table metadata
2. Validate the YAML via `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(..., TRUE)`
3. Present the generated model for review
4. Deploy the semantic view

**Step 4.5a: Test the base semantic view**

Invoke the `semantic-view` skill in **audit** mode to validate:
```
skill: semantic-view

Context: Audit the semantic view {DATABASE}.{SCHEMA}.{ONTOLOGY_NAME}_BASE
  - Run 2-3 of the business questions from Phase 1 against the base semantic view
  - Verify the generated SQL executes without errors
  - Verify the results contain expected data (non-empty, reasonable values)
  - If any query fails, use the skill's refine workflow to fix the semantic model
```

Record the base semantic view FQN:
```
base_semantic_view = "{DATABASE}.{SCHEMA}.{ONTOLOGY_NAME}_BASE"
```

**Step 4.5b: Record describe metadata for Phase 6**

Run `cortex semantic-views describe` on the base semantic view and record the output. This metadata feeds into Phase 6's tool descriptions for the agent:

```
cortex semantic-views describe {DATABASE}.{SCHEMA}.{ONTOLOGY_NAME}_BASE
```

Record: description, table count, dimension/fact/metric counts, and key dimension names. This enables Phase 6 to generate rich, data-aware tool descriptions without relying on session memory.

**Self-check before gate**: Verify the base semantic view is ready:
- [ ] Base semantic view exists: `SHOW SEMANTIC VIEWS LIKE '{ONTOLOGY_NAME}_BASE' IN SCHEMA {DATABASE}.{SCHEMA}`
  (or existing semantic view confirmed in Phase 1)
- [ ] At least 2 business questions returned non-empty results via audit
- [ ] `base_semantic_view` FQN is recorded for use in Phase 6

**⚠️ MANDATORY STOPPING POINT — DO NOT PROCEED TO PHASE 5**: When the `semantic-view` skill finishes (or the existing semantic is confirmed), you MUST STOP HERE. Do NOT begin Phase 5, do NOT describe VW_ONT_* views, do NOT start building ontology-layer semantic views. You MUST use `ask_user_question`:

```
ask_user_question: "Base semantic view is ready ({base_semantic_view}). Ready to proceed to Phase 5 (Ontology Semantic Views)?"
Options:
  - "Yes, proceed to Phase 5"
  - "No, I need to fix the base semantic view"
```

**STOP. Wait for the user's answer. Do not read ahead. Do not begin Phase 5 until the user selects an option above.**

### Phase 5: Create Ontology Semantic Views (Layer 4) — via `semantic-view` skill

> **GATE CHECK**: You should only be here if the user explicitly approved the Phase 4.5 gate above. If you arrived here without asking the user, STOP and go back to present the Phase 4.5 gate question.

> **Note**: The **base semantic view** (covering source tables directly) was already created or identified in Phase 4.5. This phase creates **ontology-layer** semantic views over the objects deployed in Phase 4 (VW_ONT_*, V_*, ONT_* tables).

Ask the user which **ontology-layer** semantic views to create:

```
ask_user_question: "Which ontology-layer semantic views should I create? (The base semantic view over source tables is already ready from Phase 4.5.)"
multiSelect: true
Options:
  - "KG Semantic View": Concrete entity views (V_{CLASS}) and relationship views (V_{REL}) for fast direct queries
    [Only available if KG path chosen]
  - "Ontology Semantic View": Abstract views (VW_ONT_*) for cross-type reasoning
  - "Metadata & Governance Semantic View": All ~22 ONT_* introspection tables for governance and data quality
```

**For each selected semantic view**, invoke the native **`semantic-view` skill** (creation mode) to generate, validate, and deploy the semantic view. The skill uses Snowflake's FastGen system function which auto-discovers columns, infers primary keys, generates dimensions/measures/metrics/relationships, and creates verified queries.

**Step 5a: Determine base tables for each model**

Gather the base tables from the ontology objects deployed in Phase 4:

- **KG Semantic View**: List all `V_{CLASS}` entity views and `V_{REL}` relationship views:
  ```sql
  SHOW VIEWS LIKE 'V_%' IN SCHEMA {DATABASE}.{SCHEMA};
  ```
- **Ontology Semantic View**: List all `VW_ONT_%` abstract views:
  ```sql
  SHOW VIEWS LIKE 'VW_ONT_%' IN SCHEMA {DATABASE}.{SCHEMA};
  ```
- **Metadata Semantic View**: List all `ONT_%` metadata tables:
  ```sql
  SHOW TABLES LIKE 'ONT_%' IN SCHEMA {DATABASE}.{SCHEMA};
  ```

**Step 5b: Invoke the `semantic-view` skill for each selected model**

For each selected model, invoke the skill with this context:

```
skill: semantic-view

Context to provide when the skill asks:
  - Semantic view name: {ONTOLOGY_NAME}_KG_MODEL (or _ONTOLOGY_MODEL or _METADATA_MODEL)
  - Target database: {DATABASE}
  - Target schema: {SCHEMA}
  - Table references: The base tables gathered in Step 5a
  - SQL queries: Convert the business questions from Phase 1 into SELECT statements
    against the base tables (these become VQRs in the semantic model)
  - Business context: Describe the model's purpose and intent-routing role
```

The `semantic-view` skill will:
1. Call FastGen to auto-generate the semantic model YAML from the actual Snowflake metadata
2. Validate the YAML via `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(..., TRUE)`
3. Present the generated model for review
4. Offer options: audit, deploy, or refine

**Step 5c: Deploy each semantic view**

Use the `semantic-view` skill's upload workflow to deploy each approved semantic view to Snowflake:
```sql
-- The skill handles this via upload_semantic_view_yaml.py, which runs:
-- CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('{DATABASE}.{SCHEMA}', $${yaml}$$, FALSE);
```

Verify deployment:
```sql
SHOW SEMANTIC VIEWS IN SCHEMA {DATABASE}.{SCHEMA};
```

**Step 5d: Test each semantic view**

For each deployed semantic view, invoke the `semantic-view` skill in **audit/test** mode to validate it returns correct results:

```
skill: semantic-view

Context: Audit the semantic view {DATABASE}.{SCHEMA}.{SEMANTIC_VIEW_NAME}
  - Run 2-3 of the business questions from Phase 1 against each semantic view
  - Verify the generated SQL executes without errors
  - Verify the results contain expected data (non-empty, reasonable values)
  - If any query fails, use the skill's refine workflow to fix the semantic model
```

The `semantic-view` skill will test queries via Cortex Analyst, show generated SQL and results, and flag any issues.

**Step 5e: Record describe metadata for Phase 6**

For EACH deployed ontology-layer semantic view, run `cortex semantic-views describe` and record the output. This metadata feeds into Phase 6's tool descriptions for the agent:

```
FOR EACH deployed semantic view from Step 5b:
  cortex semantic-views describe {DATABASE}.{SCHEMA}.{SEMANTIC_VIEW_NAME}
```

Record for each: description, table count, dimension/fact/metric counts, and key dimension names. This enables Phase 6 to generate rich, data-aware tool descriptions without relying on session memory of what was created in this phase.

**Self-check before gate**: Verify all semantic views are complete:
- [ ] `SHOW SEMANTIC VIEWS IN SCHEMA {DATABASE}.{SCHEMA}` returns one row per selected model
- [ ] Each semantic view name matches expected naming ({ONTOLOGY_NAME}_KG_MODEL, _ONTOLOGY_MODEL, _METADATA_MODEL)
- [ ] Step 5d tests passed — at least 2 business questions per view returned non-empty results
- [ ] No unresolved validation errors from the `semantic-view` skill

If any semantic view is missing or tests failed, fix before presenting the gate.

**⚠️ MANDATORY STOPPING POINT — DO NOT PROCEED TO PHASE 6**: When the last `semantic-view` skill invocation finishes, you MUST STOP HERE. Do NOT invoke the `cortex-agent` skill, do NOT begin agent creation, do NOT start discovering deployed assets. You MUST use `ask_user_question`:

```
ask_user_question: "All semantic views are deployed. Please review and test them. Ready to proceed to Phase 6 (Agent creation)?"
Options:
  - "Yes, proceed to Phase 6"
  - "No, I need to fix a semantic view"
```

**STOP. Wait for the user's answer. Do not read ahead. Do not begin Phase 6 until the user selects an option above.**

### Phase 6: Create Cortex Agent (Layer 5) — via `cortex-agent` skill

> **GATE CHECK**: You should only be here if the user explicitly approved the Phase 5 gate above. If you arrived here without asking the user, STOP and go back to present the Phase 5 gate question.

**Step 6-pre: Discover deployed assets**

Before building anything, dynamically discover all assets from prior phases using Snowflake discovery commands. This makes Phase 6 resilient to session resume — it works even if earlier phases ran in a different session.

**1. Discover semantic views:**

```
cortex semantic-views list --like '%' --in database {DATABASE} schema {SCHEMA}
```

For EACH semantic view found, run:
```
cortex semantic-views describe {DATABASE}.{SCHEMA}.{SEMANTIC_VIEW_NAME}
```

Record for each: name, FQN, description, table references, dimension/fact/metric counts, and key dimension names.

**2. Classify each semantic view by role** using the `describe` output:

- **Base**: covers source tables from Phase 1 (table names are original source tables, not VW_ONT_* or V_* views)
- **KG**: covers `V_*` typed views and/or `KG_NODE`/`KG_EDGE`
- **Ontology**: covers `VW_ONT_*` abstract views
- **Metadata**: covers `ONT_*` introspection tables

Use the table references from the `describe` output to classify. If ambiguous, check the semantic view name for hints (`_BASE`, `_KG_MODEL`, `_ONTOLOGY_MODEL`, `_METADATA_MODEL`).

**3. Discover graph UDFs (if KG path):**

```sql
SHOW USER FUNCTIONS LIKE '%_TOOL' IN SCHEMA {DATABASE}.{SCHEMA};
```

For EACH function found, run:
```sql
DESCRIBE FUNCTION {DATABASE}.{SCHEMA}.{FUNCTION_NAME}(VARCHAR);
```

Record: name, FQN, parameters, return type.

**4. Discover SPCS service functions (if any):**

```sql
SHOW FUNCTIONS LIKE '%GRAPH_%' IN SCHEMA {DATABASE}.{SCHEMA};
```

**Step 6a: Build the tool inventory from discovered assets**

Build `TOOL_INVENTORY` from the discovery results — NOT from memory of earlier phases:

```
TOOL_INVENTORY = []
```

For each classified semantic view:
- Base semantic view → add `base_query_tool` (type: `cortex_analyst_text_to_sql`, FQN from discovery)
- KG semantic view → add `kg_query_tool` (type: `cortex_analyst_text_to_sql`, FQN from discovery)
- Ontology semantic view → add `ontology_query_tool` (type: `cortex_analyst_text_to_sql`, FQN from discovery)
- Metadata semantic view → add `metadata_query_tool` (type: `cortex_analyst_text_to_sql`, FQN from discovery)

For each discovered graph UDF:
- `EXPAND_DESCENDANTS_TOOL` → add `expand_descendants_tool` (type: `generic`, FQN from discovery)
- `GET_ANCESTORS_TOOL` → add `get_ancestors_tool`
- `GET_HIERARCHY_PATH_TOOL` → add `get_hierarchy_path_tool`
- `GET_DIRECT_CHILDREN_TOOL` → add `get_direct_children_tool`

For each SPCS service function → add to TOOL_INVENTORY with type `generic`.

Only add tools for assets that were actually found by discovery. Do NOT add tools based on session memory or assumptions about what should exist.

**Cross-check:** Verify every discovered semantic view maps to exactly one tool. Flag any unclassified semantic views or missing expected assets.

**Record the final inventory with discovery metadata:**
```
TOOL_INVENTORY = [
  { name: "base_query_tool", type: "cortex_analyst_text_to_sql",
    source_fqn: "{FQN}", role: "base",
    describe_metadata: { tables: N, dimensions: N, facts: N, metrics: N,
                         key_dimensions: [...], description: "..." } },
  ...
]
Total tools: {N}
```

Present this to the user before proceeding: *"I discovered {N} deployable assets. The agent will have {N} tools: {list}. Correct?"*

**Step 6b: Check for graph analytics tools (KG path only)**

If KG path, check what graph tools were deployed in Phase 4:

- **SQL UDF graph traversal tools**: If `07_graph_traversal_tools.sql` was generated and deployed, the 4 UDFs (`EXPAND_DESCENDANTS_TOOL`, `GET_ANCESTORS_TOOL`, `GET_HIERARCHY_PATH_TOOL`, `GET_DIRECT_CHILDREN_TOOL`) are already available and should be registered as `generic` tools in the agent.
- **SPCS graph analytics tools**: If user also wants advanced graph algorithms (centrality, community detection), offer SPCS:

```
ask_user_question: "Include SPCS graph analytics tools in the agent? (SQL hierarchy tools are already deployed)"
Options:
  - "Yes": Adds centrality, community detection, shortest path tools (requires SPCS container service)
  - "No": Agent uses semantic view tools + SQL graph traversal tools only
```

If SPCS selected, generate SPCS scaffolding only (no agent spec):
```bash
uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/generate_spcs_scaffolding.py \
  --database "<DATABASE>" \
  --schema "<SCHEMA>" \
  --output-dir "/tmp/generated"
```
This produces:
- `spcs_graph_service.py` — NetworkX-based graph analytics service
- `spcs_setup.sql` — SPCS service creation, compute pool, service functions

Guide the user through deploying the SPCS service before proceeding:
```sql
-- Execute spcs_setup.sql to create the compute pool, image repository,
-- service, and service functions in {DATABASE}.{SCHEMA}
```

**Step 6c: Invoke the `cortex-agent` skill to create the agent**

Invoke the native **`cortex-agent` skill** in **create** mode. Provide the discovered tool inventory and domain context — let the skill generate the orchestration instructions, tool descriptions, and agent spec. Do NOT hand-write the orchestration prompt.

```
skill: cortex-agent

Context to provide when the skill asks:
  - Agent name: {ONTOLOGY_NAME}_AGENT
  - Target database: {DATABASE}
  - Target schema: {SCHEMA}
  - Domain: {ONTOLOGY_NAME} — {1-sentence domain description from Phase 1}

  - Tool inventory with discovery metadata (from Step 6-pre/6a):

    FOR EACH tool in TOOL_INVENTORY, provide:

      Tool #{N}: {tool.name}
        Type: {tool.type}  (cortex_analyst_text_to_sql | generic)
        Source: {tool.source_fqn}
        Role: {tool.role}  (base | kg | ontology | metadata | graph_traversal)
        Discovery metadata:
          - For semantic view tools: "{tool.describe_metadata.description}.
            Covers {tool.describe_metadata.tables} tables with
            {tool.describe_metadata.dimensions} dimensions,
            {tool.describe_metadata.facts} facts,
            {tool.describe_metadata.metrics} metrics.
            Key dimensions: {tool.describe_metadata.key_dimensions}."
          - For graph UDF tools: "Parameters: {tool.parameters}.
            Returns: {tool.return_type}."
        Sample questions this tool should answer (from Phase 1 business questions):
          - "{question mapped to this tool's role}"
          - "{another question mapped to this tool's role}"

  - Routing principles (high-level guidance for the skill to use when generating orchestration):
      * base tools: concrete source data — entity lookups, aggregations, filtering
      * ontology tools: cross-type reasoning via abstract views
      * kg tools: KG entity/relationship queries via typed views
      * metadata tools: governance, structure introspection, data quality
      * graph tools: hierarchy traversal, path finding, ancestor/descendant exploration
      (include only lines for tool roles present in TOOL_INVENTORY)

  - The `cortex-agent` skill should generate the full orchestration instructions,
    tool descriptions (with "When to Use" / "When NOT to Use" routing hints),
    and the complete agent spec including tool_resources.

  - For graph traversal UDF tools, note these are SQL UDFs (not stored procedures),
    so tool_resources should use "type": "function".
```

The `cortex-agent` skill will:
1. Verify admin setup (warehouse, role, permissions)
2. Use the tool inventory and discovery metadata to generate rich tool descriptions
3. Generate orchestration instructions with proper routing between all tools
4. Build the agent spec with all tools, tool_resources, and orchestration
5. Execute `CREATE OR REPLACE AGENT` via `$spec$` DDL
6. Run verification queries to confirm the agent is functional

**After the skill creates the agent, REVIEW the orchestration:**
- [ ] Every tool in TOOL_INVENTORY has a routing block in the orchestration
- [ ] No routing block references a tool NOT in TOOL_INVENTORY
- [ ] Sample questions from Phase 1 would route correctly based on the generated rules
- [ ] "When NOT to Use" in each tool description only references other tools that exist
- [ ] If the agent has >2 tools, the orchestration includes multi-tool combination patterns

If any issue found, ask the `cortex-agent` skill to **refine** — do NOT hand-edit the orchestration yourself.

**Step 6d: Add SPCS graph tools to the agent (if selected in 6b)**

If SPCS graph tools were selected in Step 6b, invoke the `cortex-agent` skill again in **edit** mode to add the SPCS service functions as stored-procedure tools:

```
skill: cortex-agent

Context: Edit agent {DATABASE}.{SCHEMA}.{ONTOLOGY_NAME}_AGENT
  - Add tools: Each SPCS service function as a stored-procedure tool
    (e.g., graph_centrality, graph_community_detection, graph_shortest_path)
  - Update orchestration instructions to include graph analytics routing
```

**Step 6e: Verify and test the agent**

**1. Describe the created agent via discovery:**

```
cortex agents describe {DATABASE}.{SCHEMA}.{ONTOLOGY_NAME}_AGENT
```

**2. Cross-check against TOOL_INVENTORY:**
- [ ] Agent tool count = `len(TOOL_INVENTORY)`
- [ ] Each tool name in TOOL_INVENTORY is registered in the agent (no missing, no extras)
- [ ] Each semantic view FQN in the agent matches the discovered FQN from Step 6-pre
- [ ] Each graph UDF function identifier matches the discovered FQN
- [ ] Orchestration mentions every tool by name with routing guidance

**3. Test with Phase 1 business questions via `cortex-agent` skill:**

Invoke the `cortex-agent` skill in **test/debug** mode. Map each business question from Phase 1 to its expected tool based on role:

```
skill: cortex-agent

Context: Test the agent {DATABASE}.{SCHEMA}.{ONTOLOGY_NAME}_AGENT
  - The agent has {len(TOOL_INVENTORY)} tools: {list from TOOL_INVENTORY}
  - Run one sample question per tool, using Phase 1 business questions:
    Map each question to its expected tool by role:
      - Source data questions → base_query_tool
      - Cross-type reasoning questions → ontology_query_tool (if in inventory)
      - KG entity/relationship questions → kg_query_tool (if in inventory)
      - Governance/coverage questions → metadata_query_tool (if in inventory)
      - Hierarchy questions → graph tools (if in inventory)
  - Verify each question routes to the CORRECT tool
  - Verify responses contain expected data (non-empty, reasonable values)
```

**4. If routing is wrong:** Ask the `cortex-agent` skill to **refine** the orchestration — do NOT hand-edit the orchestration prompt yourself.

**Self-check before gate**: Verify the agent matches TOOL_INVENTORY:
- [ ] `cortex agents describe` output confirms agent exists with correct tool count
- [ ] Each tool name in TOOL_INVENTORY appears in the agent description
- [ ] Step 6e tests passed — each tool received at least one correctly-routed question with non-empty response
- [ ] No unresolved errors from the `cortex-agent` skill

If agent is missing, has wrong tool count, or tests failed, fix before presenting the gate.

**⚠️ MANDATORY STOPPING POINT — DO NOT PROCEED TO PHASE 7**: When the `cortex-agent` skill finishes, you MUST STOP HERE. Do NOT begin validation, do NOT run validation queries. You MUST use `ask_user_question`:

```
ask_user_question: "The Cortex Agent is deployed. Please test it with the questions above. Ready to proceed to Phase 7 (Validation)?"
Options:
  - "Yes, proceed to Phase 7"
  - "No, I need to fix the agent"
```

**STOP. Wait for the user's answer. Do not read ahead. Do not begin Phase 7 until the user selects an option above.**

### Phase 7: End-to-End Validation

> **GATE CHECK**: You should only be here if the user explicitly approved the Phase 6 gate above. If you arrived here without asking the user, STOP and go back to present the Phase 6 gate question.

> **Note**: SQL artifacts (L1-L3) were deployed in Phase 4b. Base semantic view (L4-base) was deployed in Phase 4.5. Ontology-layer semantic views (L4-ontology) and Cortex Agent (L5) were deployed by native skills in Phases 5 and 6. This phase validates the full stack.

**Step 7a: Validate all layers**

Run validation queries across the full stack:

- **L1 Physical**: Row counts for KG_NODE and KG_EDGE (if KG path)
- **L2 Metadata**: Row counts for all ~22 ONT_* tables
- **L3 Abstract Views**: Sample queries against each VW_ONT_* view
- **L4 Semantic Views**: Verify via `SHOW SEMANTIC VIEWS IN SCHEMA {DATABASE}.{SCHEMA}` — must include base semantic (Phase 4.5) + ontology-layer semantics (Phase 5)
- **L5 Cortex Agent**: Run one end-to-end question through the agent:
  ```sql
  SELECT SNOWFLAKE.CORTEX.AGENT(
    '{DATABASE}.{SCHEMA}.{ONTOLOGY_NAME}_AGENT',
    PARSE_JSON('{"messages": [{"role": "user", "content": "<domain question from Phase 1>"}]}')
  );
  ```

Present a summary table of all deployed objects with row counts and status.

**Self-check before gate**: Verify the full stack is healthy:
- [ ] L1: KG_NODE and KG_EDGE have non-zero row counts (if KG path)
- [ ] L2: All ~22 ONT_* tables exist and have seed data (row count > 0 for at least ONT_CLASS, ONT_RELATION_DEF, ONT_CLASS_MAP)
- [ ] L3: Every VW_ONT_* view returns rows without errors
- [ ] L4: `SHOW SEMANTIC VIEWS` count matches expected: 1 base (Phase 4.5) + N ontology-layer (Phase 5 selections)
- [ ] L5: Agent query returned a meaningful response (not an error or empty)

If any layer fails validation, report the specific failure and attempt to fix (re-execute SQL, re-deploy view, etc.) before presenting the gate. Do NOT present a "deployment complete" gate with known failures.

**⚠️ MANDATORY STOPPING POINT — FINAL GATE**: You MUST use `ask_user_question`:

```
ask_user_question: "End-to-end validation complete. All layers are deployed and verified. Is the deployment complete?"
Options:
  - "Yes, deployment is complete"
  - "No, there are issues to address"
```

## Degrees of Freedom

| Decision | Freedom | Default |
|----------|---------|---------|
| KG vs direct-table path | **High** | Ask in Phase 1 |
| Use existing semantic vs create new base | **High** | Ask in Phase 1 (Step 1b) |
| Which ontology-layer semantic models (1-3) | **High** | All 3 if KG, 2 if direct |
| Graph analytics tools | **High** | Only offered if KG path |
| Ontology class/relation design | **Medium** | AI recommends, user confirms |
| View naming convention | **Low** | `VW_ONT_` prefix |
| Agent routing instructions | **Low** | Auto-generated from model descriptions |

## Error Handling

- **No tables found**: Verify DATABASE.SCHEMA exists and user has SELECT permission
- **OWL parse failure**: Check file format (OWL/XML, Turtle, RDF/XML). Parser auto-detects.
- **SQL execution error**: Show exact error, suggest GRANT statements if permission issue
- **Empty views**: Verify filter conditions in ONT_CLASS_MAP match actual data values
- **Semantic model validation failure**: Check that all referenced views exist and have data

## Session Resume

If invoked mid-session, use **discovery commands** to detect the current state from Snowflake — do not rely on session memory or local files alone.

**Discovery-first state detection:**

1. Check local artifacts: `/tmp/generated/`, `/tmp/ontology_parsed/`
2. Check Snowflake objects:
   ```
   cortex semantic-views list --like '%' --in database {DATABASE} schema {SCHEMA}
   cortex agents list --like '%' --in database {DATABASE} schema {SCHEMA}
   ```
   ```sql
   SHOW VIEWS LIKE 'VW_ONT_%' IN SCHEMA {DATABASE}.{SCHEMA};
   SHOW VIEWS LIKE 'V_%' IN SCHEMA {DATABASE}.{SCHEMA};
   SHOW TABLES LIKE 'ONT_%' IN SCHEMA {DATABASE}.{SCHEMA};
   SHOW USER FUNCTIONS LIKE '%_TOOL' IN SCHEMA {DATABASE}.{SCHEMA};
   ```

**Resume decision tree:**
- If `classes.json` exists → resume from Phase 3 (visualize)
- If `*.sql` files exist but not yet executed → resume from Phase 4b (deploy SQL)
- If `VW_ONT_*` views exist but no base semantic view → resume from Phase 4.5 (base semantic via `semantic-view` skill)
- If base semantic view exists but no ontology-layer semantic views → resume from Phase 5 (ontology semantic views via `semantic-view` skill)
- If ontology-layer semantic views exist but no agent → resume from Phase 6 (agent via `cortex-agent` skill)
  - Phase 6 will run its own discovery (Step 6-pre) to build TOOL_INVENTORY from Snowflake, so no prior session context is needed
- If agent exists → resume from Phase 7 (validation)

To classify semantic views, run `cortex semantic-views describe {FQN}` for each and check the table references:
- Covers source tables (not VW_ONT_* or V_*) → base semantic view
- Covers V_* views → KG semantic view
- Covers VW_ONT_* views → ontology semantic view
- Covers ONT_* tables → metadata semantic view

Always confirm the detected state with the user before proceeding.

## Output Files

SQL artifacts are saved to `/tmp/generated/` during the session. Semantic views and the Cortex Agent are deployed directly to Snowflake by the native skills (no local YAML or JSON files).

```
/tmp/generated/
├── 01_physical_layer.sql        (KG path only)
├── 02_concrete_views.sql        (V_{CLASS}, V_{REL} typed views)
├── 03_metadata_tables.sql       (~22 tables + auto-populated seed data)
├── 04_abstract_views.sql
├── 05_view_generator_sp.sql
├── 06_inference_engine.sql      (optional, KG path + user opted in)
├── 07_graph_traversal_tools.sql (optional, KG path + user opted in — 4 SQL UDF tools)
├── spcs_graph_service.py        (if SPCS graph tools selected)
└── spcs_setup.sql               (if SPCS graph tools selected)

Deployed directly to Snowflake (via native skills):
├── Base semantic view          (created by `semantic-view` skill in Phase 4.5, or existing)
├── Ontology semantic views     (created by `semantic-view` skill in Phase 5)
└── Cortex Agent                (created by `cortex-agent` skill in Phase 6)
```
