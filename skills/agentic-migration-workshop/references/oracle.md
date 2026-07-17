# Oracle to Snowflake Reference

## Architecture Differences

| Aspect | Oracle | Snowflake |
|--------|--------|-----------|
| Architecture | Monolithic or shared-disk (RAC); tightly coupled compute & storage | Decoupled compute, storage, and cloud services |
| Storage | DBA-managed on local disks, SAN, NAS (filesystems/ASM) | Centralized object storage with auto micro-partitioning |
| Compute | Fixed server resources (CPU, Memory, I/O) | Elastic, on-demand virtual warehouses |
| Concurrency | Limited by server hardware and session/process limits | High concurrency via multi-cluster warehouses |
| Scaling | Vertical (more powerful server) or horizontal (RAC). Often requires downtime | Instant scale up/down/out (seconds); storage scales automatically |
| Maintenance | DBA tasks: index rebuilds, statistics gathering, tablespace management | Fully managed; maintenance automated in background |
| Constraints | PK, FK, UNIQUE, CHECK all enforced | Only NOT NULL enforced; PK/FK/UNIQUE are metadata-only |

## Data Type Mapping

| Oracle | Snowflake | Notes |
|--------|-----------|-------|
| NUMBER(p,s) | NUMBER(p,s) | Direct mapping |
| NUMBER (no precision) | NUMBER(38,0) | Unspecified Oracle NUMBER → max precision integer |
| BINARY_FLOAT | FLOAT | Single-precision |
| BINARY_DOUBLE | FLOAT | Double-precision |
| VARCHAR2(n) | VARCHAR(n) | Snowflake max 16MB |
| NVARCHAR2(n) | VARCHAR(n) | Snowflake native UTF-8; N-prefix types unnecessary |
| CHAR(n) | CHAR(n) | Or VARCHAR(n) |
| NCHAR(n) | CHAR(n) | Snowflake native UTF-8 |
| CLOB | VARCHAR(16777216) | 16MB max |
| NCLOB | VARCHAR(16777216) | 16MB max |
| BLOB | BINARY(8388608) | 8MB max; consider external stage for larger |
| RAW(n) | BINARY(n) | |
| LONG | VARCHAR(16777216) | Deprecated in Oracle |
| LONG RAW | BINARY(8388608) | Deprecated in Oracle |
| DATE | TIMESTAMP_NTZ | Oracle DATE includes time component (critical difference!) |
| TIMESTAMP | TIMESTAMP_NTZ | |
| TIMESTAMP WITH TIME ZONE | TIMESTAMP_TZ | |
| TIMESTAMP WITH LOCAL TIME ZONE | TIMESTAMP_LTZ | |
| INTERVAL YEAR TO MONTH | VARCHAR | Store as string; use date functions for calculations |
| INTERVAL DAY TO SECOND | VARCHAR | Store as string; use date functions for calculations |
| BOOLEAN (21c+) | BOOLEAN | |
| XMLTYPE | VARIANT | Parse XML to VARIANT |
| SDO_GEOMETRY | GEOGRAPHY or GEOMETRY | Snowflake geospatial types |
| ROWID / UROWID | Not needed | Snowflake does not use ROWIDs |
| BFILE | External stage | Reference files in external storage |

## Feature Mapping

