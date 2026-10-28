# Migrate an ontology stack to Snowflake Business Ontology

**Who this is for:** customers who used the **ontology-stack-builder** skill (or the [ontology-on-snowflake](https://github.com/Snowflake-Labs/ontology-on-snowflake) reference repo) to build ontology assets in Snowflake, and now want to move to the native **Snowflake Business Ontology (BON)** product.

You have one of these stacks if your schema contains `ONT_CLASS`, `ONT_RELATION_DEF` and the other `ONT_*` metadata tables, usually with `KG_NODE` / `KG_EDGE`, `V_*` views, an ontology semantic view and a Cortex Agent.

**What you get:** the business meaning of your ontology (classes, hierarchy, relationships, descriptions, which tables and columns implement each concept, and metrics) as governed BON nodes, a new Cortex Agent that uses BON search, and proof that nothing was lost. Your existing stack and agent keep working throughout. Nothing is dropped unless you explicitly ask for it at the last step.

If you are starting fresh and have no stack yet, you do not need this: use the `business-ontology` skill directly.

---

## Contents

1. [Prerequisites](#1-prerequisites)
2. [Quick start](#2-quick-start)
3. [The steps, one by one](#3-the-steps-one-by-one)
4. [What moves and what stays](#4-what-moves-and-what-stays)
5. [Running the scripts by hand](#5-running-the-scripts-by-hand)
6. [Re-runs while both systems coexist](#6-re-runs-while-both-systems-coexist)
7. [Rollback](#7-rollback)
8. [Testing checklist](#8-testing-checklist)
9. [Known limits](#9-known-limits)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Prerequisites

| Requirement | How to check |
|---|---|
| **Business Ontology enabled on the account.** At the time of writing BON is in Private Preview; your account team enables it. | `SELECT SYSTEM$GET_GLOSSARY_SUMMARY();` returns JSON with `"error":false`. A feature-gate error means it is not enabled. |
| **An ontology stack** in one schema | `SHOW TABLES LIKE 'ONT_CLASS' IN SCHEMA MY_DB.MY_SCHEMA;` returns a row |
| **A role** that can read the stack schema, create tables and agents in it, and create and approve BON domains, nodes, relationships and representations | All tests so far used `ACCOUNTADMIN`. A least-privilege role has not been verified yet. |
| **Cortex Code** with this skill installed | `$ontology-stack-builder` is available in Cortex Code |
| **`uv`** and **Python 3.10+** on the machine running Cortex Code | `uv --version` |
| **A Snowflake connection** in `~/.snowflake/connections.toml` | `snow connection list`, or the connection picker in Cortex Code |
| **Recommended: the `business-ontology` skill** installed. It provides `scripts/ontology_rule_check.py`, the BON quality gate used in M3 and M4; without it the gate is skipped and only the API's own checks apply. | `~/.snowflake/cortex/skills/business-ontology` exists, or `$business-ontology` loads in Cortex Code |

---

## 2. Quick start

In Cortex Code, say:

```
$ontology-stack-builder Migrate my ontology stack in MY_DB.MY_SCHEMA to Business Ontology
```

The skill detects your stack and walks you through the steps below. It stops and asks at every decision point (gate). Plan on 15 to 30 minutes for a stack of about 10 classes, most of it waiting at gates and for the agent comparison.

---

## 3. The steps, one by one

Each step lists what happens, what you are asked, and what success looks like. The example numbers come from a real run on a stock market stack (KG path, 8 classes).

### M0. Preflight (read-only)

**What happens:** the skill checks that BON is enabled, finds the `ONT_*` tables, the ontology name(s), your semantic views and your agent, and lists your existing BON domains.

**You are asked:** *Which BON domain should this go into?* Pick an existing domain, or create a new one. The domain is the business area your nodes live in (for example "Stock Ontology KG").

If the schema holds more than one ontology, you are also asked which one. Migrate each ontology into its own domain, one run each.

**Success:** a summary such as "1 ontology STOCK, 14 ONT_* tables, 3 semantic views, agent MY_ONT_AGENT".

### M1. Extract (read-only)

**What happens:** the skill reads your `ONT_*` tables and converts them to BON candidates following [MAPPING.md](MAPPING.md). It works with all four stack layouts it knows (built by this skill, by the template, by the reference repo, or ID-keyed). It also writes a **loss report** listing everything BON cannot hold.

**Success:** counts such as *8 nodes, 10 relationships (5 HAS_VARIANT, 5 CUSTOM), 28 representations, 2 not migrated*.

### M2. Review (gate)

**You are shown:** the counts, a table of how each of your relationships maps to a BON relationship type, classes with weak generated descriptions ("Entity from table X"), placeholder root classes such as `Entity`, and the loss report headline.

**You are asked:**
- for each relationship mapped to `CUSTOM` (business verbs like `listed on`, `plays for`): keep it as `CUSTOM` with the verb as label, or map it to a standard type;
- whether to improve weak descriptions now or later in Snowsight;
- *Migrate these N nodes, R relationships and A representations into the domain?* Options: make them active, save them as drafts for review in Snowsight, or change something first.

### M3. Apply (writes to BON)

**What happens:**
1. The BON quality gate checks the candidates. Blocking findings stop the run; others are shown so you can decide.
2. The skill creates the domain if needed, drafts each node, then approves **only the items it drafted**. It never bulk-approves other people's pending drafts.
3. It drafts and approves the relationships, then the representations.

If you chose "save as drafts", it stops after the node drafts. You review them in Snowsight, then re-run to approve and write the relationships.

**Success:** *nodes 8 drafted / 8 approved, relationships 10 / 10, representations 28 / 28, 0 failed.*

### M4. Metrics (writes to BON)

**What happens:** the skill reads the metrics in your data semantic view. That is the base view on the direct path, or `{ONT}_KG_MODEL` on the KG path. For each metric it:
- creates a `METRIC` node with the formula;
- links it with `MEASURES` to the class it measures;
- links it to the semantic view.

**What is skipped:** plain counts whose description only restates the name (*Company Count = `COUNT(DISTINCT NODE_ID)`, "Count of distinct companies"*). BON's governance rules reject those because they add nothing beyond the semantic view, where they stay. You can choose to import them anyway.

**Success:** a list of the metrics imported, each with the class it measures, and a list of the metrics skipped with the reason. "0 metrics to import" is a valid result when the view has only counts or dimensions.

### M5. Reconcile (read-back proof)

**What happens:** the skill reads the domain back from BON and compares it with what was extracted. It records every source object in a new table `MY_DB.MY_SCHEMA.ONT_BON_MIGRATION_MAP`, with its BON ID, status (`MIGRATED`, `MISSING`, `NOT_MIGRATED`) and the original row. This table is your audit trail.

**Success:** parity 100%, for example *nodes 8/8, relationships 10/10, representations 28/28*. Anything `MISSING` is re-applied once automatically.

**You are asked:** continue to the agent cutover, or stop here (BON populated, nothing else changed).

### M6. Agent cutover (creates a new agent)

**What happens:** the skill copies your agent to a new one (for example `MY_BON_AGENT`). In the copy it removes the ontology and metadata query tools, keeps your data and graph tools, and adds native BON search. **Your original agent is not modified.**

It then asks both agents the same questions (at least one *"What does X mean?"*) and writes a side-by-side comparison, `agent_compare.md`.

**Wait 5+ minutes after M3 before comparing.** BON search needs that long to index a new domain. If the comparison says an answer was not grounded in your domain, wait and run it again.

**Success:** no BON tool errors, and every BON answer draws on your domain.

**You are asked:** keep the new agent, or drop it.

### M7. Retire the legacy layer (optional)

Most customers skip this at first and keep the old stack running for a release cycle.

**What happens:**
1. **Plan (read-only).** The skill lists exactly what it would drop: the ontology and metadata semantic views, `ONT_*` metadata tables, `VW_ONT_*` views, graph UDFs and inference procedures. It keeps anything that something else still depends on, and says why.
2. It also lists every agent that still calls an object on the drop list. It refuses to drop while any agent is unresolved. You can keep those objects (the new BON agent usually still uses the graph UDFs), update the agent, or accept the break.
3. **Execute, after a second confirmation.** It makes a zero-copy archive of the whole schema (`MY_SCHEMA_ONT_ARCHIVE`), drops in dependency order, and checks that every remaining view and semantic view still works.

Always kept: `KG_NODE`, `KG_EDGE`, the `V_*` views, your data semantic view and `ONT_BON_MIGRATION_MAP`.

**Tip:** rehearse on a clone first. See [Rollback](#7-rollback).

---

## 4. What moves and what stays

| Your stack | In Business Ontology |
|---|---|
| Each class (`ONT_CLASS`) | `ENTITY` node, with the description |
| Parent/child classes | `HAS_VARIANT` relationship (parent has variant child) |
| Relationships (`ONT_RELATION_DEF`) | Typed relationship (`HAS_PART`, `CLASSIFIES`, ... or `CUSTOM` with your verb as label). An inverse pair (`listed_on` / `hosts_listing`) becomes one edge. |
| Class-to-table mapping (`ONT_CLASS_MAP`) | Representation linking the node to the table or view |
| Properties (`ONT_PROPERTY`) | Column representations on the class node |
| Derived properties with SQL | `METRIC` (plain aggregates) or `TERM` nodes, named `{Class} {Property}` |
| Semantic view metrics | `METRIC` node + `MEASURES` edge + semantic view link |

**Stays where it is:** the `KG_NODE` / `KG_EDGE` data, your semantic views (dimensions, facts, filters, verified queries), inference rules and procedures, graph UDFs, SPCS services, and any staged semantic model YAML.

**Cannot be represented in BON** (all listed in the loss report and the map table): cardinality, transitivity and inverse flags; self-referential relationships such as `Company related_to Company` (BON needs two different endpoints); row-level permissions; and function-backed derived properties.

Full contract: [MAPPING.md](MAPPING.md).

---

## 5. Running the scripts by hand

The skill runs these for you. To run them yourself (for CI or debugging), run from the skill root (`skills/ontology-stack-builder/`). Every script is safe to re-run.

```bash
CONN=my_connection; DB=MY_DB; SC=MY_SCHEMA; DOM="My Domain"; OUT=/tmp/bon_migration/my_stack
R="uv run --extra migration python"

# M1 extract (add --ontology NAME if the schema holds several)
$R scripts/export_to_bon.py -c $CONN --database $DB --schema $SC --domain "$DOM" --out $OUT

# M3 quality gate (needs the business-ontology skill) and apply
BO=~/.snowflake/cortex/skills/business-ontology
python3 $BO/scripts/ontology_rule_check.py -c $CONN --domain "$DOM" --proposals-file $OUT/candidates.json \
  --object-type node --candidate-count $(python3 -c "import json;print(len(json.load(open('$OUT/candidates.json'))))")
python3 $BO/scripts/ontology_rule_check.py -c $CONN --domain "$DOM" --proposals-file $OUT/relationships.json --object-type relationship
$R scripts/bon_apply.py -c $CONN --input-dir $OUT --domain "$DOM"          # add --save-as-draft or --dry-run

# M4 metrics, then apply again
$R scripts/bon_import_metrics.py -c $CONN --input-dir $OUT                  # add --include-trivial to keep plain counts
$R scripts/bon_apply.py -c $CONN --input-dir $OUT --domain "$DOM"

# M5 reconcile (writes ONT_BON_MIGRATION_MAP)
$R scripts/bon_reconcile.py -c $CONN --database $DB --schema $SC --domain "$DOM" --input-dir $OUT

# M6 agent cutover (wait 5+ minutes after apply before compare)
$R scripts/bon_agent_cutover.py create -c $CONN --source $DB.$SC.MY_AGENT --target $DB.$SC.MY_BON_AGENT --domain "$DOM" --out $OUT
$R scripts/bon_agent_cutover.py compare -c $CONN --before $DB.$SC.MY_AGENT --after $DB.$SC.MY_BON_AGENT \
  -q "What does <concept> mean?" -q "<a data question>" --domain "$DOM" --out $OUT

# M7 retire: plan (read-only), then execute after review
$R scripts/bon_retire.py -c $CONN --stack $DB.$SC
$R scripts/bon_retire.py -c $CONN --stack $DB.$SC --execute [--keep NAME ...] [--ack-agent DB.SC.AGENT ...]
```

Output files in `$OUT`:

| File | Content |
|---|---|
| `candidates.json`, `relationships.json`, `associations.json` | What will be written to BON |
| `migration_loss_report.md` | Everything that has no BON home |
| `export_manifest.json` | Detected layout, counts, per-object actions |
| `apply_log.json` | Every BON call and response |
| `reconcile_report.md` | Parity table |
| `agent_compare.md` | Before/after agent answers |

---

## 6. Re-runs while both systems coexist

If you keep changing the stack after migrating, re-run **extract → metrics → apply → reconcile**. The map table remembers what was migrated:
- unchanged objects are skipped;
- changed descriptions or formulas become updates, shown to you first;
- new classes, relationships and metrics are added;
- objects removed from the stack are reported but **never deleted from BON automatically**.

Always run the metrics step after extract: extract rewrites its files without the metrics.

---

## 7. Rollback

| Stage | How to undo |
|---|---|
| M1-M2 | Nothing was written. Delete the output folder. |
| M3-M5 | Delete the BON domain's nodes (in Snowsight, or with the `business-ontology` skill's delete workflow), and `DROP TABLE MY_DB.MY_SCHEMA.ONT_BON_MIGRATION_MAP`. Your stack was never changed. |
| M6 | `DROP AGENT MY_DB.MY_SCHEMA.MY_BON_AGENT`. The original agent was never changed. |
| M7, whole schema | `DROP SCHEMA MY_DB.MY_SCHEMA; ALTER SCHEMA MY_DB.MY_SCHEMA_ONT_ARCHIVE RENAME TO MY_DB.MY_SCHEMA;` |
| M7, one object | `CREATE OR REPLACE TABLE MY_DB.MY_SCHEMA.<T> CLONE MY_DB.MY_SCHEMA_ONT_ARCHIVE.<T>;` |

**To rehearse M7 safely:**

```sql
CREATE SCHEMA MY_DB.MY_SCHEMA_M7TEST CLONE MY_DB.MY_SCHEMA;
```
```bash
uv run --extra migration python scripts/bon_retire.py -c $CONN --stack MY_DB.MY_SCHEMA \
  --target MY_DB.MY_SCHEMA_M7TEST --no-archive --execute [--keep ...] [--ack-agent ...]
```
```sql
DROP SCHEMA MY_DB.MY_SCHEMA_M7TEST;
```

Views in a clone still name the original schema, so a rehearsal proves the drop list and order, not that your real views keep compiling. The real run checks that afterwards and reports anything broken.

---

## 8. Testing checklist

For colleagues validating this branch:

- [ ] Unit tests pass without Snowflake: `uv run --extra dev pytest tests/` (25 tests)
- [ ] M0 detects your stack, ontology and agent
- [ ] M1 counts match what you expect from your `ONT_*` tables; the loss report makes sense
- [ ] M3 apply reports 0 failed
- [ ] M4 maps each metric to the right class, or skips it with a clear reason
- [ ] M5 parity is 100% and `ONT_BON_MIGRATION_MAP` has one row per source object
- [ ] The domain looks right in Snowsight (nodes, relationships, linked tables and columns)
- [ ] M6 comparison: no BON tool errors, answers grounded in your domain (after the 5-minute index wait)
- [ ] A second run of M1 → M5 reports everything as `skip` (no duplicates)
- [ ] M7 plan only: the drop and keep lists are right for your stack (optional: rehearse on a clone)
- [ ] Report anything unclear or wrong, with the step, the command and its output

Tested so far:

| Stack | Layout | Result |
|---|---|---|
| Soccer stack | reference repo | 13 nodes / 18 relationships / 20 representations, parity 100% |
| Stock stack | direct path | 9 / 12 / 17, parity 100% |
| Stock stack | KG path | 8 / 10 / 28, parity 100%; run end to end by a fresh agent from these docs alone |

These were also exercised: drafts, migrating into an existing domain, multi-ontology schemas, live metrics, and M7 on a clone.

---

## 9. Known limits

- **Preview status.** At the time of writing BON is in Private Preview, so API behavior can change. The new agent needs the preview flag `EnableSnowscopeBusinessOntologySearch`, which the skill sets.
- **Search index lag.** New domains take 5+ minutes to appear in BON search; deletes take longer.
- **BON search spans all domains.** The new agent is instructed to put your domain name in every BON query, so results from other domains do not crowd yours out.
- **Staged semantic model YAML** (`@...SEMANTIC_MODELS/*.yml`, used by reference-repo stacks) is out of scope. Metrics, filters and verified queries stay with the semantic layer.
- **Self-referential relationships** are not migrated (BON limitation); they are recorded as `NOT_MIGRATED`.
- **One ontology per run, one domain per ontology.**
- **Least-privilege role** not yet documented; testing used `ACCOUNTADMIN`.

---

## 10. Troubleshooting

The full table is in [SKILL.md § Troubleshooting](SKILL.md#troubleshooting). The most common problems:

| You see | Do this |
|---|---|
| Feature-gate error on any `SYSTEM$...GLOSSARY` call | BON is not enabled; contact your account team |
| *ONT_ONTOLOGY holds 2 ontologies* | Re-run with `--ontology NAME`, one domain each |
| Review lists `broken_bindings` | A source table behind a class was dropped or renamed. The class still migrates without the table link; fix the stack and re-run to add it |
| Agent comparison: `not_grounded_in_domain` | Search index lag: wait 5+ minutes and re-run `compare` |
| Agent BON tool: *corpus is experimental and currently disabled* | The agent lacks the preview flag; use an agent created by `bon_agent_cutover.py create` |
| `bon_retire.py --execute` exits 2 | An agent still uses objects on the drop list: `--keep` them, update the agent, or `--ack-agent` it |
| Login or session errors mid-run | Transient; re-run the same step |
