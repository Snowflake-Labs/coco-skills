---
name: recommend-object
title: Recommend Snowflake Object
summary: Score and rank candidate Snowflake tables/views by trust signals to recommend the best source for a data question.
description: >-
  Use when candidates have already been identified and the requester needs to
  know which one is the most trustworthy source for a given metric or concept.
  Scores candidates on semantic-view backing (and verified queries),
  Streamlit/dashboard consumers, daily refresh, service-role ownership, schema
  placement, freshness, and structural quality. Triggers: "which table should
  we use", "score these tables", "rank these objects", "most trustworthy",
  "best source for", "which table has".
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Read
prompt: "$recommend-object I have three candidate tables for daily revenue — which is the most trustworthy?"
language: en
status: Published
author: Snowflake
type: snowflake
demo-url: ""
---

# Recommend Object

Given a list of candidate Snowflake objects already identified in the conversation, score and rank them by trustworthiness to recommend the best one(s).

## When to Use

- User wants to know which table or view is the most trustworthy source for a given metric or concept
- User asks which of a set of tables is best to use for their data question
- User wants a deeper analysis of data assets to identify the highest-quality one

## Prerequisites

- `SNOWFLAKE.ACCOUNT_USAGE` access (for `OBJECT_DEPENDENCIES`)
- `SELECT` privilege on candidate tables

## Workflow

### Step 1: Gather Trust Signals

**Goal:** For each candidate (max 10), collect scoring signals via metadata queries.

**Batch queries where possible** to reduce round trips.

#### 1a. Table Metadata (SHOW TABLES / SHOW VIEWS)

For each candidate:
```sql
SHOW TABLES LIKE '<table_name>' IN SCHEMA <database>.<schema>;
```

Extract: `kind` (TABLE vs TRANSIENT vs TEMPORARY), `rows`, `bytes`, `created_on`, `retention_time`, `change_tracking`, `comment`

#### 1b. Column Analysis

```sql
DESC TABLE <database>.<schema>.<table>;
```

Extract: column count, whether column names match the user's domain terms

#### 1c. Semantic View Backing + Verified Queries (direct detection)

**⚠️ Important:** `OBJECT_DEPENDENCIES` does NOT track semantic view references — the `REFERENCING_OBJECT_DOMAIN` column never contains `'SEMANTIC VIEW'`. You must detect semantic view backing directly.

#### 1c-i. List Semantic Views

List semantic views in the candidate's schema (run once per schema, not per candidate):
```sql
SHOW SEMANTIC VIEWS IN SCHEMA <database>.<schema>;
```

#### 1c-ii. Describe Semantic Views

For each semantic view found, describe it to check if it references the candidate table:
```sql
DESC SEMANTIC VIEW <database>.<schema>.<semantic_view_name>;
```

From this single result set, extract **all** of the following:

- **Semantic view backing**: Look for rows where `object_kind = 'TABLE'` and `property = 'BASE_TABLE_NAME'`. If `property_value` matches the candidate table/view name, this semantic view backs the candidate.
- **Semantic view richness**: Count the number of rows with `object_kind` = `DIMENSION`, `FACT`, and `METRIC` (more = better curated).
- **Verified queries**: Look for rows where `object_kind = 'EXTENSION'` with `object_name = 'CA'` and `property = 'VALUE'`. Parse the JSON and extract `verified_queries` — an array of objects with `verified_by` (human name), `verified_at` (timestamp), and `sql` (the verified SQL). Check if any verified query references the candidate table name. Record the number of verified queries and verifier names.

A table backed by a semantic view with human-verified SQL is an extremely strong trust signal.

#### 1d. Downstream Dependencies

Query downstream dependencies for each candidate:
```sql
SELECT
  REFERENCING_OBJECT_DOMAIN,
  REFERENCING_DATABASE,
  REFERENCING_SCHEMA,
  REFERENCING_OBJECT_NAME,
  COUNT(*) AS dep_count
FROM SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES
WHERE REFERENCED_OBJECT_NAME = '<table_name>'
  AND REFERENCED_DATABASE = '<database>'
  AND REFERENCED_SCHEMA = '<schema>'
GROUP BY 1, 2, 3, 4
ORDER BY dep_count DESC;
```