| Oracle Feature | Snowflake Equivalent |
|---------------|---------------------|
| Tablespaces | Not needed (Snowflake manages storage) |
| Partitioning (range/list/hash/composite) | Micro-partitions (automatic); use CLUSTER BY for ordering |
| Bitmap indexes | Automatic micro-partition pruning |
| B-tree indexes | Not needed; Snowflake auto-optimizes |
| Function-based indexes | CLUSTER BY on expressions |
| Materialized views | MATERIALIZED VIEW or Dynamic Tables |
| Materialized view logs | Streams (for change tracking) |
| Synonyms | Fully qualified names or wrapper views |
| Database links (DBLinks) | Data sharing, external tables, or Snowpipe |
| Sequences | SEQUENCE (native support) |
| PL/SQL packages | Separate procedures + optional shared state via tables/stages |
| PL/SQL procedures | Snowflake Scripting (SQL) or JavaScript procedures |
| PL/SQL functions | Snowflake UDFs (SQL, JavaScript, Python) |
| Pipelined table functions | Snowflake UDTFs |
| Triggers (DML/DDL) | Streams + Tasks (event-driven) |
| Oracle Scheduler (DBMS_SCHEDULER) | Tasks (with CRON schedules) |
| Flashback queries | Time Travel (SELECT ... AT/BEFORE) |
| Flashback Data Archive | Time Travel + Fail-Safe |
| Virtual Private Database (VPD) | Row Access Policies |
| Data Redaction | Dynamic Data Masking Policies |
| Advanced Queuing (AQ) | Streams + Tasks or external messaging |
| Autonomous transactions | Not directly supported; redesign with separate transactions |
| Global temporary tables | TEMPORARY TABLE |
| External tables | External tables (S3/Azure/GCS) |
| Oracle hints (`/*+ ... */`) | Remove; Snowflake auto-optimizes (no hint system) |
| AWR / ASH performance views | Query History (ACCOUNT_USAGE.QUERY_HISTORY), Query Profile |
| DUAL table | Not needed; `SELECT 1;` is valid |
| Edition-based redefinition | Not applicable; use zero-downtime deployment via CREATE OR REPLACE |
| Oracle Data Pump (expdp/impdp) | Extract to files → Stage → COPY INTO |
| SQL*Loader | COPY INTO from staged files |
| UTL_FILE | Stages + COPY INTO / GET / PUT |
| DBMS_OUTPUT | SYSTEM$LOG() or RETURN |
| DBMS_LOB | VARCHAR/BINARY operations |
| DBMS_SQL | EXECUTE IMMEDIATE |
| %TYPE / %ROWTYPE | Not supported; use explicit type declarations |
| BULK COLLECT / FORALL | Rewrite as set-based SQL (preferred) or use RESULTSET |
| PRAGMA directives | Remove (not applicable) |
| Object types / nested tables / varrays | Flatten to native types; use VARIANT/ARRAY/OBJECT |

## Common PL/SQL to Snowflake Scripting Patterns

### DUAL Table
```sql
-- Oracle
SELECT SYSDATE FROM DUAL;
SELECT seq.NEXTVAL FROM DUAL;

-- Snowflake
SELECT CURRENT_TIMESTAMP();
SELECT my_schema.seq.NEXTVAL;
```

### Outer Join (+) Syntax → ANSI JOIN
```sql
-- Oracle (proprietary)
SELECT e.name, d.dept_name
FROM employees e, departments d
WHERE e.dept_id = d.dept_id(+);

-- Snowflake (ANSI required)
SELECT e.name, d.dept_name
FROM employees e
LEFT JOIN departments d ON e.dept_id = d.dept_id;
```

### CONNECT BY → Recursive CTE
```sql
-- Oracle
SELECT employee_id, manager_id, LEVEL, SYS_CONNECT_BY_PATH(name, '/') AS path
FROM employees
START WITH manager_id IS NULL
CONNECT BY PRIOR employee_id = manager_id
ORDER SIBLINGS BY name;

-- Snowflake
WITH RECURSIVE org AS (
  SELECT employee_id, manager_id, name, 1 AS lvl, '/' || name AS path
  FROM employees WHERE manager_id IS NULL
  UNION ALL
  SELECT e.employee_id, e.manager_id, e.name, o.lvl + 1, o.path || '/' || e.name
  FROM employees e JOIN org o ON e.manager_id = o.employee_id
)
SELECT employee_id, manager_id, lvl, path FROM org
ORDER BY path;
```

### DECODE → CASE or DECODE
```sql
-- Oracle
SELECT DECODE(status, 'A', 'Active', 'I', 'Inactive', 'Unknown') FROM items;

-- Snowflake (DECODE is supported)
SELECT DECODE(status, 'A', 'Active', 'I', 'Inactive', 'Unknown') FROM items;
-- or standard CASE
SELECT CASE status WHEN 'A' THEN 'Active' WHEN 'I' THEN 'Inactive' ELSE 'Unknown' END FROM items;
```

### ROWNUM → ROW_NUMBER / LIMIT
```sql
-- Oracle (ROWNUM applied before ORDER BY!)
SELECT * FROM (SELECT * FROM employees ORDER BY salary DESC) WHERE ROWNUM <= 10;

-- Snowflake
SELECT * FROM employees ORDER BY salary DESC LIMIT 10;
-- or with ROW_NUMBER for more control
SELECT * FROM employees QUALIFY ROW_NUMBER() OVER (ORDER BY salary DESC) <= 10;
```

