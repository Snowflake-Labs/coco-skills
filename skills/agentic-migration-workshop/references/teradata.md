# Teradata to Snowflake Reference

## Architecture Differences

| Aspect | Teradata | Snowflake |
|--------|----------|-----------|
| Architecture | Shared-nothing MPP; tightly coupled compute & storage | Decoupled compute, storage, and cloud services |
| Data distribution | Primary Index (PI) hash-based distribution across AMPs | Automatic micro-partitioning; no user-managed distribution |
| Storage | DBA-managed; data distributed across AMPs | Centralized object storage; automatic management |
| Compute | Fixed nodes; scaling requires hardware changes | Elastic virtual warehouses; instant scale up/down/out |
| Concurrency | Workload management (TASM/TIWM) with priority classes | Warehouses with multi-cluster auto-scaling |
| Statistics | Manual `COLLECT STATISTICS` | Automatic; no user intervention needed |
| Maintenance | DBA tasks: stats, space management, skew monitoring | Fully managed; all maintenance automated |

## Session Modes

Teradata has two session modes that affect SQL behavior:

| Behavior | ANSI Mode | Teradata (TERA) Mode |
|----------|-----------|---------------------|
| String comparisons | CASESPECIFIC | NOT CASESPECIFIC |
| Transaction | Explicit COMMIT required | Auto-commit after each statement |
| Truncation | Error on truncation | Silently truncates |

**Snowflake mapping:**
- ANSI Mode CASESPECIFIC → No changes needed
- ANSI Mode NOT CASESPECIFIC → Add `COLLATE 'en-cs'` in column definition
- TERA Mode CASESPECIFIC → Convert string comparisons to `RTRIM(expression)`
- TERA Mode NOT CASESPECIFIC → Convert string comparisons to `RTRIM(UPPER(expression))`

See SnowConvert AI documentation for detailed session mode translation rules.

## Data Type Mapping

| Teradata | Snowflake | Notes |
|----------|-----------|-------|
| BYTEINT | TINYINT / NUMBER | 1-byte signed integer |
| SMALLINT | SMALLINT / NUMBER | |
| INTEGER | INTEGER / NUMBER | |
| BIGINT | BIGINT / NUMBER | |
| DECIMAL(p,s) / NUMERIC(p,s) | NUMBER(p,s) | |
| FLOAT / REAL / DOUBLE PRECISION | FLOAT | |
| NUMBER | NUMBER(38,0) | Teradata NUMBER is different from Oracle NUMBER |
| CHAR(n) | VARCHAR | SnowConvert maps CHAR → VARCHAR for Teradata |
| VARCHAR(n) | VARCHAR(n) | |
| CLOB | VARCHAR(16777216) | 16MB max; not directly supported as CLOB |
| BYTE(n) | BINARY(n) | |
| VARBYTE(n) | BINARY(n) | |
| BLOB | BINARY(8388608) | 8MB max; not directly supported as BLOB |
| DATE | DATE | Teradata DATE is date-only (unlike Oracle) |
| TIME | TIME | |
| TIME WITH TIME ZONE | TIME | TIME WITH TIME ZONE not supported; stored as wall-clock only |
| TIMESTAMP | TIMESTAMP_NTZ | |
| TIMESTAMP WITH TIME ZONE | TIMESTAMP_TZ | |
| INTERVAL types (all) | VARCHAR / date functions | INTERVAL not supported; use DATEDIFF/DATEADD |
| PERIOD(DATE) | Two DATE columns (start, end) | No direct PERIOD type; split into start/end |
| PERIOD(TIMESTAMP) | Two TIMESTAMP columns | No direct PERIOD type |
| PERIOD(TIME) | Two TIME columns or VARCHAR | |
| JSON | VARIANT | |
| XML | VARIANT | |
| ARRAY | ARRAY | |
| ST_GEOMETRY | GEOGRAPHY or GEOMETRY | |
| UDT (User-Defined Type) | Not supported | Flatten to native types |
| DATASET | Not supported | |
| TD_ANYTYPE | Not supported | |

## Feature Mapping

