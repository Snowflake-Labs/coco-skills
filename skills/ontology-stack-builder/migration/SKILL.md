---
name: ontology-stack-builder-migration
description: "Path M of ontology-stack-builder: migrate an existing ontology stack (ONT_* metadata tables built by this skill or by the ontology-on-snowflake reference repo) into Snowflake native Business Ontology. Load from the parent SKILL.md router; do not run Phases 1-7."
---

# Path M: Migrate an ontology stack to Business Ontology

Moves the *meaning* of a deployed stack (classes, hierarchy, relations, descriptions, table and column bindings, derived metrics) into Snowflake native Business Ontology (BON), re-points the Cortex Agent, then optionally retires the legacy ontology layer.

Read [MAPPING.md](MAPPING.md) before starting. It is the contract for what maps to what and what is lost.

## Rules for this path

- **Every gate is a mandatory stop.** Use `ask_user_question` and wait. The global gate rule in the parent SKILL.md applies.
- **The user names the domain.** Never derive it from the schema or ontology name without asking.
- **Read back before reporting.** BON can return `error: false` for writes that never persist. Counts come from `bon_reconcile.py`, never from the write log.
- **Nothing destructive without a separate confirmation.** M7b (archive and drop) has its own gate and clones before it drops.
- **BON is Private Preview.** If any `SYSTEM$*GLOSSARY*` call returns a feature-gate error, stop and tell the user: *Business Ontology is not enabled in this account; ask your account admin to enable it.*

All scripts run with `uv` from the skill directory:

```bash
cd <skill_dir> && uv run --extra migration python scripts/<script>.py ...
```

## Workflow

```
M0 PREFLIGHT  -> M1 EXTRACT -> M2 REVIEW (gate) -> M3 APPLY NODES/EDGES/REPRESENTATIONS (gate)
   -> M4 METRICS FROM BASE SV (gate) -> M5 RECONCILE (gate) -> M6 AGENT CUTOVER (gate)
   -> M7 RETIRE LEGACY LAYER (gate, optional)
```

### M0: Preflight

Collect `{DATABASE}.{SCHEMA}` of the stack and the Snowflake connection name. Then run:

```sql
SELECT SYSTEM$GET_GLOSSARY_SUMMARY();                              -- feature gate + existing domains[]
SHOW TABLES LIKE 'ONT_%' IN SCHEMA {DATABASE}.{SCHEMA};             -- stack present?
SELECT ONTOLOGY_NAME, DESCRIPTION, VERSION FROM {DATABASE}.{SCHEMA}.ONT_ONTOLOGY;
SHOW SEMANTIC VIEWS IN SCHEMA {DATABASE}.{SCHEMA};                  -- find {ONT}_BASE and the ontology/metadata SVs
SHOW AGENTS IN SCHEMA {DATABASE}.{SCHEMA};                          -- find {ONT}_AGENT
SHOW TABLES LIKE 'ONT_BON_MIGRATION_MAP' IN SCHEMA {DATABASE}.{SCHEMA};  -- previous run?
SELECT SYSTEM$GET_GLOSSARY_TERM_DRAFTS();                           -- pending drafts that a scoped approve must not touch
```

If `ONT_CLASS` does not exist, this is not a stack; offer the build path or `$business-ontology` instead.
If `ONT_ONTOLOGY` holds more than one ontology, ask which one (`--ontology`).

**Gate M0** (`ask_user_question`): show the stack summary and existing BON domains, and ask for the target domain. Options: each existing domain, plus "Create a new domain named ...". Default suggestion: the ontology name, humanized. Record the answer as `{DOMAIN}`.

### M1: Extract

```bash
uv run --extra migration python scripts/export_to_bon.py \
  --connection {CONNECTION} --database {DATABASE} --schema {SCHEMA} \
  --domain "{DOMAIN}" [--ontology {ONTOLOGY_NAME}] [--include-shared-properties] \
  --out /tmp/bon_migration
```

Outputs in `/tmp/bon_migration/`:

| File | Content |
|---|---|
| `candidates.json` | Node candidates in the BON candidate contract (`domainSource: USER_DECLARED`) |
| `relationships.json` | `{source, target, type, label, origin}`; types already validated against the 11 BON types |
| `associations.json` | `{termName, assetRef, role, origin}`; refs in the four valid `assetRefJson` shapes |
| `migration_loss_report.md` | Everything without a BON home, by table |
| `export_manifest.json` | Variant detected, counts, source rows (feeds the map table) |

The script reads only. It prints the detected schema variant (`GENERATOR`, `TEMPLATE`, `REFERENCE` or `ID_KEYED`, see MAPPING.md) and the counts. Add `--dump-snapshot /tmp/bon_migration/snapshot.json` to keep the raw read for audit; `--snapshot` re-runs the mapping offline from it.

