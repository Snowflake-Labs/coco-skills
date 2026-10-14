---
name: dcr-v1-to-v2
title: DCR V1 to V2 Migration
summary: Migrate a Snowflake Data Clean Room from V1 SAMOOHA Provider/Consumer API to V2 Collaboration API.
description: "Use when migrating a Snowflake Data Clean Room from V1 (SAMOOHA Provider/Consumer API) to V2 (Collaboration API). Discovers V1 setup via introspection queries, maps constructs to Collaboration API equivalents, converts JinjaSQL templates, and generates provider/consumer setup plus cleanup scripts. Output is local files only — no live DDL/DML. Triggers: DCR migration, V1 to V2, clean room upgrade, clean room migration, migrate DCR, upgrade clean room, SAMOOHA to Collaboration API."
tools:
  - snowflake_sql_execute
  - Bash
  - Read
  - Write
  - Edit
prompt: Migrate my V1 Data Clean Room to V2 Collaboration API.
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# DCR V1 to V2 Migration

## Overview

Guides migration of a Snowflake Data Clean Room from **V1** (SAMOOHA Provider/Consumer API) to **V2** (Collaboration API). Produces local SQL scripts and reports — never executes DDL/DML against your accounts. Use this when you have an existing V1 clean room (calls into `samooha_by_snowflake_local_db.provider.*` / `consumer.*`) and want a generated V2 setup plus a side-by-side validation plan.

**In scope:** V1 inventories, JinjaSQL template conversion (drops the `join_policy` filter), provider + consumer setup scripts, cleanup scripts, validation checklist.

**Out of scope:** executing generated SQL, V0 direct-share setups, non-Snowflake clean rooms.

## Prerequisites

1. "Snowflake Data Clean Rooms" (SAMOOHA) installed on both accounts.
2. Quick Start completed on both (`CHECK_MOUNT_STATUS()` returns TRUE).
3. Access to the V1 provider account with `SAMOOHA_APP_ROLE` or `ACCOUNTADMIN`.

## Workflow

### Step 0 — Connection setup

Run `cortex connections list`, identify the active connection, confirm with the user that it points to the V1 **provider** account, and store it as `provider_connection`.

⚠️ STOPPING POINT: Do not run discovery queries until the user confirms the connection.

### Step 1 — Discovery

Load `discover/INSTRUCTIONS.md` and execute its workflow using `provider_connection`. Output: `discovery_report.md` (clean room name, linked datasets, join policies, templates, consumer accounts).

⚠️ STOPPING POINT: Present the discovery report and confirm before mapping.

### Step 2 — Mapping

Map each V1 construct to V2. Reference `v1_v2_mapping.md` for the full table.

- **Parties:** V1 provider → V2 owner (`COLLABORATION.INITIALIZE`); V1 consumer → V2 runner (`COLLABORATION.JOIN`, `COLLABORATION.RUN`).
- **Datasets:** secure view → `REGISTRY.REGISTER_DATA_OFFERING` with `allowed_analyses: template_only`. Wrap raw tables in a secure view first. Data stays in the provider DB.
- **Join policy:** V1 `set_join_policy` → V2 `schema_and_template_policies` per column (`passthrough` for join keys, `timestamp` for date columns).
- **Templates:** keep SQL logic; **remove** `| join_policy` filter and replace with hardcoded join columns (e.g. `ON p.EMAIL_HASH = c.EMAIL_HASH`); keep `IDENTIFIER({{ source_table[0] }})` and bare `{{ param }}` unchanged; drop `provider_id`/`consumer_id` from analysis args. Version regex: `^[A-Za-z0-9_]{1,20}$`. Template ID: `name_version`.
- **Consumer datasets:** V1 `link_datasets` → V2 `REGISTER_DATA_OFFERING` + `LINK_LOCAL_DATA_OFFERING`.

⚠️ STOPPING POINT: Confirm mapping decisions before generating scripts.

### Step 3 — Script generation

Load `generate/INSTRUCTIONS.md`. Get today's date with `date +%Y%m%d` (never hardcode). Suggest output dir `YYYYMMDD_V1_to_V2_Output`.

⚠️ STOPPING POINT: Confirm output directory before writing files.

Generate: `v2_provider_setup.sql`, `v2_consumer_setup.sql`, `v2_cleanup.sql`, `mapping_report.md`.

### Step 4 — Validation checklist

Generate `validation_checklist.md` covering pre-migration baseline, provider setup (grants `USAGE + SELECT + REFERENCE_USAGE WITH GRANT OPTION`, registered offerings/templates, `INITIALIZE`), consumer setup (`USE SECONDARY ROLES NONE`, `LINK_LOCAL_DATA_OFFERING` before first `RUN`, 3-part IDs `ALIAS.OFFERING_ID.DATASET_ALIAS`), and per-template result comparison V1 vs V2.

### Step 5 — Cleanup guidance

Generate `cleanup_guidance.md`. Recommend a 2–4 week coexistence period. Include V1 teardown (`provider.drop_cleanroom`, `consumer.uninstall_cleanroom` — keep the provider DB) and V2 teardown using the **two-call async pattern**: first `COLLABORATION.LEAVE`/`TEARDOWN` → status `LEAVING`/`DROPPING` → wait ~60s → second call → `LEFT`/`DROPPED`.

### Step 6 — Summary

Print generated file list and next steps (run provider setup, run consumer setup, compare results, then cleanup).

## Stopping Points

- Step 0 — confirm provider connection before any query
- Step 1 — confirm discovery inventory before mapping
- Step 2 — confirm mapping decisions before generation
- Step 3 — confirm output directory before writing files

## Common Mistakes

- **Executing generated SQL automatically.** Always hand off files for human review.
- **Linking raw tables.** Wrap PII-bearing tables in a secure view first.
- **Leaving `| join_policy` in converted templates.** Replace with the literal join column.
- **Dots in version names.** Regex is `^[A-Za-z0-9_]{1,20}$` — use underscores.
- **Missing role grants.** Without `USAGE + SELECT + REFERENCE_USAGE WITH GRANT OPTION` on `SAMOOHA_APP_ROLE`, `INITIALIZE`/`JOIN` fails.
- **Forgetting `LINK_LOCAL_DATA_OFFERING`.** First `COLLABORATION.RUN` will fail without it.
- **Skipping `USE SECONDARY ROLES NONE`** before Collaboration API calls.
- **Calling `LEAVE`/`TEARDOWN` once.** They are two-call async — wait ~60s and call again.
- **Querying `VIEW_REGISTERED_DATA_OFFERINGS` after `INITIALIZE`** and assuming the spec is broken. The spec consumes registry entries; use `COLLABORATION.VIEW_COLLABORATIONS()` post-JOIN.
- **Calling `COLLABORATION.REVIEW` post-JOIN.** REVIEW is pre-JOIN only.

## Sub-flows

- `discover/INSTRUCTIONS.md` — V1 introspection queries
- `generate/INSTRUCTIONS.md` — V2 Collaboration API script generation
- `v1_v2_mapping.md` — reference mapping table

