---
name: mart-profile
parent_skill: messy-migration-validation
description: "Data mart profiling — summary statistics, key analysis, distributions, anomaly detection. Produces PROFILE_RESULTS table and markdown report. Triggers: profile, profiling, summary stats, data profiling, column stats, anomaly detection, key analysis."
---

# Data Mart Profiling

**You are using the mart-profile sub-skill. Follow these instructions exactly.**

## When to Use

- Analyst needs summary statistics for a data mart before comparison
- Assessing column distributions, null rates, cardinality, and candidate keys
- Generating a baseline profile to compare across runs
- Identifying anomalies (constant columns, unexpected nulls, outliers)

## Workflow

### Prerequisites

1. Read `references/sql-patterns.md` — contains all profiling SQL templates
2. Read `../references/conventions.md` — naming standards and table schemas

### Step 1: Gather Inputs

Ask the analyst for:
1. **Target table**: Fully qualified name (`DATABASE.SCHEMA.TABLE`)
2. **Optional filter**: WHERE clause to restrict profiling scope (e.g., `WHERE AS_OF_DATE >= '2024-01-01'`)
3. **Database/schema for results**: Where to create/write `PROFILE_RESULTS` table
4. **Column scope** (optional): Does the analyst want to profile all columns or a specific subset? If the table has many columns (e.g., 50+), proactively ask whether to limit scope. Accept a list of column names, a pattern (e.g., `*_AMT`, `*_KEY`), or "all" (default).

If the analyst provided context from the router, reuse it.

**[STOP]** Confirm inputs — including column scope — before proceeding.

### Step 2: Ensure PROFILE_RESULTS Table Exists

Execute the DDL from `../references/conventions.md` to create the table if it doesn't exist.

### Step 3: Run Profiling Queries

**FIRST**: Read `references/sql-patterns.md` and execute each pattern in sequence:

1. **Table Overview** — row count, column count, sample rows
2. **Column Metadata** — data types, nullability, ordinal positions
3. **Numeric Column Profile** — generate a single batched query (UNION ALL) covering all numeric columns at once. Do not execute per-column.
4. **Categorical Column Profile** — cardinality, null rates per non-numeric column. **Exclude the following by default:**
   - **Date/time columns** (DATE, TIMESTAMP*, TIME): continuous values, rarely meaningful as categories
   - **Mid-cardinality text fields** — distinct_pct 20–90% (e.g., customer name, address, description): distributions rarely actionable

   Decide on cardinality, not name. Name patterns (`*_NAME`, `*_ADDR`, `*_DESC`) are a hint only: a low-cardinality lookup such as `PLAN_NAME` or `REGION_NAME` stays in. If cardinality is not yet known, run a quick `COUNT(DISTINCT)` for the candidate columns before proposing exclusions.

   **[STOP]** Before running, present the proposed inclusion/exclusion list to the analyst and confirm. The analyst may override any exclusion.

5. **Top Values for Categorical Columns** — present the analyst with an explicit column list before running, classified as follows:

| Classification | Criteria | Default Behavior |
|---------------|----------|-----------------|
| Low cardinality | distinct_pct < 50% and distinct_count ≤ 1,000 | Top 20 values (Pattern 6) |
| High cardinality | distinct_pct 50–90% | Top 20 values (Pattern 6) with warning |
| Near-unique | distinct_pct > 90% | Skip — flag as "high cardinality, top values not meaningful" |
| Identifier-like | distinct_count > 1,000, or values look like identifiers (emails, phone numbers, account/order numbers), regardless of distinct_pct | Skip — flag as "identifier-like, top values not meaningful" |

Low-cardinality columns with fewer than ~20 distinct values can be batched into one Pattern 6 query (UNION ALL of `column, value` pairs with a single GROUP BY).

**[STOP]** Present the classified list to the analyst and confirm which columns to include before executing. The analyst may promote skipped columns or demote included ones.
6. **Key/Uniqueness Analysis** — identify candidate keys, check uniqueness
7. **Hash Aggregate** — per-column HASH_AGG for exact-match comparison later

