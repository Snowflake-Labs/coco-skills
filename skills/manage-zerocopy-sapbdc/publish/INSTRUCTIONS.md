---
name: sap-bdc-snowflake-publish
description: "Publish a Snowflake database as a data product to SAP BDC via zero-copy connector. Covers share creation, granting access, and calling SYSTEM$SAP_PUBLISH_DATA_PRODUCT."
parent_skill: manage-zerocopy-sapbdc
---

# Publish Data from Snowflake to SAP BDC

## When to Load

Main skill Step 1: User selects "Publish a data product"

## Prerequisites

- A zerocopy connector in CONNECTED state
- SHARE_BACK enabled on the connector
- CREATE SHARE privilege on the account
- Only **Iceberg V3 tables with copy-on-write** can be shared
- Iceberg tables must use `CATALOG = 'SNOWFLAKE'` (Snowflake-managed)
- `STORAGE_SERIALIZATION_POLICY = 'COMPATIBLE'` must be set (can be set at the database, schema, or table level)
- Copy-on-write must be enabled by setting `ENABLE_ICEBERG_MERGE_ON_READ = FALSE` (can be set at the database, schema, or table level)
- Example Iceberg table creation with all required settings:
  ```sql
  CREATE ICEBERG TABLE my_db.my_schema.my_table (
    id INT,
    color STRING
  )
    ICEBERG_VERSION = 3
    CATALOG = 'SNOWFLAKE'
    STORAGE_SERIALIZATION_POLICY = 'COMPATIBLE'
    ENABLE_ICEBERG_MERGE_ON_READ = FALSE;
  ```
- Each data product maps to a single dedicated database
- **CSN Generator playbook** located at `csn-generator/INSTRUCTIONS.md` (sub-routine, used when user needs CSN document generation)

## Workflow

### Step 1: Verify Connector State, Privileges, and Share-Back

**Goal:** Ensure connector is connected, user has required privileges, and share-back is enabled.

**Actions:**

1. **Ask** user for the connector's fully qualified name:
   ```
   Provide the fully qualified connector name (e.g., MY_DB.MY_SCHEMA.MY_SAP_CONNECTOR):
   ```

2. **Execute:**
   ```sql
   DESC ZEROCOPY CONNECTOR <connector_name>;
   ```

3. **Check connector state:**
   - If status is NOT `CONNECTED`: Inform user and **Load** `troubleshoot/INSTRUCTIONS.md`.

4. **Check privileges** required for publishing:
   ```sql
   SHOW GRANTS ON ZEROCOPY CONNECTOR <connector_name>;
   ```
   Verify the current role has:
   - `OPERATE` — required for `SYSTEM$SAP_PUBLISH_DATA_PRODUCT`
   - `MODIFY` — required for `SET SHARE_BACK = TRUE` and `ADD SHARE`
   - `USAGE` — required for `DESC ZEROCOPY CONNECTOR`

   Also check account-level privilege:
   ```sql
   SHOW GRANTS TO ROLE IDENTIFIER(CURRENT_ROLE());
   ```
   Verify the role has `CREATE SHARE` on the account.

   **If any privilege is missing**, inform the user with the exact grant statements needed:
   ```
   ⚠️ Missing privileges detected. The following grants are required for publishing:

   GRANT OPERATE ON ZEROCOPY CONNECTOR <connector_name> TO ROLE <current_role>;
   GRANT MODIFY ON ZEROCOPY CONNECTOR <connector_name> TO ROLE <current_role>;
   GRANT CREATE SHARE ON ACCOUNT TO ROLE <current_role>;

   Please ask an account administrator to run these grants, then retry.
   ```
   **STOP** if privileges are missing — do not proceed until resolved.

5. **Enable SHARE_BACK** if not already enabled:
   ```sql
   ALTER ZEROCOPY CONNECTOR IF EXISTS <connector_name>
     SET SHARE_BACK = TRUE;
   ```

### Step 2: Identify Data to Publish

**Goal:** Determine which database, schemas, and tables to share.

**Actions:**

1. **Ask** user:
   ```
   Which database do you want to publish as a data product to SAP BDC?
   - Database name:
   - Schema(s) to include (or * for all):
   - Specific table(s) to include (or * for all in schema):
   ```

**STOP**: Wait for user input.

