# SQL Server to Snowflake Reference

Based on Snowflake's official SQL Server migration guide. Intended for solution architects, program managers, and migration partners.

## Architecture Differences

SQL Server is **server-centric**: a single, fixed machine (physical or virtual) that tightly couples storage and compute. Snowflake is **cloud-centric**: a logical entity that decouples storage, compute, and cloud services.

| Aspect | SQL Server | Snowflake |
|--------|-----------|-----------|
| Architecture | Monolithic; tightly coupled compute & storage | Decoupled compute, storage, and cloud services |
| Storage | Local/networked files (SAN, NAS) | Centralized, shared object storage; proprietary columnar format |
| Compute | Fixed server resources (CPU, Memory, I/O) | Elastic, on-demand virtual warehouses (independent, decoupled) |
| Concurrency | Contention-prone; dependent on server config/budget | Workload isolation; independent virtual warehouses eliminate contention |
| Scaling | Vertical (bigger server) or horizontal (Always On); requires hardware upgrades and downtime | Horizontal/automatic; scale up/down/out instantly, no downtime |
| Maintenance | DBA-managed (index rebuilds, stats, filegroups, DBCC) | Fully managed; no indexes, no partitions, no UPDATE STATISTICS |
| Constraints | PK, FK, UNIQUE, CHECK all enforced | Only NOT NULL enforced; PK/FK/UNIQUE are metadata-only |
| Cost Model | Fixed/license-based (CAPEX) | Consumption-based pay-per-use (OPEX) |

### Value Proposition Summary

| Feature | SQL Server | Snowflake | Value |
|---------|-----------|-----------|-------|
| Scalability | Vertical; requires hardware upgrades and downtime | Horizontal/automatic; instant scaling | Agility: scales to meet demand without manual intervention |
| Concurrency | Contention-prone; dependent on server budget | Workload isolation via independent virtual warehouses | Performance: different workloads run in parallel without impact |
| Cost Model | Fixed/license-based (CAPEX) | Consumption-based pay-per-use (OPEX) | Financial: shift from fixed cost to variable cost |

## Migration Methodology (8 Phases)

| Phase | Focus |
|-------|-------|
| 1. Planning and Design | Scope, strategy, team, budget, test plan, Snowflake prep |
| 2. Environments and Security | Warehouse setup, RBAC hierarchy, case sensitivity, environment separation |
| 3. Database Code Conversion | T-SQL conversion (SnowConvert AI automates 50-70%), stored procedure rewrite, feature remapping |
| 4. Data Migration and Ingestion | Initial data transfer, Snowpipe/Streams/Tasks for ongoing, SSIS modernization |
| 5. Reporting and Analytics | Tool repointing (Power BI, Tableau, etc.), connection string updates, metadata model changes |
| 6. Data Validation and Testing | Row counts, hash comparison, functional testing, performance benchmarking |
| 7. Deployment | Parallel run, cutover, decommission SQL Server |
| 8. Optimize and Run | Warehouse sizing, clustering keys, resource monitors, communicate success |

### Phase 1: Planning and Design

**Strategic goals:** This migration is not merely a cost-saving measure — it's a strategic move to prepare for advanced analytics and AI. Snowflake supports OLAP workloads, lakehouses, open/structured/semi-structured/unstructured data.

**Automated assessment:** Run SnowConvert AI assessment first (free) for data-driven scope and complexity estimates.

**Document the existing solution:**
- Database objects: List all databases, schemas, objects. Rationalize and decommission unnecessary data sets. Avoid migrating `sys` catalog tables/views.
- Data sources/processes: ETL/ELT tools (SSIS, Informatica), reporting (Power BI, Tableau), data science/ML
- Security: Roles, users, granted permissions; sensitive data sets and provisioning processes

**Migration approach selection:**
- **Lift and shift**: Migrate as-is (Snowflake recommends this for first iteration)
- **Lift and adjust**: Minor reengineering
- **Complete redesign**: Rework broken/inadequate processes
- **Snowflake recommends minimal reengineering first** — changes to data structures impact downstream tools and extend timelines

**Project logistics:**
- Prioritize data sets for quick wins with minimal effort; use SnowConvert AI for dependency documentation
- Document development environments (Dev/QA/Prod) and CI/CD processes
- Identify migration team: developer, QA engineer, business owner, project manager
- Define deadlines, budget (including Snowflake compute costs), and success/failure criteria

### Phase 2: Environments and Security

**Environment best practice — separate databases by environment:**
- Create dedicated databases per environment: `DEV_SALES_DB`, `QA_SALES_DB`, `PROD_SALES_DB`
- Create schemas matching SQL Server schemas: `PROD_SALES_DB.dbo_schema`
- Use naming convention `[ENVIRONMENT]_[DATABASE]` for warehouses and roles: `ANALYTICS_WH_DEV`, `DATA_ENGINEER_ROLE_PROD`

