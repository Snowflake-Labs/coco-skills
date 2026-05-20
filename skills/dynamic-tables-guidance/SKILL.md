---
name: dynamic-tables-guidance
title: Design Dynamic Tables
summary: Decide when Dynamic Tables fit, design the pipeline, and ship it production-ready without the usual full-refresh traps.
description: "Use when you need to decide between Dynamic Tables, materialized views, streams+tasks, or dbt for a Snowflake pipeline, design a multi-layer DT DAG, debug a DT that fell back to FULL refresh, or harden a DT pipeline for production. Triggers: dynamic tables, DT design, DT vs MV, DT vs streams tasks, DT vs dbt, DT pitfalls, DT best practices, target lag, downstream lag, refresh mode, INCREMENTAL, FULL refresh, IMMUTABLE WHERE, BACKFILL FROM, primary key RELY, DT monitoring, DT pipeline."
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Read
  - Write
  - Edit
  - Grep
prompt: Help me design a Dynamic Tables pipeline for my bronze/silver/gold workflow and avoid the full-refresh trap.
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Design Dynamic Tables

## Overview

Dynamic Tables (DTs) are declarative, auto-refreshing materialized queries. You write a `SELECT`, set a `TARGET_LAG`, and Snowflake keeps the results fresh, picking INCREMENTAL or FULL refresh automatically. This skill helps you choose DTs over the alternatives, design a clean DAG, and ship it without the common gotchas.

## Quick Decision

| Need | Use |
|------|-----|
| Single-table query acceleration | Materialized View |
| Multi-step SQL pipeline, continuous freshness | **Dynamic Tables** |
| Stream-static joins, append-only patterns | Custom Incremental DTs (PrPr) |
| Cross-warehouse portability, `dbt test` | dbt models |
| Procedural logic, IF/ELSE, API calls, notifications | Streams + Tasks |
| Sub-15-second latency | Streams + Tasks |

DTs win when the work is pure SQL transforms inside Snowflake and you want self-orchestration. Reach for streams+tasks only when you hit procedural logic, side effects, or sub-15s latency.

## Pipeline Pattern: Bronze → Silver → Gold

```sql
-- Intermediate layers: TARGET_LAG = DOWNSTREAM
CREATE DYNAMIC TABLE bronze_events
  TARGET_LAG = DOWNSTREAM
  WAREHOUSE = pipeline_wh
  AS
    SELECT record_content:event_id::STRING AS event_id,
           record_content:event_type::STRING AS event_type,
           record_content:timestamp::TIMESTAMP_NTZ AS event_ts
    FROM raw_events;

-- Leaf layer: only one with a time-based lag
CREATE DYNAMIC TABLE gold_hourly_sales
  TARGET_LAG = '5 minutes'
  WAREHOUSE = pipeline_wh
  AS
    SELECT DATE_TRUNC('hour', event_ts) AS sales_hour,
           COUNT(*) AS order_count
    FROM bronze_events
    GROUP BY 1;
```

Rule: only the leaf DT gets a time-based `TARGET_LAG`; everything upstream uses `DOWNSTREAM`. Use a dedicated warehouse to isolate refresh cost from interactive queries.

## Monitoring

```sql
SELECT name, scheduling_state, last_completed_refresh_state,
       refresh_mode, time_within_target_lag_ratio
FROM TABLE(INFORMATION_SCHEMA.DYNAMIC_TABLES())
ORDER BY time_within_target_lag_ratio ASC;

SELECT name, state, state_message, refresh_action
FROM TABLE(INFORMATION_SCHEMA.DYNAMIC_TABLE_REFRESH_HISTORY(
  NAME_PREFIX => '<db>.<schema>', ERROR_ONLY => TRUE
))
ORDER BY refresh_start_time DESC LIMIT 10;
```

Alert when `time_within_target_lag_ratio < 0.95` or refresh failures appear in `SNOWFLAKE.ACCOUNT_USAGE.DYNAMIC_TABLE_REFRESH_HISTORY`.

## Production Checklist

- Explicit column lists (no `SELECT *` — adds break incremental)
- Change tracking enabled on base tables
- Intermediates use `TARGET_LAG = DOWNSTREAM`; leaf lag ≥ all upstream lags
- `refresh_mode = INCREMENTAL` confirmed (check `refresh_mode_reason` if FULL)
- Dedicated refresh warehouse; `INITIALIZATION_WAREHOUSE` for big first loads
- `IMMUTABLE WHERE` on partitions that never change (compliance, cost)
- `PRIMARY KEY ... RELY` set so downstream DTs stay incremental
- Failure alerting wired up

## Common Mistakes

- **`SELECT *` everywhere.** Schema drift forces FULL refresh. Always list columns.
- **Time-based lag on every layer.** Causes redundant refreshes. Only the leaf gets a time lag; intermediates use `DOWNSTREAM`.
- **Leaf lag tighter than upstream.** Snowflake can't honor it. Leaf lag must be ≥ max upstream lag.
- **Forgetting change tracking.** Without it, refreshes go FULL. Enable on base tables explicitly or let Snowflake auto-enable on first DT creation.
- **No `PRIMARY KEY RELY`.** Causes `INSERT OVERWRITE` reprocessing and breaks incremental-after-full chains downstream.
- **`DISTINCT` over wide rows.** Triggers fanout and FULL refresh. Pre-aggregate or use `QUALIFY ROW_NUMBER()`.
- **Misusing `IMMUTABLE WHERE`.** It freezes rows; if upstream rows in that range change later, results drift silently.
- **Treating DTs as a streaming engine.** Minimum lag is 15s (preview). Use streams+tasks for sub-second pipelines.
- **Calling external functions with side effects.** Not supported. Wrap with a stream+task on the leaf DT.

## Workflow

1. Use the decision table to confirm DTs fit. Stop and route otherwise.
2. Map layers (Bronze/Silver/Gold), pick `TARGET_LAG` per layer, assign warehouses.
3. Apply the production checklist. Verify `refresh_mode = INCREMENTAL` after first refresh.
4. For stream-static joins or append-only patterns, see `references/custom-incremental.md`.
5. For git-native deployment via DCM, see `references/dcm-for-dts.md`.

## References

- `references/pitfalls-and-pks.md` — full pitfalls list, `PRIMARY KEY RELY`, `IMMUTABLE WHERE`, `BACKFILL FROM`
- `references/custom-incremental.md` — Custom Incremental DTs (PrPr) syntax and patterns
- `references/dcm-for-dts.md` — DCM `DEFINE DYNAMIC TABLE` workflow