2. **Detect FDN vs Iceberg tables explicitly** — DO NOT skip this step. Run BOTH queries and compare results:

   ```sql
   -- Query A: All tables in schema (includes FDN, Iceberg, dynamic, etc.)
   SHOW TABLES IN SCHEMA <db>.<schema>;

   -- Query B: Only Iceberg tables in schema
   SHOW ICEBERG TABLES IN SCHEMA <db>.<schema>;
   ```

   **Decision rules** based on the comparison:
   - **If Query B returns 0 rows** → ALL tables are FDN (regular Snowflake tables). **STOP** the publish flow immediately. Inform the user:
     ```
     ⚠️ Your tables in <db>.<schema> are standard Snowflake (FDN) tables, not Iceberg V3.
     SAP BDC zero-copy publishing requires Iceberg V3 tables with copy-on-write.

     I will guide you through converting them. You have two options:
     A. CTAS — one-time snapshot (creates a new database with static Iceberg copies)
     B. Dynamic Iceberg — auto-syncing copies that refresh from source FDN tables

     Which approach would you like?
     ```
     **STOP**: Wait for user choice, then **Load Step 2A** to guide the conversion.

   - **If Query A returns more rows than Query B** → MIXED case (some FDN, some Iceberg). Identify the FDN ones by set-difference:
     ```sql
     -- Pseudocode: tables_in_A_not_in_B = FDN tables needing conversion
     ```
     Inform the user which tables are FDN and offer Step 2A conversion for those, OR ask which tables to publish (only Iceberg V3 ones can be shared).

   - **If Query A row count == Query B row count** → ALL tables are Iceberg. Proceed to **Step 2B** (verify Iceberg V3 + COW prerequisites). DO NOT skip Step 2B — Iceberg v2 tables also exist and must be excluded.

3. **Verify Iceberg version is V3 + copy-on-write enabled** (only if all tables are Iceberg from step 2 above):

   For each Iceberg table, confirm:
   - `ICEBERG_VERSION = 3` (v2 tables are NOT supported by SAP BDC publishing)
   - `STORAGE_SERIALIZATION_POLICY = 'COMPATIBLE'` set at database, schema, or table level
   - `ENABLE_ICEBERG_MERGE_ON_READ = FALSE` (i.e., copy-on-write enabled)

   Use `DESC ICEBERG TABLE <db>.<schema>.<table>;` to inspect each. If any of these conditions are not met, inform the user and route to Step 2A (which sets these correctly during conversion).

   > 🚧 **Why this multi-query check matters**: `SHOW ICEBERG TABLES` alone does not distinguish FDN from Iceberg-but-wrong-version. Earlier versions of this skill stopped at `SHOW ICEBERG TABLES` and would proceed to publish FDN tables, hitting cryptic copy-on-write errors deep in the flow. The two-query approach above detects FDN explicitly and routes to Step 2A *before* any share-creation work begins. Source: Kevin Poskitt's testing report (May 8, 2026).

4. **Verify primary keys** — ALWAYS check this regardless of whether tables are already Iceberg or need conversion. SAP BDC requires a primary key on every shared table:
   ```sql
   SELECT tc.TABLE_NAME, kcu.COLUMN_NAME
   FROM "<db>".INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
   JOIN "<db>".INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
     ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
     AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
   WHERE tc.TABLE_SCHEMA = '<schema>'
     AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
   ORDER BY tc.TABLE_NAME, kcu.ORDINAL_POSITION;
   ```

   Compare results against the tables selected for publishing. **For any table without a primary key:**
   ```
   ⚠️ Primary key check: The following tables are missing primary key constraints.
   SAP BDC requires a primary key on every shared table.

   Tables without primary keys:
   - <table_1>
   - <table_2>
   ```

   **Ask** user which column(s) should be the primary key for each:
   ```
   For table <table_name>, which column(s) uniquely identify each row?
   (Comma-separate for composite keys, e.g., ORDER_ID,LINE_ITEM_ID)
   ```

   **STOP**: Wait for user input on each missing PK.

   **Add the primary key constraints** the user provided:
   ```sql
   ALTER TABLE <db>.<schema>.<table_name>
     ADD PRIMARY KEY (<column_list>);
   ```

   Note: Snowflake does not enforce primary keys at runtime, but SAP BDC reads
   them from metadata to construct CSN entity keys. Missing PKs cause CSN
   import to fail in SAP Datasphere with "Missing on property" errors.

### Step 2A: Convert FDN Tables to Iceberg V3

**Goal:** Convert existing Snowflake native (FDN) tables to Iceberg V3 tables that meet the publishing prerequisites.

**Actions:**