### Cursor Loop
```sql
-- Oracle
FOR rec IN (SELECT col1, col2 FROM my_table) LOOP
  DBMS_OUTPUT.PUT_LINE(rec.col1);
END LOOP;

-- Snowflake Scripting
DECLARE
  c1 CURSOR FOR SELECT col1, col2 FROM my_table;
  v_col1 VARCHAR;
BEGIN
  OPEN c1;
  LOOP
    FETCH c1 INTO v_col1;
    IF (NOT FOUND) THEN LEAVE; END IF;
    -- process v_col1
  END LOOP;
  CLOSE c1;
END;
-- PREFERRED: Rewrite as set-based SQL whenever possible
```

### BULK COLLECT / FORALL → Set-Based SQL
```sql
-- Oracle
DECLARE
  TYPE id_array IS TABLE OF NUMBER;
  v_ids id_array;
BEGIN
  SELECT employee_id BULK COLLECT INTO v_ids FROM employees WHERE dept_id = 10;
  FORALL i IN 1..v_ids.COUNT
    UPDATE audit_log SET processed = 'Y' WHERE emp_id = v_ids(i);
END;

-- Snowflake: Rewrite as single set-based statement
UPDATE audit_log a
SET processed = 'Y'
FROM employees e
WHERE a.emp_id = e.employee_id AND e.dept_id = 10;
```

### Exception Handling
```sql
-- Oracle
BEGIN
  INSERT INTO t VALUES (1);
EXCEPTION
  WHEN DUP_VAL_ON_INDEX THEN
    UPDATE t SET col = 'val' WHERE id = 1;
  WHEN NO_DATA_FOUND THEN
    NULL;
  WHEN OTHERS THEN
    RAISE;
END;

-- Snowflake Scripting
BEGIN
  INSERT INTO t VALUES (1);
EXCEPTION
  WHEN OTHER THEN
    LET err_code := SQLCODE;
    LET err_msg := SQLERRM;
    UPDATE t SET col = 'val' WHERE id = 1;
END;
-- Note: Snowflake only supports WHEN OTHER (no named exceptions)
```

### Dynamic SQL
```sql
-- Oracle
EXECUTE IMMEDIATE 'SELECT COUNT(*) FROM ' || v_table INTO v_count;

-- Snowflake
EXECUTE IMMEDIATE 'SELECT COUNT(*) FROM ' || v_table;
LET v_count := (SELECT COUNT(*) FROM TABLE(RESULT_SCAN(LAST_QUERY_ID())));
```

### MERGE Statement
```sql
-- Oracle
MERGE INTO target t USING source s ON (t.id = s.id)
WHEN MATCHED THEN UPDATE SET t.val = s.val
WHEN NOT MATCHED THEN INSERT (id, val) VALUES (s.id, s.val);

-- Snowflake (same ANSI syntax)
MERGE INTO target t USING source s ON t.id = s.id
WHEN MATCHED THEN UPDATE SET t.val = s.val
WHEN NOT MATCHED THEN INSERT (id, val) VALUES (s.id, s.val);
```

### Date/Time Functions
```sql
-- Oracle                                  -- Snowflake
SYSDATE                                    CURRENT_TIMESTAMP() or CURRENT_DATE()
SYSTIMESTAMP                               CURRENT_TIMESTAMP()
ADD_MONTHS(dt, 3)                          DATEADD('month', 3, dt)
MONTHS_BETWEEN(d1, d2)                     DATEDIFF('month', d2, d1)
LAST_DAY(dt)                               LAST_DAY(dt)  -- same
NEXT_DAY(dt, 'FRIDAY')                     NEXT_DAY(dt, 'FR')
TRUNC(dt)                                  DATE_TRUNC('day', dt) or dt::DATE
TRUNC(dt, 'MM')                            DATE_TRUNC('month', dt)
EXTRACT(YEAR FROM dt)                      EXTRACT(YEAR FROM dt) or YEAR(dt)
TO_DATE('01-JAN-2024', 'DD-MON-YYYY')     TO_DATE('01-JAN-2024', 'DD-MON-YYYY')
TO_CHAR(dt, 'YYYY-MM-DD')                 TO_CHAR(dt, 'YYYY-MM-DD')
TO_TIMESTAMP(str, 'fmt')                   TO_TIMESTAMP(str, 'fmt')
```

