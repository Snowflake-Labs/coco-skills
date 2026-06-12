---
name: dynamic-tables-guidance
title: Dynamic Tables Guidance
summary: Decide when to use Dynamic Tables vs MVs, streams+tasks, or dbt, and design production-ready DT pipelines.
description: "Use when choosing between Dynamic Tables and alternatives (materialized views, streams+tasks, dbt), designing multi-layer DT pipelines, debugging FULL-refresh fallback, or hardening DTs for production. Covers comparison matrices, decision flowcharts, common pitfalls, monitoring queries, and hybrid DT+task patterns. Triggers: dynamic tables guidance, when to use DT, DT vs MV, DT vs streams tasks, DT vs dbt, DT pitfalls, DT best practices, DT pipeline design, target lag, downstream lag, full refresh fallback."
tools:
  - snowflake_sql_execute
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
prompt: Should I use Dynamic Tables or streams+tasks for my CDC pipeline?
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Dynamic Tables Guidance

## Overview

Dynamic Tables (DTs) are declarative, auto-refreshing materialized queries. You write a `SELECT`, set a `TARGET_LAG`, and Snowflake keeps results fresh on the schedule you pick. This skill helps you decide when DTs are the right tool, design multi-layer pipelines, and avoid the failure modes that catch real teams in production.

Use this skill when picking between DTs, materialized views, streams+tasks, or dbt — or when an existing DT pipeline is misbehaving (full-refresh fallback, lag drift, runaway cost).

## Quick Decision Flowchart

```
Need to transform data in Snowflake?
  ├─ Single table, accelerate queries?           → Materialized View
  ├─ Multi-step SQL pipeline, fresh data?        → Dynamic Tables
  ├─ Stream-static joins / append-only?          → Custom Incremental DTs (PrPr)
  ├─ Cross-warehouse portability or dbt tests?   → dbt models
  ├─ Procedural logic, IF/ELSE, API calls?       → Streams + Tasks
  └─ Sub-15-second latency?                      → Streams + Tasks
```

## Comparison Matrix

| Dimension | Dynamic Tables | Materialized Views | Streams + Tasks | dbt |
|---|---|---|---|---|
| Refresh | Target lag (15s+) | Auto, near-real-time | Manual schedule/trigger | Batch (`dbt run`) |
| SQL support | Full SELECT, JOINs, windows | Single table only | Full + procedural | Full + Jinja |
| Chaining | `TARGET_LAG = DOWNSTREAM` | No | Manual DAG | Ref graph |
| Incremental | Built-in for supported ops | Auto | You write it | Manual `is_incremental` |
| Side effects | None | None | Email, API, externals | None |
| Cost | Your warehouse | Serverless | Your warehouse | Your warehouse |

**Rule of thumb:** Start with DTs. Reach for streams+tasks only when you need procedural logic, side effects, or sub-15s latency. Use dbt when you need its testing framework or cross-warehouse portability.

## Pipeline Pattern: Bronze → Silver → Gold

```sql
-- Bronze: parse raw VARIANT
CREATE DYNAMIC TABLE bronze_events
  TARGET_LAG = DOWNSTREAM
  WAREHOUSE = pipeline_wh
  AS SELECT
    record_content:event_id::STRING AS event_id,
    record_content:event_type::STRING AS event_type,
    record_content:timestamp::TIMESTAMP_NTZ AS event_ts
  FROM raw_events_topic;

-- Silver: business logic + joins
CREATE DYNAMIC TABLE silver_purchases
  TARGET_LAG = DOWNSTREAM
  WAREHOUSE = pipeline_wh
  AS SELECT e.event_id, e.event_ts, p.product_name, p.category
     FROM bronze_events e
     JOIN products p ON e.payload:product_id::STRING = p.product_id
     WHERE e.event_type = 'purchase';

-- Gold: only the leaf has a time-based lag
CREATE DYNAMIC TABLE gold_hourly_sales
  TARGET_LAG = '5 minutes'
  WAREHOUSE = pipeline_wh
  AS SELECT DATE_TRUNC('hour', event_ts) AS sales_hour, category,
            COUNT(*) AS order_count
     FROM silver_purchases
     GROUP BY 1, 2;
```

**Key rule:** Only the leaf DT has a time-based `TARGET_LAG`. Intermediates use `DOWNSTREAM` so Snowflake derives their lag from the leaf.

## Monitoring Essentials