1. **Ask** user for conversion approach:
   ```
   Your tables are standard Snowflake (FDN) tables and need to be converted to Iceberg V3.
   Which approach would you like?

   A. CTAS (one-time snapshot)
      - Creates a new database with static Iceberg copies of your FDN tables.
      - Original database remains untouched.
      - Best when: source data is stable, or you only need a point-in-time export.

   B. Dynamic Iceberg Tables (automatic sync)
      - Creates dynamic Iceberg tables that automatically refresh from the source FDN tables.
      - Data stays in sync based on a target lag you specify.
      - Best when: source FDN tables are continuously updated and you want the published data product to stay current.
   ```

**STOP**: Wait for user selection.

2. **Ask** user for conversion scope:
   ```
   Which tables should be converted?
   A. All tables in the selected schema(s)
   B. Let me pick specific tables
   ```

**STOP**: Wait for user selection.

3. **List** the tables to convert:
   ```sql
   SHOW TABLES IN SCHEMA <db>.<schema>;
   ```
   If user chose specific tables, present the list and let them select.

#### Option A: CTAS (One-Time Snapshot)

4. **Ask** user for the new database name, then create it:
   ```sql
   CREATE DATABASE <new_db>
     STORAGE_SERIALIZATION_POLICY = 'COMPATIBLE';
   CREATE SCHEMA <new_db>.<schema>;
   ```

5. **Set session default** to prevent accidental Iceberg v2 creation:
   ```sql
   ALTER SESSION SET ICEBERG_VERSION_DEFAULT = 3;
   ```

6. **For each table**, create the Iceberg copy:
   ```sql
   CREATE ICEBERG TABLE <new_db>.<schema>.<table>
     ICEBERG_VERSION = 3
     CATALOG = 'SNOWFLAKE'
     STORAGE_SERIALIZATION_POLICY = 'COMPATIBLE'
     ENABLE_ICEBERG_MERGE_ON_READ = FALSE
     AS SELECT * FROM <db>.<schema>.<table>;
   ```
   **CRITICAL**: The `ICEBERG_VERSION = 3` parameter is mandatory. Never omit it — account defaults may create v2 tables which are incompatible with SAP BDC publishing.

7. **Verify** — proceed to Step 2B.

#### Option B: Dynamic Iceberg Tables (Automatic Sync)

4. **Ask** user for the new database name and target lag:
   ```
   - New database name for the Iceberg tables:
   - Target lag (how fresh should the data be?): e.g., '20 minutes', '1 hour', '1 day'
   - Warehouse to use for refreshes:
   ```

5. **Create** the database and schema:
   ```sql
   CREATE DATABASE <new_db>
     STORAGE_SERIALIZATION_POLICY = 'COMPATIBLE';
   CREATE SCHEMA <new_db>.<schema>;
   ```

6. **Set session default** to prevent accidental Iceberg v2 creation:
   ```sql
   ALTER SESSION SET ICEBERG_VERSION_DEFAULT = 3;
   ```

7. **For each table**, create a dynamic Iceberg table:
   ```sql
   CREATE DYNAMIC ICEBERG TABLE <new_db>.<schema>.<table>
     TARGET_LAG = '<target_lag>'
     WAREHOUSE = <warehouse>
     CATALOG = 'SNOWFLAKE'
     STORAGE_SERIALIZATION_POLICY = 'COMPATIBLE'
     ENABLE_ICEBERG_MERGE_ON_READ = FALSE
     AS SELECT * FROM <db>.<schema>.<table>;
   ```
   **CRITICAL**: The `ICEBERG_VERSION = 3` parameter is not available on dynamic Iceberg tables — setting the session default above ensures v3 is used. If the session default cannot be set, verify the account-level default with `SHOW PARAMETERS LIKE 'ICEBERG_VERSION_DEFAULT' IN ACCOUNT;`.

8. **Verify** — proceed to Step 2B.

### Step 2B: Verify Iceberg Table Prerequisites

**Goal:** Confirm all converted tables meet the SAP BDC publishing prerequisites.

**Actions:**

1. **List** the Iceberg tables:
   ```sql
   SHOW ICEBERG TABLES IN SCHEMA <new_db>.<schema>;
   ```
   Confirm all target tables are now Iceberg V3.