### M2: Review

Present, using the parent skill's summary style:

1. Counts: nodes, relationships by type, representations (object / column), loss items.
2. The relationship mapping table: each `REL_NAME`, chosen BON type, direction. Ask about every `CUSTOM` choice in bulk ("keep as CUSTOM with label, or map to ...").
3. Nodes flagged `needs_description` (generated text such as "Entity from table X"). Offer: write better descriptions now (the agent drafts them from table comments and sample values, the user approves), or migrate as-is and fix in Snowsight later.
4. **B-10 warning.** If every class maps to exactly one source table (schema-introspection build), say so: the BON quality gate discourages one ENTITY per base table. Offer to drop pure lookup/junction classes, or keep them (the user confirmed them during the original build, which is steward evidence).
5. The loss report headline.

Re-run M1 with `--rel-type-override REL=TYPE` or edits to the JSON files as needed.

**Gate M2** (`ask_user_question`): "Migrate these N nodes, R relationships and A representations into domain {DOMAIN}?" Options: "Yes, make them active", "Yes, but save everything as draft for review in Snowsight", "No, change something first".

### M3: Apply nodes, relationships and representations

Run the BON quality gate first when the `business-ontology` skill is installed (`~/.snowflake/cortex/skills/business-ontology` or the bundled location):

```bash
python3 <business_ontology_dir>/scripts/ontology_rule_check.py -c {CONNECTION} --domain "{DOMAIN}" \
  --proposals-file /tmp/bon_migration/candidates.json --object-type node --candidate-count <number of candidates>
python3 <business_ontology_dir>/scripts/ontology_rule_check.py -c {CONNECTION} --domain "{DOMAIN}" \
  --proposals-file /tmp/bon_migration/relationships.json --object-type relationship
```

Read `summary.block` / `summary.gate` in the JSON output.

Show BLOCK findings and stop on them. Show GATE findings and let the user choose per the BON permission ladder (supply value / draft anyway / override).

Then apply:

```bash
uv run --extra migration python scripts/bon_apply.py \
  --connection {CONNECTION} --input-dir /tmp/bon_migration --domain "{DOMAIN}" [--save-as-draft]
```

`bon_apply.py` follows the BON backend model exactly:

1. Creates `{DOMAIN}` if it does not exist (`SYSTEM$CREATE_GLOSSARY_DOMAIN`).
2. Drafts every node (`SYSTEM$DRAFT_GLOSSARY_TERM`), then approves only the term IDs it drafted (`SYSTEM$APPROVE_ALL_GLOSSARY_TERMS('[ids]')`). Existing nodes with the same name in the domain are reused, not duplicated.
3. Drafts relationships only between ACTIVE endpoints (`SYSTEM$DRAFT_GLOSSARY_RELATIONSHIP` addressed by term ID, so names containing dots are safe), then approves only its own tuples (`SYSTEM$APPROVE_ALL_GLOSSARY_RELATIONSHIPS('[{source,target,type}]')`). The label argument is sent only for `CUSTOM`.
4. Drafts representations (`SYSTEM$DRAFT_GLOSSARY_ASSET`), then approves only its own refs (`SYSTEM$APPROVE_ALL_GLOSSARY_ASSETS`).
5. Never calls the no-argument `APPROVE_ALL_*` forms (they approve the whole account's queue).
6. Writes `/tmp/bon_migration/apply_log.json` with every call and response.

With `--save-as-draft` it stops after the drafts. Drafted relationships are invisible in the graph and in Snowsight until approved; say so to the user.

Delegation note: you may instead hand `candidates.json`, `relationships.json` and `associations.json` to `$business-ontology` import Path C. `bon_apply.py` exists because Path C's `batch_import.py` does not write relationships and hard-codes the representation role; use it unless the user prefers the native flow.

**Gate M3**: show the apply summary (drafted / approved / failed per object type, with failures listed) and ask to continue to metrics.

### M4: Metrics from the base semantic view

The stack never stored metrics; they live in the base semantic view (`{ONT}_BASE`, or the existing SV the user chose during the build). Load `$business-ontology` and run its import **Path H** (live semantic views) scoped to that one view, with `--domain "{DOMAIN}"` so every metric is `USER_DECLARED`:

- It creates `METRIC` nodes with `formula` and `RELATED_SEMANTIC_VIEW` representations.
- Then add `ENTITY MEASURES METRIC` edges for each metric whose base table is bound to a migrated class (use `associations.json` to match table FQNs). Write them with `SYSTEM$DRAFT_GLOSSARY_RELATIONSHIP` + scoped approve, or add them to `relationships.json` and re-run `bon_apply.py` (it skips what already exists).

Skip `{ONT}_KG_MODEL`, `{ONT}_ONTOLOGY_MODEL` and `{ONT}_METADATA_MODEL`: they describe the stack's own plumbing, not business metrics.

**Gate M4**: show the metrics imported and the `MEASURES` edges, and ask to reconcile.

**No live semantic view?** Stacks built from the reference repo keep their models as staged YAML (`@...SEMANTIC_MODELS/*.yml`). Skip M4 and say so: metrics, filters and verified queries stay with the semantic layer and are out of scope for this migration. Do not parse staged YAML into BON.

### M5: Reconcile

```bash
uv run --extra migration python scripts/bon_reconcile.py \
  --connection {CONNECTION} --database {DATABASE} --schema {SCHEMA} \
  --domain "{DOMAIN}" --input-dir /tmp/bon_migration
```

It reads the BON graph back (`SYSTEM$GET_GLOSSARY_GRAPH('{DOMAIN}')`, falling back to per-term `SYSTEM$GET_GLOSSARY_TERM` when the graph call fails on large accounts), compares it with the export, writes `/tmp/bon_migration/reconcile_report.md`, and upserts `{DATABASE}.{SCHEMA}.ONT_BON_MIGRATION_MAP` (DDL in `references/migration_map_table.sql`): one row per source object with its BON term ID, status (`MIGRATED`, `MISSING`, `NOT_MIGRATED`) and the original source row.

Parity must be 100% for every in-scope object. For each `MISSING` item, re-run `bon_apply.py` once (it is idempotent and only writes what is missing). If an item is still missing, report it; do not loop.

**Gate M5**: show the parity table. Options: "Parity confirmed, continue to agent cutover", "Stop here (BON populated, legacy stack untouched)".

### M6: Agent cutover

Goal: a new agent that answers from BON context plus the data tools instead of the ontology and metadata semantic models. The original agent is never modified.

```bash
uv run --extra migration python scripts/bon_agent_cutover.py create -c {CONNECTION} \
  --source {AGENT_DB}.{AGENT_SCHEMA}.{ONT}_AGENT --target {DATABASE}.{SCHEMA}.{ONT}_BON_AGENT --domain "{DOMAIN}"

uv run --extra migration python scripts/bon_agent_cutover.py compare -c {CONNECTION} \
  --before {AGENT_DB}.{AGENT_SCHEMA}.{ONT}_AGENT --after {DATABASE}.{SCHEMA}.{ONT}_BON_AGENT \
  -q "<business question 1>" -q "<definition question>" -q "<cross-type question>"
```

`create` saves `agent_spec_before.json` / `agent_spec_after.json`, removes the analyst tools whose semantic model is the ontology or metadata model (override with `--drop TOOL`), keeps data and graph tools, and adds the BON search tool below. `compare` runs each question through both agents via the Cortex Agents REST API and writes `agent_compare.md`; it exits non-zero if the BON tool returned an error. Use at least one definition question ("What does <derived term> mean?") so BON is actually exercised.

What `create` writes, for reference or manual edits:
   ```json
   "experimental": {"EnableSnowscopeBusinessOntologySearch": true},
   "tools": [{"tool_spec": {"type": "snowscope_search", "name": "business_ontology", "description": "..."}}],
   "tool_resources": {"business_ontology": {"corpus": "businessOntology"}}
   ```
   The `experimental` flag is required while BON is in preview: without it every call fails with *businessOntology corpus is experimental and currently disabled*. The search spans every domain in the account, so the agent is instructed to start each query with the domain name (`"{DOMAIN} Career Duration"`); bare queries rank other domains first.

For deeper tuning use `$cortex-agent` or `agent-optimization` on the new agent.

**Gate M6**: show `agent_compare.md`. Options: "Keep the new agent", "Drop the new agent" (the original was never changed, so no rollback is needed).

### M7: Retire the legacy ontology layer (optional)

Ask first whether to retire at all. Many customers keep the stack read-only for a release cycle.

**Gate M7a**: "Retire the legacy ontology layer now?" Options: "Yes, archive and drop", "Not now".

If yes, show this exact SQL and require a second confirmation (**Gate M7b**) before running it:

```sql
CREATE SCHEMA IF NOT EXISTS {DATABASE}.{SCHEMA}_ONT_ARCHIVE
  CLONE {DATABASE}.{SCHEMA};   -- full zero-copy archive of the stack schema
DROP SEMANTIC VIEW IF EXISTS {DATABASE}.{SCHEMA}.{ONT}_ONTOLOGY_MODEL;
DROP SEMANTIC VIEW IF EXISTS {DATABASE}.{SCHEMA}.{ONT}_METADATA_MODEL;
-- metadata tables that are now owned by BON
DROP TABLE IF EXISTS {DATABASE}.{SCHEMA}.ONT_CLASS;            -- repeat for every table listed below
```

Tables dropped in M7b: `ONT_CLASS`, `ONT_RELATION_DEF`, `ONT_PROPERTY`, `ONT_SHARED_PROPERTY`, `ONT_DERIVED_PROPERTY`, `ONT_INTERFACE`, `ONT_INTERFACE_PROPERTY`, `ONT_INTERFACE_IMPL`, `OBJ_VIEW_DEF`, `OBJ_VIEW_FIELD`, `ONT_ROLE`, `ONT_ROLE_BINDING`, `ONT_PERMISSION`. Views `VW_ONT_*` and `REL_RESOLVED` are dropped too, because they are only consumed by the ontology semantic view.

**Kept**, because something still runs on them: `KG_NODE`, `KG_EDGE`, `V_*` views, `ONT_CLASS_MAP`, `ONT_REL_MAP`, `ONT_OBJECT_SOURCE`, `ONT_LINK_SOURCE` (read by `SP_GENERATE_ONTOLOGY_VIEWS` and the KG loaders), `ONT_RULE`, `REL_EDGE_INFERRED`, `ONT_CONSTRAINT_VIOLATION`, `ACT_*`, `ONT_FUNCTION*`, procedures, graph UDFs, SPCS objects, the base semantic view, `{ONT}_KG_MODEL`, `ONT_ONTOLOGY` and `ONT_BON_MIGRATION_MAP`.

Before dropping, run `SELECT * FROM TABLE(SNOWFLAKE.CORE.GET_LINEAGE('{DATABASE}.{SCHEMA}.ONT_CLASS', 'TABLE', 'DOWNSTREAM', 2))` (or the `lineage` skill) and show any dependents outside this schema; stop if there are any the user did not expect.

Rollback: `CREATE OR REPLACE TABLE {DATABASE}.{SCHEMA}.<T> CLONE {DATABASE}.{SCHEMA}_ONT_ARCHIVE.<T>`.

## Resume

| State found | Resume at |
|---|---|
| `/tmp/bon_migration/candidates.json` exists, no `apply_log.json` | M2 |
| `apply_log.json` exists, no `ONT_BON_MIGRATION_MAP` | M5 (then M4 if no METRIC nodes in the domain) |
| `ONT_BON_MIGRATION_MAP` exists with `MISSING` rows | M3 (re-apply), then M5 |
| Map table all `MIGRATED`, agent still has `ontology_query_tool` | M6 |
| Agent cut over, `ONT_CLASS` still present | M7 |

## Incremental re-runs (delta sync)

While both systems coexist, rerun M1 -> M3 -> M5. The exporter compares each source row's checksum with `ONT_BON_MIGRATION_MAP.SOURCE_CHECKSUM`. Unchanged rows are skipped, changed descriptions become `SYSTEM$UPDATE_GLOSSARY_TERM` patches (shown at M2 as old -> new, because changing a governed value is drift), and new rows are drafted. Removed source rows are reported, never deleted from BON automatically.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Term not found` on a relationship | endpoint still DRAFT | approve the node first, or re-run without `--save-as-draft` |
| `source and target must be different terms` | self-referential relation edited into `relationships.json` by hand | remove it; the exporter already reports these in the loss report |
| Relationship reported written but absent | BON silent drop | re-run `bon_apply.py` once; report if still missing |
| `GET_GLOSSARY_GRAPH` error 300002 | account graph too large | reconcile falls back to per-term reads automatically |
| COLUMN representation rejected | column not in the catalog (KG path view built with different casing) | check `DESCRIBE VIEW V_{CLASS}`; the column is reported, not retried |
| Domain name rejected by BON tools | name with characters a tool does not accept | `bon_apply.py` binds names as string literals; keep the name the user chose |
| Reconcile says representations are missing right after a clean apply | read-side objects report `objectType` (`Column`, `View`) instead of `refType` | handled by `bon_migration_common.asset_key`; if a new object type appears, extend it there |
| Agent BON tool fails: *businessOntology corpus is experimental and currently disabled* | agent spec lacks the preview flag | `bon_agent_cutover.py create` sets `experimental.EnableSnowscopeBusinessOntologySearch`; add it by hand for manually built agents |
| Agent says a migrated term "does not exist" in BON | bare query ranked other domains first, or the node name is class-qualified (`Player Career Duration`) | query with the domain prefix (the agent is instructed to); the exporter adds the unqualified name as a synonym |
| BON search returns nodes from a domain you deleted | the search index lags deletes | wait for re-indexing; do not re-delete |
| `Failed to login ... IP ... is not allowed` mid-run | account network policy | fix network access, then re-run the same step; every script is safe to re-run |
