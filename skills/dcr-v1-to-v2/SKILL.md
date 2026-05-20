---
name: dcr-v1-to-v2
title: Migrate DCR V1 to V2
summary: Migrate a Snowflake Data Clean Room from V1 (SAMOOHA Provider/Consumer API) to V2 (Collaboration API).
description: "Use when migrating an existing Snowflake Data Clean Room from the V1 SAMOOHA Provider/Consumer API to the V2 Collaboration API. Discovers the V1 setup, maps constructs to V2, converts JinjaSQL templates (drops the join_policy filter), and emits provider/consumer setup scripts plus a validation checklist. Output is local files only — no live DDL or DML is executed. Triggers: DCR migration, V1 to V2, clean room upgrade, clean room migration, migrate DCR, upgrade clean room, SAMOOHA to Collaboration API."
tools:
  - snowflake_sql_execute
  - Bash
  - Read
  - Write
  - Edit
prompt: "Migrate my DCR V1 clean room to V2 Collaboration API."
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# DCR V1 → V2 Migration

## Overview

Guides migration of a Snowflake Data Clean Room from V1 (SAMOOHA Provider/Consumer API: `samooha_by_snowflake_local_db.provider.*` / `consumer.*`) to V2 (Collaboration API). Produces local SQL scripts and reports — never executes DDL/DML.

## Prerequisites

1. "Snowflake Data Clean Rooms" (SAMOOHA) installed on both provider and consumer accounts.
2. Quick Start completed on both accounts:
   ```sql
   USE ROLE ACCOUNTADMIN;
   ALTER APPLICATION IF EXISTS Snowflake_Data_Clean_Rooms RENAME TO SAMOOHA_BY_SNOWFLAKE;
   CALL SAMOOHA_BY_SNOWFLAKE.APP_SCHEMA.PREPARE_MOUNT_SCRIPT();
   EXECUTE IMMEDIATE FROM @SAMOOHA_BY_SNOWFLAKE.APP_SCHEMA.MOUNT_CODE_STAGE/dcr_loader.sql;
   USE ROLE SAMOOHA_APP_ROLE;
   CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.LIBRARY.CHECK_MOUNT_STATUS();  -- must return TRUE
   ```
3. SAMOOHA_APP_ROLE or ACCOUNTADMIN access to the V1 provider account.

## Workflow

Each step has a stopping point — confirm with the user before continuing.

### Step 0 — Connection Setup
Run `cortex connections list`, confirm the active connection is the V1 **provider** account, store it as `provider_connection`.

### Step 1 — Discovery
Load `discover/SKILL.md`. Inventory the V1 clean room: name, linked datasets, join policies, templates, consumer accounts. Output: `discovery_report.md`.

### Step 2 — Mapping
Map each V1 construct to V2. Reference `v1_v2_mapping.md`.

- **Parties:** V1 provider → V2 owner (`COLLABORATION.INITIALIZE`); V1 consumer → analysis runner (`COLLABORATION.JOIN`, `RUN`).
- **Datasets:** Each V1 linked dataset → `REGISTRY.REGISTER_DATA_OFFERING` with `allowed_analyses: template_only`. Wrap raw tables in secure views first.
- **Join policy:** V1 `set_join_policy` (TABLE:COLUMN) → per-column `schema_and_template_policies`: join keys → `passthrough`, dates → `timestamp`.
- **Templates:** Keep SQL logic. **Drop** the `| join_policy` filter — replace `IDENTIFIER({{ provider_id | join_policy }}) = IDENTIFIER({{ consumer_id | join_policy }})` with hardcoded columns (e.g., `p.EMAIL_HASH = c.EMAIL_HASH`). Keep `IDENTIFIER({{ source_table[0] }})` and bare `{{ param }}` unchanged. Version names must match `^[A-Za-z0-9_]{1,20}$` (no dots).

Output: `mapping_report.md`.

### Step 3 — Script Generation
Load `generate/SKILL.md`. Use `date +%Y%m%d` for the output dir (`YYYYMMDD_V1_to_V2_Output`). Emits `v2_provider_setup.sql`, `v2_consumer_setup.sql`, `v2_cleanup.sql`.

### Step 4 — Validation Checklist
Generate `validation_checklist.md` covering: pre-migration baseline, provider setup (data offerings, templates, `INITIALIZE`), consumer setup (`USE SECONDARY ROLES NONE`, `JOIN`, `LINK_LOCAL_DATA_OFFERING`), per-template result comparison, cutover.

### Step 5 — Cleanup Guidance
Generate `cleanup_guidance.md`. Recommend a 2–4 week parallel validation window. Document V1 teardown (`provider.drop_cleanroom`, `consumer.uninstall_cleanroom` — but **do not drop the provider DB**) and the V2 two-call async pattern: `COLLABORATION.LEAVE` → wait ~60s → call again → `LEFT`; `COLLABORATION.TEARDOWN` → wait ~60s → call again → `DROPPED`.

### Step 6 — Summary
List generated files and next steps.

## Common Mistakes

- **Linking raw PII tables.** Always wrap in secure views before registering as data offerings.
- **Leaving `| join_policy` in templates.** V2 has no equivalent — hardcode the join column.
- **Dots in version names.** `^[A-Za-z0-9_]{1,20}$` only — use underscores.
- **Missing `USE SECONDARY ROLES NONE`** before Collaboration API calls.
- **Skipping `LINK_LOCAL_DATA_OFFERING`** before the first `COLLABORATION.RUN`.
- **Forgetting LEAVE/TEARDOWN is two-call async.** First call sets `LEAVING`/`DROPPING`; wait ~60s; second call completes.
- **Missing `SAMOOHA_APP_ROLE` grants** (USAGE + SELECT + REFERENCE_USAGE WITH GRANT OPTION) on both provider and consumer DBs.
- **Using `VIEW_REGISTERED_DATA_OFFERINGS` post-INITIALIZE.** It returns empty — the spec consumes registry entries. Use `COLLABORATION.VIEW_COLLABORATIONS()`.
- **Calling `COLLABORATION.REVIEW` after JOIN.** REVIEW is pre-JOIN only.
- **Dropping the V1 provider DB.** V2 uses it in place.
- **Executing the generated SQL automatically.** This skill outputs files only — the user runs them manually.

## Output Files

| File | Description |
|---|---|
| `discovery_report.md` | V1 inventory |
| `mapping_report.md` | V1→V2 construct mapping |
| `v2_provider_setup.sql` | V2 provider setup |
| `v2_consumer_setup.sql` | V2 consumer setup |
| `v2_cleanup.sql` | V2 LEAVE/TEARDOWN script |
| `validation_checklist.md` | Pre/post checklist |
| `cleanup_guidance.md` | V1 archive + V2 cleanup |

## Sub-Skill Files

- `discover/SKILL.md` — V1 introspection queries
- `generate/SKILL.md` — V2 script generation
- `v1_v2_mapping.md` — Reference mapping table