**Security model shift (RBAC):**
- SQL Server uses DAC + RBAC mix → Snowflake uses pure hierarchical RBAC
- SQL Server Login + User → unified Snowflake User object
- Best practice: authenticate via SSO/OAuth, not SQL logins
- Prioritize automated provisioning via IdP (Okta/Entra ID) with SCIM

**Role hierarchy:**

| Role Type | Description | Example |
|-----------|-------------|--------|
| Access Roles | Low-level; specific permissions on objects | `WH_ANALYTICS_USAGE`, `DB_SALES_READ` |
| Functional Roles | High-level; aligned with business functions, granted Access Roles | `DATA_ANALYST_ROLE`, `DATA_ENGINEER_ROLE` |

**Case sensitivity (critical):**
- SQL Server is typically case-insensitive (depending on collation)
- Snowflake is case-sensitive for unquoted and all quoted identifiers
- Reporting tools that auto-generate double-quoted SQL will fail if objects are uppercase
- **Solution:** Set `QUOTED_IDENTIFIERS_IGNORE_CASE = TRUE` to resolve compatibility errors

**Security checklist:**
- Use future grants for auto-applying permissions to new objects
- Enable MFA for all human users, especially privileged roles
- Establish audit processes for role/user creation, deletion, privilege changes

### Phase 3: Database Code Conversion

**SnowConvert AI reduces manual conversion by 50-70%:**
- Converts DDL, DML, and procedural T-SQL to Snowflake SQL
- Handles complex syntax differences (DATETIME→TIMESTAMP_NTZ, proprietary T-SQL constructs)
- After conversion, remaining EWIs are analyzed by Migration Assistant (Cortex AI) for fixes

**T-SQL feature remapping:**

| SQL Server Feature | Conversion Action | Snowflake Equivalent |
|-------------------|------------------|---------------------|
| Indexes/Partitioning | Remove all | N/A (micro-partitions + clustering keys) |
| Constraints | Not enforced (except NOT NULL); externalize validation | Metadata-only |
| Stored Procedures | Rewrite to Snowflake Scripting (SQL/JavaScript/Python) | Snow SQL-based procedural code |
| UDF DML Operations | Convert to Stored Procedure (UDFs cannot do DML) | N/A |
| Temporal Tables | Replace with Streams (CDC) + Tasks for automation | Streams and Tasks |
| Error Handling | Custom UDF needed (e.g., ERROR_SEVERITY()) | N/A for built-in functions |

### Phase 4: Data Migration and Ingestion

**Initial data transfer options:**

| Tool/Method | Use Case | Volume |
|-------------|----------|--------|
| SnowConvert AI | Optimized transfer with migration and validation | Large-scale (TB/PB) |
| Physical appliances | AWS Snowball, Azure Data Box, Google Transfer Appliance | Petabytes of on-premises data |
| BCP / SnowSQL | Export to compressed files (50-250MB), PUT to stage, COPY INTO | Small to medium (BCP not supported by all SQL Server editions) |

**Continuous data ingestion (ELT pattern):**
- **Snowpipe**: Automated, continuous ingestion from cloud storage (minutes/seconds)
- **Streams + Tasks**: CDC and procedural orchestration; replace/improve SQL Server loading
- **dbt**: Transform step with incremental materializations, tests, documentation, lineage
- **Zero-copy cloning**: Move data within Snowflake (QA→Dev) without additional storage costs

**SSIS migration strategies:**

| Strategy | Goal | Target | Recommendation |
|----------|------|--------|---------------|
| Modernize | Rewrite entire package into cloud-native tools | ADF, dbt, Snowflake Procedures | **Recommended** for 100% cloud architecture. SnowConvert AI converts SSIS/Informatica→dbt |
| Refactor | Keep SSIS control flow, enable high-speed bulk loading | SSIS with updated components | Use specialized connector (e.g., CData) for bulk load; direct ODBC is too slow |

### Phase 5: Reporting and Analytics

- Update all connection strings, ODBC/JDBC drivers, authentication to Snowflake
- **Power BI**: SnowConvert AI offers automatic connection repointing
- **Metadata-layer tools** (Cognos, Business Objects): Update metadata model to reflect Snowflake schema
- Compare tool output and evaluate performance after repointing

### Phase 6: Data Validation and Testing

**Validation methods:**
- Row count checks, distinct value counts, null counts, numerical metrics
- **MD5 hash comparison**: Create hash across key columns in SQL Server; generate corresponding hash in Snowflake
- SnowConvert AI data migration feature automates hash-based validation
- Functional testing: Validate refactored T-SQL (now Streams/Tasks/Python procedures) produces same results

**Critical platform differences to understand during testing:**
- Collation behavior (case sensitivity)
- Floating point arithmetic differences
- Date/time precision differences (DATETIME 3.33ms → TIMESTAMP_NTZ nanosecond)
- Business users must understand these for UAT

