---
name: sap-bdc-snowflake-consume
description: "Consume SAP BDC data products in Snowflake via zero-copy connector. Covers listing data products and mounting catalog-linked databases."
parent_skill: manage-zerocopy-sapbdc
---

# Consume Data Products from SAP BDC

## When to Load

Main skill: User selects "Consume shared data products"

## Workflow

### Step 1: Select a Connector

**Goal:** Identify which zero-copy connector to use. A Snowflake account can have multiple connectors.

**Actions:**

1. **Ask** user for the database and schema where the connector lives:
   ```
   Which SAP BDC zero-copy connector would you like to use?
   Provide the database and schema (e.g., SAP_BDC_DB.SAP_CONNECTOR):
   ```

2. **Execute** to list connectors:
   ```sql
   SHOW ZEROCOPY CONNECTORS IN SCHEMA <db>.<schema>;
   ```

3. **If multiple connectors exist:** Present the list and ask user to select one.
4. **If one connector exists and is CONNECTED:** Use it and proceed to Step 2.
5. **If connector exists but NOT connected:** Inform user and **Load** `troubleshoot/INSTRUCTIONS.md`.
6. **If no connectors exist:** Inform user and **Load** `create-connector/INSTRUCTIONS.md`. After connector creation, return here and proceed to Step 2.

### Step 2: List Available Data Products

**Goal:** Show user which SAP data products are available.

**Actions:**

1. **Execute:**
   ```sql
   WITH raw AS (
     SELECT PARSE_JSON(
       SYSTEM$ZEROCOPY_CONNECTOR_LIST_SHARES('<db>.<schema>.<connector_name>')
     ) AS json_data
   )
   SELECT
     f.value:name::STRING         AS share_name,
     f.value:id::STRING           AS share_id,
     f.value:display_name::STRING AS display_name,
     f.value:comment::STRING      AS description
   FROM raw,
   LATERAL FLATTEN(INPUT => json_data) f;
   ```

2. **If the list is empty** (no data products returned), present to user:
   ```
   No data products have been shared with this zero-copy connector yet.

   To share data products from SAP BDC to Snowflake, a user with the following SAP BDC global role privileges
   must share them from the central SAP Business Data Cloud catalog:
   - BDC Data Packages (read) — to access SAP Business Data Cloud
   - Catalog Asset (read) — to access the catalog and view objects
   - Cloud Data Product (share) — to share data products to target systems

   Steps in SAP BDC:
   1. In the central SAP Business Data Cloud catalog, select data products to share
   2. From Catalog & Marketplace, search for or filter to find the data products
   3. Select "Share" on the data product to open the Manage Share Access dialog
   4. Under Target System, choose the Snowflake account with the enrolled Zerocopy Connector
   5. Select "Update" to start the sharing process

   For more details, see:
   https://help.sap.com/docs/business-data-cloud/sap-business-data-cloud-connect/sharing-data-products-from-sap-business-data-cloud-trough-sap-bdc-connect

   Come back here after you have shared a few data products from SAP BDC to consume in Snowflake.
   ```

   **STOP**: Wait for user to confirm they have shared data products, then re-run the query in action 1.

3. **Present** the data products in a table to the user.

4. **Ask** user which data product(s) to mount:
   For each data product, suggest a default Snowflake database name derived from the `display_name`. First strip any trailing parenthetical technical suffix (e.g. `"Sales Order (BDF730, sap.s4pce:apiResource:SalesOrder:v1)"` → `"Sales Order"`), then convert to UPPER_SNAKE_CASE (uppercase, spaces and hyphens replaced with underscores, remove remaining special characters). For example, "Sales Order" → `SALES_ORDER`, "Material Master - Full" → `MATERIAL_MASTER_FULL`.
   ```
   Which data product(s) would you like to mount as catalog-linked database(s)?
   Provide the share_name(s) from the list above.

   Suggested Snowflake database name(s):
   - <share_name_1> → <SUGGESTED_DB_NAME_1>
   - <share_name_2> → <SUGGESTED_DB_NAME_2>

   You can accept these defaults or provide your own database name(s).
   ```

**STOP**: Wait for user to select data product(s) and provide database name(s).

### Step 3: Create Catalog-Linked Database(s)

**Goal:** Mount the selected SAP data product(s) as Snowflake databases.

**Actions:**

1. **Execute** for each selected data product:
   ```sql
   CREATE DATABASE <database_name>
     LINKED_ZEROCOPY_CONNECTOR = (
       CONNECTOR_NAME = '<db>.<schema>.<connector_name>',
       SHARE_NAME = '<share_name>',
       SYNC_INTERVAL_SECONDS = 86400
     );
   ```

2. **Verify:**
   ```sql
   SHOW DATABASES LIKE '<database_name>%';
   ```

3. **Note for user:** A read-only `snowflake$` schema is automatically created with Semantic Views generated from the SAP Core Schema Notation (CSN) for the shared data product. These enable Cortex Analyst for natural-language querying of SAP data.

**Output:** Catalog-linked database(s) mounted and ready to query.

### Step 4: Quick Data Preview

**Goal:** Confirm data is accessible.

**Actions:**

1. **List tables:**
   ```sql
   SHOW TABLES IN DATABASE <database_name>;
   ```

2. **Preview data:**
   ```sql
   SELECT * FROM <database_name>.<schema>.<table> LIMIT 10;
   ```

3. **Present** results to user.

## Stopping Points

- After Step 1: If no connectors exist, route to create-connector sub-skill
- After Step 2, action 2: If no data products shared, wait for user to share from the SAP Business Data Cloud catalog
- After Step 2, action 4: Wait for user to select data products
- After Step 3: Confirm databases created

## Output

One or more catalog-linked databases in Snowflake containing live SAP BDC data products, ready for querying and analysis.
