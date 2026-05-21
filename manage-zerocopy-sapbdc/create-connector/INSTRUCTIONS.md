---
name: sap-bdc-snowflake-create-connector
description: "Create a new SAP BDC zero-copy connector in Snowflake and enroll it with SAP for Me. Covers database/schema creation, connector creation, SAP registration, and connection establishment."
parent_skill: manage-zerocopy-sapbdc
---

# Create a New Zero-Copy Connector

## When to Load

Main skill: User selects "Create a new zero-copy connector", or Consume/Publish sub-skill determines no suitable connector exists.

## Workflow

### Step 0: Check for Auto-Provisioned Connector (SAP Snowflake Greenfield Only)

**Note:** If the user is on an SAP Snowflake (greenfield) account, a connector named `DEFAULT_SAP_BDC_CONNECTOR` under `CONNECTORS.ZEROCOPY` is automatically created and enrolled during provisioning. Check for it:

```sql
SHOW ZEROCOPY CONNECTORS IN SCHEMA CONNECTORS.ZEROCOPY;
```

If a connector exists and is already CONNECTED, inform the user they can skip connector creation and proceed directly to consuming or publishing data products.

### Step 1: Verify Privileges and Create the Connector

**Goal:** Verify required privileges, then create the database, schema, and zerocopy connector object.

**Actions:**

1. **Check privileges** before proceeding:
   ```sql
   SHOW GRANTS TO ROLE IDENTIFIER(CURRENT_ROLE());
   ```
   Verify the current role has:
   - `CREATE DATABASE` on the account (or USAGE on an existing database)
   - `CREATE ZEROCOPY CONNECTOR` on the target schema (or will be granted after schema creation)

   **If privileges are missing**, inform the user:
   ```
   ⚠️ Your current role needs the following privileges to create a connector:

   GRANT CREATE DATABASE ON ACCOUNT TO ROLE <current_role>;
   -- After database/schema exist:
   GRANT CREATE ZEROCOPY CONNECTOR ON SCHEMA <db>.<schema> TO ROLE <current_role>;

   Please ask an account administrator to run these grants, then retry.
   ```

2. **Ask** user for the database, schema, and connector name:
   ```
   Provide the following for your new SAP BDC connector:
   - Database name (e.g., SAP_BDC_DB)
   - Schema name (e.g., SAP_CONNECTOR)
   - Connector name (e.g., MY_SAP_CONNECTOR)
   ```

**STOP**: Wait for user input.

3. **Execute:**
   ```sql
   CREATE DATABASE IF NOT EXISTS <db>;
   CREATE SCHEMA IF NOT EXISTS <db>.<schema>;
   ```

   **Grant connector creation privilege** on the new schema (if running as ACCOUNTADMIN or a role with MANAGE GRANTS):
   ```sql
   GRANT CREATE ZEROCOPY CONNECTOR ON SCHEMA <db>.<schema> TO ROLE IDENTIFIER(CURRENT_ROLE());
   ```
   Note: If the current role already owns the schema (created it), this grant is implicit.

   **Create the connector:**
   ```sql
   CREATE ZEROCOPY CONNECTOR IF NOT EXISTS <db>.<schema>.<connector_name>
     PARTNER = SAP_BDC;
   ```

   **Grant operational privileges on the connector** (needed for later operations like connect, publish, etc.):
   ```sql
   GRANT OPERATE ON ZEROCOPY CONNECTOR <db>.<schema>.<connector_name> TO ROLE IDENTIFIER(CURRENT_ROLE());
   GRANT USAGE ON ZEROCOPY CONNECTOR <db>.<schema>.<connector_name> TO ROLE IDENTIFIER(CURRENT_ROLE());
   GRANT MODIFY ON ZEROCOPY CONNECTOR <db>.<schema>.<connector_name> TO ROLE IDENTIFIER(CURRENT_ROLE());
   ```
   Note: If the current role owns the connector (created it), these grants are implicit. They are included for cases where a different role will perform publish operations.

4. **Verify** creation:
   ```sql
   DESC ZEROCOPY CONNECTOR <db>.<schema>.<connector_name>;
   ```
   Confirm status is `NEW`.

### Step 2: Enroll with SAP BDC

**Goal:** Register the connector with SAP for Me and establish the connection.

**Actions:**

1. **Derive** the Snowflake account URL to use as the Partner Software System Instance Identifier:
   ```sql
   SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME();
   ```
   Then construct the account URL: `https://<orgName>-<accountName>.snowflakecomputing.com`
   - The URL must be **all lowercase**
   - Replace any `_` (underscores) with `-` (dashes) for RFC compliance
   - Example: org `MY_ORG`, account `MY_ACCOUNT` → `https://my-org-my-account.snowflakecomputing.com`

2. **Present** output to user:
   ```
   Provision an SAP Business Data Cloud Connect instance in the SAP for Me portal:
   https://help.sap.com/docs/business-data-cloud/administering-sap-business-data-cloud/provisioning-sap-bdc-connect

   For the "External System Instance Identifier" field, enter the Snowflake account URL derived above:
   <account_url>

   Once provisioning is complete and you have the invitation link, provide it here.
   ```

**STOP**: Wait for user to provide the invitation link from SAP for Me.

3. **Execute** connection using the invitation link:
   ```sql
   ALTER ZEROCOPY CONNECTOR IF EXISTS <db>.<schema>.<connector_name>
     CONNECT WITH CONFIG = (
       INVITATION_LINK = '<invitation_link_from_user>'
     );
   ```

### Step 3: Add to SAP Formation and Verify Connection

**Goal:** Ensure the SAP BDC Connect instance is ready, added to a formation, and the connector reaches CONNECTED state.

**Actions:**

1. **Present** to user:
   ```
   In the SAP for Me portal, check the status of the SAP Business Data Cloud Connect instance you provisioned.
   Now that the zero-copy connector has been enrolled, its status should change from "Processing" to "Ready" within a few minutes.

   When the status is "Ready":
   1. Choose the "Customer Landscape" tab
   2. Under the "Formations" tab, choose "Include Systems" to add the SAP BDC Connect instance to an existing formation

   To create a new formation, see:
   https://help.sap.com/docs/business-data-cloud/administering-sap-business-data-cloud/creating-sap-business-data-cloud-formations

   Please confirm once the SAP BDC Connect instance has been added to a formation.
   ```

**STOP**: Wait for user to confirm the instance has been added to a formation.

2. **Poll** connector state:
   ```sql
   DESC ZEROCOPY CONNECTOR <db>.<schema>.<connector_name>;
   ```
   - If `CONNECTING`: Wait a few seconds and re-check.
   - If `CONNECTED`: Connector is ready. Present confirmation and return to the calling skill.
   - If `CONNECT_ERROR`: Show the `connection_error` value and **Load** `troubleshoot/INSTRUCTIONS.md`.

## Stopping Points

- After Step 1, action 1: Wait for database/schema/connector names
- After Step 2, action 2: Wait for invitation link from SAP for Me
- After Step 3, action 1: Wait for user to confirm formation setup in SAP for Me

## Output

A zerocopy connector in CONNECTED state, ready for consuming data products or publishing.