### Phase 7: Deployment

**Parallel run strategy:**
- Run SQL Server and Snowflake simultaneously until migration is validated
- High confidence from automated testing allows minimal parallel run window
- Cutover only after: initial data migrated, processes keep data current, all testing complete, all tools redirected
- **Cutover**: Turn off SQL Server data processes, revoke user/tool access
- **Define cutover plan early** — lack of clarity creates parallel environment overhead

### Phase 8: Optimize and Run

**Zero management advantage:**
- Remove SQL Server commands: `DBCC`, locking hints, `FOR REPLICATION`, `UPDATE STATISTICS` — all unnecessary
- No managing physical table partitions or indexes

**Performance optimization:**
- **Warehouse sizing**: Primary cost/performance lever. Right-size continuously; separate instances for workload isolation
- **Auto-suspend**: Set aggressive auto-suspend (60 seconds) on all warehouses
- **Resource monitors**: Track usage; take action at limits
- **Clustering keys**: For very large tables (>1TB) with frequent range filters
- **Query Profile**: Debug and optimize slow/inefficient queries

**Communicate success:** Document actual benefits vs. captured outcomes from planning phase

## Data Type Mapping

| SQL Server | Snowflake | Notes |
|------------|-----------|-------|
| TINYINT | TINYINT | SQL Server: 0-255; Snowflake: 0-255 |
| SMALLINT | SMALLINT | |
| INT / INTEGER | INTEGER | |
| BIGINT | BIGINT | |
| DECIMAL(p,s) / NUMERIC(p,s) | NUMBER(p,s) | |
| FLOAT(n) | FLOAT | |
| REAL | FLOAT | |
| MONEY | NUMBER(19,4) | |
| SMALLMONEY | NUMBER(10,4) | |
| BIT | BOOLEAN / NUMBER | Use NUMBER for value-to-value migration; BOOLEAN for ternary logic (TRUE/FALSE/NULL) |
| CHAR(n) | CHAR(n) | |
| VARCHAR(n) | VARCHAR(n) | VARCHAR(MAX) → VARCHAR(16777216) |
| NCHAR(n) | CHAR(n) | Snowflake native UTF-8; N-prefix types unnecessary |
| NVARCHAR(n) | VARCHAR(n) | NVARCHAR(MAX) → VARCHAR(16777216) |
| TEXT | VARCHAR(16777216) | Deprecated in SQL Server |
| NTEXT | VARCHAR(16777216) | Deprecated in SQL Server |
| BINARY(n) | BINARY(n) | |
| VARBINARY(n) | VARBINARY(n) | VARBINARY(MAX) → BINARY(8388608) |
| IMAGE | BINARY(8388608) | Deprecated in SQL Server |
| DATE | DATE | |
| TIME | TIME | |
| DATETIME | TIMESTAMP_NTZ(3) | SQL Server datetime is not ANSI-compliant. TIMESTAMP_NTZ(3) is recommended explicit mapping. Precision: 3.33ms → Snowflake ns |
| DATETIME2 | TIMESTAMP_NTZ | Time-zone-unaware |
| SMALLDATETIME | TIMESTAMP_NTZ | Minute precision |
| DATETIMEOFFSET | TIMESTAMP_LTZ | Maps to TIMESTAMP with Local Time Zone |
| UNIQUEIDENTIFIER | VARCHAR(36) | Store as string; generate with UUID_STRING() |
| XML | VARIANT | Parse XML content; use XMLGET() for querying |
| SQL_VARIANT | VARIANT | |
| GEOGRAPHY | GEOGRAPHY | |
| GEOMETRY | GEOMETRY | |
| HIERARCHYID | VARCHAR | Serialize to string; process with UDFs |
| ROWVERSION / TIMESTAMP | Not needed | Use Snowflake Streams for change tracking; SQL Server TIMESTAMP is not a date/time type |
| INTERVAL MINUTE TO SECOND | Not supported | INTERVAL data types not supported; use DATEDIFF/DATEADD functions instead |
| TABLE (type) | TEMPORARY TABLE | |
| CURSOR (type) | CURSOR in Snowflake Scripting | |
| SYSNAME | VARCHAR(128) | System name type |

## Feature Mapping

