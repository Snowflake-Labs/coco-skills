---
name: snowpipe-bcdr
title: Snowpipe BCDR on Azure
summary: Snowpipe disaster recovery patterns for Azure ADLS Gen2 covering 6 failover options with RPO/RTO tradeoffs.
description: |
  Use when designing Snowpipe disaster recovery on Azure ADLS Gen2, choosing between failover options based on RPO/RTO/cost, implementing dual-pipe or Azure GRS/RA-GRS patterns, running failover/failback/catchup procedures, or troubleshooting pipe gaps after an outage.
  Triggers: snowpipe BCDR, snowpipe disaster recovery, snowpipe failover, dual pipe, dual storage, active-passive pipe, active-active pipe, Azure GRS snowpipe, RA-GRS snowpipe, GZRS snowpipe, snowpipe catchup, pipe failback, snowpipe high availability, snowpipe monitoring, pipe health check, snowpipe deduplication, failover group snowpipe, copy history catchup, pipe refresh catchup, snowpipe RPO RTO, storage queue retention.
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Read
  - Write
prompt: Help me design a Snowpipe BCDR strategy on Azure ADLS Gen2 with near-zero RPO.
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Snowpipe BCDR on Azure

## Overview

This skill helps you design and operate business continuity / disaster recovery for Snowpipe ingestion on Azure ADLS Gen2. It covers six patterns with different RPO, RTO, cost, and complexity tradeoffs, then walks through implementation, validation, and failover/failback runbooks.

### Option Comparison

| Option | Pattern | RPO | RTO | Cost | Complexity |
|---|---|---|---|---|---|
| 1 — Manual Catchup | Single pipe, manual recovery | Min–hrs | 30–90 min | Lowest | Low |
| 2 — Active-Active + Dedup | Dual pipes, hash dedup | Zero | ~1 min | Highest | High |
| 3 — RA-GRS Read | Single pipe + RA-GRS reads | Near-zero | 5–15 min | Medium | Medium |
| 4 — Active-Passive (SF+GRS) | Dual pipes, single active | Near-zero | 5–15 min | Medium | Medium |
| 5 — Snowflake-Only Failover | Failover group promotion | Near-zero | 5–15 min | Low–Med | Low |
| 6 — Dual Storage Dual Pipes | Dual storage, single active | Near-zero | 5–10 min | Medium | Medium |

Options 2–6 require **Business Critical Edition** (Failover Groups). Without it, only Option 1 applies.

### Decision Tree

```
Business Critical?
├── No → Option 1
└── Yes
    ├── Zero data loss → Option 2
    ├── Azure-region failure → Option 4 or 6
    ├── Snowflake-region failure → Option 5
    ├── RA-GRS available → Option 3
    └── Budget constrained → Option 1 or 5
```

## Critical Rules

1. **Single-writer per file set** — only one pipe active per file set. Exception: Option 2 with dedup.
2. **Storage integration includes both URLs** — `STORAGE_ALLOWED_LOCATIONS` must list primary and secondary blob URLs, or failover fails.
3. **COPY_HISTORY** — `INFORMATION_SCHEMA.COPY_HISTORY` (14-day window) is replicated by Failover Groups. Maintain a `FILE_LOAD_HISTORY` table for older history.
4. **PIPE REFRESH 7-day limit** — `ALTER PIPE ... REFRESH` only loads files staged within the last 7 days (not configurable). For older files use `DIRECTORY(@stage)` + `COPY INTO`.
5. **Inbound notification integrations do NOT replicate** — after failover-group promotion, manually recreate `DIRECTION = INBOUND` notification integrations in the DR account and re-establish the Event Grid subscription.

## Implementation Sketch

**Dual pipe pattern (Options 4, 6):**

```sql
CREATE PIPE PIPE_PRIMARY AUTO_INGEST=TRUE INTEGRATION='NOTIF_INT_A' AS
  COPY INTO TARGET_TABLE (..., 'PRIMARY' AS _source_region) FROM @STAGE_PRIMARY;
CREATE PIPE PIPE_SECONDARY AUTO_INGEST=TRUE INTEGRATION='NOTIF_INT_B' AS
  COPY INTO TARGET_TABLE (..., 'SECONDARY' AS _source_region) FROM @STAGE_SECONDARY;
ALTER PIPE PIPE_SECONDARY SET PIPE_EXECUTION_PAUSED = TRUE;
```

**Active-Active dedup (Option 2):** add `MD5(METADATA$FILENAME)` and `MD5(CONCAT_WS('|', $1,$2,...))` columns, dedup via Dynamic Table with `QUALIFY ROW_NUMBER() ... = 1`.

**Catchup logic:** compare `DIRECTORY(@stage) WHERE LAST_MODIFIED >= cutoff` against `INFORMATION_SCHEMA.COPY_HISTORY WHERE STATUS='Loaded'` via LEFT JOIN — NULL matches are missed files.

## Validation

```sql
SELECT PARSE_JSON(SYSTEM$PIPE_STATUS('pipe_name')):executionState::STRING AS state,
       PARSE_JSON(SYSTEM$PIPE_STATUS('pipe_name')):pendingFileCount::NUMBER AS pending;
```

Alert thresholds: pending files > 1000 for >15 min, error rate >5%/hr, load latency >30 min, queue depth rising >30 min.

## Failover / Failback

1. Detect via health view.
2. Pause failing pipe (`PIPE_EXECUTION_PAUSED=TRUE`).
3. Record checkpoint from COPY_HISTORY.
4. Resume secondary pipe.
5. Recreate inbound notification integration if using Failover Groups.
6. Run catchup (PIPE REFRESH for short outages, COPY INTO for large backlogs).
7. Verify no gaps; log event.

Failback reverses the order with a 15–30 min safety buffer on `MODIFIED_AFTER`.

## Common Mistakes

- **Both pipes running simultaneously** — produces duplicates outside Option 2. Always pause the standby.
- **Missing secondary URL in storage integration** — causes `ACCESS_DENIED` at failover. Include both URLs from day one.
- **Relying on COPY_HISTORY past 14 days** — back it up to a `FILE_LOAD_HISTORY` table.
- **No Event Grid subscription on secondary** — `pendingFileCount` stays at 0. Provision both regions up front.
- **PIPE REFRESH clock skew** — subtract a 15–30 min buffer from checkpoint times.
- **Recreating pipe without `AUTO_INGEST=TRUE` + `INTEGRATION`** — pipe stops auto-loading silently.
- **Forgetting inbound notification integrations don't replicate** — pipe looks healthy but receives no events after promotion.
- **Using PIPE REFRESH for >7-day-old files** — silently skips them. Use `DIRECTORY(@stage)` + `COPY INTO` instead.

## References

- [Stage, Pipe, and Load History Replication](https://docs.snowflake.com/en/user-guide/account-replication-stages-pipes-load-history)
- [Automating Snowpipe for Azure Blob Storage](https://docs.snowflake.com/en/user-guide/data-load-snowpipe-auto-azure)
