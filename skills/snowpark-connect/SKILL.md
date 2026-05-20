---
name: snowpark-connect
title: Migrate to Snowpark Connect
summary: Migrate, validate, optimize, and deploy PySpark workloads on Snowflake using Snowpark Connect (SCOS).
description: |
  Use when migrating PySpark to Snowpark Connect, validating SCOS migrations, analyzing Spark
  compatibility, optimizing SCOS pipeline performance, or deploying PySpark jobs to Snowflake
  compute pools via snowpark-submit.
  Triggers: snowpark connect, scos, pyspark migration, spark connect, validate migration, pyspark compatibility, snowpark-submit.
tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - snowflake_sql_execute
prompt: Help me migrate this PySpark job to Snowpark Connect and validate it runs on Snowflake.
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Migrate to Snowpark Connect (SCOS)

## Overview

Snowpark Connect for Spark (SCOS) lets you run PySpark code on Snowflake compute with minimal changes. Most workloads only need to swap the `SparkSession` builder for `snowpark_connect.init_spark_session()`. This skill routes you through the lifecycle: set up a local dev environment, migrate code, validate behavior against the real SCOS runtime, tune performance, and deploy to production via `snowpark-submit` on SPCS compute pools.

## Prerequisites

- Snowflake account with an active warehouse
- `spark-connect` connection configured in `~/.snowflake/config.toml`
- Python 3.11 (conda recommended)

## Quick Reference

| Mode | Compute | Command | Use Case |
|------|---------|---------|----------|
| SCOS Local | Warehouse | `python script.py` | Development, testing |
| Snowpark Submit | SPCS Compute Pool | `snowpark-submit` | Production |

### Key code change

```python
# Standard PySpark
from pyspark.sql import SparkSession
spark = SparkSession.builder.appName("App").getOrCreate()

# SCOS
from snowflake import snowpark_connect
spark = snowpark_connect.init_spark_session()
```

## Workflow

Recommended order: **Setup → Migrate → Validate → Optimize → Deploy**

### Step 1: Detect intent

Ask the user which phase they need:

```
What would you like to do with Snowpark Connect?

1. Set up a local SCOS testing environment
2. Migrate PySpark code to SCOS
3. Validate a completed SCOS migration
4. Optimize SCOS pipeline performance
5. Deploy a Spark job to Snowflake via snowpark-submit
```

Wait for user selection before proceeding.

### Step 2: Route to sub-skill

| # | Phase | Triggers | Load |
|---|-------|----------|------|
| 1 | Setup | "setup", "local testing", "configure" | `scos-local-testing/SKILL.md` |
| 2 | Migrate | "migrate", "convert", "port" | `migrate-pyspark-to-snowpark-connect/SKILL.md` |
| 3 | Validate | "validate", "verify", "smoke test" | `validate-pyspark-to-snowpark-connect/SKILL.md` |
| 4 | Optimize | "slow", "performance", "cross join", "memory" | `scos-performance/SKILL.md` |
| 5 | Deploy | "snowpark-submit", "production", "compute pool" | `snowpark-submit/SKILL.md` |

If intent is ambiguous, clarify before routing. If the user is new to SCOS, recommend starting with Phase 1.

## Common Mistakes

- **Skipping local setup.** Trying to migrate without a working `spark-connect` connection in `~/.snowflake/config.toml` produces opaque auth errors. Verify the connection first.
- **Mixing PySpark and SCOS sessions.** Don't keep a `SparkSession.builder` call alongside `snowpark_connect.init_spark_session()`. Replace it fully.
- **Assuming 1:1 API parity.** Some PySpark APIs (RDDs, certain UDFs, Hive-specific features) aren't supported. Run the validation phase against real SCOS before declaring done.
- **Using a Python version other than 3.11.** SCOS pins to 3.11; mismatched envs cause import failures.
- **Deploying before validating.** `snowpark-submit` runs on SPCS compute pools — debug locally first to avoid burning compute on broken jobs.
- **Cross joins on large tables.** SCOS will execute them, but they'll be slow. Use the optimize phase to detect and rewrite.
- **Hardcoding warehouse names.** Pull warehouse and role from `config.toml` so the same code runs in dev and prod.

## Output

The user is routed to the appropriate sub-skill, which handles the detailed workflow for that phase.