```sql
-- Health check
SELECT name, scheduling_state, last_completed_refresh_state,
       refresh_mode, time_within_target_lag_ratio
FROM TABLE(INFORMATION_SCHEMA.DYNAMIC_TABLES())
ORDER BY time_within_target_lag_ratio ASC;

-- Recent refresh history
SELECT name, state, refresh_action,
       DATEDIFF('second', refresh_start_time, refresh_end_time) AS duration_sec
FROM TABLE(INFORMATION_SCHEMA.DYNAMIC_TABLE_REFRESH_HISTORY(
  NAME_PREFIX => '<db>.<schema>'))
ORDER BY refresh_start_time DESC LIMIT 20;

-- Errors only
SELECT name, state, state_message
FROM TABLE(INFORMATION_SCHEMA.DYNAMIC_TABLE_REFRESH_HISTORY(
  NAME_PREFIX => '<db>.<schema>', ERROR_ONLY => TRUE));
```

For account-wide DT cost, query `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` filtered to refresh queries.

## Common Mistakes

- **`SELECT *` in DT definition** — breaks incremental refresh on schema changes. Always use explicit column lists.
- **Time-based lag on every layer** — only the leaf should have a time-based lag. Use `TARGET_LAG = DOWNSTREAM` on intermediates.
- **Change tracking off on base tables** — DTs require `CHANGE_TRACKING = TRUE` for incremental refresh. Check with `SHOW TABLES`.
- **Falling back to FULL refresh silently** — check `refresh_mode_reason` if `refresh_mode` shows `FULL` when you expected `INCREMENTAL`.
- **Missing PRIMARY KEY RELY** — without it, `INSERT OVERWRITE` reprocesses everything downstream and you lose incremental chains.
- **DISTINCT/UNION fanout** — these operators force full refresh. Refactor with `QUALIFY ROW_NUMBER()` or `UNION ALL` where possible.
- **Sharing one warehouse with interactive queries** — DT refreshes will compete with user queries. Use a dedicated warehouse.
- **No `INITIALIZATION_WAREHOUSE` for big initial loads** — first refresh on large DTs can OOM a small warehouse. Set a larger init WH, then unset.

## Production Readiness Checklist

- [ ] Explicit column lists (no `SELECT *`)
- [ ] `CHANGE_TRACKING = TRUE` on all base tables
- [ ] Intermediates use `TARGET_LAG = DOWNSTREAM`
- [ ] Leaf target lag ≥ all upstream lags
- [ ] `refresh_mode` is `INCREMENTAL` (verify `refresh_mode_reason`)
- [ ] Dedicated warehouse for DT refreshes
- [ ] Monitoring on `time_within_target_lag_ratio > 0.95`
- [ ] Alerting on refresh failures
- [ ] `INITIALIZATION_WAREHOUSE` set for large initial loads

## Hybrid Pattern: DT + Task for Side Effects

```sql
CREATE STREAM gold_metrics_stream ON DYNAMIC TABLE gold_metrics;

CREATE TASK notify_on_refresh
  WAREHOUSE = ops_wh
  WHEN SYSTEM$STREAM_HAS_DATA('gold_metrics_stream')
AS
BEGIN
  LET change_count INT := (SELECT COUNT(*) FROM gold_metrics_stream);
  CALL SYSTEM$SEND_EMAIL('team@co.com', 'Metrics Updated',
    change_count || ' rows changed');
  CREATE OR REPLACE TEMP TABLE _consume AS SELECT * FROM gold_metrics_stream;
END;
```

## Workflow

1. **Assess fit** — run the decision flowchart. If DTs aren't the right tool, stop here.
2. **Pick refresh mode** — `AUTO` (default), `INCREMENTAL` (force, fail if ineligible), `FULL`, or `CUSTOM_INCREMENTAL` (PrPr).
3. **Design layers** — Bronze→Silver→Gold with `DOWNSTREAM` on intermediates.
4. **Harden** — apply the production checklist.

⚠️ STOPPING POINT: Before running `CREATE OR REPLACE DYNAMIC TABLE` against existing pipelines, show the user the planned DDL and confirm. Replacing a DT triggers a full reload and may invalidate downstream incremental chains.

⚠️ STOPPING POINT: Before applying `ALTER DYNAMIC TABLE ... SUSPEND` or `DROP DYNAMIC TABLE`, confirm with the user — downstream DTs depending on the target will stop refreshing.

## Stopping Points

- Workflow Step 4 — confirm DDL before `CREATE OR REPLACE DYNAMIC TABLE` on existing pipelines
- Workflow Step 4 — confirm before `ALTER ... SUSPEND` or `DROP DYNAMIC TABLE`

## References

- `references/pitfalls-and-pks.md` — full pitfall deep-dive plus `PRIMARY KEY RELY`, `IMMUTABLE WHERE`, `BACKFILL FROM`
- `references/custom-incremental.md` — Custom Incremental DTs (PrPr) syntax and patterns
- `references/dcm-for-dts.md` — Database Change Management for git-native DT deployment
- Built-in Cortex Code skill: `dynamic-tables`