| SQL Server Feature | Snowflake Equivalent |
|-------------------|---------------------|
| Clustered index | CLUSTER BY (optional, auto-maintained) |
| Non-clustered indexes | Not needed (auto micro-partition pruning) |
| Columnstore indexes | Not needed (Snowflake is columnar natively) |
| Filtered indexes | Not needed; rely on micro-partition pruning |
| Included columns | Not applicable |
| Filegroups / partitions | Micro-partitions (automatic) |
| Computed columns | Virtual columns not supported; use views or pre-compute in ETL |
| Schema-bound objects | Not applicable; views are always late-binding |
| Linked servers | External tables, data sharing, or external functions |
| SQL Server Agent jobs | Snowflake Tasks (with CRON/interval schedules) |
| SSIS packages | Snowpipe, Tasks, dbt, or external ETL tools; re-architect, don't repoint |
| SSRS reports | Decommission; rebuild in Power BI, Tableau, or Streamlit |
| SSAS cubes | Snowflake aggregation + BI layer |
| Always On / Availability Groups | Built-in replication and failover |
| Temporal tables (system-versioned) | Streams + Time Travel |
| Change Data Capture (CDC) | Streams; use CDC from transaction log for incremental replication |
| Change Tracking | Streams |
| T-SQL stored procedures | Snowflake Scripting or JavaScript procedures |
| T-SQL functions (scalar) | Snowflake UDFs (SQL, JavaScript, Python, Java) |
| T-SQL functions (table-valued) | Snowflake UDTFs or views |
| CLR stored procedures | JavaScript/Python procedures (full rewrite required) |
| Triggers (DML) | Streams + Tasks (event-driven pattern) |
| Triggers (DDL) | Not supported; use governance policies or alerts |
| Service Broker | External messaging + Tasks |
| TDE (Transparent Data Encryption) | Built-in (always encrypted at rest and in transit) |
| Dynamic Data Masking | Dynamic Data Masking Policies |
| Row-Level Security | Row Access Policies |
| Always Encrypted | Not direct equivalent; use masking policies |
| Contained databases | Not applicable (Snowflake is SaaS) |
| Replication (transactional/merge) | Snowflake replication / data sharing |
| Log shipping | Not needed (built-in durability + Fail-Safe) |
| Resource Governor | Warehouses + resource monitors |
| Query Store | Query History view (ACCOUNT_USAGE.QUERY_HISTORY) |
| Database snapshots | Time Travel (AT / BEFORE) |
| Synonyms | Fully qualified names or wrapper views |
| User-defined types (UDTs) | Flatten to native Snowflake types |
| Table variables | TEMPORARY TABLE or Snowflake Scripting arrays |
| Cursors | Eliminate; rewrite as set-based SQL (cursors are anti-pattern in Snowflake) |
| System databases (master, msdb, tempdb, model) | No equivalent; exclude from migration scope |

## Common T-SQL to Snowflake Patterns

### TRY...CATCH → BEGIN...EXCEPTION
```sql
-- SQL Server
BEGIN TRY
  INSERT INTO t VALUES (1, 'test');
END TRY
BEGIN CATCH
  SELECT ERROR_MESSAGE() AS msg, ERROR_NUMBER() AS num;
END CATCH;

-- Snowflake Scripting
BEGIN
  INSERT INTO t VALUES (1, 'test');
EXCEPTION
  WHEN OTHER THEN
    LET msg := SQLERRM;
    LET code := SQLCODE;
    RETURN msg;
END;
```

### CROSS APPLY / OUTER APPLY → LATERAL
```sql
-- SQL Server
SELECT o.order_id, d.product_id
FROM orders o
CROSS APPLY (
  SELECT TOP 1 product_id FROM order_details 
  WHERE order_id = o.order_id ORDER BY amount DESC
) d;

-- Snowflake
SELECT o.order_id, d.product_id
FROM orders o,
LATERAL (
  SELECT product_id FROM order_details 
  WHERE order_id = o.order_id ORDER BY amount DESC LIMIT 1
) d;

-- OUTER APPLY → LEFT JOIN LATERAL
SELECT o.order_id, d.product_id
FROM orders o
LEFT JOIN LATERAL (
  SELECT product_id FROM order_details
  WHERE order_id = o.order_id ORDER BY amount DESC LIMIT 1
) d;
```

### STRING_SPLIT → SPLIT_TO_TABLE / FLATTEN
```sql
-- SQL Server
SELECT value FROM STRING_SPLIT('a,b,c', ',');

-- Snowflake (option 1)
SELECT value FROM TABLE(SPLIT_TO_TABLE('a,b,c', ','));
-- Snowflake (option 2)
SELECT value FROM LATERAL FLATTEN(INPUT => SPLIT('a,b,c', ','));
```

### FOR XML PATH (String Aggregation) → LISTAGG
```sql
-- SQL Server
SELECT dept_id, STUFF((
  SELECT ',' + name FROM employees e2 WHERE e2.dept_id = d.dept_id
  FOR XML PATH('')
), 1, 1, '') AS names
FROM departments d;

-- Snowflake
SELECT dept_id, LISTAGG(name, ',') WITHIN GROUP (ORDER BY name) AS names
FROM employees
GROUP BY dept_id;
```

### STRING_AGG → LISTAGG
```sql
-- SQL Server (2017+)
SELECT dept_id, STRING_AGG(name, ',') WITHIN GROUP (ORDER BY name) AS names
FROM employees GROUP BY dept_id;

-- Snowflake (identical syntax)
SELECT dept_id, LISTAGG(name, ',') WITHIN GROUP (ORDER BY name) AS names
FROM employees GROUP BY dept_id;
```

