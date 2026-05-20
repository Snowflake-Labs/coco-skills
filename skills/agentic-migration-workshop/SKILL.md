---
name: agentic-migration-workshop
title: Migrate to Snowflake
summary: Convert DDL, load data, and translate SQL when moving from Oracle, Teradata, Redshift, or SQL Server to Snowflake.
description: "Use when migrating a database to Snowflake from Oracle, Teradata, Amazon Redshift, or SQL Server, or when converting legacy SQL, DDL, stored procedures, SSIS packages, or Power BI reports. Routes between assessment, schema conversion, data migration, and query translation phases, with SnowConvert AI for bulk automated conversion. Triggers: migrate, migration, convert, translate SQL, SnowConvert, SSIS, Power BI, DDL conversion, ETL replatform, schema conversion, data migration, query translation, Oracle to Snowflake, Teradata to Snowflake, Redshift to Snowflake, SQL Server to Snowflake"
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
prompt: I'm migrating from Oracle to Snowflake. Where do I start?
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Migrate to Snowflake

## Overview

Walks a developer or data engineer through migrating a database to Snowflake from Oracle, Teradata, Amazon Redshift, or SQL Server. The skill routes between four technical phases — assessment, schema conversion, data migration, query translation — and supports SSIS replatform and Power BI repointing as add-ons. SnowConvert AI handles bulk automated conversion (DDL, code, data type mapping).

## When to Use

Use when:
- Planning a migration and inventorying source objects to estimate scope and effort
- Converting source DDL to Snowflake-equivalent tables, views, sequences, and constraints
- Loading data into Snowflake and validating it against the source (row counts, aggregates, samples)
- Translating SQL queries, stored procedures, functions, or scripts to Snowflake SQL
- Replatforming SSIS packages onto Snowflake Tasks + dbt
- Repointing Power BI reports to Snowflake data sources

## Setup

Install **SnowConvert AI** (free, no license required):

- macOS Apple Silicon: https://snowconvert.snowflake.com/storage/darwin_arm64/prod/scd/Snowconvert-arm64.dmg
- macOS Intel: https://snowconvert.snowflake.com/storage/darwin_x64/prod/scd/Snowconvert.dmg
- Windows x64: https://snowconvert.snowflake.com/storage/windows/prod/scd/Snowconvert%20Setup.exe
- Windows ARM64: https://snowconvert.snowflake.com/storage/windows_arm64/prod/scd/Snowconvert%20Setup-arm64.exe

Requires Windows 11+ or macOS 13.3+, 4 GB RAM (8 GB recommended). Access codes auto-generate from v1.2.0.

Verify Snowflake connectivity before running SQL:

```sql
SELECT CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_DATABASE();
```

If migration privileges are needed:

```sql
GRANT CREATE MIGRATION ON ACCOUNT TO ROLE <role>;
```

## Workflow

First, ask the user which platform they're migrating from. Load `references/best-practices.md` plus the matching platform reference:

- `references/oracle.md`
- `references/teradata.md`
- `references/redshift.md`
- `references/sqlserver.md`

Then route to the right phase sub-skill:

| Phase | Sub-skill | Output |
|---|---|---|
| 1. Assessment | `assessment/SKILL.md` | Object inventory, complexity scoring, effort estimate |
| 2. Schema Conversion | `schema-conversion/SKILL.md` | Snowflake DDL, data type mapping, ordered deploy script |
| 3. Data Migration | `data-migration/SKILL.md` | Staging DDL, load scripts, row-count + aggregate reconciliation |
| 4. Query Translation | `query-translation/SKILL.md` | Converted SQL, behavioral differences log |
| Bulk conversion | `snowconvert-ai/SKILL.md` | Automated DDL/code conversion (covers phases 2–4) |
| SSIS Replatform | `ssis-replatform/SKILL.md` | Snowflake Tasks + dbt project |
| Power BI Repoint | `powerbi-repointing/SKILL.md` | Repointed `.pbit`/`.pbix` files |

### Intent routing

If the user skips the welcome flow and goes straight to a request, route by keywords:

| Triggers | Route to |
|---|---|
| assess, readiness, complexity, scope, estimate effort | Phase 1 |
| convert schema, DDL, data types, create tables | Phase 2 |
| load data, migrate data, validate, reconcile | Phase 3 |
| translate SQL, convert query, stored procedure | Phase 4 |
| SnowConvert, automated, bulk conversion | SnowConvert AI |
| SSIS, dtsx, Integration Services | SSIS Replatform |
| Power BI, repoint, pbit | Power BI Repoint |

## DDL Extraction

If the user has no source DDL on hand:

- Use the public Snowflake-Labs DDL export scripts: https://github.com/Snowflake-Labs/SC.DDLExportScripts
- Query the source `INFORMATION_SCHEMA` directly when reachable
- Accept pasted object definitions

## Common Mistakes

- **Skipping assessment.** Going straight to schema conversion without an inventory leads to surprise scope and missed dependencies (synonyms, materialized views, package bodies, triggers).
- **Ignoring data type semantics.** Oracle `NUMBER` with no precision, Teradata `PERIOD`, Redshift `SUPER`, and SQL Server `DATETIME2` all need explicit mapping decisions — don't accept tool defaults blindly.
- **Validating only row counts.** Counts can match while sums and averages diverge. Always check row count + sum/avg of numeric columns + spot-check sample rows.
- **Treating EWI warnings as advisory.** SnowConvert AI EWI (Error/Warning/Issue) markers must be resolved before deployment. FDM (Functional Difference Marker) warnings need a behavioral-impact review.
- **Forgetting case sensitivity and collation.** Snowflake folds unquoted identifiers to uppercase; SQL Server collation rules and Oracle quoted-identifier semantics don't carry over.
- **One-shot loads on huge tables.** Use staged `COPY INTO` with file splitting and `ON_ERROR = CONTINUE` for first-pass loads, then reconcile and reload rejects.
- **Forgetting to size the warehouse for the load.** Bulk `COPY INTO` benefits from a larger warehouse during migration; resize back down after cutover.
