# Common Pitfalls, Primary Keys, Immutability & Backfill

## Common Pitfalls and How to Fix Them

### Pitfall 1: FULL Refresh When You Expected INCREMENTAL

**Symptom:** `refresh_mode = FULL` and `refresh_mode_reason = QUERY_NOT_SUPPORTED_FOR_INCREMENTAL`

**Common causes and fixes:**

| Cause | Fix |
|-------|-----|
| `UNION DISTINCT` | Use `UNION ALL` + `QUALIFY ROW_NUMBER() = 1` for dedup |
| `EXCEPT` / `INTERSECT` | Rewrite as `LEFT JOIN ... WHERE b.id IS NULL` (anti-join) |
| `CURRENT_TIMESTAMP()` in SELECT list | Move to WHERE clause, or use `METADATA$ROW_LAST_COMMIT_TIME` |
| Self outer join (same table on both sides) | Break into intermediate DT |
| Outer join with GROUP BY subqueries on both sides | Materialize aggregations as separate DTs |
| Non-deterministic functions (`RANDOM()`, `UUID_STRING()`) | Move to application layer |

**Diagnostic:**
```sql
SHOW DYNAMIC TABLES LIKE '<dt_name>' IN SCHEMA <db>.<schema>;
-- Check: refresh_mode, refresh_mode_reason
```

**Key insight:** When AUTO chooses FULL, try explicit `REFRESH_MODE = INCREMENTAL` first — it often works for window functions and other "Limited" operators.

### Pitfall 2: SELECT * Breaks on Schema Changes

**Symptom:** DT refresh fails after a column is added/removed from the source table.

**Fix:** Always use explicit column lists.

```sql
-- BAD
CREATE DYNAMIC TABLE my_dt AS SELECT * FROM source_table;

-- GOOD
CREATE DYNAMIC TABLE my_dt AS SELECT id, name, amount, created_at FROM source_table;
```

### Pitfall 3: Change Tracking Not Enabled

**Symptom:** `CHANGE_TRACKING_NOT_ENABLED` in refresh_mode_reason, or DT creation fails.

**Fix:**
```sql
ALTER TABLE source_table SET CHANGE_TRACKING = TRUE;

-- Verify
SELECT table_name, change_tracking
FROM information_schema.tables
WHERE table_schema = 'MY_SCHEMA' AND table_name = 'SOURCE_TABLE';
```

DTs auto-enable change tracking on their sources at creation time, but if it's explicitly disabled afterward, refreshes break.

### Pitfall 4: Target Lag Shorter Than Upstream

**Symptom:** Downstream DT can never meet its target lag.

```sql
-- WRONG: child asks for 1 min but parent delivers every 10 min
CREATE DYNAMIC TABLE parent TARGET_LAG = '10 minutes' AS ...;
CREATE DYNAMIC TABLE child  TARGET_LAG = '1 minute' AS SELECT * FROM parent;
```

**Fix:** Child target lag must be ≥ parent target lag. Use `DOWNSTREAM` for intermediates.

### Pitfall 5: Monolithic DT That's Slow and Expensive

**Symptom:** Single DT with 5+ JOINs, refresh takes >50% of target lag, disk spill.

**Fix: Decompose.** Break into intermediate DTs:

```
BEFORE: SourceA + SourceB + SourceC → [Complex 5-JOIN query] → final_dt

AFTER:
SourceA + SourceB → intermediate_1 (TARGET_LAG = DOWNSTREAM, INCREMENTAL)
                          ↓
intermediate_1 + SourceC → final_dt (TARGET_LAG = '5 minutes', INCREMENTAL)
```

Intermediates use `TARGET_LAG = DOWNSTREAM` so they only refresh when the final DT needs fresh data.

### Pitfall 6: Incremental DT Depends on Full-Refresh DT

**Symptom:** Creation fails or unexpected FULL refreshes propagate.

