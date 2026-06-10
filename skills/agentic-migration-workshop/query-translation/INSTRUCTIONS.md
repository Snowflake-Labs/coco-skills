
# Workshop Session: Query Translation (Day 4 — Data Integration & Transformation)

## Session Overview

**Present to user:**
> Welcome to **Query Translation** — part of Day 4: Data Integration & Transformation. This is where we convert your SQL queries, stored procedures, and functions to run natively on Snowflake.
>
> Here's our approach:
> 1. Collect and categorize your source SQL by complexity
> 2. Apply platform-specific translation rules systematically
> 3. Convert stored procedures and functions
> 4. Optionally use SnowConvert AI for automated verification
> 5. Test everything against your Snowflake data
> 6. Produce a Translation Report
>
> Let's see what you're working with.

## Prerequisites
- Platform reference file read (from `<SKILL_DIRECTORY>/references/`)
- Source SQL queries or stored procedures available
- Target Snowflake tables exist (for validation)
- `references/best-practices.md` read

## Session Flow

### Part 1: Collect and Categorize Source SQL

**Ask the user** to provide their source SQL:
- SQL query files
- Stored procedure definitions
- Function definitions
- Scheduled job/ETL scripts
- Report queries
- SnowConvert AI converted output (if available — we'll review and fix)

**Categorize by complexity** and share with user:

| Category | What It Means | Examples |
|----------|-------------|---------|
| Simple | Basic SELECT/JOIN/WHERE — quick translation | Reporting queries |
| Moderate | Subqueries, window functions, CTEs | Analytics queries |
| Complex | Dynamic SQL, cursors, temp tables | ETL procedures |
| Critical | Platform-specific extensions, optimizer hints | Heavily tuned queries |

**Present:** *"I've categorized your [N] SQL objects: [X] simple, [Y] moderate, [Z] complex, [W] critical. Let me start translating — I'll explain the important changes as I go."*

### Part 2: Apply Translation Rules

**Explain to user:**
> Most SQL translations follow predictable patterns. I'll apply these systematically and highlight anything that changes behavior — not just syntax.

**Universal translations (all platforms):**

| Source Pattern | Snowflake Equivalent | Notes |
|---------------|---------------------|-------|
| `TOP N` (SQL Server/Teradata) | `LIMIT N` | |
| `ROWNUM` (Oracle) | `ROW_NUMBER() OVER()` or `LIMIT` | |
| `NVL()` (Oracle) | `NVL()` or `COALESCE()` | Both work in Snowflake |
| `ISNULL()` (SQL Server) | `NVL()` or `COALESCE()` | |
| `GETDATE()` (SQL Server) | `CURRENT_TIMESTAMP()` | |
| `SYSDATE` (Oracle) | `CURRENT_TIMESTAMP()` | |
| `DATEADD` variations | `DATEADD(part, amount, date)` | |
| `DATEDIFF` variations | `DATEDIFF(part, start, end)` | |
| `CONVERT(type, expr)` | `CAST(expr AS type)` or `TRY_CAST()` | TRY_CAST returns NULL on failure |
| `STRING_AGG` / `LISTAGG` | `LISTAGG(col, delim)` | |
| Recursive CTE | Same ANSI syntax | |
| `MERGE` | Snowflake MERGE (ANSI-compliant) | |
| Temp tables `#temp` / `DECLARE GTT` | `CREATE TEMPORARY TABLE` | |

**Platform-specific translations:**

**Oracle → Snowflake:**

| Oracle | Snowflake | Teaching Moment |
|--------|-----------|----------------|
| `(+)` outer join | ANSI `LEFT/RIGHT JOIN` | Snowflake only supports ANSI join syntax |
| `CONNECT BY / START WITH` | Recursive CTE | Same logic, cleaner syntax |
| `DECODE()` | `CASE WHEN` or `DECODE()` | Both supported — CASE is more readable |
| `TO_DATE('str', 'fmt')` | `TO_DATE('str', 'fmt')` | Verify format tokens match |
| PL/SQL blocks | Snowflake Scripting (SQL) or JavaScript UDF | |
| `DBMS_OUTPUT.PUT_LINE` | `SYSTEM$LOG()` | |
| `%TYPE` / `%ROWTYPE` | Explicit type declarations | |
| `BULK COLLECT / FORALL` | Set-based operations or RESULTSET | |
| `CURSOR` loops | Snowflake CURSOR in Scripting or set-based rewrite | Set-based is preferred |

**Teradata → Snowflake:**

| Teradata | Snowflake | Teaching Moment |
|----------|-----------|----------------|
| `SEL` | `SELECT` | Abbreviation not supported |
| `QUALIFY` | `QUALIFY` | Snowflake supports this natively! |
| `SAMPLE n` | `SAMPLE (n ROWS)` or `TABLESAMPLE` | |
| `FORMAT 'fmt'` | `TO_CHAR(col, 'fmt')` | |
| `CHARACTERS()` | `LENGTH()` | |
| `TITLE 'alias'` | `AS alias` | |
| `CASESPECIFIC` / `NOT CASESPECIFIC` | `COLLATE` or `UPPER()`/`LOWER()` | |
| `COLLECT STATISTICS` | Remove | Snowflake auto-manages statistics |
| `LOCKING ROW FOR ACCESS` | Remove | Snowflake MVCC handles concurrency |

**Redshift → Snowflake:**

| Redshift | Snowflake | Teaching Moment |
|----------|-----------|----------------|
| `GETDATE()` | `CURRENT_TIMESTAMP()` | |
| `LEN()` | `LENGTH()` | |
| `STRTOL()` | `TRY_TO_NUMBER()` with base | |
| `JSON_EXTRACT_PATH_TEXT()` | `col:path::STRING` (dot notation) | Snowflake's semi-structured access is much cleaner |
| `APPROXIMATE COUNT(DISTINCT)` | `APPROX_COUNT_DISTINCT()` | |
| `UNLOAD TO` | `COPY INTO @stage` | |
| Spectrum queries | External tables or data sharing | |
| `WLM` queue references | Remove | Use separate Snowflake warehouses instead |

**SQL Server → Snowflake:**

| SQL Server | Snowflake | Teaching Moment |
|------------|-----------|----------------|
| `SET NOCOUNT ON` | Remove | Not needed in Snowflake |
| `@@ROWCOUNT` | `SQLROWCOUNT` in Scripting | |
| `@@ERROR` | `SQLCODE` in Scripting | |
| `TRY...CATCH` | `BEGIN...EXCEPTION...END` | |
| `sp_executesql` | `EXECUTE IMMEDIATE` | |
| `CROSS APPLY` / `OUTER APPLY` | `LATERAL JOIN` / `LATERAL FLATTEN` | |
| `PIVOT` / `UNPIVOT` | Snowflake `PIVOT` / `UNPIVOT` | |
| `STRING_SPLIT()` | `SPLIT_TO_TABLE()` or `LATERAL FLATTEN(SPLIT())` | |
| `FOR XML PATH` | `LISTAGG()` or `ARRAY_AGG()` | |
| `OPENROWSET` / `OPENQUERY` | External tables or stages | |

**As you translate, explain significant changes:**
> "I'm changing your `CROSS APPLY` to a `LATERAL JOIN` — functionally identical, but this is Snowflake's syntax for correlated subqueries in the FROM clause."

### Part 3: Convert Stored Procedures

**Explain to user:**
> Stored procedures are usually the most complex part of query translation. For each one, I'll determine the best Snowflake approach.

**Assessment strategy for each procedure:**

| Source Pattern | Best Snowflake Approach | When to Use |
|---------------|------------------------|-------------|
| Simple cursor loop | Rewrite as set-based SQL | Always preferred — much faster |
| Complex cursor with business logic | Snowflake Scripting with CURSOR | When set-based isn't feasible |
| Dynamic SQL | `EXECUTE IMMEDIATE` with binds | |
| Temp table pipeline | Snowflake temp tables + Scripting | |
| Error handling | `BEGIN...EXCEPTION...END` | |
| Output parameters | RETURN value or RESULTSET | |
| Package (Oracle) | Separate procedures + shared tables/stages | No package concept in Snowflake |

**Generate** Snowflake procedure DDL:
```sql
CREATE OR REPLACE PROCEDURE proc_name(param1 TYPE, param2 TYPE)
  RETURNS VARCHAR
  LANGUAGE SQL
  EXECUTE AS CALLER
AS
$$
BEGIN
  -- Converted logic
  RETURN 'Success';
END;
$$;
```

**Validate** each procedure compiles: `snowflake_sql_execute` with `only_compile: true`

### Part 4: AI Verification (Optional)

**Introduce to user:**
> If you have SnowConvert AI, we can use its AI Verification feature to automatically test and fix conversion errors. The AI agents execute the converted code in your Snowflake account and fix issues — all grounded with tests over synthetic data.

**If user has SnowConvert AI:**
1. Select converted objects for verification
2. AI agents execute and fix issues automatically
3. Review AI results per object ("SEE DETAILS")
4. Manually merge AI fixes with initial conversion

**Track code completeness:**

| Status | Count | Action |
|--------|-------|--------|
| Green (ready) | [n] | Deploy as-is |
| Yellow (FDM) | [n] | Review and document; may deploy with caveats |
| Red (EWI) | [n] | Must resolve manually |

### Part 5: Test Translated Queries

**Explain to user:**
> Let's verify that your translated SQL produces the right results. I'll run each query against your Snowflake data and check for correctness.

**For each translated query:**
1. Execute against Snowflake: `snowflake_sql_execute`
2. Check for compilation errors
3. Verify result set structure matches expected output
4. Compare against source results if available (row counts, column values, aggregates)

**Performance check:**
```sql
SELECT * FROM TABLE(GET_QUERY_OPERATOR_STATS(LAST_QUERY_ID()));
```

**Document behavioral differences** (these are important for UAT):
- NULL handling differences between platforms
- String collation differences
- Date/time precision differences
- Rounding behavior differences

### Part 6: Translation Report

**Compile** all results into a polished report:

```
# Query Translation Report
## Date: [Today]

### Translation Summary
| Category | Total | Translated | Validated | Issues |
|----------|-------|-----------|-----------|--------|
| Queries  |       |           |           |        |
| Procedures |     |           |           |        |
| Functions |      |           |           |        |

### Translation Details
| Object | Source Lines | Snowflake Lines | Complexity | Status |
|--------|------------|----------------|-----------|--------|

### Behavioral Differences
| Object | Difference | Impact | Mitigation |
|--------|-----------|--------|------------|

### Manual Review Required
| Object | Reason | Guidance |
|--------|--------|----------|

### Recommended Testing
- [ ] Unit test each procedure with sample inputs
- [ ] Compare query results against source for key reports
- [ ] Performance test with production-scale data
```

**Present to user:**
> Here's your Query Translation Report. All [N] objects have been converted and validated. Let me highlight the behavioral differences you should be aware of for UAT...

**CHECKPOINT:** Wait for user approval.

## Session Wrap-Up

**Present to user:**
> Query Translation complete! Here's the summary:
> - [X] queries translated and validated
> - [Y] stored procedures converted
> - [Z] behavioral differences documented
> - All objects compile and execute in Snowflake

## Workshop Context (Day 4 — Data Integration & Transformation)

During the LiftOff engagement, this session covers:
- Data source catalog: frequency and volume of extraction
- Loading and transforming data into Snowflake
- Stored procedure conversion demo using SnowConvert AI
- SSIS/Informatica to dbt conversion demo
- Data load best practices
- Estimation of data integration migration LOE and timeline

**Key estimation factors:** Object count and complexity, data product inventory, deployment framework, technology POCs, third-party library evaluation, orchestration/monitoring compatibility, external system connections
