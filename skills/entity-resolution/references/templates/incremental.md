# Incremental Processing — Delta Entity Resolution

Use incremental processing when the cost of a full pipeline refresh outweighs the overhead of change tracking. Rule of thumb:

| Scenario | Approach |
|----------|----------|
| < 500K source records | Full refresh (simpler, no stream management) |
| > 500K records, < 5% change rate per cycle | Incremental (streams + tasks) |
| > 20% of records change per cycle | Full refresh (delta overhead exceeds savings) |
| Schema change or blocking strategy change | Full refresh (required) |

The full-refresh pipeline uses dynamic tables. Incremental processing uses **Snowflake Streams + Tasks** because streams are consumed on read — dynamic tables cannot drive stream-based incrementals.

---

## Change Detection via Streams

Create a stream on the source table. Set `APPEND_ONLY = FALSE` to capture updates and deletes, not just inserts:

```sql
CREATE OR REPLACE STREAM source_entity_stream
    ON TABLE source_entities
    APPEND_ONLY = FALSE;
```

Check the stream to understand what changed before processing:

```sql
SELECT
    METADATA$ACTION,       -- 'INSERT' or 'DELETE' (updates appear as DELETE + INSERT pair)
    METADATA$ISUPDATE,     -- TRUE if this row is part of an update operation
    METADATA$ROW_ID,       -- Stable row identifier across the stream's lifetime
    *
FROM source_entity_stream
LIMIT 100;
```

**Important:** Querying the stream without consuming it (e.g., a bare `SELECT`) does not advance the stream offset. Only DML operations that read from the stream inside a transaction advance the offset.

---

## Incremental Normalization

Normalize only new/changed source records and MERGE them into `normalized_entities`. This avoids re-running AI_EXTRACT and name normalization on unchanged rows.

```sql
-- Normalize new/changed records from stream
CREATE OR REPLACE TEMPORARY TABLE new_normalized AS
SELECT
    s.METADATA$ROW_ID                          AS row_id,
    s.source_id,
    UPPER(TRIM(s.raw_name))                    AS normalized_name,
    s.blocking_key_field                       AS blocking_key,
    -- Apply same normalization logic as normalization.md
    ai_parsed:street::STRING                   AS normalized_street,
    ai_parsed:city::STRING                     AS normalized_city,
    UPPER(TRIM(ai_parsed:state::STRING))       AS normalized_state,
    LEFT(REGEXP_REPLACE(ai_parsed:zip::STRING, '[^0-9]', ''), 5) AS normalized_zip
FROM source_entity_stream s,
     LATERAL FLATTEN(INPUT => ARRAY_CONSTRUCT(
         AI_EXTRACT(s.raw_address, '{"street":"...","city":"...","state":"...","zip":"..."}')
     )) f
WHERE s.METADATA$ACTION = 'INSERT';

-- Merge into normalized_entities
MERGE INTO normalized_entities tgt
USING new_normalized src
ON tgt.source_id = src.source_id
WHEN MATCHED THEN UPDATE SET
    tgt.normalized_name   = src.normalized_name,
    tgt.normalized_street = src.normalized_street,
    tgt.normalized_city   = src.normalized_city,
    tgt.normalized_state  = src.normalized_state,
    tgt.normalized_zip    = src.normalized_zip,
    tgt.blocking_key      = src.blocking_key,
    tgt.updated_at        = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
    source_id, normalized_name, normalized_street,
    normalized_city, normalized_state, normalized_zip,
    blocking_key, updated_at
) VALUES (
    src.source_id, src.normalized_name, src.normalized_street,
    src.normalized_city, src.normalized_state, src.normalized_zip,
    src.blocking_key, CURRENT_TIMESTAMP()
);
```

Handle deletes — remove records from `normalized_entities` when the source row is deleted:

```sql
DELETE FROM normalized_entities
WHERE source_id IN (
    SELECT source_id
    FROM source_entity_stream
    WHERE METADATA$ACTION = 'DELETE'
      AND METADATA$ISUPDATE = FALSE  -- True deletes only, not the DELETE half of an update
);
```

---

## Incremental Candidate Pair Generation

Key insight: when a new record arrives, it only needs to be paired with **existing records in the same block** — not re-paired with each other. Pairs among pre-existing records are already in `candidate_pairs`.