### Temp Tables
```sql
-- SQL Server
CREATE TABLE #temp (id INT, val VARCHAR(50));
SELECT * INTO #temp2 FROM source_table;

-- Snowflake
CREATE TEMPORARY TABLE temp (id INT, val VARCHAR(50));
CREATE TEMPORARY TABLE temp2 AS SELECT * FROM source_table;
```

### Table Variables → Temporary Tables
```sql
-- SQL Server
DECLARE @results TABLE (id INT, name VARCHAR(100));
INSERT INTO @results SELECT id, name FROM employees;

-- Snowflake
CREATE TEMPORARY TABLE results (id INT, name VARCHAR(100));
INSERT INTO results SELECT id, name FROM employees;
```

### Dynamic SQL
```sql
-- SQL Server
DECLARE @sql NVARCHAR(MAX) = N'SELECT * FROM ' + QUOTENAME(@tablename);
EXEC sp_executesql @sql, N'@param INT', @param = 42;

-- Snowflake Scripting
LET sql_text := 'SELECT * FROM ' || :tablename;
EXECUTE IMMEDIATE :sql_text;
```

### Identity and Sequences
```sql
-- SQL Server
CREATE TABLE t (id INT IDENTITY(1,1) PRIMARY KEY, name VARCHAR(100));
INSERT INTO t (name) VALUES ('Alice');
SELECT SCOPE_IDENTITY();

-- Snowflake
CREATE TABLE t (id INT AUTOINCREMENT START 1 INCREMENT 1, name VARCHAR(100));
INSERT INTO t (name) VALUES ('Alice');
-- No SCOPE_IDENTITY(); use LAST_QUERY_ID() + RESULT_SCAN if needed
```

### IF OBJECT_ID → DROP IF EXISTS / CREATE OR REPLACE
```sql
-- SQL Server
IF OBJECT_ID('dbo.my_table', 'U') IS NOT NULL DROP TABLE dbo.my_table;
CREATE TABLE dbo.my_table (...);

-- Snowflake
CREATE OR REPLACE TABLE my_table (...);
-- or
DROP TABLE IF EXISTS my_table;
CREATE TABLE my_table (...);
```

### MERGE Statement
```sql
-- SQL Server
MERGE INTO target t
USING source s ON t.id = s.id
WHEN MATCHED THEN UPDATE SET t.val = s.val
WHEN NOT MATCHED THEN INSERT (id, val) VALUES (s.id, s.val)
WHEN NOT MATCHED BY SOURCE THEN DELETE
OUTPUT $action, inserted.*, deleted.*;

-- Snowflake
MERGE INTO target t
USING source s ON t.id = s.id
WHEN MATCHED THEN UPDATE SET t.val = s.val
WHEN NOT MATCHED THEN INSERT (id, val) VALUES (s.id, s.val);
-- Note: WHEN NOT MATCHED BY SOURCE and OUTPUT clause not supported
-- Use Streams to capture changes instead of OUTPUT
```

### OUTPUT Clause → Streams
```sql
-- SQL Server (capture affected rows)
DELETE FROM orders OUTPUT deleted.* INTO @deleted_orders WHERE status = 'cancelled';

-- Snowflake: No OUTPUT clause. Use Streams for change capture:
CREATE STREAM orders_changes ON TABLE orders;
DELETE FROM orders WHERE status = 'cancelled';
SELECT * FROM orders_changes WHERE METADATA$ACTION = 'DELETE';
```

### IIF and CHOOSE
```sql
-- SQL Server
SELECT IIF(score >= 70, 'Pass', 'Fail') FROM exams;
SELECT CHOOSE(status, 'Draft', 'Active', 'Closed') FROM items;

-- Snowflake
SELECT IFF(score >= 70, 'Pass', 'Fail') FROM exams;  -- IIF → IFF
SELECT CASE status WHEN 1 THEN 'Draft' WHEN 2 THEN 'Active' WHEN 3 THEN 'Closed' END FROM items;
```

### TRY_CONVERT / TRY_CAST
```sql
-- SQL Server
SELECT TRY_CONVERT(INT, '123abc');  -- Returns NULL
SELECT TRY_CAST('2024-01-01' AS DATE);

-- Snowflake
SELECT TRY_CAST('123abc' AS INT);   -- Returns NULL
SELECT TRY_CAST('2024-01-01' AS DATE);
```

### OPENJSON → PARSE_JSON / LATERAL FLATTEN
```sql
-- SQL Server
SELECT j.[key], j.value
FROM OPENJSON('{"a":1,"b":2}') j;

-- Snowflake
SELECT f.key, f.value
FROM LATERAL FLATTEN(INPUT => PARSE_JSON('{"a":1,"b":2}')) f;
```