From this result set, extract:
- **Streamlit app consumers**: rows where `REFERENCING_OBJECT_DOMAIN = 'STREAMLIT'`
- **Downstream dep count and quality**: total deps, dep types, schema quality of dependents (FINANCE/SALES/PRODUCT >> TEMP.USERNAME)

#### 1e. Service Role Ownership

From the SHOW TABLES output (Step 1a), extract the `owner` field:
- **Service role**: owner name ends with `_RL`, `_ROLE`, `_SVC`, or matches a pattern like `<TEAM>_MODELING_RL` — signals programmatic pipeline ownership
- **Personal user**: owner is a username (e.g., `JSMITH`) — signals ad-hoc creation, lower trust

Also check if **all peer tables in the same schema** share the same owner (unified ownership = governed pipeline).

#### 1f. Daily Refresh Detection (SWAP/RECREATE pattern)

From the SHOW TABLES output (Step 1a), compare `created_on` to today:
- If `created_on::date = CURRENT_DATE()`, the table is recreated daily by a pipeline (CREATE OR REPLACE / table swap pattern)
- This is a stronger freshness signal than just checking MAX(date_column) — it proves an automated pipeline is actively running
- Also check how many peer tables in the same schema were also refreshed today (high ratio = governed pipeline)

#### 1g. Schema Consistency Check

Run once per schema (not per candidate):
```sql
SHOW TABLES IN SCHEMA <database>.<schema>;
```

From the results, check for governance signals across the schema:
- **Uniform retention_time**: do all/most tables share the same retention? (consistency = policy)
- **Uniform change_tracking**: is change_tracking ON for all tables?
- **Uniform ownership**: does a single service role own all tables?
- **Naming convention**: do table names follow consistent prefix patterns (e.g., `DIM_`, `RPT_`, `C_`, `FCT_`)?

A schema where all tables share consistent retention, ownership, and naming is a strong governance signal.

#### 1h. Data Freshness

Query the table's date/time column for MAX value:
```sql
SELECT MAX(<date_column>) FROM <database>.<schema>.<table>;
```

Pick the most obvious date/timestamp column from DESC TABLE output (_DATE, DATE, CREATED_AT, UPDATED_AT, etc.). If no date/timestamp column exists, skip this signal and score freshness as 0.


### Step 2: Score and Rank Candidates

**Goal:** Assign a trust score to each candidate using weighted signals.

#### Scoring Rubric

| Signal | Base | Scoring Logic |
|--------|------|---------------|
| **Semantic view backing** | 20 | +20 if table backs a semantic view; +10 if it backs multiple. If the semantic view contains **verified queries** referencing this table, add +15 (verified by a named human = highest trust). If the verified query directly answers the user's question, add +5 more |
| **Streamlit/dashboard consumer** | 10 | +10 if a Streamlit app or dashboard references this table as a data source (detected via OBJECT_DEPENDENCIES with REFERENCING_OBJECT_DOMAIN = 'STREAMLIT') |
| **Daily refresh (pipeline activity)** | 10 | +10 if `created_on::date = CURRENT_DATE()` (table is recreated daily by pipeline); +5 if created within last 7 days; +0 otherwise. This detects the SWAP/RECREATE pattern used by production data pipelines |
| **Service role ownership** | 10 | +10 if owner is a service role (ends with `_RL`, `_ROLE`, `_SVC`, or follows `<TEAM>_MODELING_RL` pattern); +5 if owner is a shared functional role; +0 if owner is a personal user account |
| **Schema placement** | 10 | Production DB/schema (PRODUCT, FINANCE, SALES, named-team schemas) = +10; domain-scoped schema (DIMENSIONS, FCT) = +8; personal TEMP.USERNAME = +2; TEMP.PUBLIC = +1 |
| **Schema consistency** | 5 | +5 if peer tables in the same schema share uniform retention, ownership, and naming conventions; +3 if 2 of 3 are consistent; +0 if schema is inconsistent (mixed owners, mixed retention, no naming pattern) |
| **Table kind** | 5 | TABLE = +5; DYNAMIC TABLE = +4; VIEW = +3; TRANSIENT = +2; TEMPORARY = +0 |
| **Column relevance** | 10 | % of user's domain terms found in column names x 10; bonus +5 if table name itself contains the domain term |
| **Downstream dep quality** | 10 | Count deps in production schemas (non-TEMP, non-personal) x 2, cap at 10. Bonus: +3 if dependents include FINANCE or SALES schema objects |
| **Freshness** | 5 | Data within 1 day = +5; within 7 days = +4; within 30 days = +2; older = +1 |
| **Retention time** | 5 | retention >= 4 = +5; 2-3 = +3; 1 = +1; 0 = +0 |
| **Row count appropriateness** | 5 | Penalize extremes: very large raw tables (>1B rows) get +1 for aggregation questions; focused tables (<100M) get +5 for specific-concept questions |

