---
name: ontology-stack-builder-migrate-to-business-ontology
description: "Path M of ontology-stack-builder: migrate an existing ontology stack (ONT_* metadata tables built by this skill or by the ontology-on-snowflake reference repo) into Snowflake native Business Ontology. Load from the parent SKILL.md router; do not run Phases 1-7. Human guide: README.md in this folder."
---

# Path M: Migrate an ontology stack to Business Ontology

Moves the *meaning* of a deployed stack (classes, hierarchy, relations, descriptions, table and column bindings, derived metrics) into Snowflake native Business Ontology (BON), re-points the Cortex Agent, then optionally retires the legacy ontology layer.

Read [MAPPING.md](MAPPING.md) before starting. It is the contract for what maps to what and what is lost. Paths below (`scripts/`, `references/`) are relative to the skill root, one level up from this file.

## Rules for this path

- **Every gate is a mandatory stop.** Use `ask_user_question` and wait. The global gate rule in the parent SKILL.md applies.
- **The user names the domain.** Never derive it from the schema or ontology name without asking.
- **Read back before reporting.** BON can return `error: false` for writes that never persist. Counts come from `bon_reconcile.py`, never from the write log.
- **Nothing destructive without a separate confirmation.** M7b (archive and drop) has its own gate and clones before it drops.
- **BON may not be enabled.** At the time of writing BON is in Private Preview. If any `SYSTEM$*GLOSSARY*` call returns a feature-gate error, stop and tell the user: *Business Ontology is not enabled in this account; ask your account admin to enable it.*

All scripts run with `uv` from the skill directory:

```bash
cd <skill_dir> && uv run --extra migration python scripts/<script>.py ...
```

`{OUT}` below is the working directory for one migration (default `/tmp/bon_migration`). Use one directory per stack and pass the same value to every script (`--out` for `export_to_bon.py` and `bon_agent_cutover.py`, `--input-dir` for the others) so all artifacts land together.

Stacks do not always follow the generator's names. Find the real objects in M0 and use those names everywhere: the data semantic view is not always `{ONT}_BASE`, and the agent is not always `{ONT}_AGENT` (for example `MY_ONT_AGENT`).

## Workflow

```
M0 PREFLIGHT  -> M1 EXTRACT -> M2 REVIEW (gate) -> M3 APPLY NODES/EDGES/REPRESENTATIONS (gate)
   -> M4 METRICS FROM THE DATA SV (gate) -> M5 RECONCILE (gate) -> M6 AGENT CUTOVER (gate)
   -> M7 RETIRE LEGACY LAYER (gate, optional)
```

### M0: Preflight

Collect `{DATABASE}.{SCHEMA}` of the stack and the Snowflake connection name. Then run:

```sql
SELECT SYSTEM$GET_GLOSSARY_SUMMARY();                              -- feature gate + existing domains[]
SHOW TABLES LIKE 'ONT_%' IN SCHEMA {DATABASE}.{SCHEMA};             -- stack present? previous run (ONT_BON_MIGRATION_MAP)?
SELECT ONTOLOGY_NAME, DESCRIPTION, VERSION FROM {DATABASE}.{SCHEMA}.ONT_ONTOLOGY;
SHOW SEMANTIC VIEWS IN SCHEMA {DATABASE}.{SCHEMA};                  -- data SV (base view or {ONT}_KG_MODEL) + ontology/metadata SVs
SHOW AGENTS IN SCHEMA {DATABASE}.{SCHEMA};                          -- the stack's agent (name varies)
SELECT SYSTEM$GET_GLOSSARY_TERM_DRAFTS();                           -- pending drafts that a scoped approve must not touch
```

If no agent is in the stack schema, also check `SHOW AGENTS IN SCHEMA SNOWFLAKE_INTELLIGENCE.AGENTS` and ask the user which agent serves this stack.