```sql
-- Only generate pairs involving new/changed records
INSERT INTO candidate_pairs (id_left, id_right, blocking_key)
SELECT
    new.source_id    AS id_left,
    existing.source_id AS id_right,
    new.blocking_key
FROM new_normalized new
JOIN normalized_entities existing
    ON  new.blocking_key  = existing.blocking_key
    AND new.source_id    != existing.source_id
WHERE NOT EXISTS (
    SELECT 1 FROM candidate_pairs cp
    WHERE (cp.id_left = new.source_id AND cp.id_right = existing.source_id)
       OR (cp.id_left = existing.source_id AND cp.id_right = new.source_id)
);
```

Also clean up pairs for deleted records:

```sql
DELETE FROM candidate_pairs
WHERE id_left  IN (SELECT source_id FROM source_entity_stream WHERE METADATA$ACTION = 'DELETE' AND METADATA$ISUPDATE = FALSE)
   OR id_right IN (SELECT source_id FROM source_entity_stream WHERE METADATA$ACTION = 'DELETE' AND METADATA$ISUPDATE = FALSE);
```

---

## Incremental Matching

Run matching tiers only on new candidate pairs (those without an existing entry in `match_results`):

```sql
-- Identify new pairs not yet scored
CREATE OR REPLACE TEMPORARY TABLE new_candidate_pairs AS
SELECT cp.*
FROM candidate_pairs cp
WHERE NOT EXISTS (
    SELECT 1 FROM match_results mr
    WHERE mr.id_left = cp.id_left AND mr.id_right = cp.id_right
);

-- Score new pairs and merge into match_results
MERGE INTO match_results tgt
USING (
    SELECT
        cp.id_left,
        cp.id_right,
        cp.blocking_key,
        JAROWINKLER_SIMILARITY(l.normalized_name, r.normalized_name) / 100.0   AS name_jw,
        JAROWINKLER_SIMILARITY(l.normalized_street, r.normalized_street) / 100.0 AS street_jw,
        IFF(l.normalized_zip = r.normalized_zip, 1.0, 0.0)                     AS zip_exact,
        -- Composite score — adapt weights from matching.md
        (0.6 * JAROWINKLER_SIMILARITY(l.normalized_name, r.normalized_name) / 100.0
         + 0.25 * JAROWINKLER_SIMILARITY(l.normalized_street, r.normalized_street) / 100.0
         + 0.15 * IFF(l.normalized_zip = r.normalized_zip, 1.0, 0.0))          AS composite_score
    FROM new_candidate_pairs cp
    JOIN normalized_entities l ON cp.id_left  = l.source_id
    JOIN normalized_entities r ON cp.id_right = r.source_id
) src
ON tgt.id_left = src.id_left AND tgt.id_right = src.id_right
WHEN NOT MATCHED THEN INSERT (
    id_left, id_right, blocking_key,
    name_jw, street_jw, zip_exact, composite_score,
    tier, created_at
) VALUES (
    src.id_left, src.id_right, src.blocking_key,
    src.name_jw, src.street_jw, src.zip_exact, src.composite_score,
    'tier1', CURRENT_TIMESTAMP()
);
```

---

## Entity Group Recalculation

A new match can bridge two previously separate entity groups, so group membership cannot be updated incrementally in a simple way. Two options:

### Option 1: Full Recalculation (recommended for < 100K confirmed matches)

Simpler and less error-prone. Re-run the connected-components query over all confirmed matches:

```sql
-- See blocking.md for the full connected-components pattern.
-- Truncate and rebuild entity_groups from match_results where composite_score >= threshold.
CREATE OR REPLACE TABLE entity_groups AS
WITH confirmed_matches AS (
    SELECT id_left, id_right
    FROM match_results
    WHERE composite_score >= <match_threshold>
),
-- ... (connected components logic from blocking.md)
```

### Option 2: Incremental Union-Find (for > 100K matches)

Only re-traverse graph components that include at least one newly added match edge. Requires tracking which `entity_id` values appear in new matches:

```sql
-- Step 1: Identify affected entity IDs
CREATE OR REPLACE TEMPORARY TABLE affected_ids AS
SELECT DISTINCT id_left AS entity_id FROM new_match_results
UNION
SELECT DISTINCT id_right FROM new_match_results;

-- Step 2: Find all group_ids that contain any affected entity
CREATE OR REPLACE TEMPORARY TABLE affected_groups AS
SELECT DISTINCT group_id
FROM entity_groups
WHERE entity_id IN (SELECT entity_id FROM affected_ids);

-- Step 3: Delete stale group assignments for affected components
DELETE FROM entity_groups
WHERE group_id IN (SELECT group_id FROM affected_groups);

-- Step 4: Re-run connected components for affected entities only
-- (Use the same union-find or graph traversal as the full recalc,
--  scoped to entities whose group_id was in affected_groups)
INSERT INTO entity_groups (entity_id, group_id, canonical_id)
-- ... scoped connected-components query ...
```

> **Trade-off:** Option 2 avoids re-processing unaffected components but is significantly more complex to implement and test. Only use it if full recalculation is too slow in practice.

---

## Incremental Pipeline DAG

Use Snowflake Tasks (not dynamic tables) for incremental processing. Streams are consumed on read — each task in the chain must read from the stream exactly once per cycle.

```
source_entity_stream
  └─> task_incremental_normalize        (scheduled, e.g. EVERY 1 HOUR)
        └─> task_incremental_block_and_pair
              └─> task_incremental_match
                    └─> task_recalc_groups
                          └─> task_refresh_entity_master
```

Example task definitions:

```sql
-- Root task: normalize new/changed records
CREATE OR REPLACE TASK task_incremental_normalize
    WAREHOUSE = <warehouse_name>
    SCHEDULE  = 'USING CRON 0 * * * * UTC'  -- every hour
    WHEN SYSTEM$STREAM_HAS_DATA('source_entity_stream')  -- skip if no changes
AS
    CALL sp_incremental_normalize();

-- Downstream tasks chain via AFTER
CREATE OR REPLACE TASK task_incremental_block_and_pair
    WAREHOUSE = <warehouse_name>
    AFTER     task_incremental_normalize
AS
    CALL sp_incremental_block_and_pair();

CREATE OR REPLACE TASK task_incremental_match
    WAREHOUSE = <warehouse_name>
    AFTER     task_incremental_block_and_pair
AS
    CALL sp_incremental_match();

CREATE OR REPLACE TASK task_recalc_groups
    WAREHOUSE = <warehouse_name>
    AFTER     task_incremental_match
AS
    CALL sp_recalc_entity_groups();

CREATE OR REPLACE TASK task_refresh_entity_master
    WAREHOUSE = <warehouse_name>
    AFTER     task_recalc_groups
AS
    CALL sp_refresh_entity_master();

-- Resume all tasks (tasks are suspended by default after CREATE)
ALTER TASK task_refresh_entity_master  RESUME;
ALTER TASK task_recalc_groups          RESUME;
ALTER TASK task_incremental_match      RESUME;
ALTER TASK task_incremental_block_and_pair RESUME;
ALTER TASK task_incremental_normalize  RESUME;
```

`SYSTEM$STREAM_HAS_DATA()` on the root task prevents the entire DAG from firing when there are no new records, saving compute costs.

---

## When to Fall Back to Full Refresh

Abandon the incremental path and run a full refresh when any of the following are true:

| Condition | Reason |
|-----------|--------|
| Source table schema changed | Stream may be invalidated or blocking keys affected |
| Blocking strategy changed | All pairs must be re-generated from scratch |
| Match thresholds tuned | Historical match decisions may flip; full re-score required |
| Stream staleness > 14 days | Standard retention expires; stream becomes invalid |
| Source change rate > 20% per cycle | Incremental overhead exceeds full-refresh cost |
| `SYSTEM$STREAM_BACKLOG_BYTES` very large | Accumulated lag; a single cycle may overwhelm task compute |

Check stream health before each scheduled cycle:

```sql
SELECT
    SYSTEM$STREAM_HAS_DATA('source_entity_stream')   AS has_data,
    -- Stale streams return an error on consumption; check with:
    SHOW STREAMS LIKE 'source_entity_stream';
-- Look for STALE = TRUE in results; if stale, drop/recreate the stream
-- and trigger a full refresh before resuming incremental cycles.
```
