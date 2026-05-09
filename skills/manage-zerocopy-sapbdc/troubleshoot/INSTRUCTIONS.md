---
name: sap-bdc-snowflake-troubleshoot
description: "Troubleshoot SAP BDC <=> Snowflake zero-copy connector issues. Covers state errors, privilege checks, connection failures, and catalog-linked database problems."
parent_skill: manage-zerocopy-sapbdc
---

# Troubleshoot SAP BDC Connector Issues

## When to Load

Main skill Step 1: User selects "Troubleshoot", or redirected from another sub-skill on error.

## Workflow

### Step 1: Identify the Problem

**Goal:** Classify the issue type.

**Actions:**

1. **Ask** user:
   ```
   What issue are you experiencing?

   1. Connector won't connect (CONNECT_ERROR)
   2. Connector won't disconnect (DISCONNECT_ERROR)
   3. Cannot create a catalog-linked database
   4. Catalog-linked database shows no tables
   5. Catalog-linked database does not show the snowflake$ schema
   6. Catalog-linked database has snowflake$ schema but no semantic view
   7. Cannot publish / share back to SAP BDC
   8. Data is stale or not refreshing
   9. Privilege / access denied errors
   10. Other issue
   ```

**STOP**: Wait for user selection.

**Route:**
- Options 1-2 -> Step 2 (Connection State Errors)
- Option 3 -> Step 3, actions 1-4 (CLD Creation Issues)
- Option 4 -> Step 3, action 5 (CLD Shows No Tables)
- Option 5 -> Step 3, action 6 (Missing snowflake$ Schema)
- Option 6 -> Step 3, action 7 (Missing Semantic View)
- Option 7 -> Step 4 (Publishing Issues)
- Option 8 -> Step 5 (Data Freshness Issues)
- Option 9 -> Step 6 (Privilege Issues)
- Option 10 -> Step 7 (General Diagnostics)

### Step 2: Connection State Errors

**Goal:** Diagnose CONNECT_ERROR or DISCONNECT_ERROR.

**Actions:**

1. **Ask** for the connector name.

2. **Execute:**
   ```sql
   DESC ZEROCOPY CONNECTOR <connector_name>;
   ```

3. **Check** the `connection_error` field for the error message.

4. **Common causes and fixes:**

   | Error Pattern | Likely Cause | Fix |
   |---------------|--------------|-----|
   | Invalid invitation link | Link expired, malformed, or already used | Regenerate from SAP for Me. Verify the account URL used as the External System Instance Identifier is correct (all lowercase, underscores replaced with dashes). Note: each invitation link can only be used once |
   | Connection timeout | Network or SAP-side issue | Verify SAP-side setup is complete; retry |
   | Already connecting | Previous attempt still in progress | Wait and re-check state |

5. **For CONNECT_ERROR**, retry:
   Ask user to verify the account URL and regenerate invitation link from SAP for Me, then:
   ```sql
   ALTER ZEROCOPY CONNECTOR IF EXISTS <connector_name>
     CONNECT WITH CONFIG = (INVITATION_LINK = '<new_link>');
   ```

6. **For DISCONNECT_ERROR**, check for remaining CLDs and share-back status:
   ```sql
   DESC ZEROCOPY CONNECTOR <connector_name>;
   ```
   - If `share_back` is TRUE, disable it first:
     ```sql
     ALTER ZEROCOPY CONNECTOR IF EXISTS <connector_name>
       SET SHARE_BACK = FALSE;
     ```
   - If `catalog_linked_databases` is not empty, drop all CLDs:
     ```sql
     DROP DATABASE <cld_name>;
     ```
   Then retry:
   ```sql
   ALTER ZEROCOPY CONNECTOR IF EXISTS <connector_name> DISCONNECT;
   ```

### Step 3: Catalog-Linked Database Issues

**Goal:** Resolve issues creating or using CLDs.

**Actions:**

1. **Check** CLD link status and configuration:
   ```sql
   SELECT SYSTEM$CATALOG_LINK_STATUS('<cld_name>');
   SELECT SYSTEM$GET_CATALOG_LINKED_DATABASE_CONFIG('<cld_name>');
   ```

2. **Verify** connector is CONNECTED:
   ```sql
   DESC ZEROCOPY CONNECTOR <connector_name>;
   ```
   If not CONNECTED, route to Step 2.