### JSON_VALUE / JSON_QUERY → Dot Notation
```sql
-- SQL Server
SELECT JSON_VALUE(data, '$.customer.name') FROM orders;
SELECT JSON_QUERY(data, '$.items') FROM orders;

-- Snowflake (dot notation)
SELECT data:customer.name::STRING FROM orders;
SELECT data:items FROM orders;
```

### PIVOT / UNPIVOT
```sql
-- SQL Server
SELECT * FROM sales_data
PIVOT (SUM(amount) FOR quarter IN ([Q1],[Q2],[Q3],[Q4])) p;

-- Snowflake
SELECT * FROM sales_data
PIVOT (SUM(amount) FOR quarter IN ('Q1','Q2','Q3','Q4')) p;
-- Note: Snowflake uses single quotes, not brackets
```

### TOP → LIMIT
```sql
-- SQL Server
SELECT TOP 10 * FROM employees ORDER BY hire_date DESC;
SELECT TOP 10 PERCENT * FROM employees;
SELECT TOP 5 WITH TIES * FROM employees ORDER BY salary DESC;

-- Snowflake
SELECT * FROM employees ORDER BY hire_date DESC LIMIT 10;
-- TOP PERCENT: No direct equivalent; use window functions:
SELECT * FROM (
  SELECT *, NTILE(10) OVER (ORDER BY hire_date) AS tile FROM employees
) WHERE tile = 1;
-- TOP WITH TIES: use QUALIFY
SELECT * FROM employees QUALIFY RANK() OVER (ORDER BY salary DESC) <= 5;
```

### Date/Time Functions
```sql
-- SQL Server                              -- Snowflake
GETDATE()                                  CURRENT_TIMESTAMP()
GETUTCDATE()                               CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())
SYSDATETIME()                              CURRENT_TIMESTAMP()
DATEADD(day, 7, @dt)                       DATEADD('day', 7, dt)
DATEDIFF(day, @start, @end)                DATEDIFF('day', start_dt, end_dt)
DATENAME(month, @dt)                       MONTHNAME(dt) or TO_CHAR(dt, 'MMMM')
DATEPART(year, @dt)                        YEAR(dt) or DATE_PART('year', dt)
FORMAT(@dt, 'yyyy-MM-dd')                  TO_CHAR(dt, 'YYYY-MM-DD')
EOMONTH(@dt)                               LAST_DAY(dt)
ISDATE('2024-01-01')                       TRY_TO_DATE('2024-01-01') IS NOT NULL
SWITCHOFFSET(@dto, '+05:30')               CONVERT_TIMEZONE('+05:30', dto)
TODATETIMEOFFSET(@dt, '-08:00')            CONVERT_TIMEZONE('UTC', '-08:00', dt)
```

### String Functions
```sql
-- SQL Server                              -- Snowflake
LEN(str)                                   LENGTH(str)
DATALENGTH(str)                            OCTET_LENGTH(str)
CHARINDEX('abc', str)                      CHARINDEX('abc', str)  -- same
PATINDEX('%pattern%', str)                 REGEXP_INSTR(str, 'pattern')
REPLACE(str, 'old', 'new')                REPLACE(str, 'old', 'new')  -- same
STUFF(str, start, len, repl)              INSERT(str, start, len, repl)
REPLICATE(str, n)                          REPEAT(str, n)
REVERSE(str)                               REVERSE(str)  -- same
QUOTENAME(name)                            '"' || name || '"'
CONCAT_WS(',', a, b, c)                   CONCAT_WS(',', a, b, c)  -- same (Snowflake supports)
STRING_ESCAPE(str, 'json')                 No direct equivalent; use REPLACE chains
TRANSLATE(str, 'abc', 'xyz')              TRANSLATE(str, 'abc', 'xyz')  -- same
TRIM(str)                                  TRIM(str)  -- same
```

### System Variables and Functions
```sql
-- SQL Server                              -- Snowflake
@@ROWCOUNT                                 SQLROWCOUNT (in Snowflake Scripting)
@@ERROR                                    SQLCODE (in Snowflake Scripting)
@@IDENTITY / SCOPE_IDENTITY()              Not available; use sequences or RESULT_SCAN
@@SERVERNAME                               CURRENT_ACCOUNT()
@@VERSION                                  CURRENT_VERSION()
DB_NAME()                                  CURRENT_DATABASE()
SCHEMA_NAME()                              CURRENT_SCHEMA()
USER_NAME() / SUSER_SNAME()               CURRENT_USER()
NEWID()                                    UUID_STRING()
SET NOCOUNT ON                             Remove (not needed)
SET ANSI_NULLS ON                          Remove (Snowflake is ANSI-compliant)
SET QUOTED_IDENTIFIER ON                   Remove (always on in Snowflake)
PRINT 'message'                            SYSTEM$LOG('info', 'message') or remove
RAISERROR('msg', 16, 1)                    Use Snowflake exception handling
THROW 50000, 'msg', 1                      Use Snowflake exception handling
WAITFOR DELAY '00:00:05'                   SYSTEM$WAIT(5)
```