| Teradata Feature | Snowflake Equivalent |
|-----------------|---------------------|
| PRIMARY INDEX (PI) | Not needed (Snowflake auto-distributes) |
| Secondary indexes (USI/NUSI) | Not needed (micro-partition pruning) |
| Hash indexes | Not needed |
| Join indexes | Materialized views or Dynamic Tables |
| PARTITION BY (TD-style) | Micro-partitions (automatic); CLUSTER BY for ordering |
| MULTISET tables | Default behavior (Snowflake allows duplicates) |
| SET tables | Add DISTINCT or UNIQUE constraints; handle in INSERT |
| Volatile tables | TEMPORARY TABLE |
| Global temporary tables | TEMPORARY TABLE |
| COLLECT STATISTICS | Not needed (Snowflake auto-manages statistics) |
| LOCKING ROW FOR ACCESS | Not needed (Snowflake MVCC handles concurrency) |
| QUALIFY clause | QUALIFY (Snowflake supports natively) |
| SAMPLE | SAMPLE or TABLESAMPLE |
| TITLE column alias | AS alias |
| FORMAT column format | TO_CHAR() for display formatting |
| CASESPECIFIC / NOT CASESPECIFIC | COLLATE or UPPER()/LOWER() |
| COMPRESS values | Not needed (Snowflake auto-compresses) |
| FALLBACK / NO FALLBACK | Not needed (Snowflake has built-in redundancy) |
| Journal tables | Streams (change tracking) |
| Macros | Stored procedures or views (macros not supported) |
| Stored procedures (SPL) | Snowflake Scripting or JavaScript procedures |
| UDFs | Snowflake UDFs (SQL, JavaScript, Python) |
| Teradata Scheduler | Snowflake Tasks |
| Access logging (DBQL) | ACCESS_HISTORY view (ACCOUNT_USAGE) |
| Row-level security | Row Access Policies |
| BTEQ scripts | Snowflake SQL worksheets, SnowSQL, or Python scripts |
| FastLoad | COPY INTO (bulk load) |
| FastExport | COPY INTO @stage (bulk unload) |
| MultiLoad | COPY INTO with MERGE pattern |
| TPT (Teradata Parallel Transporter) | Snowpipe or COPY INTO |
| TASM / TIWM (workload mgmt) | Warehouses + resource monitors |
| Data dictionary (DBC views) | INFORMATION_SCHEMA / ACCOUNT_USAGE |
| Surrogate keys | AUTOINCREMENT or SEQUENCE |

## Databases to Exclude from Migration

The following Teradata system databases should NOT be migrated:
`DBC`, `Sys_Calendar`, `SystemFe`, `SYSJDBC`, `SYSLIB`, `SYSSPATIAL`, `SYSUDTLIB`, `SysAdmin`, `TDStats`, `TD_SYSFNLIB`, `TD_SYSXML`, `TDPUSER`, `tdwm`, `All`, `Crashdumps`, `dbcmngr`, `Default`, `External_AP`, `EXTUSER`, `LockLogShredder`, `PUBLIC`, `SQLJ`, `SYSBAR`, `SYSUIF`, `TD_SERVER_DB`, `TD_SYSGPL`, `viewpoint`, `console`

## Common Teradata SQL Patterns

### QUALIFY (Native Support)
```sql
-- Teradata (same in Snowflake)
SELECT customer_id, order_date, amount
FROM orders
QUALIFY ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date DESC) = 1;
```

### SAMPLE
```sql
-- Teradata
SELECT * FROM my_table SAMPLE 100;
SELECT * FROM my_table SAMPLE 0.10;  -- 10%

-- Snowflake
SELECT * FROM my_table SAMPLE (100 ROWS);
SELECT * FROM my_table SAMPLE (10);  -- 10% of rows
```

### SET Table Behavior (Auto-Dedup)
```sql
-- Teradata SET table (auto-dedup on insert)
CREATE SET TABLE my_table (...);

-- Snowflake: No SET tables; handle dedup explicitly
INSERT INTO my_table
SELECT DISTINCT * FROM source_table;
-- Or use MERGE to prevent duplicates
```

### PERIOD Columns → Split Columns
```sql
-- Teradata
CREATE TABLE emp (
  emp_id INTEGER,
  emp_period PERIOD(DATE),
  salary DECIMAL(10,2)
);
SELECT emp_id FROM emp WHERE emp_period P_INTERSECT PERIOD(DATE '2024-01-01', DATE '2024-12-31');

-- Snowflake
CREATE TABLE emp (
  emp_id INTEGER,
  emp_period_start DATE,
  emp_period_end DATE,
  salary NUMBER(10,2)
);
SELECT emp_id FROM emp
WHERE emp_period_start < '2024-12-31' AND emp_period_end > '2024-01-01';
```