3. **Verify** privileges:
   ```sql
   SHOW GRANTS TO ROLE <current_role>;
   ```
   Required: `CREATE DATABASE` on account + `USAGE` on connector.

4. **Verify** the share exists:
   ```sql
   SELECT SYSTEM$ZEROCOPY_CONNECTOR_LIST_SHARES('<connector_name>');
   ```
   If the data product is not listed, the SAP-side has not shared it yet.

5. **If CLD creation fails**, present error and check:
   - Share name matches exactly (these are URN-style strings)
   - SYNC_INTERVAL_SECONDS is within 30-86400

6. **If CLD shows no tables:**
   - Check grants on the catalog-linked database and the connector:
     ```sql
     SHOW GRANTS ON DATABASE <cld_name>;
     SHOW GRANTS TO ROLE <current_role>;
     SHOW GRANTS ON ZEROCOPY CONNECTOR <connector_name>;
     ```
     Required: `USAGE` on the connector, `USAGE` on the catalog-linked database, and `USAGE` on schemas within the CLD. If grants are missing:
     ```sql
     GRANT USAGE ON DATABASE <cld_name> TO ROLE <role>;
     GRANT USAGE ON ALL SCHEMAS IN DATABASE <cld_name> TO ROLE <role>;
     ```
   - Check if schemas exist:
     ```sql
     SHOW SCHEMAS IN DATABASE <cld_name>;
     ```
   - Check if tables exist in each schema:
     ```sql
     SHOW TABLES IN DATABASE <cld_name>;
     ```
   - If no tables are listed, the SAP-side data product may not have shared any tables yet, or the initial sync has not completed.
   - Wait for the sync interval to elapse and re-check. If still empty, verify on the SAP side that the data product contains tables and is fully provisioned.

7. **If CLD does not show the `snowflake$` schema:**
   - The `snowflake$` schema is auto-created when the SAP data product includes a CSN (Core Schema Notation) document.
   - Check if the schema exists:
     ```sql
     SHOW SCHEMAS IN DATABASE <cld_name>;
     ```
   - If `snowflake$` is missing, the SAP data product was published without a CSN document. Contact the SAP data product provider and request they include a CSN document in the data product definition.
   - A sync must also complete before the schema appears. Check the sync interval and wait if the CLD was just created.

8. **If `snowflake$` schema exists but contains no Semantic View:**
   - List objects in the `snowflake$` schema:
     ```sql
     SHOW SEMANTIC VIEWS IN SCHEMA <cld_name>."snowflake$";
     ```
   - If no semantic views are returned:
     - The CSN document included with the SAP data product may be incomplete or malformed, preventing Snowflake from generating a semantic view.
     - Check if the CSN was recently updated — the sync interval must elapse for changes to propagate.
     - Contact the SAP data product provider to verify the CSN document is valid and includes entity definitions with proper annotations.
   - If a semantic view exists but is not usable, describe it to check for errors:
     ```sql
     DESC SEMANTIC VIEW <cld_name>."snowflake$".<view_name>;
     ```

### Step 4: Publishing Issues

**Goal:** Resolve issues publishing data back to SAP BDC.

**Actions:**

1. **Verify** SHARE_BACK is enabled:
   ```sql
   DESC ZEROCOPY CONNECTOR <connector_name>;
   ```
   If not enabled:
   ```sql
   ALTER ZEROCOPY CONNECTOR IF EXISTS <connector_name>
     SET SHARE_BACK = TRUE;
   ```

2. **Verify** CREATE SHARE privilege:
   ```sql
   SHOW GRANTS TO ROLE <current_role>;
   ```

3. **Verify** tables are Iceberg V3 with copy-on-write:
   ```sql
   SHOW ICEBERG TABLES IN SCHEMA <db>.<schema>;
   ```

4. **Verify** share is associated:
   ```sql
   DESC ZEROCOPY CONNECTOR <connector_name>;
   ```

5. **If SYSTEM$SAP_PUBLISH_DATA_PRODUCT fails**, check:
   - ORD metadata JSON is valid
   - CSN document JSON is valid
   - Connector is CONNECTED
   - Share has proper grants (USAGE on DB/schema, SELECT on tables)

### Step 5: Data Freshness Issues

**Goal:** Resolve stale data in catalog-linked databases.

**Actions:**

1. **Check** sync interval:
   ```sql
   SHOW DATABASES LIKE '<cld_name>';
   ```