If the analyst specified a column scope in Step 1, only run column-level patterns (3–7) for the columns in scope. Patterns 1–2 always run on the full table.

For each query:
- Substitute `{{TABLE_NAME}}`, `{{COLUMN_NAME}}`, `{{FILTER}}` with analyst-provided values
- Execute via `snowflake_sql_execute`
- Collect results

### Full Value Review (Analyst Override)

After presenting the default top-20 distributions, ask the analyst:

```
Would you like the full value list for any specific columns?
(Useful for categorical columns where the top 20 may not capture all meaningful values.)
```

If the analyst selects columns, re-run Pattern 6 for those columns **without the LIMIT** clause (use Pattern 6a). Still subject to the cardinality guard — if distinct_pct > 90%, warn the analyst before proceeding.

### Step 4: Write Results to PROFILE_RESULTS

Generate a unique `RUN_ID` (UUID) and insert one row per column into `PROFILE_RESULTS` with all collected metrics.

After the INSERT completes, run a verification query to confirm the rows were written correctly:

```sql
SELECT COLUMN_NAME, DATA_TYPE, DISTINCT_COUNT, NULL_PCT, HASH_AGG
FROM {{DB}}.{{SCHEMA}}.PROFILE_RESULTS
WHERE RUN_ID = '{{RUN_ID}}'
ORDER BY ORDINAL_POSITION;
```

**[STOP]** Present the verification results to the analyst. If the row count does not match the expected number of profiled columns, investigate before proceeding.

## Step 5: Generate Profile Report

Create a markdown report at `validation-workspace/profiles/{mart-name}-profile-{YYYY-MM-DD}.md` containing:

1. **Header**: Table name, row count, column count, profile date, RUN_ID
2. **Column Summary Table**: column name, type, null %, distinct %, min, max, avg
3. **Anomalies Flagged**:
   - Columns with >0% nulls where none expected
   - Columns with cardinality = 1 (constant value)
   - Columns with cardinality = row count (candidate unique key)
   - Numeric columns with values > 3 stddevs from mean
   - Columns with 100% null rate
4. **Key Analysis**: Candidate primary keys, uniqueness confirmation

Present the anomalies summary to the analyst.

**[STOP]** Analyst reviews findings.

### Step 6: Log to Testing Record

Log per `../references/conventions.md` — Logging to VALIDATION_TEST_LOG:
- `TEST_TYPE`: 'profile'
- `RESULT_SUMMARY`: Count of anomalies, row count, column count
- `STATUS`: 'info'
- `SQL_EXECUTED`: Summary of queries run

### Step 7: Offer Semantic View (Opt-In)

After profiling completes, offer:

```
Profile complete. Would you like to:
a) Generate a semantic view for this mart (enables natural-language exploration via Cortex Analyst)
b) Proceed to baseline model development
c) Investigate flagged anomalies further
```

If the analyst selects (a):
- **Delegate to the `semantic-view` skill** (invoke it to create a new semantic view)
- Pass the profiling metadata (column names, types, descriptions inferred from profiling, key relationships) as context
- Save the resulting YAML to `validation-workspace/semantic-views/{mart-name}.yaml`

## Output

- `PROFILE_RESULTS` table populated for this mart
- `validation-workspace/profiles/{mart-name}-profile-{date}.md` report
- `VALIDATION_TEST_LOG` entry
- (Optional) `validation-workspace/semantic-views/{mart-name}.yaml`

## Important Rules

1. Use the SQL patterns from `references/sql-patterns.md` exactly — do not generate custom profiling SQL
2. Always generate a RUN_ID so profiles can be compared across runs
3. Flag anomalies based on data, not assumptions — present findings neutrally
4. The HASH_AGG per column enables later exact-match comparison in UC3
5. Respect column scope — if the analyst limited which columns to profile, only run column-level patterns on those columns
6. Always apply the cardinality guard before running top-value queries — never GROUP BY a near-unique column without analyst opt-in
7. At every [STOP] point, emit a one-line session hygiene reminder per `../references/conventions.md` — Session Hygiene