### NORMALIZE (Merge Overlapping Periods)
```sql
-- Teradata
SELECT emp_id, BEGIN(emp_period), END(emp_period)
FROM emp NORMALIZE ON emp_period;

-- Snowflake: Rewrite with window functions (gap-and-islands pattern)
WITH ordered AS (
  SELECT emp_id, emp_period_start, emp_period_end,
    CASE WHEN emp_period_start <= LAG(emp_period_end) OVER (PARTITION BY emp_id ORDER BY emp_period_start)
         THEN 0 ELSE 1 END AS new_group
  FROM emp
),
grouped AS (
  SELECT *, SUM(new_group) OVER (PARTITION BY emp_id ORDER BY emp_period_start) AS grp
  FROM ordered
)
SELECT emp_id, MIN(emp_period_start), MAX(emp_period_end)
FROM grouped GROUP BY emp_id, grp;
```

### EXPAND ON (Temporal Expansion)
```sql
-- Teradata
SELECT emp_id, BEGIN(pd) AS cal_date, salary
FROM emp EXPAND ON emp_period AS pd BY INTERVAL '1' DAY;

-- Snowflake: Use GENERATOR or date spine
SELECT e.emp_id, d.cal_date, e.salary
FROM emp e
JOIN (
  SELECT DATEADD('day', seq, '2020-01-01')::DATE AS cal_date
  FROM TABLE(GENERATOR(ROWCOUNT => 3650))
  t(seq)
) d ON d.cal_date >= e.emp_period_start AND d.cal_date < e.emp_period_end;
```

### Date Arithmetic
```sql
-- Teradata: integer date format (days since 1900-01-01)
-- When exporting, always export as DATE strings, not internal integers

-- Teradata interval arithmetic
SELECT order_date + INTERVAL '30' DAY FROM orders;
-- Snowflake
SELECT DATEADD('day', 30, order_date) FROM orders;

-- Teradata: date - date = integer (days)
SELECT date1 - date2 FROM t;
-- Snowflake
SELECT DATEDIFF('day', date2, date1) FROM t;
```

### SEL Abbreviation
```sql
-- Teradata allows abbreviated keywords
SEL * FROM my_table;
INS INTO my_table VALUES (1);
DEL FROM my_table WHERE id = 1;
UPD my_table SET val = 'x' WHERE id = 1;

-- Snowflake: Use full keywords
SELECT * FROM my_table;
INSERT INTO my_table VALUES (1);
DELETE FROM my_table WHERE id = 1;
UPDATE my_table SET val = 'x' WHERE id = 1;
```

### TITLE and FORMAT
```sql
-- Teradata
SELECT emp_name (TITLE 'Employee Name'), salary (FORMAT '$$$,$$9.99')
FROM employees;

-- Snowflake
SELECT emp_name AS "Employee Name", TO_CHAR(salary, '$999,999.99') AS salary
FROM employees;
```

### Teradata-Specific Functions
```sql
-- Teradata                                -- Snowflake
CHARACTERS(str)                            LENGTH(str)
ZEROIFNULL(val)                            ZEROIFNULL(val)  -- supported
NULLIFZERO(val)                            NULLIFZERO(val)  -- supported
HASHROW(cols)                              HASH(cols)
INDEX(str, 'sub')                          POSITION('sub' IN str) or CHARINDEX('sub', str)
OREPLACE(str, 'old', 'new')               REPLACE(str, 'old', 'new')
OTRANSLATE(str, 'from', 'to')             TRANSLATE(str, 'from', 'to')
STRTOK(str, delim, n)                      STRTOK(str, delim, n)  -- same
RESET WHEN condition                       Rewrite with CASE in window functions
NAMED 'alias'                              AS alias
COALESCE(a, b)                             COALESCE(a, b)  -- same
```

### LOCKING Clause → Remove
```sql
-- Teradata
LOCKING TABLE my_table FOR ACCESS
SELECT * FROM my_table;

LOCKING ROW FOR ACCESS
SELECT * FROM big_table WHERE id = 123;

-- Snowflake: Remove all LOCKING clauses
SELECT * FROM my_table;
SELECT * FROM big_table WHERE id = 123;
```

### COLLECT STATISTICS → Remove
```sql
-- Teradata
COLLECT STATISTICS ON my_table COLUMN (customer_id);
COLLECT STATISTICS ON my_table INDEX (primary_idx);
HELP STATISTICS my_table;

-- Snowflake: Remove all COLLECT STATISTICS; Snowflake auto-manages
-- No action needed
```

