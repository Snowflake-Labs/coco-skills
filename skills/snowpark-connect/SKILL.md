---
name: snowpark-connect
title: Snowpark Connect for Spark
summary: Route PySpark migration, validation, and deployment work to the right Snowpark Connect (SCOS) sub-flow.
description: |
  Use when migrating PySpark code to Snowpark Connect (SCOS), setting up a local SCOS testing
  environment, validating an SCOS migration, tuning SCOS pipeline performance, or deploying a
  PySpark job to Snowflake compute pools via snowpark-submit. This umbrella skill detects intent
  and routes to the matching sub-flow.
  Triggers: snowpark connect, scos, pyspark migration, spark connect, validate migration, pyspark compatibility, snowpark-submit
tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
prompt: Help me migrate my PySpark job to Snowpark Connect.
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Snowpark Connect for Spark (SCOS)

## Overview

Snowpark Connect for Spark (SCOS) lets you run PySpark code against Snowflake compute. This skill is an umbrella that routes you to the right sub-flow based on what you need: setting up a local dev loop, migrating existing PySpark, validating a migration, tuning performance, or deploying to production.

The only required code change to switch a PySpark job to SCOS is the session bootstrap:

```python
# Standard PySpark
from pyspark.sql import SparkSession
spark = SparkSession.builder.appName("App").getOrCreate()

# SCOS
from snowflake import snowpark_connect
spark = snowpark_connect.init_spark_session()
```

## Prerequisites

- Snowflake account with an active warehouse
- A `spark-connect` connection configured in `~/.snowflake/config.toml`
- Python 3.11 (conda recommended)

## Run modes

| Mode | Compute | Command | Use case |
|------|---------|---------|----------|
| SCOS Local | Warehouse | `python script.py` | Development, testing |
| Snowpark Submit | SPCS Compute Pool | `snowpark-submit` | Production |

## Workflow

Recommended order: Setup → Migrate → Validate → Optimize → Deploy.

### Step 1: Detect intent

Ask the user which sub-flow they need:

1. Set up local SCOS testing environment
2. Migrate PySpark code to SCOS
3. Validate a completed SCOS migration
4. Optimize SCOS pipeline performance
5. Deploy a Spark job via `snowpark-submit`

⚠️ STOPPING POINT: Wait for the user to pick a sub-flow before loading any sub-skill. If the request is ambiguous, ask one clarifying question first. If the user is new to SCOS, recommend starting with sub-flow 1 (Setup).

### Step 2: Route to sub-flow

| # | Phase | Trigger keywords | Load |
|---|-------|------------------|------|
| 1 | Setup | setup, local testing, dev environment, configure | `scos-local-testing/INSTRUCTIONS.md` |
| 2 | Migrate | migrate, convert, port, rewrite for SCOS | `migrate-pyspark-to-snowpark-connect/INSTRUCTIONS.md` |
| 3 | Validate | validate, verify, test migration, smoke test | `validate-pyspark-to-snowpark-connect/INSTRUCTIONS.md` |
| 4 | Optimize | slow, performance, cross join, memory, optimize | `scos-performance/INSTRUCTIONS.md` |
| 5 | Deploy | snowpark-submit, deploy, production, compute pool | `snowpark-submit/INSTRUCTIONS.md` |

Each sub-flow contains its own multi-step workflow, code diffs, and verification commands.

## Common Mistakes

- Skipping Setup and trying to migrate first — the local dev loop catches issues fast; production runs do not.
- Editing more than the session bootstrap during migration. Start by changing only `SparkSession.builder...` to `snowpark_connect.init_spark_session()`, run, then fix what actually breaks.
- Mixing run modes mid-flow. Use SCOS Local for iteration; switch to `snowpark-submit` only when the job is stable.
- Tuning performance before the job runs end-to-end. Validate correctness first, optimize second.
- Hardcoding credentials. Configure `spark-connect` in `~/.snowflake/config.toml` and let the SDK resolve auth.

## Stopping Points

- Step 1 — wait for the user to pick a sub-flow before loading any sub-skill content or running commands.

## Output

The user is routed into the matching sub-flow, which then drives the rest of the work.