2. **Check** the catalog-linked database status and configuration:
   ```sql
   SELECT SYSTEM$CATALOG_LINK_STATUS('<cld_name>');
   SELECT SYSTEM$GET_CATALOG_LINKED_DATABASE_CONFIG('<cld_name>');
   ```
   Review the status output for errors or warnings. If the link status shows an error, the connector may need to be reconnected (route to Step 2).

3. **Force an immediate rediscovery** of schemas and tables from the remote catalog by suspending and resuming discovery:
   ```sql
   ALTER DATABASE <cld_name> SUSPEND DISCOVERY;
   ALTER DATABASE <cld_name> RESUME DISCOVERY;
   ```
   Confirm discovery is running again:
   ```sql
   SELECT SYSTEM$CATALOG_LINK_STATUS('<cld_name>');
   ```
   Verify `executionState` is `RUNNING`, then check for updated tables:
   ```sql
   SHOW TABLES IN DATABASE <cld_name>;
   ```
   To refresh metadata for all Iceberg tables in the CLD, **Execute:**
   ```sql
   DECLARE
     c CURSOR FOR
       SELECT "schema_name", "name"
       FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))
       WHERE "schema_name" != 'snowflake$';
     schema_name VARCHAR;
     table_name VARCHAR;
   BEGIN
     SHOW ICEBERG TABLES IN DATABASE <cld_name>;
     OPEN c;
     LOOP
       FETCH c INTO schema_name, table_name;
       IF (SQLCODE != 0) THEN
         LEAVE;
       END IF;
       EXECUTE IMMEDIATE 'ALTER ICEBERG TABLE <cld_name>.' || schema_name || '.' || table_name || ' REFRESH';
     END LOOP;
     CLOSE c;
   END;
   ```

4. **If data is stale beyond the sync interval**, check:
   - Connector is still CONNECTED
   - SAP-side data product is still active
   - No errors in connector state

5. **Suggest** reducing sync interval if faster refresh is needed:
   ```sql
   ALTER DATABASE <cld_name> UPDATE LINKED_CATALOG SET SYNC_INTERVAL_SECONDS = <new_interval>;
   ```
   Minimum is 30 seconds.

### Step 6: Privilege Issues

**Goal:** Diagnose and resolve access denied errors.

**Actions:**

1. **Check** current role and grants:
   ```sql
   SELECT CURRENT_ROLE();
   SHOW GRANTS TO ROLE <current_role>;
   SHOW GRANTS ON ZEROCOPY CONNECTOR <connector_name>;
   ```

2. **Compare** against required privileges (from main SKILL.md):
   - CREATE ZEROCOPY CONNECTOR on schema -> to create
   - OPERATE on connector -> to connect/disconnect
   - USAGE on connector -> to describe, list shares, create CLD
   - MODIFY on connector -> to set/unset properties
   - CREATE DATABASE on account -> to create CLD
   - CREATE SHARE on account -> to publish

3. **Grant** missing privileges (requires sufficient role):
   ```sql
   GRANT <privilege> ON <object_type> <object_name> TO ROLE <role>;
   ```

### Step 7: General Diagnostics

**Goal:** Collect diagnostic information for unknown issues.

**Actions:**

1. **Execute** full diagnostic:
   ```sql
   SHOW ZEROCOPY CONNECTORS IN SCHEMA <db>.<schema>;
   DESC ZEROCOPY CONNECTOR <connector_name>;
   SELECT CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_SCHEMA();
   SHOW GRANTS TO ROLE <current_role>;
   ```

2. **If a catalog-linked database is involved**, also run:
   ```sql
   SELECT SYSTEM$CATALOG_LINK_STATUS('<cld_name>');
   SELECT SYSTEM$GET_CATALOG_LINKED_DATABASE_CONFIG('<cld_name>');
   ```

3. **Present** all diagnostic output to user.

4. **If issue persists**, recommend:
   ```
   The issue may require SAP-side investigation or Snowflake Support.
   - Check SAP for Me for connector status on the SAP side
   - Contact Snowflake Support with the diagnostic output above
   ```

## Stopping Points

- After Step 1: Wait for issue classification
- After each diagnostic step: Present findings before suggesting fixes

## Output

Diagnosed issue with recommended fix, or diagnostic output for escalation to Snowflake Support.