### Transaction Patterns
```sql
-- SQL Server
BEGIN TRANSACTION;
  UPDATE accounts SET balance = balance - 100 WHERE id = 1;
  UPDATE accounts SET balance = balance + 100 WHERE id = 2;
COMMIT;

-- Snowflake
BEGIN;
  UPDATE accounts SET balance = balance - 100 WHERE id = 1;
  UPDATE accounts SET balance = balance + 100 WHERE id = 2;
COMMIT;
-- Note: Snowflake auto-commits DDL. DML within a procedure uses explicit transactions.
```

### Window Functions (mostly compatible)
```sql
-- SQL Server
SELECT *, ROW_NUMBER() OVER (PARTITION BY dept ORDER BY salary DESC) rn FROM emp;
-- Snowflake: identical syntax

-- SQL Server specific: WITHIN GROUP
SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY salary) OVER (PARTITION BY dept) FROM emp;
-- Snowflake: identical syntax
```

### Common Table Expressions (CTEs)
```sql
-- SQL Server recursive CTE
WITH cte AS (
  SELECT id, parent_id, name, 0 AS level FROM org WHERE parent_id IS NULL
  UNION ALL
  SELECT o.id, o.parent_id, o.name, c.level + 1 FROM org o JOIN cte c ON o.parent_id = c.id
)
SELECT * FROM cte OPTION (MAXRECURSION 100);

-- Snowflake
WITH RECURSIVE cte AS (
  SELECT id, parent_id, name, 0 AS level FROM org WHERE parent_id IS NULL
  UNION ALL
  SELECT o.id, o.parent_id, o.name, c.level + 1 FROM org o JOIN cte c ON o.parent_id = c.id
)
SELECT * FROM cte;
-- Note: Add RECURSIVE keyword; no OPTION (MAXRECURSION); Snowflake has built-in depth limit
```

### Stored Procedure Conversion Patterns
```sql
-- SQL Server procedure with output parameters
CREATE PROCEDURE dbo.GetEmployeeCount
  @dept_id INT,
  @count INT OUTPUT
AS
BEGIN
  SET NOCOUNT ON;
  SELECT @count = COUNT(*) FROM employees WHERE department_id = @dept_id;
END;

-- Snowflake: No output parameters; return value or result set
CREATE OR REPLACE PROCEDURE get_employee_count(dept_id INT)
  RETURNS INT
  LANGUAGE SQL
  EXECUTE AS CALLER
AS
BEGIN
  LET cnt INT := (SELECT COUNT(*) FROM employees WHERE department_id = :dept_id);
  RETURN cnt;
END;
```

### Cursor Elimination (Preferred Approach)
```sql
-- SQL Server (row-by-row cursor)
DECLARE @id INT, @name VARCHAR(100);
DECLARE cur CURSOR FOR SELECT id, name FROM employees;
OPEN cur;
FETCH NEXT FROM cur INTO @id, @name;
WHILE @@FETCH_STATUS = 0 BEGIN
  UPDATE audit_log SET last_seen = GETDATE() WHERE emp_id = @id;
  FETCH NEXT FROM cur INTO @id, @name;
END;
CLOSE cur; DEALLOCATE cur;

-- Snowflake: Rewrite as set-based SQL (preferred)
UPDATE audit_log a
SET last_seen = CURRENT_TIMESTAMP()
FROM employees e
WHERE a.emp_id = e.id;
-- Cursors are a severe performance anti-pattern in Snowflake; always prefer set-based
```

## DDL Conversion Checklist

When converting SQL Server DDL to Snowflake:

1. **Remove** physical storage clauses: `ON [filegroup]`, `TEXTIMAGE_ON`, `WITH (PAD_INDEX = ...)`, `FILLFACTOR`
2. **Remove** index definitions: `CLUSTERED`, `NONCLUSTERED`, `COLUMNSTORE` (Snowflake is columnar)
3. **Convert** `NVARCHAR/NCHAR` → `VARCHAR/CHAR` (Snowflake is native UTF-8)
4. **Convert** `DATETIME/DATETIME2` → `TIMESTAMP_NTZ`, `DATETIMEOFFSET` → `TIMESTAMP_TZ`
5. **Convert** `UNIQUEIDENTIFIER` → `VARCHAR(36)`
6. **Convert** `BIT` → `BOOLEAN`
7. **Convert** `MONEY/SMALLMONEY` → `NUMBER(19,4)` / `NUMBER(10,4)`
8. **Convert** `IDENTITY(seed,increment)` → `AUTOINCREMENT START seed INCREMENT increment`
9. **Remove** `SET NOCOUNT ON`, `SET ANSI_NULLS ON`, `SET QUOTED_IDENTIFIER ON`
10. **Remove** `GO` batch separators
11. **Replace** `dbo.` schema prefix → use fully qualified `DB.SCHEMA.TABLE`
12. **Convert** bracket-quoted identifiers `[name]` → double-quoted `"name"` or remove if unnecessary
13. **Note** constraints: PK, FK, UNIQUE defined but **not enforced** in Snowflake; move integrity checks to ETL