If `ONT_CLASS` does not exist, this is not a stack; offer the build path or `$business-ontology` instead.
If `ONT_ONTOLOGY` holds more than one ontology, ask which one and pass `--ontology` (the exporter refuses to mix them). Migrate each ontology into its own domain, one run each.
If `ONT_BON_MIGRATION_MAP` is in the listing, this is a re-run: see **Resume** and **Incremental re-runs** below.

**Gate M0** (`ask_user_question`): show the stack summary and existing BON domains, and ask for the target domain. Options: each existing domain, plus "Create a new domain named ...". Default suggestion: the ontology name, humanized. Record the answer as `{DOMAIN}`.

### M1: Extract

```bash
uv run --extra migration python scripts/export_to_bon.py \
  --connection {CONNECTION} --database {DATABASE} --schema {SCHEMA} \
  --domain "{DOMAIN}" [--ontology {ONTOLOGY_NAME}] [--include-shared-properties] \
  --out {OUT}
```

Outputs in `{OUT}/`:

| File | Content |
|---|---|
| `candidates.json` | Node candidates in the BON candidate contract (`domainSource: USER_DECLARED`) |
| `relationships.json` | `{source, target, type, label, origin}`; types already validated against the 11 BON types |
| `associations.json` | `{termName, assetRef, role, origin}`; refs in the four valid `assetRefJson` shapes |
| `migration_loss_report.md` | Everything without a BON home, by table |
| `export_manifest.json` | Variant detected, counts, source rows (feeds the map table) |

The script reads only. It prints the detected schema variant (`GENERATOR`, `TEMPLATE`, `REFERENCE` or `ID_KEYED`, see MAPPING.md) and the counts. Add `--dump-snapshot {OUT}/snapshot.json` to keep the raw read for audit; `--snapshot` re-runs the mapping offline from it.

### M2: Review

Present, using the parent skill's summary style:

1. Counts: nodes, relationships by type, representations (object / column), loss items.
2. The relationship mapping table: each `REL_NAME`, chosen BON type, direction. Ask about every `CUSTOM` choice in bulk ("keep as CUSTOM with label, or map to ...").
3. Nodes flagged `needs_description` (generated text such as "Entity from table X"). Offer: write better descriptions now (the agent drafts them from table comments and sample values, the user approves), or migrate as-is and fix in Snowsight later.
4. Nodes flagged `generic_root`: an abstract root with a placeholder name (`Entity`, `Object`, `Node`, `Concept`, ...). Offer to drop it and re-parent its children. Named abstract roots such as `MarketParticipant` are real business concepts and are not flagged; migrate them.
5. **B-10 warning.** If every class maps to exactly one source table (schema-introspection build), say so: the BON quality gate discourages one ENTITY per base table. Offer to drop pure lookup/junction classes, or keep them (the user confirmed them during the original build, which is steward evidence).
6. The loss report headline.

Re-run M1 with `--rel-type-override REL=TYPE` or edits to the JSON files as needed.

**Gate M2** (`ask_user_question`): "Migrate these N nodes, R relationships and A representations into domain {DOMAIN}?" Options: "Yes, make them active", "Yes, but save everything as draft for review in Snowsight", "No, change something first".

### M3: Apply nodes, relationships and representations

Run the BON quality gate first when the `business-ontology` skill is installed. It ships `scripts/ontology_rule_check.py`; this skill does not. `<business_ontology_dir>` is that skill's folder: `~/.snowflake/cortex/skills/business-ontology` for a user install, otherwise the base directory shown when `$business-ontology` loads. If it is not installed, say so at Gate M3 and continue; BON's own API checks still apply.

```bash
N=$(python3 -c "import json;print(len(json.load(open('{OUT}/candidates.json'))))")
python3 <business_ontology_dir>/scripts/ontology_rule_check.py -c {CONNECTION} --domain "{DOMAIN}" \
  --proposals-file {OUT}/candidates.json --object-type node --candidate-count $N > {OUT}/gate_nodes.json
python3 <business_ontology_dir>/scripts/ontology_rule_check.py -c {CONNECTION} --domain "{DOMAIN}" \
  --proposals-file {OUT}/relationships.json --object-type relationship > {OUT}/gate_rels.json
```

