# DCR V1 → V2 Migration Skill — User Guide

## What This Skill Does

This Cortex Code Skill automates the migration of a Snowflake Data Clean Room from
**V1** (SAMOOHA Provider/Consumer API) to **V2** (Collaboration API). It walks you through
discovery, mapping, script generation, validation, and cleanup — producing local SQL files
you review and run yourself. The skill never executes DDL or DML.

---

## What "V1" and "V2" Mean in This Skill

Both V1 and V2 use the same SAMOOHA app (`SAMOOHA_BY_SNOWFLAKE` + `SAMOOHA_BY_SNOWFLAKE_LOCAL_DB`).
The difference is which API schema they call:

| Version | API | Provider example | Consumer example |
|---|---|---|---|
| **V1** | SAMOOHA Provider/Consumer API | `samooha_by_snowflake_local_db.provider.cleanroom_init(...)` | `samooha_by_snowflake_local_db.consumer.run_analysis(...)` |
| **V2** | Collaboration API | `SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.INITIALIZE(...)` | `SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.RUN(...)` |

> **Note:** This skill migrates V1 → V2. If your setup uses raw Snowflake shares
> (no SAMOOHA app), that is a different migration path not covered here.

---

## Installation

Copy the skill directory into your Cortex Code skills folder:

```bash
cp -r DCR_V1_to_V2/ ~/.snowflake/cortex/skills/dcr-v1-to-v2/
```

Verify it appears:

```bash
ls ~/.snowflake/cortex/skills/dcr-v1-to-v2/
# Expected: SKILL.md  discover/  generate/  v1_v2_mapping.md  user_guide.md
```

The skill is available in your next Cortex Code session.

---

## Trigger Phrases

Say any of the following to invoke the skill (do not trigger yet):

- `/dcr-v1-to-v2`
- `Migrate my DCR from V1 to V2`
- `Upgrade my clean room from Provider/Consumer API to Collaboration API`
- `Convert my cleanroom to the Collaboration API`
- `DCR V1 to V2 migration`
- `Migrate from SAMOOHA Provider/Consumer to Collaboration API`

---

## Prerequisites

Before starting:

1. **V1 provider account access** — SAMOOHA_APP_ROLE or ACCOUNTADMIN to run discovery queries
   > **Note:** Connecting to the V1 provider account may require MFA. Check your device for an
   > authentication prompt when establishing the connection.
2. **"Snowflake Data Clean Rooms" (SAMOOHA) installed** on both provider and consumer accounts
3. **Quick Start completed** on both accounts:
   ```sql
   USE ROLE ACCOUNTADMIN;
   ALTER APPLICATION IF EXISTS Snowflake_Data_Clean_Rooms RENAME TO SAMOOHA_BY_SNOWFLAKE;
   CALL SAMOOHA_BY_SNOWFLAKE.APP_SCHEMA.PREPARE_MOUNT_SCRIPT();
   EXECUTE IMMEDIATE FROM @SAMOOHA_BY_SNOWFLAKE.APP_SCHEMA.MOUNT_CODE_STAGE/dcr_loader.sql;
   USE ROLE SAMOOHA_APP_ROLE;
   CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.LIBRARY.CHECK_MOUNT_STATUS();  -- must return TRUE
   ```
4. **The V1 clean room is active and functional** — capture baseline analysis results before migrating

> **Important:** This skill runs entirely from the **V1 provider account**. You do not need to run
> it from the consumer account. The generated consumer setup file (`v2_consumer_setup.sql`) is
> handed off to the consumer to run on their account separately.

---

## How It Works

The skill runs in **7 steps (0–6)** with 4 stopping points where you confirm before proceeding.

### Step 0 — Connection Setup

The skill checks the active Snowflake connection and asks you to confirm it is your V1
**provider** account. All discovery queries run against the confirmed connection.

**You confirm:** Which connection to use.

### Step 1 — Discovery

Read-only SAMOOHA introspection calls build a complete V1 inventory:

