---
name: error-handler
id: error-handler 
title: Capture Procedure Errors
summary: Log Snowflake stored procedure failures to a central BATCH_ERROR_LOG table using a reusable error-capture procedure.
description: "Set up and use a common error-logging framework for Snowflake Scripting stored procedures (BATCH_ERROR_LOG table + SNOW_ERROR_CAPTURE procedure). Use when: adding error handling to a SQL stored procedure, logging failures from ETL/batch jobs, capturing SQLCODE/SQLERRM/SQLSTATE into a table, converting Oracle-style exception handling to Snowflake, or debugging why a procedure's errors are not being logged. Triggers: error handler, error logging, error log table, BATCH_ERROR_LOG, SNOW_ERROR_CAPTURE, EXCEPTION WHEN OTHER, log procedure errors, capture SQLERRM, batch error log. Do NOT use for: Snowflake Alerts, task failure notifications, event-table logging/tracing, or data-quality checks."
tools:
  - snowflake_sql_execute
  - Read
  - Write
  - Edit
prompt: Add error handling to my stored procedure LOAD_CUSTOMERS so failures are logged to BATCH_ERROR_LOG
language: en
status: beta
author: Himanshu Kandpal
type: community
---

# Snowflake Error Handler

A reusable pattern for logging stored procedure failures to a central table.
The setup script is `.cortex/skills/error-handler-setup.sql` in this skill directory.

## Components

| Object | Type | Purpose |
|---|---|---|
| `BATCH_ERROR_LOG` | Table | Primary error log (date, batch id, job name, description, failing statement) |
| `SNOW_ERROR_CAPTURE(p_batch_id, p_job_name, p_error_desc, p_err_stmt)` | Procedure | Central writer into `BATCH_ERROR_LOG`; defaults NULL inputs; returns `'SUCCESS'` or a JSON error object |
| `ERROR_LOG` + `SEQ_ERR` | Table + Sequence | Legacy simple log, kept for backward compatibility |
| `HK_TEST()` | Procedure | Example caller that forces a divide-by-zero and logs it |

## Workflow

1. **Confirm target location.** Ask the user for the database and schema; verify they exist and the current role can create objects there. Use fully qualified names.
2. **Check for existing objects** before running setup:
   ```sql
   SHOW TABLES LIKE '%ERROR_LOG' IN SCHEMA <db>.<schema>;
   ```
   The script uses `CREATE OR REPLACE TABLE`, which **drops existing log rows**. If the tables already exist, switch to `CREATE TABLE IF NOT EXISTS` or get explicit user confirmation.
3. **Run setup** (`error-handler-setup.sql`) once per environment.
4. **Wire into procedures** using the pattern below.
5. **Verify** with `CALL HK_TEST();` then
   `SELECT * FROM BATCH_ERROR_LOG ORDER BY ERROR_DATE DESC LIMIT 10;`

## Calling Pattern

Track the current step in a variable so the log shows where the failure happened:

```sql
CREATE OR REPLACE PROCEDURE MY_JOB()
RETURNS VARCHAR
LANGUAGE SQL
AS
$$
DECLARE
    v_err_stmt VARCHAR;
BEGIN
    v_err_stmt := 'Load stage table';
    -- ... work ...
    v_err_stmt := 'Merge into target';
    -- ... work ...
    RETURN 'SUCCESS';
EXCEPTION
    WHEN OTHER THEN
        CALL SNOW_ERROR_CAPTURE(
            100,
            'MY_JOB',
            'SQLCODE: ' || :sqlcode || ' SQLERRM: ' || :sqlerrm || ' SQLSTATE: ' || :sqlstate,
            :v_err_stmt
        );
        RAISE;  -- re-raise so callers/tasks see the failure
END;
$$;
```

## Rules and Pitfalls

- **Reference variables with a colon** (`:v_err_stmt`) inside SQL statements in the procedure body; otherwise they are treated as column names.
- **Re-raise after logging** (`RAISE;`) or return an explicit failure status. Swallowing the exception makes tasks report success on failed runs. The bundled `HK_TEST` example does not re-raise; do not copy that behavior into production jobs.
- **Prefer bind variables over dynamic SQL.** `SNOW_ERROR_CAPTURE` builds its INSERT by string concatenation and only escapes `p_err_stmt`; quotes in `p_job_name`/`p_error_desc` can break it and allow SQL injection. Recommended replacement for the dynamic block:
  ```sql
  INSERT INTO BATCH_ERROR_LOG (ERROR_DATE, BATCH_ID, JOB_NAME, DESCRIPTION, ERROR_STMT)
  VALUES (CURRENT_TIMESTAMP, :v_batch_id, :v_job_name, :v_error_desc, :v_err_stmt);
  ```
  With binds, the manual `REPLACE` escaping in both procedures is unnecessary.
- `JOB_NAME` is `VARCHAR(100)`; longer names fail the insert. Truncate with `LEFT(:p_job_name, 100)` if needed.
- `FROM DUAL` is valid in Snowflake; removing it is optional, not a bug fix.
- Oracle `VARCHAR2` maps to `VARCHAR`.

# Examples

**Prompt:** `Set up the error handler framework in ANALYTICS.ETL`
**Expected behavior:** Agent confirms the schema exists, checks for existing `BATCH_ERROR_LOG`/`ERROR_LOG` tables, runs `.cortex/skills/error-handler-setup.sql` with fully qualified names, then runs `CALL HK_TEST();` and shows the new row in `BATCH_ERROR_LOG`.

**Prompt:** `Add error handling to my stored procedure LOAD_CUSTOMERS`
**Expected behavior:** Agent reads the procedure DDL, adds a `v_err_stmt` step tracker before each major statement, adds an `EXCEPTION WHEN OTHER` block that calls `SNOW_ERROR_CAPTURE(...)` followed by `RAISE;`, and shows the updated DDL for approval before running it.
