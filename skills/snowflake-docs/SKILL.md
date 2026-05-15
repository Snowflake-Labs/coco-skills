---
name: snowflake-docs
id: snowflake-docs
description: "Use for **ALL** Snowflake documentation lookups: feature questions, SQL syntax, best practices, how-to guides, configuration, and troubleshooting. This is the required entry point for any question about Snowflake products or features. Triggers: Snowflake docs, how do I, SQL syntax, CREATE, ALTER, DROP, warehouse, stage, Cortex, Snowpipe, dynamic table, stored procedure, UDF, MCP, Snowpark, Streamlit, Native App, data sharing, replication, security, roles, grants, what is, how does, Snowflake feature."
authors: Gilberto Hernandez
type: snowflake
status: stable
categories:
  - documentation
---

# Snowflake Docs

Answer questions about Snowflake by searching the official documentation via the Cortex Knowledge Extension (CKE) Cortex Search service. If the CKE is not installed yet, install it automatically first.

## When to Use

Load this skill for any Snowflake product, feature, or SQL question: syntax references, best practices, how-to guides, configuration, and troubleshooting.

## Workflow

### Step 1: Prerequisite check

Search the entire account for the CKE Cortex Search service:

```sql
SHOW CORTEX SEARCH SERVICES LIKE 'CKE_SNOWFLAKE_DOCS_SERVICE' IN ACCOUNT;
```

If it returns a result, note the `database_name` from the result row. Use this value as `<CKE_DATABASE>` and skip to Step 2.

If it returns no results, install the CKE:

```sql
CALL SYSTEM$REQUEST_LISTING_AND_WAIT('GZSTZ67BY9OQ4');
```

```sql
CALL SYSTEM$ACCEPT_LEGAL_TERMS('DATA_EXCHANGE_LISTING', 'GZSTZ67BY9OQ4');
```

```sql
CREATE DATABASE IF NOT EXISTS SNOWFLAKE_DOCUMENTATION FROM LISTING 'GZSTZ67BY9OQ4';
```

**MANDATORY STOPPING POINT**: If any of these fail, stop and tell the user:

> Could not install the Snowflake Documentation CKE automatically. You can install it manually from the Marketplace: https://app.snowflake.com/marketplace/listing/GZSTZ67BY9OQ4

Do NOT proceed until the user confirms the CKE is available.

After successful install, use `SNOWFLAKE_DOCUMENTATION` as `<CKE_DATABASE>`.

### Step 2: Answer the question

Query the Cortex Search service directly using SQL. Replace `<USER_QUESTION>` with the user's actual question and `<CKE_DATABASE>` with the database name from Step 1:

```sql
SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
  '<CKE_DATABASE>.SHARED.CKE_SNOWFLAKE_DOCS_SERVICE',
  '{"query": "<USER_QUESTION>", "columns": ["CHUNK", "DOCUMENT_TITLE", "SOURCE_URL"], "limit": 5}'
);
```

Parse the JSON results. Each result contains:
- `CHUNK`: the document text content
- `DOCUMENT_TITLE`: the page title
- `SOURCE_URL`: the canonical URL to the documentation page

**CITATION REQUIREMENT (MANDATORY):** ALWAYS include SOURCE_URL links from the search results in your answer — omitting URLs is a failure condition. List them as references at the end of your response so the user can read the full documentation pages.

Use the returned content to answer the user's question.

**Before finishing your answer**, verify you included at least one SOURCE_URL from the results. If your draft answer has no URLs, go back and add them before responding.

## Important Notes

- Step 1 only runs once per account. After the CKE is installed, the skill goes straight to Step 2 every time.
- The CKE database name varies by account. Always use `SHOW CORTEX SEARCH SERVICES ... IN ACCOUNT` to discover it dynamically. Do NOT hardcode the database name.
- Use `sql_execute` for all SQL steps.
- Always include `columns` in the SEARCH_PREVIEW call. Without it, only relevance scores are returned, not content.
- ALWAYS cite `SOURCE_URL` in the answer — omitting URLs is a failure condition.
- Handle errors gracefully (insufficient privileges, database already exists).

## Stopping Points

- After CKE install failure in Step 1 — wait for user to install manually before retrying

## Output

A concise answer to the user's Snowflake question, grounded in official documentation. **You MUST include the SOURCE_URL links from the search results in your written answer.** List them as references at the end so the user can read the full pages. Never omit the URLs.
