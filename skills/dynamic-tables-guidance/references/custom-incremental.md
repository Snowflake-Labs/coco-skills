# Custom Incremental Dynamic Tables (Private Preview)

Custom incremental DTs let you define refresh logic using **imperative DML** (MERGE or INSERT INTO) instead of a declarative SELECT. This unlocks patterns that standard DTs can't express efficiently.

**When to use:** Standard DTs should always be your first choice. Use custom incremental only when:
- You need **stream-static joins** (fact stream + dimension snapshot)
- You need **append-only pipelines** (only process inserts, ignore updates/deletes)
- You need **user-defined semantics** (audit deletes, soft-delete, running aggregates)

## Syntax

```sql
CREATE OR REPLACE DYNAMIC TABLE my_dt (
  col1 TYPE, col2 TYPE  -- explicit columns required
)
  TARGET_LAG = '5 minutes'
  WAREHOUSE = my_wh
  REFRESH_MODE = CUSTOM_INCREMENTAL
  [ BACKFILL FROM existing_table ]
  REFRESH USING (
    -- MERGE INTO SELF or INSERT INTO SELF
  );
```

Key concepts:
- `SELF` references the DT being created (you cannot use the DT's name)
- `CHANGES(INFORMATION => { DEFAULT | APPEND_ONLY })` consumes changes since last refresh
- Tables outside `CHANGES()` are read as static snapshots at refresh time
- Explicit column schema is required (no `AS SELECT` inference)

## Pattern: Stream-Static Join (Append-Only)

Enrich new events with current dimension data. Only new events are processed — dimension changes don't trigger reprocessing.

```sql
CREATE OR REPLACE DYNAMIC TABLE enriched_clicks (
  click_id INT, user_id INT, page_title STRING,
  section STRING, click_ts TIMESTAMP
)
  TARGET_LAG = DOWNSTREAM
  WAREHOUSE = my_wh
  REFRESH USING (
    INSERT INTO SELF
    SELECT c.click_id, c.user_id, p.page_title, p.section, c.click_ts
    FROM clicks CHANGES(INFORMATION => APPEND_ONLY) AS c
    LEFT OUTER JOIN pages AS p ON c.page_id = p.page_id
  );
```

## Pattern: Stream-Static Join (MERGE with Updates/Deletes)

When the fact table has updates and deletes, use MERGE with `ROW_NUMBER()` dedup:

```sql
CREATE OR REPLACE DYNAMIC TABLE enriched_inventory (
  sku_id INT, product_name STRING, category STRING,
  warehouse_name STRING, region STRING, qty_on_hand INT
)
  TARGET_LAG = DOWNSTREAM
  WAREHOUSE = my_wh
  REFRESH USING (
    MERGE INTO SELF AS tgt
    USING (
      SELECT sku_id, product_name, category, warehouse_name, region,
             qty_on_hand, action
      FROM (
        SELECT s.sku_id, p.product_name, p.category,
               w.warehouse_name, w.region, s.qty_on_hand,
               s.METADATA$ACTION AS action,
               ROW_NUMBER() OVER (
                 PARTITION BY s.sku_id
                 ORDER BY CASE s.METADATA$ACTION WHEN 'INSERT' THEN 0 ELSE 1 END
               ) AS rn
        FROM stock CHANGES(INFORMATION => DEFAULT) AS s
        LEFT OUTER JOIN products AS p ON s.product_id = p.product_id
        LEFT OUTER JOIN warehouses AS w ON s.warehouse_id = w.warehouse_id
      )
      WHERE rn = 1
    ) AS src
    ON tgt.sku_id = src.sku_id
    WHEN MATCHED AND src.action = 'DELETE' THEN DELETE
    WHEN MATCHED AND src.action = 'INSERT' THEN
      UPDATE SET tgt.product_name = src.product_name,
                 tgt.category = src.category,
                 tgt.warehouse_name = src.warehouse_name,
                 tgt.region = src.region,
                 tgt.qty_on_hand = src.qty_on_hand
    WHEN NOT MATCHED AND src.action = 'INSERT' THEN
      INSERT (sku_id, product_name, category, warehouse_name, region, qty_on_hand)
      VALUES (src.sku_id, src.product_name, src.category, src.warehouse_name,
              src.region, src.qty_on_hand)
  );
```

## Example: Stream-Static Join End-to-End

A complete walkthrough showing how a stream-static join works in practice. Scenario: an IoT pipeline where sensor readings (high-volume, append-only) are enriched with device metadata (low-volume, rarely changes).

```sql
-- 1. Setup: fact table (append-only sensor readings) + dimension table (device registry)
CREATE TABLE sensor_readings (
  reading_id INT AUTOINCREMENT,
  device_id INT,
  temperature FLOAT,
  humidity FLOAT,
  reading_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
);
ALTER TABLE sensor_readings SET CHANGE_TRACKING = TRUE;

CREATE TABLE devices (
  device_id INT PRIMARY KEY,
  device_name STRING,
  location STRING,
  floor INT
);

-- 2. Custom incremental DT: enrich readings with device info
--    - sensor_readings is the STREAM side (CHANGES => APPEND_ONLY)
--    - devices is the STATIC side (read in full at each refresh, changes ignored)
CREATE OR REPLACE DYNAMIC TABLE enriched_readings (
  reading_id INT,
  device_id INT,
  device_name STRING,
  location STRING,
  floor INT,
  temperature FLOAT,
  humidity FLOAT,
  reading_ts TIMESTAMP
)
  TARGET_LAG = '1 minute'
  WAREHOUSE = iot_wh
  REFRESH USING (
    INSERT INTO SELF
    SELECT
      r.reading_id, r.device_id,
      d.device_name, d.location, d.floor,
      r.temperature, r.humidity, r.reading_ts
    FROM sensor_readings CHANGES(INFORMATION => APPEND_ONLY) AS r
    LEFT OUTER JOIN devices AS d ON r.device_id = d.device_id
  );
```

**What happens at each refresh:**
1. `CHANGES(APPEND_ONLY)` returns only new sensor readings since last refresh
2. Each new reading is joined to the **current** device metadata (static snapshot)
3. Results are appended to the DT — previously enriched rows are never touched
4. If a device name changes in `devices`, old readings keep the old name — only new readings pick up the update

**Why this matters:** A standard DT would reprocess ALL readings whenever a device name changes (since it depends on `devices`). The custom incremental version only processes new readings, making it orders of magnitude cheaper for high-volume fact tables with slowly-changing dimensions.

---

## Pattern: Audit Deletes Log

Append-only log of every deletion from a source table:

```sql
CREATE OR REPLACE DYNAMIC TABLE deletions_log (id INT, name STRING, email STRING)
  TARGET_LAG = DOWNSTREAM
  WAREHOUSE = my_wh
  INITIALIZE = ON_SCHEDULE
  REFRESH USING (
    INSERT INTO SELF
    SELECT * EXCLUDE (METADATA$ISUPDATE, METADATA$ACTION)
    FROM users CHANGES(INFORMATION => DEFAULT)
    WHERE NOT METADATA$ISUPDATE AND METADATA$ACTION = 'DELETE'
  );
```

## Limitations (PrPr)

- No cloning or replication
- No DCM/dbt integration yet
- No data governance policies on custom incremental DTs
- No CREATE OR ALTER — must use CREATE OR REPLACE
- Correctness is the user's responsibility (not delayed-view semantics)
