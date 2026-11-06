---
name: validation-log
parent_skill: messy-migration-validation
description: "Testing record management — view, annotate, and generate validation reports from the test log. Supports both local markdown and Snowflake table persistence. Triggers: testing record, test log, sign-off, validation report, test status, annotate test."
---

# Testing Record Management

**You are using the validation-log sub-skill. Follow these instructions exactly.**

## When to Use

- Viewing the history of validation tests run for a mart
- Adding analyst notes or updating status on a test entry
- Generating a validation summary report for stakeholder sign-off
- Viewing cross-mart validation progress

## Workflow

### Prerequisites

1. Read `../references/conventions.md` — naming standards and VALIDATION_TEST_LOG schema

### Step 1: Determine Intent

Ask the analyst what they want to do:

```
1. View the testing record for a specific mart
2. Add analyst notes to a test entry
3. Update the status of a test entry
4. Generate a validation summary report for sign-off
5. View cross-mart summary
```

### Step 2: Route by Intent

### 2a. View Testing Record

```sql
SELECT TEST_ID, TEST_TYPE, TEST_DESCRIPTION, STATUS, RESULT_SUMMARY,
       ANALYST_NOTES, CREATED_AT, CREATED_BY
FROM {{DB}}.{{SCHEMA}}.VALIDATION_TEST_LOG
WHERE MART_NAME IN ('{{MART_FQN}}', '{{MART_TABLE}}')  -- FQN is standard; short name covers older rows
ORDER BY CREATED_AT DESC;
```

Also check for a local markdown log at `validation-workspace/testing-records/{mart-name}-test-log.md`.

Present the records in a readable table format.

**Create checkpoint** per `../references/conventions.md` — Checkpoint Initialization. The checkpoint must be written now, before the first [STOP], so a resumable record exists from session start.

### 2b. Add Analyst Notes

Ask for:
1. **TEST_ID** to annotate
2. **Notes** to add

```sql
UPDATE {{DB}}.{{SCHEMA}}.VALIDATION_TEST_LOG
SET ANALYST_NOTES = '{{NOTES}}'
WHERE TEST_ID = '{{TEST_ID}}';
```

**[STOP]** Confirm the UPDATE target (TEST_ID and notes) with the analyst before executing. **Update checkpoint.**

Also append to the local markdown log.

### 2c. Update Status

Ask for:
1. **TEST_ID** to update
2. **New status**: pass, fail, info, warning

```sql
UPDATE {{DB}}.{{SCHEMA}}.VALIDATION_TEST_LOG
SET STATUS = '{{STATUS}}'
WHERE TEST_ID = '{{TEST_ID}}';
```

**[STOP]** Confirm the status change (TEST_ID, old status → new status) with the analyst before executing. **Update checkpoint.**

### 2d. Generate Validation Summary Report

Before generating, ask the analyst:

```
Who is the primary audience for this report?
  a) Business user — executive summary, pass/fail counts, recommendation only
  b) Technical user — full detail, SQL executed, per-test breakdowns
  c) Other — (free text: describe the audience)
```

Tailor the report depth and language accordingly.

Generate a sign-off report at `validation-workspace/testing-records/{mart-name}-validation-summary.md`:

```sql
SELECT TEST_TYPE,
       COUNT(*) AS total_tests,
       SUM(CASE WHEN STATUS = 'pass' THEN 1 ELSE 0 END) AS passed,
       SUM(CASE WHEN STATUS = 'fail' THEN 1 ELSE 0 END) AS failed,
       SUM(CASE WHEN STATUS = 'warning' THEN 1 ELSE 0 END) AS warnings,
       SUM(CASE WHEN STATUS = 'info' THEN 1 ELSE 0 END) AS info_only
FROM {{DB}}.{{SCHEMA}}.VALIDATION_TEST_LOG
WHERE MART_NAME IN ('{{MART_FQN}}', '{{MART_TABLE}}')
GROUP BY TEST_TYPE
ORDER BY TEST_TYPE;
```

If the log holds entries from more than one validation run for this mart, ask the analyst which runs (or date range) the summary should cover before generating it.

The summary report should contain:
1. **Header**: Mart name, report date, analyst name
2. **Test Summary Table**: By test type — total, passed, failed, warnings
3. **Detailed Results**: Each test with description, SQL, result, status, notes
4. **Recommendation**: Overall validation recommendation (pass/conditional pass/fail)
5. **Sign-off block**: Space for analyst signature and date

**[STOP]** Present draft summary. Analyst reviews, adds notes, confirms recommendation. **Update checkpoint.**

### 2e. Cross-Mart Summary

```sql
SELECT MART_NAME,
       COUNT(*) AS total_tests,
       SUM(CASE WHEN STATUS = 'pass' THEN 1 ELSE 0 END) AS passed,
       SUM(CASE WHEN STATUS = 'fail' THEN 1 ELSE 0 END) AS failed,
       MIN(CREATED_AT) AS first_test,
       MAX(CREATED_AT) AS last_test
FROM {{DB}}.{{SCHEMA}}.VALIDATION_TEST_LOG
GROUP BY MART_NAME
ORDER BY MART_NAME;
```

## Dual Persistence

Every write to VALIDATION_TEST_LOG (including 2b annotations and 2c status changes) is also appended to the local markdown log, using the block format in `../references/conventions.md` — Logging to VALIDATION_TEST_LOG.

## Output

- Query results presented inline
- Updated `VALIDATION_TEST_LOG` entries (if annotated/updated)
- `validation-workspace/testing-records/{mart-name}-validation-summary.md` (if summary requested)

## Important Rules

1. Never delete test log entries — the record is append-only
2. Status transitions should be logged (old status → new status) in analyst notes
3. The sign-off report is the primary handoff artifact — it must be comprehensive
4. Always write to both Snowflake table and local markdown — dual persistence is mandatory
5. At every [STOP] point, emit a one-line session hygiene reminder per `../references/conventions.md` — Session Hygiene. Checkpoint writes are mandatory — never skip them.