2. **For each table**, verify settings:
   ```sql
   SHOW PARAMETERS LIKE 'ICEBERG_VERSION_DEFAULT' IN TABLE <new_db>.<schema>.<table>;
   SHOW PARAMETERS LIKE 'STORAGE_SERIALIZATION_POLICY' IN TABLE <new_db>.<schema>.<table>;
   SHOW PARAMETERS LIKE 'ENABLE_ICEBERG_MERGE_ON_READ' IN TABLE <new_db>.<schema>.<table>;
   ```
   - `ICEBERG_VERSION_DEFAULT` must be `3`
   - `STORAGE_SERIALIZATION_POLICY` must be `COMPATIBLE`
   - `ENABLE_ICEBERG_MERGE_ON_READ` must be `FALSE` (copy-on-write)
   - `CATALOG` must be `SNOWFLAKE` (visible in `SHOW ICEBERG TABLES` output)

   **If ICEBERG_VERSION is 2 (not 3):** The table must be recreated — there is no ALTER to upgrade from v2 to v3. Drop the v2 table and re-run the CTAS with `ICEBERG_VERSION = 3` (ensure `ALTER SESSION SET ICEBERG_VERSION_DEFAULT = 3` is set first):
   ```sql
   DROP ICEBERG TABLE <new_db>.<schema>.<table>;
   CREATE ICEBERG TABLE <new_db>.<schema>.<table>
     ICEBERG_VERSION = 3
     CATALOG = 'SNOWFLAKE'
     STORAGE_SERIALIZATION_POLICY = 'COMPATIBLE'
     ENABLE_ICEBERG_MERGE_ON_READ = FALSE
     AS SELECT * FROM <source_db>.<schema>.<table>;
   ```

   If other settings are incorrect, fix them:
   ```sql
   ALTER ICEBERG TABLE <new_db>.<schema>.<table>
     SET STORAGE_SERIALIZATION_POLICY = 'COMPATIBLE';

   ALTER ICEBERG TABLE <new_db>.<schema>.<table>
     SET ENABLE_ICEBERG_MERGE_ON_READ = FALSE;
   ```

### Step 3: Generate CSN Document

**Goal:** Generate a minimal CSN Interop v1.0 JSON document (SDK-compatible) for SAP BDC.

**Actions:**

1. **Load** `csn-generator/INSTRUCTIONS.md` and run through its workflow:
   - Use the database, schema, and tables already identified in Step 2 of this skill as inputs
   - The CSN Generator will generate **minimal** CSN Interop v1.0 matching SAP BDC Connect SDK output format
   - NO user choices (no modes, no options) - generates minimal CSN only
   - NO semantic annotations (except FK associations), NO i18n, NO entity classification
   - NO mandatory reviews (no association review, no PII review)
   - Fast generation (<1 sec), small file size (~400 bytes per entity)
   - Once the CSN Generator delivers the output file, read the generated CSN JSON and store its contents for use in Step 5

### Step 4: Create and Configure the Share

**Goal:** Create a Snowflake share and grant access to the selected objects.

**Actions:**

1. **Ask** user for a share name (default to `SAP_<DB>_SHARE` based on the database name from Step 2):
   ```
   Suggested share name: SAP_<DB>_SHARE
   You can change this if you'd like. Provide the share name to use:
   ```

2. **Execute:**
   ```sql
   CREATE SHARE IF NOT EXISTS <share_name>;
   GRANT USAGE ON DATABASE <db> TO SHARE <share_name>;
   GRANT USAGE ON SCHEMA <db>.<schema> TO SHARE <share_name>;
   ```

3. **For each table** to share:
   ```sql
   GRANT SELECT ON TABLE <db>.<schema>.<table> TO SHARE <share_name>;
   ```

4. **Associate** the share with the connector:
   ```sql
   ALTER ZEROCOPY CONNECTOR <connector_name>
     ADD SHARE <share_name>;
   ```

### Step 5: Publish the Data Product

**Goal:** Make the data product discoverable in SAP BDC.

**Actions:**