- `provider.view_cleanrooms()` — clean room name, state, consumer accounts
- `provider.view_provider_datasets()` — linked data objects (tables and secure views)
- `provider.view_join_policy()` — join columns per dataset
- `provider.view_added_templates()` — template names
- Template body retrieval from clean room app package (or user-provided from original scripts)
- Provider DB table and column structures

**You review:** A `discovery_report.md`. Confirm before continuing.

### Step 2 — Mapping

Each V1 construct maps to its V2 equivalent:

| V1 | V2 |
|---|---|
| `library.register_schema` | Explicit SAMOOHA_APP_ROLE grants (USAGE + SELECT + REFERENCE_USAGE WITH GRANT OPTION) |
| `provider.cleanroom_init` | `COLLABORATION.INITIALIZE` (YAML spec) |
| `provider.link_datasets` | `REGISTRY.REGISTER_DATA_OFFERING` |
| `provider.set_join_policy` | `schema_and_template_policies` in data offering YAML |
| `provider.add_custom_sql_template` | `REGISTRY.REGISTER_TEMPLATE` (YAML with template body) |
| `provider.add_consumers` + `create_or_update_cleanroom_listing` | `collaborator_identifier_aliases` in INITIALIZE spec |
| `consumer.install_cleanroom` | `COLLABORATION.JOIN` |
| `consumer.link_datasets` | `REGISTRY.REGISTER_DATA_OFFERING` + `COLLABORATION.LINK_LOCAL_DATA_OFFERING` |
| `consumer.run_analysis(name, tmpl, [consumer], [provider], args)` | `COLLABORATION.RUN(name, tmpl_id, [provider], [consumer], args)` |
| `provider.drop_cleanroom` | `COLLABORATION.TEARDOWN` (two-call async) |
| `consumer.uninstall_cleanroom` | `COLLABORATION.LEAVE` (two-call async) |

Each template is classified as **auto-portable** or **stub-required**.

**You review:** A mapping table. Confirm before continuing.

### Step 3 — Script Generation

Three SQL files are generated:

| File | Contents |
|---|---|
| `v2_provider_setup.sql` | Warehouse, SAMOOHA_APP_ROLE grants, data offerings, templates, COLLABORATION.INITIALIZE, JOIN, validation |
| `v2_consumer_setup.sql` | Warehouse, SAMOOHA_APP_ROLE grants, COLLABORATION.JOIN, data offering, LINK_LOCAL_DATA_OFFERING, RUN calls, validation |
| `v2_cleanup.sql` | COLLABORATION.LEAVE (consumer, two calls) and COLLABORATION.TEARDOWN (provider, two calls) |

**You review:** Confirm the output directory before files are written.

### Step 4 — Validation Checklist

A `validation_checklist.md` covers:
- Prerequisites (SAMOOHA install, Quick Start, SAMOOHA_APP_ROLE grants)
- Provider and consumer setup verification
- LINK_LOCAL_DATA_OFFERING confirmation
- Result comparison (V1 vs V2 for each template)
- Cutover readiness

### Step 5 — Cleanup Guidance

A `cleanup_guidance.md` covers:
- V1 archive recommendations (keep for rollback during validation period)
- V1 cleanup SQL (`provider.drop_cleanroom`, `consumer.uninstall_cleanroom`)
- V2 cleanup SQL (LEAVE/TEARDOWN two-call async pattern)

### Step 6 — Summary

Lists all generated files and the next steps to execute the migration.

---

## Output Files

```
<output_dir>/
  discovery_report.md       — V1 inventory
  mapping_report.md         — V1→V2 construct mapping
  v2_provider_setup.sql     — V2 provider setup (Collaboration API)
  v2_consumer_setup.sql     — V2 consumer setup (Collaboration API)
  v2_cleanup.sql            — V2 cleanup (LEAVE/TEARDOWN two-call pattern)
  validation_checklist.md   — Pre/post migration checklist
  cleanup_guidance.md       — V1 archive and V2 decommission steps
```

---

## Key Concepts

### SAMOOHA_APP_ROLE Grants

V1 used `library.register_schema` to grant privileges automatically. V2 requires explicit grants
on both provider and consumer DBs before `INITIALIZE` or `JOIN`. The critical grant is:

```sql
GRANT REFERENCE_USAGE ON DATABASE <db> TO ROLE SAMOOHA_APP_ROLE WITH GRANT OPTION;
```

This enables the SAMOOHA backend to share data object references cross-account.
Without it, `INITIALIZE` and `JOIN` will fail.

### USE SECONDARY ROLES NONE

All Collaboration API calls require `USE SECONDARY ROLES NONE` in the session before calling:
`INITIALIZE`, `JOIN`, `RUN`, `LEAVE`, `TEARDOWN`, `LINK_LOCAL_DATA_OFFERING`.
Forgetting this causes permission errors even when SAMOOHA_APP_ROLE is active.

### join_policy Filter Removal

V1 templates use `{{ provider_id | join_policy }}` / `{{ consumer_id | join_policy }}` to
enforce the join column at runtime. V2 has no `join_policy` filter — the join column is
hardcoded in the template body:

```sql
-- V1 template body
ON IDENTIFIER({{ provider_id | join_policy }}) = IDENTIFIER({{ consumer_id | join_policy }})

-- V2 template body (column name from view_join_policy)
ON p.EMAIL_HASH = c.EMAIL_HASH
```

Also: remove `provider_id` and `consumer_id` from the `OBJECT_CONSTRUCT()` in `COLLABORATION.RUN`.

### Arg Order Reversal

**V1 `run_analysis`:** consumer tables (arg 3) → provider tables (arg 4)
**V2 `COLLABORATION.RUN`:** provider source_tables (arg 3) → consumer my_tables (arg 4)

This is reversed. Swapping these two args is one of the most common migration errors.

### LINK_LOCAL_DATA_OFFERING

After joining and registering their data offering, the consumer must call
`COLLABORATION.LINK_LOCAL_DATA_OFFERING` before the first `COLLABORATION.RUN`. This creates
data access views in the collaboration-local database. Without this step, `RUN` fails
with "Object does not exist."

### LEAVE/TEARDOWN Two-Call Pattern

V2 cleanup is asynchronous:
1. First call → status changes to LEAVING (consumer) or DROPPING (provider)
2. Wait ~60 seconds
3. Check `GET_STATUS` — when status is `LOCAL_DROP_PENDING`, call again
4. Second call completes cleanup (LEFT / DROPPED)

The consumer must complete LEAVE before the provider runs TEARDOWN.

### Version Naming

V2 offering and template versions must match `^[A-Za-z0-9_]{1,20}$`:
- No dots, hyphens, or spaces
- Max 20 characters
- Recommended format: `YYYYMMDD_V1` (13 chars)

Once a version is registered, it is immutable. Use bumped version strings for updates.

---

## Limitations

- **No live execution.** The skill generates files — you run the SQL yourself.
- **Complex template logic requires manual porting.** The skill generates stubs for templates
  containing UDFs or procedural logic.
- **Collaborator list is fixed after INITIALIZE.** All participants must be known before
  creating the collaboration.
- **Template versions are immutable.** Once registered, a version cannot be overridden.
- **Non-standard V1 implementations** are out of scope.

---

## Skill File Structure

```
dcr-v1-to-v2/
  SKILL.md                   — Main entry point (7-step workflow)
  skill_evidence.yaml        — Skill metadata
  discover/SKILL.md          — Sub-skill: V1 SAMOOHA introspection queries
  discover/skill_evidence.yaml
  generate/SKILL.md          — Sub-skill: V2 Collaboration API script generation
  generate/skill_evidence.yaml
  v1_v2_mapping.md           — Reference: V1→V2 mapping tables and conversion rules
  user_guide.md              — This file
```

| File | Lines | Role |
|---|---|---|
| `SKILL.md` | ~307 | Orchestrates the full 7-step workflow; handles Steps 2/4/5/6 inline |
| `discover/SKILL.md` | ~262 | Step 1: V1 SAMOOHA introspection discovery |
| `generate/SKILL.md` | ~741 | Step 3: Collaboration API script generation with template conversion |
| `v1_v2_mapping.md` | ~293 | Reference mapping tables (used by Steps 2 and 3) |
| `user_guide.md` | — | This user-facing guide |
