---
name: agentic-migration-workshop
title: Migration to Snowflake
summary: Guide developers through migrating Oracle, Teradata, Redshift, or SQL Server workloads to Snowflake.
description: |
  Use when migrating a database from Oracle, Teradata, Amazon Redshift, or SQL Server to Snowflake. Covers assessment, schema conversion, data loading, SQL translation, and optional SSIS/Power BI repointing. Routes to focused sub-flows for each phase. Triggers: migrate, migration, convert, translate SQL, SnowConvert, SSIS, Power BI repointing, data validation, ETL, DDL conversion, schema conversion.
tools:
  - snowflake_sql_execute
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
prompt: I want to migrate my Oracle database to Snowflake.
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Migration to Snowflake

## Overview

Migrate a legacy database (Oracle, Teradata, Redshift, SQL Server) to Snowflake. The skill walks through five focused phases — each lives in its own sub-flow with detailed steps. Pick the phase that matches where you are; you do not need to run them in order.

| Phase | Sub-flow | Output |
|---|---|---|
| 1. Assess | `assessment/INSTRUCTIONS.md` | Inventory, complexity score, effort estimate |
| 2. Convert schema | `schema-conversion/INSTRUCTIONS.md` | Snowflake DDL + type mapping |
| 3. Load data | `data-migration/INSTRUCTIONS.md` | Staging, COPY scripts, reconciliation |
| 4. Translate SQL | `query-translation/INSTRUCTIONS.md` | Converted queries, procs, behavioral diffs |
| 5. Automate (optional) | `snowconvert-ai/INSTRUCTIONS.md` | Bulk conversion via SnowConvert AI |

Add-ons: `ssis-replatform/INSTRUCTIONS.md` (SQL Server) and `powerbi-repointing/INSTRUCTIONS.md`.

## When to Use

Use when you have a workload on Oracle, Teradata, Redshift, or SQL Server and want to move it to Snowflake yourself. Use the Assess sub-flow first if you do not yet know scope or effort. Skip straight to Schema Conversion if your DDL is ready, or Query Translation if your tables already exist in Snowflake.

## Setup

1. Verify connectivity:
   ```sql
   SELECT CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_DATABASE();
   ```
2. Load `references/best-practices.md` plus the file matching your source: `references/oracle.md`, `teradata.md`, `redshift.md`, or `sqlserver.md`.
3. Optional but recommended — install **SnowConvert AI** (free) for automated DDL extraction and bulk conversion. Download from `https://snowconvert.snowflake.com`.

## Routing

Detect intent from the first user message:

| Trigger phrases | Sub-flow |
|---|---|
| assess, readiness, complexity, scope, effort | `assessment/INSTRUCTIONS.md` |
| convert schema, DDL, data types, create tables | `schema-conversion/INSTRUCTIONS.md` |
| load data, migrate data, reconcile | `data-migration/INSTRUCTIONS.md` |
| translate SQL, convert query, stored procedure | `query-translation/INSTRUCTIONS.md` |
| SnowConvert, automated conversion | `snowconvert-ai/INSTRUCTIONS.md` |
| SSIS, dtsx, integration services | `ssis-replatform/INSTRUCTIONS.md` |
| Power BI, .pbit, repoint | `powerbi-repointing/INSTRUCTIONS.md` |

If the user asks for an end-to-end run, start with `assessment/INSTRUCTIONS.md` and chain phases.

⚠️ STOPPING POINT: After detecting intent, confirm with the user which phase to load before proceeding. Do not chain into a sub-flow without explicit user approval.

## Stopping Points

⚠️ STOPPING POINT: Before running any DDL, INSERT, COPY, or GRANT against the target Snowflake account, show the user the exact SQL and wait for explicit approval.

⚠️ STOPPING POINT: Before applying SnowConvert output, show the EWI/FDM summary and let the user resolve EWI errors first.

⚠️ STOPPING POINT: Before deleting or truncating staging tables after a load, confirm reconciliation passed.

Per-step stops:
- Routing — confirm which phase the user wants before loading any sub-flow
- Assessment — none (read-only)
- Schema conversion — confirm DDL before `CREATE`
- Data migration — confirm load plan, then confirm cleanup
- Query translation — confirm replacement before overwriting source files
- SnowConvert AI — confirm before deploying converted artifacts

## Common Mistakes

- **Skipping assessment.** Jumping into conversion without an object inventory leads to missed dependencies (sequences, synonyms, materialized views).
- **One-to-one data type mapping.** Oracle `NUMBER` and Teradata `DECIMAL` often need precision tuning; Redshift `VARCHAR(MAX)` should not always become `VARCHAR(16777216)`.
- **Ignoring behavioral differences.** Implicit casting, NULL ordering, date arithmetic, and empty-string handling differ across platforms. Capture each in a behavioral diff log.
- **Validating only row counts.** Counts match while values diverge. Validate counts, aggregates (SUM, MIN, MAX), and row-level samples.
- **Loading before reconciling staging.** Stage to a scratch schema, reconcile, then promote. Do not load directly into production tables.
- **Treating EWI warnings as optional.** SnowConvert EWI errors block correctness; resolve them before deployment. FDM warnings still need a business-impact review.
- **No rollback plan.** Keep source available read-only until validation passes end-to-end.

## Troubleshooting

- **No source DDL** — extract with SnowConvert AI or `https://github.com/Snowflake-Labs/SC.DDLExportScripts`, or query the source `INFORMATION_SCHEMA`.
- **Unsupported source feature** — document a Snowflake-native alternative and confirm with the user before substituting.
- **Permission errors** — most migrations need `CREATE DATABASE`, `CREATE SCHEMA`, `CREATE TABLE`, `CREATE STAGE`, and `USAGE` on a warehouse. Use a dedicated migration role.