### String Functions
```sql
-- Oracle                                  -- Snowflake
NVL(expr, default)                         NVL(expr, default) or IFNULL / COALESCE
NVL2(expr, if_not_null, if_null)           NVL2(expr, if_not_null, if_null)  -- same
INSTR(str, 'sub')                          POSITION('sub', str) or CHARINDEX('sub', str)
SUBSTR(str, start, len)                    SUBSTR(str, start, len)  -- same
LENGTH(str)                                LENGTH(str)  -- same
LPAD(str, n, 'x')                          LPAD(str, n, 'x')  -- same
RPAD(str, n, 'x')                          RPAD(str, n, 'x')  -- same
REPLACE(str, 'old', 'new')                REPLACE(str, 'old', 'new')  -- same
REGEXP_SUBSTR(str, pattern)                REGEXP_SUBSTR(str, pattern)  -- same
REGEXP_REPLACE(str, pattern, repl)         REGEXP_REPLACE(str, pattern, repl)  -- same
LISTAGG(col, ',')                          LISTAGG(col, ',')  -- same
```

## DDL Conversion Checklist

1. **Remove** physical storage: `TABLESPACE`, `STORAGE`, `PCTFREE`, `INITRANS`, `LOGGING/NOLOGGING`
2. **Remove** all index DDL (B-tree, bitmap, function-based); consider CLUSTER BY for large tables
3. **Convert** `VARCHAR2` → `VARCHAR`, `NVARCHAR2` → `VARCHAR`, `NCHAR` → `CHAR`
4. **Convert** `DATE` → `TIMESTAMP_NTZ` (Oracle DATE includes time!)
5. **Convert** `CLOB/NCLOB` → `VARCHAR(16777216)`, `BLOB` → `BINARY(8388608)`
6. **Convert** `RAW/LONG RAW` → `BINARY`
7. **Convert** `XMLTYPE` → `VARIANT`
8. **Remove** Oracle hints (`/*+ ... */`)
9. **Remove** `STORAGE` and `LOB` storage clauses
10. **Replace** synonyms with fully qualified names or views
11. **Replace** DB Links with data sharing or external tables
12. **Note** constraints: PK, FK, UNIQUE defined but **not enforced**; move integrity checks to ETL
13. **Convert** sequences: syntax is similar; verify START WITH and INCREMENT BY

## Data Extraction Methods

| Method | Best For |
|--------|---------|
| Oracle Data Pump (expdp) | Large-scale export to dump files |
| SQL*Plus spooling | Simple CSV extraction |
| UTL_FILE package | File-based extraction |
| Third-party tools (Fivetran, etc.) | Managed CDC replication |
| SnowConvert AI (file-based) | DDL export scripts → conversion |

## Common Pitfalls

1. **Oracle DATE includes time**: `DATE` in Oracle stores both date and time; must map to `TIMESTAMP_NTZ`, not `DATE`.
2. **Empty string = NULL**: Oracle treats `''` as `NULL`; Snowflake treats `''` as empty string. Test NVL/COALESCE logic.
3. **Constraint enforcement**: Oracle enforces PK/FK/UNIQUE; Snowflake does not. Move integrity to ETL.
4. **PL/SQL packages**: No direct equivalent; decompose into separate procedures with shared state via tables.
5. **Named exceptions**: Snowflake only supports `WHEN OTHER` (no `DUP_VAL_ON_INDEX`, `NO_DATA_FOUND`, etc.).
6. **Autonomous transactions**: Not supported; redesign with separate transaction patterns.
7. **ROWNUM behavior**: `ROWNUM` is applied before `ORDER BY` in Oracle; use `LIMIT` or `ROW_NUMBER()` in Snowflake.
8. **Implicit commit on DDL**: Both Oracle and Snowflake auto-commit DDL, but verify transaction patterns.
9. **Sequence caching**: Snowflake sequences may have gaps; similar to Oracle but verify application assumptions.
10. **Oracle hints**: All removed; Snowflake auto-optimizes. Monitor Query Profile for performance issues.