**Base total: 105 points.** Bonuses (multiple semantic views, verified queries, column name match, FINANCE/SALES deps) can push the raw total above 105. Normalize for presentation: `score = min(100, round((raw_total / 105) * 100))`

#### Signal Priority

When signals conflict, use this priority order (highest first):
1. **Verified queries in semantic view** — a human domain expert validated the SQL
2. **Daily refresh + service role ownership** — proves active automated pipeline
3. **Streamlit/dashboard consumer** — production app depends on this data
4. **Schema placement + consistency** — organizational governance signals
5. All other signals

#### Tiebreakers

If two candidates score within 5 points of each other:
1. Prefer the one **referenced in verified semantic view queries**
2. Prefer the one with a **service role owner** (not personal user)
3. Prefer the one **refreshed today** (created_on = today)
4. Prefer the one with a **comment** set (documented tables signal intentionality)
5. Prefer the one with **change_tracking = ON**
6. Prefer the one in a **non-TEMP database**
7. Prefer the one with **fewer columns** (more focused/curated)

If still tied after tiebreakers, flag both to the user and suggest checking with the owning team.

### Step 3: Present Recommendations

**Goal:** Show the user the ranked results with reasoning.

**Format:**

```
## Recommended Object(s)

#### 1. <database>.<schema>.<table> (Score: XX/100)
- Why: [1-2 sentence explanation of top signals]
- Key columns: [list relevant columns]
- Freshness: [max date value]
- Backed by semantic view: Yes/No (if yes, note verified query count and verifier names)
- Pipeline: [Daily refresh: Yes/No] [Owner: service role name or personal user]
- Streamlit consumer: [Yes/No — name of Streamlit app if found]
- Downstream usage: X production dependencies

#### 2. <database>.<schema>.<table> (Score: XX/100)
- Why: [explanation]
...

### Not Recommended
- <database>.<schema>.<table>: [reason]
```

### Step 4: Offer to Certify Strong Candidates (Optional)

**Goal:** After presenting scores, ask the user if they want to officially certify any high-scoring candidates.

After presenting the ranked results, ask:
```
Would you like to certify any of these objects as trusted sources in the data catalog?
Certifying marks them with SNOWFLAKE.CORE.CERTIFICATION_STATUS = 'CERTIFIED',
making them discoverable as trusted sources for future users.
```

**If the user wants to certify**, apply `SNOWFLAKE.CORE.CERTIFICATION_STATUS = 'CERTIFIED'` via the standard Snowflake catalog tag flow. Confirm permission and target object before tagging.

**If the user declines**, the workflow ends after presenting the ranked results.

## Stopping Points

- ✋ Step 4: Ask user before applying any certification tag; stop if declined

## Output

- Ranked list of candidate objects with trust scores and reasoning
- Optionally: a certified object via `SNOWFLAKE.CORE.CERTIFICATION_STATUS`

## Notes

- The scoring rubric is a starting framework; adjust weights based on the specific account's conventions
- TEMP databases with personal schemas (TEMP.USERNAME) are almost always experimental/scratch — deprioritize heavily
- Tables referenced by FINANCE or SALES schemas are generally production-grade