1. **Generate** default metadata by inspecting the shared data:

   **Step 5.1a: Gather metadata**
   ```sql
   SHOW TABLES IN DATABASE <db>;
   ```
   Identify the table names and group them by theme (e.g., master data vs country-specific vs lookup/text tables).

   Pick the 2-3 most significant tables (largest or most central based on naming) and describe their columns:
   ```sql
   DESC TABLE <db>.<schema>.<table_1>;
   DESC TABLE <db>.<schema>.<table_2>;
   ```

   Sample data from those tables to detect the actual domain, coverage, and terminology:
   ```sql
   SELECT * FROM <db>.<schema>.<table_1> LIMIT 3;
   ```

   If date columns exist (e.g., `STARTDATE`, `CREATIONDATE`), detect the date range:
   ```sql
   SELECT MIN(<date_col>), MAX(<date_col>) FROM <db>.<schema>.<table>;
   ```

   **Step 5.1b: Generate Title**
   Derive from the dominant entity pattern in the table names — not from the database name.
   - If table names share a common domain prefix or theme (e.g., `person`, `personaldetail`, `jobdetail*`), use that domain as the title theme.
   - Format: `<Domain> <Entity Type> Data` — keep under 60 characters.
   - Examples:
     - Tables `person`, `personaldetail`, `jobdetailbra`... → "Workforce Person Data"
     - Tables `education`, `outsideworkexperience`, `candidateskill`... → "Recruiting Candidate Data"
     - Tables `PRODUCT`, `PRODUCTPLANT`, `PRODUCTDESCRIPTION`... → "Product Master Data"

   **Step 5.1c: Generate Short Description** (1-2 sentences)
   Compose from the key business attributes found in column names of the top tables. Describe what the data is and its key attributes. Do NOT include row counts.
   - Examples:
     - "Workforce person master data including personal details, demographics, nationality, and country-specific job assignments covering 16 countries."
     - "Recruiting candidate data including education history, work experience, skills, marketing consent, and referral source information."

   **Step 5.1d: Generate Full Description** (paragraph)
   Compose from schema inspection and sample data. Include:
   - What the data product provides and its source system (if detectable)
   - Core entities and their key business attributes (from column names)
   - Table groupings by theme (e.g., "Core entities include... Country-specific tables provide...")
   - Geographic or domain coverage (from sample data, e.g., country codes found)
   - Date range of the data (if date columns exist)
   - Do NOT include row counts
   - Examples:
     - "This data product provides workforce person data from SAP SuccessFactors. Core entities include person identifiers and personal details such as first name, last name, gender, nationality, marital status, and preferred language. Country-specific job detail tables provide localized employment information for 16 countries: BRA, CHE, CHL, CHN, DEU, DNK, EGY, GTM, ITA, NZL, PER, RUS, SGP, TUR, VEN, and ZAF. Employment records span from 2002 to present."
     - "This data product provides recruiting candidate data from SAP SuccessFactors. It includes candidate education history with school names and degree timelines, inside and outside work experience, skills and skill profiles, marketing consent records, partner source tracking, profile sharing preferences, and candidate tags. Education records span from 1993 to 2013."

   **Step 5.1e: Present to user**
   ```
   Suggested metadata for the SAP BDC data product (you can edit any of these before publishing):

   Title: <suggested_title>
   Short description: <suggested_short_description>
   Full description: <suggested_full_description>
   ```

**STOP**: Wait for user to confirm or modify metadata.

2. **Execute** the publish call:
   ```sql
   SELECT SYSTEM$SAP_PUBLISH_DATA_PRODUCT(
     '<connector_name>',
     '<share_name>',
     '<ord_metadata_json>',
     '<csn_document_json>'
   );
   ```

   Where `ord_metadata_json` follows this structure:
   ```json
   {
     "title": "<title>",
     "shortDescription": "<short_description>",
     "description": "<full_description>"
   }
   ```

   And `csn_document_json` is the CSN content obtained in Step 3.

3. **Confirm** success and present result to user.

### Step 6: Verify Publication

**Actions:**

1. **Verify** the share is associated:
   ```sql
   DESC ZEROCOPY CONNECTOR <connector_name>;
   ```

2. **Present** confirmation:
   ```
   Data product published successfully!
   - Share: <share_name>
   - Connector: <connector_name>
   - Title: <title>
   The data product should now be discoverable from SAP BDC.
   ```

## Revoking a Published Data Product

If the user needs to revoke:

```sql
ALTER ZEROCOPY CONNECTOR <connector_name>
  REMOVE SHARE <share_name>;

REVOKE SELECT ON TABLE <db>.<schema>.<table> FROM SHARE <share_name>;
REVOKE USAGE ON SCHEMA <db>.<schema> FROM SHARE <share_name>;
REVOKE USAGE ON DATABASE <db> FROM SHARE <share_name>;
```

## Stopping Points

- After Step 2, action 1: Wait for user to identify tables
- After Step 3: Wait for CSN document path, or generate a minimal CSN via `csn-generator/INSTRUCTIONS.md` (no modes/reviews)
- After Step 5, action 1: Wait for metadata confirmation

## Output

A Snowflake database published as a data product in SAP BDC, discoverable and accessible from the SAP side.