**Rule:** An incremental DT cannot have a FULL-refresh DT as an upstream dependency — unless the upstream has a **primary key** (declared or derived). With a PK, Snowflake can diff the FULL refresh output and pass only actual changes downstream. See the [Primary Keys](#primary-keys-rely--the-smallest-change-with-the-biggest-impact) section below.

Without a PK, fix the upstream DT first, or accept FULL on the downstream.

### Pitfall 7: DISTINCT-Masked Join Fanout in Migrations

**Symptom:** Converting a `SELECT DISTINCT ... FROM a JOIN b` query to a DT produces 10x the expected rows.

**Root cause:** The original query used `DISTINCT` to mask a many-to-many join. Removing DISTINCT (or not having it in a DT) exposes the fanout.

**Fix:** Check join key uniqueness BEFORE creating the DT. Replace DISTINCT with `GROUP BY` on the intended grain:
```sql
-- WRONG: naive migration
CREATE DYNAMIC TABLE my_dt AS
SELECT a.*, b.lookup_col FROM facts a JOIN lookup b ON a.key = b.key;
-- ↑ Fanout: lookup has duplicate keys

-- RIGHT: explicit grain
CREATE DYNAMIC TABLE my_dt AS
SELECT a.key, a.data_col, MAX(b.lookup_col) AS lookup_col
FROM facts a JOIN lookup b ON a.key = b.key
GROUP BY a.key, a.data_col;
```

### Pitfall 8: Immutability Misuse

`IMMUTABLE WHERE` logically partitions a DT into an **immutable region** (rows matching the predicate are frozen — never recomputed) and a **mutable region** (rows that don't match continue refreshing normally). This is powerful for audit trails, cost optimization, and data lifecycle management.

**Restrictions** (designed so the immutable boundary is deterministic and can only grow):
- No subqueries
- No UDFs or external functions
- No non-deterministic functions (except timestamp functions used in a monotonically-growing pattern)
- No `METADATA$` column references
- No aggregate/window function columns in the predicate

```sql
-- GOOD: timestamp-based immutability (region only grows)
CREATE DYNAMIC TABLE my_dt
  IMMUTABLE WHERE (event_time < CURRENT_TIMESTAMP() - INTERVAL '7 days')
  TARGET_LAG = '5 minutes'
  WAREHOUSE = my_wh
  AS SELECT * FROM events;

-- GOOD: status-based immutability (order tracking audit trail)
CREATE DYNAMIC TABLE shipped_orders_log
  TARGET_LAG = '1 minute'
  WAREHOUSE = my_wh
  IMMUTABLE WHERE (status = 'SHIPPED')
  AS SELECT order_id, status, amount, last_updated FROM orders;
-- Once an order ships, the row is frozen — even if someone updates the source.
-- Query METADATA$IS_IMMUTABLE to verify which rows are locked.

-- BAD: subquery
CREATE DYNAMIC TABLE my_dt
  IMMUTABLE WHERE (id IN (SELECT id FROM archived)) -- ERROR
  ...
```

**Real-world use cases for IMMUTABLE WHERE:**
- **Audit trail protection**: Lock shipped/completed orders so source corrections can't corrupt history
- **Data deletion resilience**: Freeze historical data before purging base tables for compliance — downstream DTs retain the locked rows
- **Schema evolution**: Lock old data when adding new columns — only future records get refreshed with the new schema
- **Dimension change isolation**: Freeze historical aggregates so dimension table updates don't trigger reprocessing old fact records

---

## BACKFILL FROM: Migrate Without Reprocessing History

`BACKFILL FROM` seeds a new DT with data from an existing table, eliminating the need to recompute all historical data on first refresh.

```sql
CREATE OR REPLACE DYNAMIC TABLE regional_sales_summary
  IMMUTABLE WHERE (sale_date < '2024-01-01')
  TARGET_LAG = '60 seconds'
  WAREHOUSE = my_wh
  BACKFILL FROM historical_sales
  AS
    SELECT sale_id, product_name, sale_amount, DATE(sale_timestamp) AS sale_date
    FROM enriched_sales
    WHERE country IN ('US', 'CA');
```

**Key rules:**
- Clustering keys must be identical between the new DT and the backfill table
- Only data matching the `IMMUTABLE WHERE` constraint can be backfilled
- Without `BACKFILL FROM`, the first refresh runs the `REFRESH USING` query against the entire source

**Use cases:**
- Migrating legacy pipelines to DTs without reprocessing years of history
- Splitting or restructuring DTs while preserving data
- Seeding with pre-computed historical aggregates

---

## Primary Keys (RELY) — The Smallest Change with the Biggest Impact

Adding `PRIMARY KEY RELY` to base tables unlocks two critical DT optimizations:

**Problem 1: INSERT OVERWRITE resets change tracking.** Many ELT patterns use `INSERT OVERWRITE` to reload dimension tables. This resets Snowflake's change-tracking columns — every row looks "new", and your incremental DT reprocesses everything.

**Problem 2: FULL refresh propagates downstream.** If a DT uses FULL refresh (e.g., float aggregation with JOINs), every downstream DT is also forced to FULL.

**The fix — one line of DDL:**

```sql
ALTER TABLE dimension_products ADD PRIMARY KEY (product_id) RELY;
```

`RELY` tells Snowflake: "this key is unique and stable — use it for change detection." Instead of comparing change-tracking columns, Snowflake compares rows by primary key values. Only rows that actually changed get processed.

**Real numbers (INSERT OVERWRITE scenario):**
- Without PK: `{"insertedRows": 100, "copiedRows": 0, "deletedRows": 100}` — reprocessed everything
- With PK RELY: `{"insertedRows": 2, "copiedRows": 98, "deletedRows": 2}` — only changed rows

**Derived keys — Snowflake reads your SQL:**
- `GROUP BY` columns automatically become derived unique keys
- `QUALIFY ROW_NUMBER() OVER (PARTITION BY ...) = 1` — partition columns become derived keys
- Check with: `SHOW UNIQUE KEYS IN my_dynamic_table` — look for `SYS_CONSTRAINT_DERIVED_PK`

**Incremental after FULL (the chain reaction fix):**

```sql
CREATE DYNAMIC TABLE sales_summary
  TARGET_LAG = '1 minute'
  WAREHOUSE = my_wh
  REFRESH_MODE = AUTO  -- resolves to FULL (float aggregation + joins)
  AS
    SELECT region_name, channel_name, COUNT(*) AS sale_count, SUM(amount) AS total
    FROM fact_sales f
    LEFT JOIN dim_region r ON f.region_id = r.region_id
    GROUP BY region_name, channel_name;
-- Snowflake auto-derives PK on (region_name, channel_name) from the GROUP BY

CREATE DYNAMIC TABLE regional_trends
  TARGET_LAG = '1 minute'
  WAREHOUSE = my_wh
  REFRESH_MODE = INCREMENTAL  -- works because parent has derived PK
  AS
    SELECT region_name, SUM(sale_count) AS total_sales, SUM(total) AS total_revenue
    FROM sales_summary
    GROUP BY region_name;
```

**Important:** Adding a PK to a base table does NOT retroactively update existing downstream DTs. You must `CREATE OR REPLACE` downstream DTs for the key to take effect. When switching from FULL to INCREMENTAL, you must explicitly set `REFRESH_MODE = INCREMENTAL` — `AUTO` will still resolve to FULL.
