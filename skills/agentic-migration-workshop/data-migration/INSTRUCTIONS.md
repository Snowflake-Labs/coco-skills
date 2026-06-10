
# Workshop Session: Data Migration (Day 4 — Data Migration)

## Session Overview

**Present to user:**
> Welcome to **Data Migration** — this is Day 4 of the LiftOff framework. Now that your schema is in Snowflake, we'll load your data and make sure everything arrived correctly.
>
> Here's our plan:
> 1. Figure out the best way to get your data into Snowflake
> 2. Set up staging infrastructure (file formats, stages)
> 3. Generate and run load scripts
> 4. Validate completeness — row counts, aggregates, and spot-checks
> 5. Run through testing phases
> 6. Produce a Reconciliation Report
>
> Let's start with how your data is currently stored.

## Prerequisites
- Target Snowflake tables exist (from Schema Conversion or pre-existing)
- Source data accessible (files, cloud storage, or direct connection)
- `references/best-practices.md` read

## Session Flow

### Part 1: Determine Data Source

**Ask the user** (via `ask_user_question`) how they'll provide source data:
- CSV/Parquet/JSON files (local or cloud storage)
- Direct export from source database (I'll generate export commands)
- Data is already in cloud storage (S3, Azure Blob, GCS)
- Snowflake data sharing / replication
- SnowConvert AI data migration (automated, supports SQL Server & Redshift direct)

**Based on selection, gather details:**

| Source Type | What I Need From You |
|-------------|---------------------|
| Local files | File paths, format, delimiter, encoding, header row |
| Cloud storage | Bucket/container URL, credentials or storage integration, file format |
| Direct export | Source connection details, preferred export tool |
| Data sharing | Provider account, share name |
| SnowConvert AI | SnowConvert AI handles extraction and loading |

### Part 2: Create Staging Infrastructure

**Explain to user:**
> Before we can load data, Snowflake needs two things: a **file format** (how to parse your files) and a **stage** (where to find them). Let me set those up.

**Generate and execute** file format DDL:

```sql
CREATE OR REPLACE FILE FORMAT migration_csv_format
  TYPE = 'CSV'
  FIELD_DELIMITER = ','
  SKIP_HEADER = 1
  FIELD_OPTIONALLY_ENCLOSED_BY = '"'
  NULL_IF = ('NULL', 'null', '')
  EMPTY_FIELD_AS_NULL = TRUE
  ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE;
```

**Generate and execute** stage DDL (adapt based on source type):

For local files:
```sql
CREATE OR REPLACE STAGE migration_stage
  FILE_FORMAT = migration_csv_format;
```

For S3:
```sql
CREATE OR REPLACE STAGE migration_stage
  URL = 's3://bucket/path/'
  STORAGE_INTEGRATION = [integration_name]
  FILE_FORMAT = migration_csv_format;
```

Execute via `snowflake_sql_execute`.

### Part 3: Generate Load Scripts

**For each table**, generate COPY INTO:

```sql
COPY INTO target_db.target_schema.table_name
  FROM @migration_stage/table_name/
  FILE_FORMAT = migration_csv_format
  ON_ERROR = 'CONTINUE'
  PURGE = FALSE;
```

**Handle special cases** (explain each to user):

| Scenario | Approach | Why |
|----------|----------|-----|
| Large tables (>1B rows) | Multiple files, parallel loading | Snowflake processes files in parallel across nodes |
| Semi-structured data | COPY INTO with VARIANT column, then FLATTEN | Preserves nested structure for later querying |
| Incremental loads | COPY with FORCE=FALSE | Skips already-loaded files automatically |
| Type mismatches | Explicit CAST in SELECT from stage | Prevents silent truncation |
| Date format differences | DATE_FORMAT/TIMESTAMP_FORMAT in file format | Ensures correct parsing |

**Platform-specific export guidance:**

**Redshift → Snowflake (via S3):**
> "The recommended path is: Unload from Redshift to Parquet files in S3, then COPY from S3 into Snowflake. Important: your S3 bucket should be in the same region as your Redshift cluster. Redshift distribution styles and sort keys don't need to be preserved — Snowflake auto-optimizes data layout. No VACUUM or ANALYZE needed post-load."

**SQL Server → Snowflake:**
> "You can use BCP for bulk export, or if you have SnowConvert AI, it supports direct streaming from SQL Server to Snowflake with real-time progress monitoring."

**Oracle → Snowflake:**
> "Use Data Pump (expdp), SQL*Plus spool, or UTL_FILE to export to CSV/Parquet, then upload to cloud storage."

**Teradata → Snowflake:**
> "Use BTEQ .EXPORT, FastExport, or TPT for extraction, then upload to cloud storage."

**Performance tips to share:**
> "A few tips to get the best load performance: split large files into 100-250MB chunks, use a larger warehouse for the initial bulk load (you can scale down after), and use PARQUET format when possible — it preserves schema and compresses better."

**CHECKPOINT:** *"Here are the load scripts I've generated. Want to review them before we run?"*

### Part 4: Execute Data Load

**If local files:** Use PUT + COPY pattern:
```sql
PUT file:///path/to/data/*.csv @migration_stage/table_name/ AUTO_COMPRESS=TRUE;

COPY INTO target_db.target_schema.table_name
  FROM @migration_stage/table_name/
  FILE_FORMAT = migration_csv_format;
```

**Execute** via `snowflake_sql_execute` and capture results.

**Present load status:**
```
| Table | Files Loaded | Rows Loaded | Errors | Status |
|-------|-------------|-------------|--------|--------|
```

**If errors occur:**
- Query COPY_HISTORY for error details
- Show rejected rows from VALIDATE function
- Suggest fixes (type casting, null handling, encoding)
- *"Don't worry — errors during loading are normal. Let me diagnose what happened..."*

### Part 5: Data Validation

**Explain to user:**
> Now for the most critical part: making sure everything arrived correctly. I'll run validation at multiple levels — from simple row counts up to cell-level spot-checks.

**Schema Validation** (structural integrity):
- Table names match exactly
- Column names preserved correctly
- Ordinal positions maintained
- Data types converted appropriately
- Character lengths and numeric precision preserved

**Row Count Validation:**
```sql
SELECT '[table_name]' AS table_name,
       COUNT(*) AS snowflake_count
FROM target_db.target_schema.table_name;
```
Compare against source counts (ask user to provide or query source).

**Null Distribution Check:**
```sql
SELECT
  COUNT(*) AS total_rows,
  COUNT(col1) AS col1_non_null,
  COUNT(col2) AS col2_non_null
FROM target_db.target_schema.table_name;
```

**Aggregate Validation** (sum, min, max on numeric columns):
```sql
SELECT
  SUM(amount_col) AS total_amount,
  MIN(date_col) AS min_date,
  MAX(date_col) AS max_date
FROM target_db.target_schema.table_name;
```

**Statistical Validation:**
```sql
SELECT
  MIN(numeric_col) AS min_val,
  MAX(numeric_col) AS max_val,
  AVG(numeric_col) AS avg_val,
  STDDEV(numeric_col) AS stddev_val,
  COUNT(DISTINCT key_col) AS distinct_count
FROM target_db.target_schema.table_name;
```

**Sample Spot-Check:**
```sql
SELECT * FROM target_db.target_schema.table_name
WHERE primary_key_col IN ([user-provided sample PKs])
ORDER BY primary_key_col;
```

**Present validation results using this scale:**

| Level | Meaning | Action |
|-------|---------|--------|
| Pass | Values match exactly | No action needed |
| Warning | Minor differences (e.g., higher precision) | Verify acceptable business impact |
| Fail | Values don't match | Investigation required |

### Part 6: Testing Phases

**Explain to user:**
> Beyond data validation, a production migration needs systematic testing. Here's the testing roadmap — we can work through whichever phases apply to your situation.

| Phase | Purpose | Typical Duration |
|-------|---------|-----------------|
| Integration Testing | Verify data flows between migrated components | 1-2 weeks |
| SIT (System Integration) | Validate full system behavior across all integrations | 1-2 weeks |
| Performance Testing | Benchmark queries against source baseline | 1 week |
| Load & Stress Testing | Simulate peak concurrency, validate auto-scaling | 3-5 days |
| Security Testing | Test RBAC, masking policies, SSO/MFA | 3-5 days |
| UAT (User Acceptance) | Business users validate reports and workflows | 2-3 weeks |
| Parallel Run | Run both systems simultaneously, compare outputs | 2-4 weeks |

**Validation layers:**

| Layer | What to Check |
|-------|---------------|
| Completeness | Row counts, table counts, object counts |
| Accuracy | Cell-level comparison on critical tables |
| Integrity | Referential integrity, business rule validation |
| Consistency | Cross-table aggregation checks, business metric reconciliation |
| Timeliness | Incremental data freshness, pipeline latency |

**Performance benchmarking:**
1. Capture baseline query set from source (top N by frequency/cost)
2. Execute same queries against Snowflake; compare runtimes
3. Use Query Profile to analyze slow queries
4. Right-size warehouses based on workload patterns
5. Add CLUSTER BY for large tables (>1TB) with frequent range filters

### Part 7: Reconciliation Report

**Compile** all validation results into a polished report:

```
# Data Migration Reconciliation Report
## Date: [Today]

### Load Summary
| Table | Source Rows | Snowflake Rows | Match | Errors |
|-------|------------|---------------|-------|--------|

### Aggregate Checks
| Table | Column | Source Value | Snowflake Value | Match |
|-------|--------|-------------|-----------------|-------|

### Issues Found
| Issue | Table | Details | Resolution |
|-------|-------|---------|------------|

### Testing Summary
| Test Phase | Status | Notes |
|-----------|--------|-------|

### Data Quality Notes
- [Any encoding issues, truncations, null handling differences]
```

**Present to user:**
> Here's your Data Migration Reconciliation Report. Everything that passed is green-lit for production. Let me walk you through any items that need attention.

**CHECKPOINT:** Wait for user sign-off before proceeding.

## Session Wrap-Up

**Present to user:**
> Data Migration complete! Here's the summary:
> - [X] tables loaded successfully
> - [Y] total rows migrated
> - Row counts match: [pass/fail]
> - Aggregate checks: [pass/fail]
> - [Any issues and their resolutions]

## Next Session

If Full Workshop → proceed to **Query Translation** (read `query-translation/SKILL.md`)