Read `summary.block` / `summary.gate` in each JSON output. Use system `python3`; the business-ontology skill's own `uv` environment may be unable to download dependencies behind a proxy.

Show BLOCK findings and stop on them. Show GATE findings and let the user choose per the BON permission ladder (supply value / draft anyway / override).

Findings with status `agent-review` (for example N-03 description quality, N-11 near-duplicates) are checks the script cannot decide. Do them yourself: read each flagged node's description and name against the rest of the batch and the domain, and list anything that fails at Gate M3. Many rules report `skipped` on a small or new domain (no semantic-view index, no steward); that is expected, not a pass you need to explain.

Then apply:

```bash
uv run --extra migration python scripts/bon_apply.py \
  --connection {CONNECTION} --input-dir {OUT} --domain "{DOMAIN}" [--save-as-draft] [--dry-run]
```

`bon_apply.py` follows the BON backend model exactly:

1. Creates `{DOMAIN}` if it does not exist (`SYSTEM$CREATE_GLOSSARY_DOMAIN`).
2. Drafts every node (`SYSTEM$DRAFT_GLOSSARY_TERM`), then approves only the term IDs it drafted (`SYSTEM$APPROVE_ALL_GLOSSARY_TERMS('[ids]')`). Existing nodes with the same name in the domain are reused, not duplicated.
3. Drafts relationships only between ACTIVE endpoints (`SYSTEM$DRAFT_GLOSSARY_RELATIONSHIP` addressed by term ID, so names containing dots are safe), then approves only its own tuples (`SYSTEM$APPROVE_ALL_GLOSSARY_RELATIONSHIPS('[{source,target,type}]')`). The label argument is sent only for `CUSTOM`.
4. Drafts representations (`SYSTEM$DRAFT_GLOSSARY_ASSET`), then approves only its own refs (`SYSTEM$APPROVE_ALL_GLOSSARY_ASSETS`).
5. Never calls the no-argument `APPROVE_ALL_*` forms (they approve the whole account's queue).
6. Writes `{OUT}/apply_log.json` with every call and response.

With `--save-as-draft` it stops after the node drafts: relationships and representations need ACTIVE endpoints, so they are written by the next run without the flag (which approves the drafts it finds and continues). Drafts are visible in Snowsight for review; drafted relationships would be invisible in the graph until approved, which is why they wait. Migrating into an existing domain is safe: same-name nodes are reused, and nothing outside this export is touched.

Delegation note: you may instead hand `candidates.json`, `relationships.json` and `associations.json` to `$business-ontology` import Path C. `bon_apply.py` exists because Path C's `batch_import.py` does not write relationships and hard-codes the representation role; use it unless the user prefers the native flow.

**Gate M3**: show the apply summary (drafted / approved / failed per object type, with failures listed) and ask to continue to metrics.

### M4: Metrics from the data semantic view

The stack never stored metrics; they live in its data semantic view: the base view on the direct path (`{ONT}_BASE`, or the existing view the user chose during the build), and `{ONT}_KG_MODEL` on the KG path, whose `V_*` views are the class bindings. `{ONT}_ONTOLOGY_MODEL` and `{ONT}_METADATA_MODEL` describe the stack's own plumbing and are never used.

```bash
uv run --extra migration python scripts/bon_import_metrics.py \
  --connection {CONNECTION} --input-dir {OUT} [--semantic-view DB.SC.SV ...] [--include-trivial]
```

Without `--semantic-view` it scans the stack schema and uses every view whose base tables include a migrated class binding and no plumbing (`ONT_*`, `VW_ONT_*`, `REL_RESOLVED`). For each metric it:

1. finds the class it measures: the class bound to the metric's base table, or for a relation view `V_{REL}`, the relation's source class (also for relations that were not migrated as edges);
2. adds a `METRIC` candidate (formula, synonyms, description), a `metric MEASURES class` edge and a `RELATED_SEMANTIC_VIEW` representation to the export files, so `bon_apply.py` and `bon_reconcile.py` handle them with the same scoped approve, read-back, map table and delta;
3. reports, and does not import, metrics with no migrated class to measure (orphans), and **trivial** metrics: a plain `COUNT/SUM/AVG/MIN/MAX` whose description only restates its name (`COUNT(DISTINCT NODE_ID)`, *Count of distinct companies*). BON rule B-09 rejects those because they mirror the semantic view instead of governing it; they stay in the semantic view. `--include-trivial` imports them anyway (the gate will flag them).

Then run the M3 quality gate again on the merged `candidates.json` / `relationships.json`, and `bon_apply.py`. METRIC nodes need a defined grain (rule N-10): the `RELATED_SEMANTIC_VIEW` representation provides it once approved, so an N-10 GATE before apply is expected for SV metrics.

**Gate M4**: show the metrics imported with the class each measures, the metrics skipped and why, and the gate findings. Options: "Import as listed", "Also import the trivial ones", "Skip metrics". Then apply and continue to reconcile.

**No live semantic view?** Stacks built from the reference repo keep their models as staged YAML (`@...SEMANTIC_MODELS/*.yml`). Skip M4 and say so: metrics, filters and verified queries stay with the semantic layer and are out of scope for this migration. Do not parse staged YAML into BON.

**0 metrics?** A data view with only dimensions and facts, or only trivial counts, imports nothing; that is the correct result, not a failure. Do not turn dimensions into nodes (column-dictionary anti-pattern). Report "0 metrics to import" at Gate M4; the skipped list still goes into the map table as `NOT_MIGRATED`.

### M5: Reconcile

```bash
uv run --extra migration python scripts/bon_reconcile.py \
  --connection {CONNECTION} --database {DATABASE} --schema {SCHEMA} \
  --domain "{DOMAIN}" --input-dir {OUT}
```

It reads the BON graph back (`SYSTEM$GET_GLOSSARY_GRAPH('{DOMAIN}')`, falling back to per-term `SYSTEM$GET_GLOSSARY_TERM` when the graph call fails on large accounts), compares it with the export, writes `{OUT}/reconcile_report.md`, and upserts `{DATABASE}.{SCHEMA}.ONT_BON_MIGRATION_MAP` (DDL in `references/migration_map_table.sql`): one row per source object with its BON term ID, status (`MIGRATED`, `MISSING`, `NOT_MIGRATED`) and the original source row.

Parity must be 100% for every in-scope object. For each `MISSING` item, re-run `bon_apply.py` once (it is idempotent and only writes what is missing). If an item is still missing, report it; do not loop.

**Gate M5**: show the parity table. Options: "Parity confirmed, continue to agent cutover", "Stop here (BON populated, legacy stack untouched)".

### M6: Agent cutover

Goal: a new agent that answers from BON context plus the data tools instead of the ontology and metadata semantic models. The original agent is never modified.

`{AGENT}` is the stack's agent found in M0; `{NEW_AGENT}` defaults to `{AGENT}` with `_BON` inserted (ask at Gate M5).

```bash
uv run --extra migration python scripts/bon_agent_cutover.py create -c {CONNECTION} \
  --source {AGENT} --target {DATABASE}.{SCHEMA}.{NEW_AGENT} --domain "{DOMAIN}" --out {OUT}

uv run --extra migration python scripts/bon_agent_cutover.py compare -c {CONNECTION} \
  --before {AGENT} --after {DATABASE}.{SCHEMA}.{NEW_AGENT} \
  -q "<business question 1>" -q "<definition question>" -q "<cross-type question>" --domain "{DOMAIN}" --out {OUT}
```

**Index lag.** BON search indexes a new domain 5+ minutes after M3. Run `compare` at least 5 minutes after the last apply; if it reports `not_grounded_in_domain`, wait and re-run before drawing conclusions (reconcile already proved the nodes exist). `compare` makes two agent calls per question, often 30-90 s each, and prints progress to stderr; run it in the background for more than 3 questions.

`create` saves `agent_spec_before.json` / `agent_spec_after.json`, removes the analyst tools whose semantic model is the ontology or metadata model (override with `--drop TOOL`), keeps data and graph tools, and adds the BON search tool below. `compare` runs each question through both agents via the Cortex Agents REST API and writes `agent_compare.md`; it exits non-zero if the `business_ontology` tool returned an error, or (with `--domain`) if a BON answer drew only on other domains' nodes. That second case is how a stale search index shows up: an answer that sounds right but comes from an older domain is not evidence the migration works. Data-tool errors are listed separately as `other_tool_errors`. Use at least one definition question ("What does <derived term> mean?") so BON is actually exercised.

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

**Gate M7a**: "Retire the legacy ontology layer now?" Options: "Yes, plan it", "Not now".

If yes, plan first (read-only; nothing changes):

```bash
uv run --extra migration python scripts/bon_retire.py -c {CONNECTION} --stack {DATABASE}.{SCHEMA} > {OUT}/retire_plan.json
```

The plan lists every object to drop, with the exact SQL, and the rollback. Candidates are the layer BON replaces: `ONT_*` metadata tables (never `ONT_BON_MIGRATION_MAP`), `REL_EDGE_INFERRED`, `VW_ONT_*` and `REL_RESOLVED` views, graph-tool UDFs (`*_TOOL`), inference and constraint procedures (`SP_INFER*`, `SP_CHECK*`, `SP_RUN_ONTOLOGY*`), and the `*_ONTOLOGY_MODEL` / `*_METADATA_MODEL` semantic views. A candidate is **kept** when anything that stays depends on it, and the plan says why:

- a BON representation points at it (from the map table);
- the DDL of a kept view, semantic view, function, procedure, or a task in the schema names it (checked transitively, so `KG_NODE` / `KG_EDGE` and anything a KG loader reads survive);
- `SNOWFLAKE.CORE.GET_LINEAGE(..., 'DOWNSTREAM', 2)` reaches an object outside the candidate set;
- `--keep NAME` was passed.

Always kept: `KG_NODE`, `KG_EDGE`, `V_*` views, the data semantic view, `ONT_BON_MIGRATION_MAP`, and every non-candidate object.

The plan also lists every agent whose spec calls a candidate (`agents_using_dropped`). The BON agent created in M6 usually still calls the graph UDFs, and the original agent calls the ontology and metadata models. For each one, the user decides: keep those objects (`--keep GET_ANCESTORS_TOOL ...`), update the agent first, or accept the break (`--ack-agent DB.SC.AGENT`). `--execute` refuses (exit 2) while any agent is unresolved. Agents the current role cannot describe are listed with an error and do not block.

**Gate M7b**: show the drop list, the kept list with reasons, the agents, and the rollback. Options: "Archive and drop", "Change the plan", "Cancel". Only then:

```bash
uv run --extra migration python scripts/bon_retire.py -c {CONNECTION} --stack {DATABASE}.{SCHEMA} \
  --execute [--keep NAME ...] [--ack-agent DB.SC.AGENT ...]
```

It creates `{DATABASE}.{SCHEMA}_ONT_ARCHIVE` as a zero-copy clone of the whole schema, drops in dependency order (semantic views, views, functions, procedures, tables), then checks that every remaining view still compiles and every remaining semantic view still describes (`broken_after`). Exit 1 means something broke: show it and offer the rollback.

Rollback (whole schema): `DROP SCHEMA {DATABASE}.{SCHEMA}; ALTER SCHEMA {DATABASE}.{SCHEMA}_ONT_ARCHIVE RENAME TO {DATABASE}.{SCHEMA};`. Single object: `CREATE OR REPLACE TABLE {DATABASE}.{SCHEMA}.<T> CLONE {DATABASE}.{SCHEMA}_ONT_ARCHIVE.<T>`.

**Rehearse on a clone** when the user is unsure: `CREATE SCHEMA {DATABASE}.{SCHEMA}_M7TEST CLONE {DATABASE}.{SCHEMA}`, then run with `--target {DATABASE}.{SCHEMA}_M7TEST --no-archive --execute`, and drop the clone. Views in a clone still name the original schema in their DDL, so a rehearsal proves the drop list and order, not that the real schema's views keep compiling; `broken_after` on the real run does that.

## Resume

| State found | Resume at |
|---|---|
| `{OUT}/candidates.json` exists, no `apply_log.json` | M2 |
| `apply_log.json` exists, no `ONT_BON_MIGRATION_MAP` | M5 (then M4 if the manifest has no `metrics` key) |
| `ONT_BON_MIGRATION_MAP` exists with `MISSING` rows | M3 (re-apply), then M5 |
| Map table has no `METRIC` rows and the stack has a data semantic view | M4 |
| Map table all `MIGRATED` / `NOT_MIGRATED`, no BON agent yet | M6 |
| Agent cut over, `ONT_CLASS` still present | M7 |

A delta re-run is M1 -> M4 -> M3 apply -> M5: run `bon_import_metrics.py` after every `export_to_bon.py`, because the export rewrites the files without metrics.

## Incremental re-runs (delta sync)

While both systems coexist, rerun M1 -> M4 -> M3 -> M5. The exporter compares each source row's checksum with `ONT_BON_MIGRATION_MAP.SOURCE_CHECKSUM`. Unchanged rows are skipped, changed descriptions become `SYSTEM$UPDATE_GLOSSARY_TERM` patches (shown at M2 as old -> new, because changing a governed value is drift), and new rows are drafted. Removed source rows are reported, never deleted from BON automatically.

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
| `compare` reports `not_grounded_in_domain` right after M3 | the search index lags new domains (measured: 5+ minutes; reconcile already proves the nodes exist) | wait and re-run `compare`; it passes once the domain is indexed. Answers until then may come from an older domain covering the same subject |
| `Failed to login ... IP ... is not allowed` mid-run | account network policy | fix network access, then re-run the same step; every script is safe to re-run |
| Review gate lists `broken_bindings` | `ONT_CLASS_MAP` names a source table that was dropped or renamed; the generator's `V_{CLASS}` view selects from it and no longer compiles | the class still migrates without a table binding (never bound to the broken view). Repair the stack (point the map and view at the new table), then re-run M1 -> M3 -> M5: the delta adds the bindings. The base semantic view usually has the same drift; check it before M4 |
| `compare` reports `other_tool_errors` | a data tool failed (often the same dropped table) | not a migration failure; it usually hits both agents. Only `bon_tool_errors` fail M6 |
| `export_to_bon.py` exits 2: *ONT_ONTOLOGY holds N ontologies* | several ontologies share the schema | pass `--ontology NAME`; one domain per ontology. Properties and bindings of the other ontologies are left out, not reported as losses |
| `bon_import_metrics.py` imports 0 metrics, all *not bound to a migrated class* | the semantic view's base tables are in another schema than the bindings (often a cloned stack whose SV still names the original) | pass `--semantic-view` for the right view, or fix the view; metrics only attach to migrated classes |
| `bon_retire.py --execute` exits 2 | an agent still calls objects in the drop list | `--keep` those objects, update the agent, or `--ack-agent` it after the user agrees |
| `Failed to refresh session ... missing field sessionToken` | transient connector session error on long runs | re-run the same step; every script is safe to re-run |