### BTEQ Script Patterns
```sql
-- Teradata BTEQ
.LOGON server/user,password
.SET WIDTH 200
.EXPORT FILE=/tmp/output.csv
SELECT * FROM my_table;
.EXPORT RESET
.IF ERRORCODE <> 0 THEN .GOTO ERROR_HANDLER
.LOGOFF
.QUIT

-- Snowflake equivalent (SnowSQL or Python)
-- Use SnowSQL CLI:
-- snowsql -a account -u user -q "SELECT * FROM my_table" -o output_format=csv -o output_file=/tmp/output.csv
-- Or use SnowConvert AI to auto-translate BTEQ → Python
```

### Macro → Stored Procedure
```sql
-- Teradata
CREATE MACRO get_recent_orders AS (
  SELECT * FROM orders WHERE order_date > CURRENT_DATE - 30;
);
EXEC get_recent_orders;

-- Snowflake: Use stored procedure or view
CREATE OR REPLACE VIEW get_recent_orders AS
  SELECT * FROM orders WHERE order_date > DATEADD('day', -30, CURRENT_DATE());
-- or
CREATE OR REPLACE PROCEDURE get_recent_orders()
  RETURNS TABLE()
  LANGUAGE SQL
AS
BEGIN
  LET res RESULTSET := (SELECT * FROM orders WHERE order_date > DATEADD('day', -30, CURRENT_DATE()));
  RETURN TABLE(res);
END;
```

## DDL Conversion Checklist

1. **Remove** `PRIMARY INDEX`, `PARTITION BY` (Teradata-style), `UNIQUE PRIMARY INDEX`
2. **Remove** `SET` / `MULTISET` table keywords; handle dedup in INSERT logic for SET tables
3. **Remove** `FALLBACK` / `NO FALLBACK`, `JOURNAL`, `FREESPACE`, `CHECKSUM`
4. **Remove** all secondary index DDL (USI, NUSI, hash, join indexes)
5. **Remove** `COMPRESS` value lists (Snowflake auto-compresses)
6. **Remove** `COLLECT STATISTICS` statements
7. **Remove** `LOCKING` clauses
8. **Convert** `BYTEINT` → `TINYINT` or `NUMBER`
9. **Convert** `PERIOD(DATE/TIMESTAMP)` → two separate columns (start/end)
10. **Convert** `INTERVAL` types → `VARCHAR` or rewrite with DATEADD/DATEDIFF
11. **Convert** `CLOB` → `VARCHAR(16777216)`, `BLOB` → `BINARY(8388608)`
12. **Convert** `CHAR` → `VARCHAR` (SnowConvert default for Teradata)
13. **Replace** VOLATILE tables → `TEMPORARY TABLE`
14. **Replace** macros → stored procedures or views
15. **Note** constraints: PK, FK, UNIQUE defined but **not enforced** in Snowflake

## Script Translation Tools

| Teradata Tool | SnowConvert Translation Target |
|---------------|-------------------------------|
| BTEQ | → Snowflake SQL or Python scripts |
| FastLoad | → Python with COPY INTO |
| MultiLoad | → Python with MERGE + COPY INTO |
| TPT | → Python scripts |
| Stored procedures | → Snowflake Scripting or JavaScript |
| Macros | → Views or stored procedures |

## Data Extraction Methods

| Method | Best For |
|--------|---------|
| BTEQ .EXPORT | Small to medium table extraction |
| FastExport | Large table bulk export |
| TPT (export operator) | High-performance parallel extraction |
| SnowConvert AI (file-based) | DDL export scripts → stage → COPY INTO |

## Common Pitfalls

1. **Primary Index removal**: Removing PI changes data distribution; Snowflake handles this automatically. No action needed.
2. **SET table semantics**: Teradata SET tables reject duplicates on INSERT; Snowflake allows all duplicates. Add dedup logic.
3. **TERA mode string comparison**: NOT CASESPECIFIC default means case-insensitive; Snowflake is case-sensitive. Wrap in UPPER().
4. **INTERVAL types**: Not supported in Snowflake; rewrite all INTERVAL arithmetic with DATEADD/DATEDIFF.
5. **PERIOD types**: Not supported; split into two columns and rewrite temporal predicates.
6. **Date integer format**: Teradata stores DATE internally as integer (days since 1900-01-01); export as DATE strings, not integers.
7. **QUALIFY**: Snowflake supports QUALIFY natively — this is one of the easiest translations.
8. **Macros**: Not supported; convert to views (for simple queries) or procedures (for parameterized logic).
9. **System databases**: Exclude all Teradata system databases (DBC, Sys_Calendar, etc.) from migration scope.
10. **Surrogate key lifecycle**: Surrogate keys from Teradata may behave differently in Snowflake AUTOINCREMENT; synchronize during cutover.