## Migration Tool Ecosystem

| Tool | Use Case |
|------|----------|
| SnowConvert AI | Automated DDL/DML/T-SQL conversion (free) |
| BCP (Bulk Copy Program) | Extract large tables to flat files for staging |
| Snowpipe | Continuous ingestion from cloud storage |
| Streams + Tasks | Replace triggers, CDC, SQL Server Agent |
| dbt | Replace SSIS transformation logic (ELT pattern) |
| Airflow / Azure Data Factory | Replace complex SQL Server Agent job chains |
| Snowflake Data Sharing | Replace linked servers and replication |

## Data Extraction Methods

| Method | Best For |
|--------|---------|
| BCP utility | Large table bulk export to CSV/delimited files |
| SSIS export | When SSIS is already in use for data movement |
| SnowConvert AI direct streaming | SQL Server → Snowflake real-time transfer |
| Azure Data Factory | Azure-centric environments; built-in Snowflake connector |
| Fivetran / Airbyte | Managed CDC replication |

## Common Pitfalls

1. **Constraint enforcement**: SQL Server enforces PK/FK/UNIQUE; Snowflake does not. Externalize validation logic; reengineer load processes to prevent duplicate/orphaned records.
2. **IDENTITY gaps**: Snowflake AUTOINCREMENT does not guarantee gap-free sequences.
3. **Case sensitivity (critical)**: SQL Server is case-insensitive by default; Snowflake is case-sensitive for unquoted and all quoted identifiers. Reporting tools auto-generating double-quoted SQL will fail. Set `QUOTED_IDENTIFIERS_IGNORE_CASE = TRUE`.
4. **NULL handling in strings**: SQL Server can concatenate NULL + string = NULL or string depending on settings; Snowflake NULL + string = NULL.
5. **DATETIME precision**: SQL Server DATETIME has 3.33ms resolution; map to `TIMESTAMP_NTZ(3)` explicitly to preserve precision semantics.
6. **Implicit conversions**: SQL Server has extensive implicit type conversion; Snowflake is stricter. Add explicit CAST/TRY_CAST.
7. **Collation and floating point**: SQL Server supports per-column collation and its own floating-point arithmetic; test string comparisons and numeric calculations thoroughly. Business users must understand these for UAT.
8. **Empty string vs NULL**: SQL Server treats '' as empty string; Snowflake treats '' as '' (not NULL, unlike Oracle).
9. **System databases**: Never attempt to migrate `master`, `msdb`, `tempdb`, `model`. Avoid migrating `sys` prefix catalog tables/views.
10. **SSRS connectivity**: SSRS → Snowflake is problematic; plan to decommission SSRS and rebuild reports.
11. **UDF DML operations**: SQL Server UDFs can perform DML; Snowflake UDFs cannot. Convert DML-performing UDFs to stored procedures.
12. **CURRENT_TIMESTAMP into DATETIME columns**: CURRENT_TIMESTAMP() returns TIMESTAMP_LTZ; cannot insert into DATETIME (TIMESTAMP_NTZ) without session parameter.
13. **SSIS ODBC performance**: Direct ODBC from SSIS to Snowflake is too slow for bulk loads; use specialized connectors (CData) or modernize to dbt.
14. **Zero management misconception**: Remove all SQL Server system commands (DBCC, locking hints, FOR REPLICATION, UPDATE STATISTICS) — they are incompatible and unnecessary in Snowflake.

## Utilities Mapping

| SQL Server Utility | Snowflake Equivalent | Notes |
|-------------------|---------------------|-------|
| MSSQL-CLI / SQLCMD | SnowSQL | Command-line client for SQL execution, DDL/DML operations |
| BCP (Bulk Copy Program) | COPY INTO | COPY INTO supports AVRO, Parquet, JSON, CSV, etc. BCP also used for extraction to cloud staging |
| SQL Server Management Studio | Snowsight | Web-based UI for queries, worksheets, monitoring |
| SQL Server Agent | Snowflake Tasks | CRON/interval-based scheduling with DAG support |
| SQL Server Profiler / Extended Events | Query History (ACCOUNT_USAGE) | Snowflake Query Profile for visual query analysis |

## Professional Services and Partners

- **Snowflake Professional Services**: Accelerated migration using SnowConvert AI, high-performing architectures with Snowpark and Adaptive Compute, POC and implementation from planning to cutover
- **Global Solution Partners**: Code conversion (ETL/stored procedures/reports), AI/ML enablement (Snowpark, Cortex), end-to-end delivery (validation, performance tuning, FinOps, compliance)
- Contact: Snowflake sales team or Snowflake Community
