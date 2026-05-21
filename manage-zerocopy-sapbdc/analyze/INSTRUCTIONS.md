---
name: sap-bdc-snowflake-analyze
description: "Analyze shared SAP BDC data products in Snowflake. Covers exploration, querying, cross-product joins, CTAS for persisted results, and Semantic Views."
parent_skill: manage-zerocopy-sapbdc
---

# Analyze Shared Data Products in Snowflake

## When to Load

Main skill Step 1: User selects "Analyze shared data"

## Prerequisites

- One or more catalog-linked databases already created from SAP BDC data products
- USAGE privilege on the catalog-linked database(s)

## Workflow

### Step 1: Identify Available Data

**Goal:** Discover what SAP data is mounted in Snowflake.

**Actions:**

1. **Ask** user:
   ```
   Do you know the name(s) of the catalog-linked database(s) you want to analyze?
   If not, I can search for them.
   ```

2. **If user doesn't know**, search:
   ```sql
   SHOW DATABASES;
   ```
   Filter for databases with `LINKED_ZEROCOPY_CONNECTOR` origin, or ask user to identify them.

3. **List schemas and tables:**
   ```sql
   SHOW SCHEMAS IN DATABASE <database_name>;
   SHOW TABLES IN DATABASE <database_name>;
   ```

4. **Present** the available tables to the user.

### Step 2: Explore Table Schemas

**Goal:** Understand the structure of the data.

**Actions:**

1. **Ask** user which table(s) to explore.

2. **For each table:**
   ```sql
   DESC TABLE <database>.<schema>.<table>;
   SELECT * FROM <database>.<schema>.<table> LIMIT 5;
   ```

3. **Present** column names, types, and sample data.

### Step 3: Check Semantic Views

**Goal:** Leverage auto-generated Semantic Views from SAP CSN for richer context.

**Actions:**

1. **Check** for the `snowflake$` schema (auto-created with Semantic Views):
   ```sql
   SHOW SCHEMAS IN DATABASE <database_name>;
   ```

2. **If `snowflake$` exists**, list semantic views:
   ```sql
   SHOW SEMANTIC VIEWS IN SCHEMA <database_name>."snowflake$";
   ```

3. **Inform** user:
   ```
   This catalog-linked database has auto-generated Semantic Views in the snowflake$ schema.
   These define business metrics, entities, and relationships from the SAP CSN.
   You can use Cortex Analyst to query these with natural language.
   Would you like to explore the Semantic Views or write SQL directly?
   ```

**STOP**: Wait for user preference.

### Step 4: Query and Analyze

**Goal:** Run analytical queries based on user's questions.

**Actions:**

1. **Ask** user what they want to analyze:
   ```
   What business question would you like to answer?
   Examples:
   - Top customers by revenue
   - Sales orders by region and status
   - Inventory levels below threshold
   - Join data across multiple SAP data products
   ```

**STOP**: Wait for user's analysis question.

2. **Write and execute** the SQL query based on user's request.

3. **For cross-database joins** (multiple SAP data products):
   ```sql
   SELECT ...
   FROM <database_1>.<schema>.<table> a
   JOIN <database_2>.<schema>.<table> b
     ON a.<key> = b.<key>
   ...;
   ```

4. **Present** results to user. Offer visualizations if appropriate.

### Step 5: Persist Results (Optional)

**Goal:** Save analysis results as native Snowflake tables via CTAS.

**Actions:**

1. **Ask** user:
   ```
   Would you like to persist these results as a native Snowflake table (CTAS)?
   This creates a writable copy independent of the SAP data product.
   - If yes, provide a target database.schema.table name.
   - If no, we're done.
   ```

2. **If yes, execute:**
   ```sql
   CREATE DATABASE IF NOT EXISTS <target_db>;
   CREATE SCHEMA IF NOT EXISTS <target_db>.<target_schema>;

   CREATE OR REPLACE TABLE <target_db>.<target_schema>.<table_name> AS
   <user_query>;
   ```

3. **Verify:**
   ```sql
   SELECT COUNT(*) FROM <target_db>.<target_schema>.<table_name>;
   SELECT * FROM <target_db>.<target_schema>.<table_name> LIMIT 5;
   ```

## Stopping Points

- After Step 3: Semantic View vs SQL preference
- After Step 4, action 1: Wait for analysis question
- After Step 5, action 1: Persist or skip

## Output

Query results from SAP BDC data products, optionally persisted as native Snowflake tables.
