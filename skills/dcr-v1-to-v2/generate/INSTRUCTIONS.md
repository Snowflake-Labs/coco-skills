
# Phase 3 — V2 Script Generation

## When to Use

Load this sub-skill when executing **Step 3 — Script Generation** of the `dcr-v1-to-v2`
migration skill. Use it to:
- Generate `v2_provider_setup.sql`, `v2_consumer_setup.sql`, and `v2_cleanup.sql` from a
  confirmed V1→V2 mapping
- Convert V1 JinjaSQL templates (with `join_policy` filter) to V2 Collaboration API format
- Produce all output as local files — never execute DDL/DML

## Prerequisites

- Discovery report completed and confirmed (Phase 1)
- Mapping report completed and confirmed (Phase 2)
- User has confirmed the output directory
- Output directory determined dynamically: `date +%Y%m%d` → format `YYYYMMDD_V1_to_V2_Output`

## Input

From the mapping report:
- Provider account (org.account) and DB name
- Consumer account (org.account) and DB name
- Linked objects per DB, with join columns
- Converted V2 templates (or stubs for complex logic)
- Collaboration name (suggest `DCR_V2_COLLABORATION` or match V1 name)
- Collaborator aliases for the YAML spec (suggest `owner_account` / `collab_account`)
- Version string for offerings/templates (suggest `YYYYMMDD_V1` where YYYYMMDD is today's date)
- Warehouse name


## Workflow

### Step 1 — Generate Provider Setup Script

Write `v2_provider_setup.sql` with the following structure.

#### Header

```sql
-- =============================================================================
-- DCR V2 Provider Setup (Migrated from V1 — Collaboration API)
-- Account  : <provider_account>
-- Source   : Migrated from V1 clean room <v1_cleanroom_name>
-- Generated: <date>
-- =============================================================================
--
-- PREREQUISITES — Complete both steps below before running this file.
--
-- Step A: Install "Snowflake Data Clean Rooms" from Marketplace (do once per account)
-- Step B: Run Quick Start (rename + mount — do once per account)
--
--   USE ROLE ACCOUNTADMIN;
--   ALTER APPLICATION IF EXISTS Snowflake_Data_Clean_Rooms RENAME TO SAMOOHA_BY_SNOWFLAKE;
--   CALL SAMOOHA_BY_SNOWFLAKE.APP_SCHEMA.PREPARE_MOUNT_SCRIPT();
--   EXECUTE IMMEDIATE FROM @SAMOOHA_BY_SNOWFLAKE.APP_SCHEMA.MOUNT_CODE_STAGE/dcr_loader.sql;
--   USE ROLE SAMOOHA_APP_ROLE;
--   CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.LIBRARY.CHECK_MOUNT_STATUS();  -- must return TRUE
--
-- NOTE: All Collaboration API calls require USE SECONDARY ROLES NONE in the session.
--
-- =============================================================================

SET collab_name    = '<collaboration_name>';  -- NOTE: DCR V2 names cannot contain spaces — use underscores
SET owner_alias    = 'owner_account';          -- provider alias in YAML spec
SET collab_alias   = 'collab_account';         -- consumer alias in YAML spec
SET collab_account = '<consumer_org.account>';
SET provider_db    = '<provider_db>';
```

#### Provider Step 1 — Warehouse

```sql
-- =============================================================================
-- STEP 1: Warehouse Setup
-- Run as: ACCOUNTADMIN
-- =============================================================================

USE ROLE ACCOUNTADMIN;

CREATE WAREHOUSE IF NOT EXISTS <warehouse>
    WAREHOUSE_SIZE = XSMALL
    AUTO_SUSPEND   = 60
    AUTO_RESUME    = TRUE
    COMMENT        = 'Warehouse for DCR V2';

GRANT USAGE ON WAREHOUSE <warehouse> TO ROLE SAMOOHA_APP_ROLE;
```

#### Provider Step 2 — Data Objects and SAMOOHA_APP_ROLE Grants

Include this block in full — it replaces V1's `library.register_schema`:

```sql
-- =============================================================================
-- STEP 2: Data Objects and SAMOOHA_APP_ROLE Grants
-- Run as: ACCOUNTADMIN
-- Data stays in the provider DB — no copies needed.
-- V1's library.register_schema is replaced by explicit grants below.
-- REFERENCE_USAGE WITH GRANT OPTION is required so the SAMOOHA backend can
-- share data object references cross-account for the collaboration data layer.
-- =============================================================================

USE ROLE ACCOUNTADMIN;

-- [If provider data objects need to be created or secure views need to be added,
--  include CREATE TABLE / CREATE SECURE VIEW statements here.]
-- If V1 already has the correct objects in the provider DB, skip creation.

-- Grant SAMOOHA_APP_ROLE access to provider data (must be done before INITIALIZE).
GRANT USAGE           ON DATABASE <provider_db>                       TO ROLE SAMOOHA_APP_ROLE;
GRANT USAGE           ON SCHEMA   <provider_db>.<schema>              TO ROLE SAMOOHA_APP_ROLE;
GRANT SELECT          ON ALL TABLES IN SCHEMA <provider_db>.<schema>  TO ROLE SAMOOHA_APP_ROLE;
GRANT SELECT          ON ALL VIEWS  IN SCHEMA <provider_db>.<schema>  TO ROLE SAMOOHA_APP_ROLE;
GRANT REFERENCES      ON ALL VIEWS  IN SCHEMA <provider_db>.<schema>  TO ROLE SAMOOHA_APP_ROLE;
GRANT REFERENCE_USAGE ON DATABASE <provider_db>                       TO ROLE SAMOOHA_APP_ROLE WITH GRANT OPTION;
```

#### Provider Step 3 — Register Data Offerings

For each V1 linked dataset, generate a `REGISTER_DATA_OFFERING` call:

```sql
-- =============================================================================
-- STEP 3: Register Data Offerings
-- Run as: SAMOOHA_APP_ROLE
-- Version format: ^[A-Za-z0-9_]{1,20}$ — no dots; use underscores (e.g., YYYYMMDD_V1).
-- Data offering ID = name_version (e.g., provider_customer_spend_v_YYYYMMDD_V1).
-- =============================================================================

USE ROLE      SAMOOHA_APP_ROLE;
USE WAREHOUSE <warehouse>;
USE SECONDARY ROLES NONE;

-- Offering: <dataset_name>
-- V1 equivalent: link_datasets(['<provider_db>.<schema>.<object>'])
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.REGISTRY.REGISTER_DATA_OFFERING($$
api_version: 2.0.0
spec_type: data_offering
name: <offering_name>             -- snake_case, e.g., provider_customer_spend_v
version: <YYYYMMDD_V1>
description: <description>
datasets:
  - alias: <dataset_alias>        -- short name used in 3-part RUN ID (ALIAS.OFFERING_ID.ALIAS)
    data_object_fqn: <db>.<schema>.<object>
    allowed_analyses: template_only
    schema_and_template_policies:
      <JOIN_COL>:
        category: passthrough     -- for hashed/synthetic join keys
      <DATE_COL>:
        category: timestamp       -- for date/time columns
      <OTHER_COL>:
        category: passthrough
$$);
```

Repeat for each linked dataset. After all offerings:

```sql
-- Verify all offerings registered
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.REGISTRY.VIEW_REGISTERED_DATA_OFFERINGS();
```

#### Provider Step 4 — Register Templates

For each V1 template, apply the conversion rules (see section below), then generate:

```sql
-- =============================================================================
-- STEP 4: Register Analysis Templates
-- Run as: SAMOOHA_APP_ROLE
-- Template ID = name_version (e.g., overlap_analysis_YYYYMMDD_V1).
-- Cannot re-register an existing version — use a bumped version string to update.
-- =============================================================================

USE ROLE SAMOOHA_APP_ROLE;
USE WAREHOUSE <warehouse>;
USE SECONDARY ROLES NONE;

-- Template: <template_name>
-- V1 equivalent: provider.add_custom_sql_template($cleanroom_name, '<template_name>', $$...$$)
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.REGISTRY.REGISTER_TEMPLATE($$
api_version: 2.0.0
spec_type: template
name: <template_name>
version: <YYYYMMDD_V1>
type: sql_analysis
description: <description>
template: |
    <converted JinjaSQL body — see Template Conversion Reference>
$$);
```

For stub templates that cannot be auto-converted:

```sql
-- WARNING: This template requires manual porting from V1.
-- Original V1 template: <template_name>
-- TODO: Replace the placeholder with the actual SQL body.
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.REGISTRY.REGISTER_TEMPLATE($$
api_version: 2.0.0
spec_type: template
name: <template_name>
version: <YYYYMMDD_V1>
type: sql_analysis
description: <description> — STUB: requires manual port
template: |
    -- TODO: Port from V1 template <template_name>
    -- Original logic:
    --   <brief description of what the V1 template does>
    -- Table references:
    --   Provider: IDENTIFIER({{ source_table[0] }}) p
    --   Consumer: IDENTIFIER({{ my_table[0] }}) c
    --   Join:     ON p.<join_col> = c.<join_col>
    SELECT 1 AS placeholder
$$);
```

After all templates:

```sql
-- Verify all templates registered
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.REGISTRY.VIEW_REGISTERED_TEMPLATES();
```

#### Provider Step 5 — Initialize Collaboration

```sql
-- =============================================================================
-- STEP 5: Initialize Collaboration
-- Run as: SAMOOHA_APP_ROLE
-- The YAML spec declares all collaborators, data offerings, and templates.
-- collaborator_identifier_aliases: alias → org.account
-- analysis_runners: which account can run analyses, from which providers, using which templates
-- Collaborator list is FIXED after INITIALIZE — cannot add or remove later.
-- =============================================================================

USE ROLE      SAMOOHA_APP_ROLE;
USE WAREHOUSE <warehouse>;
USE SECONDARY ROLES NONE;

CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.INITIALIZE($$
api_version: 2.0.0
spec_type: collaboration
name: <collaboration_name>
owner: owner_account
collaborator_identifier_aliases:
    owner_account: <provider_org.account>
    collab_account: <consumer_org.account>
analysis_runners:
    collab_account:
        data_providers:
            owner_account:
                data_offerings:
                    - id: <offering_id_1>    -- name_version, e.g., provider_customer_spend_v_YYYYMMDD_V1
                    - id: <offering_id_N>
        templates:
            - id: <template_id_1>            -- name_version, e.g., overlap_analysis_YYYYMMDD_V1
            - id: <template_id_N>
$$, '<warehouse>');

-- Verify collaboration was created
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.VIEW_COLLABORATIONS();

-- Provider joins as owner (INITIALIZE schedules auto-join; explicit call ensures completion)
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.JOIN($collab_name);
-- DCR joins can be slow — verify when ready:
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.GET_STATUS($collab_name);
-- Expected: JOINED for owner_account
```

#### Provider Step 6 — Validation

```sql
-- =============================================================================
-- STEP 6: Validation
-- Run as: SAMOOHA_APP_ROLE
-- =============================================================================

USE ROLE      SAMOOHA_APP_ROLE;
USE WAREHOUSE <warehouse>;
USE SECONDARY ROLES NONE;

CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.GET_STATUS($collab_name);
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.VIEW_UPDATE_REQUESTS($collab_name);
-- NOTE: VIEW_REGISTERED_DATA_OFFERINGS / VIEW_REGISTERED_TEMPLATES return empty after
-- COLLABORATION.INITIALIZE. COLLABORATION.REVIEW also fails post-JOIN ("already reviewed").
-- Use VIEW_COLLABORATIONS to inspect the full spec after joining.
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.VIEW_COLLABORATIONS();

-- =============================================================================
-- STOP: Wait for consumer to complete Steps 1-5 of v2_consumer_setup.sql.
-- =============================================================================
```


### Step 2 — Generate Consumer Setup Script

Write `v2_consumer_setup.sql` with the following structure.

#### Header

```sql
-- =============================================================================
-- DCR V2 Consumer Setup (Migrated from V1 — Collaboration API)
-- Account  : <consumer_account>
-- Source   : Migrated from V1 — previously installed <v1_cleanroom_name>
-- Generated: <date>
-- =============================================================================
-- NOTE: Run after provider completes v2_provider_setup.sql (Step 5 complete).
--       USE SECONDARY ROLES NONE required before all Collaboration API calls.
--
-- NOTE: This setup only adds a new V2 collaboration. Existing V2 collaborations
--       on this account are not affected.
-- =============================================================================

SET collab_name  = '<collaboration_name>';  -- NOTE: Must match provider exactly; DCR V2 names cannot contain spaces
SET consumer_db  = '<consumer_db>';         -- TODO: replace with consumer database name (new or existing — see Step 2)
SET consumer_wh  = '<warehouse>';           -- TODO: replace with warehouse name on this account
                                            --       (if creating a new one, uncomment Step 1 below and match this name)
```

#### Consumer Step 1 — Warehouse

```sql
-- =============================================================================
-- STEP 1: Warehouse Setup (OPTIONAL — skip if using an existing warehouse)
-- Run as: ACCOUNTADMIN
--
-- If you already have a warehouse, set consumer_wh above to its name and skip
-- this step. If you need a new warehouse, update consumer_wh above to match
-- the name below, then uncomment and run this block.
-- =============================================================================

-- USE ROLE ACCOUNTADMIN;

-- CREATE WAREHOUSE IF NOT EXISTS <warehouse>
--     WAREHOUSE_SIZE = XSMALL
--     AUTO_SUSPEND   = 60
--     AUTO_RESUME    = TRUE
--     COMMENT        = 'Warehouse for DCR V2';

-- GRANT USAGE ON WAREHOUSE <warehouse> TO ROLE SAMOOHA_APP_ROLE;
```

#### Consumer Step 2 — Data and SAMOOHA_APP_ROLE Grants

```sql
-- =============================================================================
-- STEP 2: Consumer Data and SAMOOHA_APP_ROLE Grants
-- Run as: ACCOUNTADMIN
-- If reusing the existing V1 consumer DB, skip CREATE/INSERT — only add grants.
-- REFERENCE_USAGE WITH GRANT OPTION required before COLLABORATION.JOIN.
-- =============================================================================

USE ROLE ACCOUNTADMIN;

-- [Include CREATE DATABASE / CREATE TABLE / INSERT if V2 uses new consumer data]
-- [If reusing V1 consumer DB, skip to the grants block below]

-- Grant SAMOOHA_APP_ROLE access to consumer data (must be done before JOIN)
GRANT USAGE           ON DATABASE IDENTIFIER($consumer_db)                       TO ROLE SAMOOHA_APP_ROLE;
GRANT USAGE           ON ALL SCHEMAS IN DATABASE IDENTIFIER($consumer_db)        TO ROLE SAMOOHA_APP_ROLE;
GRANT SELECT          ON ALL TABLES IN DATABASE IDENTIFIER($consumer_db)         TO ROLE SAMOOHA_APP_ROLE;
GRANT REFERENCE_USAGE ON DATABASE IDENTIFIER($consumer_db)                       TO ROLE SAMOOHA_APP_ROLE WITH GRANT OPTION;
```

#### Consumer Step 3 — Review and Join

```sql
-- =============================================================================
-- STEP 3: Review and Join Collaboration
-- Run as: SAMOOHA_APP_ROLE
-- =============================================================================

USE ROLE      SAMOOHA_APP_ROLE;
USE WAREHOUSE IDENTIFIER($consumer_wh);
USE SECONDARY ROLES NONE;

-- Optional: check what collaborations are available
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.VIEW_COLLABORATIONS();

-- Review the collaboration spec before joining
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.REVIEW($collab_name, '<provider_org.account>');

-- Join the collaboration as the analysis runner
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.JOIN($collab_name);

-- DCR joins can be slow — verify when ready:
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.GET_STATUS($collab_name);
-- Expected: JOINED for collab_account
```

#### Consumer Step 4 — Register Consumer Data Offering and Link

```sql
-- =============================================================================
-- STEP 4: Register Consumer Data Offering and Link
-- Run as: SAMOOHA_APP_ROLE
-- V1 equivalent: consumer.link_datasets + consumer.set_join_policy
-- IMPORTANT: LINK_LOCAL_DATA_OFFERING must be called before the first RUN.
--
-- NOTE: REGISTER_DATA_OFFERING registers the consumer's OWN data with the SAMOOHA
-- registry so the collaboration analysis engine can access it during RUN calls.
-- This does NOT expose consumer data to the provider — the provider can only
-- access results produced by registered templates, not the raw consumer table.
-- =============================================================================

USE ROLE      SAMOOHA_APP_ROLE;
USE WAREHOUSE IDENTIFIER($consumer_wh);
USE SECONDARY ROLES NONE;

CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.REGISTRY.REGISTER_DATA_OFFERING($$
api_version: 2.0.0
spec_type: data_offering
name: <consumer_offering_name>    -- e.g., consumer_customers
version: <YYYYMMDD_V1>
description: <description>
datasets:
  - alias: <dataset_alias>        -- e.g., customers — used in 3-part RUN ID
    data_object_fqn: <consumer_db>.<schema>.<table>
    allowed_analyses: template_only
    schema_and_template_policies:
      <JOIN_COL>:
        category: passthrough
      <OTHER_COL>:
        category: passthrough
$$);

CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.REGISTRY.VIEW_REGISTERED_DATA_OFFERINGS();

-- Link consumer's data offering to the collaboration.
-- Creates data access views in the collaboration-local DB used by the analysis engine.
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.LINK_LOCAL_DATA_OFFERING(
    $collab_name,
    '<consumer_offering_id>'    -- e.g., consumer_customers_YYYYMMDD_V1
);
```

#### Consumer Step 5 — Run Analyses

```sql
-- =============================================================================
-- STEP 5: Run Analyses
-- Run as: SAMOOHA_APP_ROLE
-- ID format: COLLABORATOR_ALIAS.DATA_OFFERING_ID.DATASET_ALIAS
-- IMPORTANT: Arg order is (collab, template_id, source_tables[], my_tables[], args)
--   source_tables = provider offerings (arg 3)   → populates source_table[] in template
--   my_tables     = consumer offerings (arg 4)   → populates my_table[] in template
-- NOTE: This is REVERSED from V1 run_analysis(cleanroom, template, [consumer], [provider], args)
-- =============================================================================

USE ROLE      SAMOOHA_APP_ROLE;
USE WAREHOUSE IDENTIFIER($consumer_wh);
USE SECONDARY ROLES NONE;
```

For each template, generate the `COLLABORATION.RUN` call with the comment showing the V1 equivalent:

```sql
-- Template: <template_name>
-- V1 equivalent:
--   CALL samooha_by_snowflake_local_db.consumer.run_analysis(
--       $cleanroom_name, '<v1_template_name>',
--       ['<consumer_table_fqn>'],    -- consumer was arg 3 in V1
--       ['<provider_table_fqn>'],    -- provider was arg 4 in V1
--       object_construct('provider_id', 'p.<col>', 'consumer_id', 'c.<col>', ...)
--   );
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.RUN(
    $collab_name,
    '<template_id>',                                                -- name_version
    ['<owner_alias>.<provider_offering_id>.<provider_dataset_alias>'],  -- provider first (arg 3)
    ['<collab_alias>.<consumer_offering_id>.<consumer_dataset_alias>'], -- consumer second (arg 4)
    OBJECT_CONSTRUCT(<param_name>, <value>, ...)   -- no provider_id/consumer_id needed
);
```

#### Consumer Step 6 — Validation

```sql
-- =============================================================================
-- STEP 6: Validation
-- Run as: SAMOOHA_APP_ROLE
-- =============================================================================

USE ROLE      SAMOOHA_APP_ROLE;
USE WAREHOUSE IDENTIFIER($consumer_wh);
USE SECONDARY ROLES NONE;

CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.GET_STATUS($collab_name);
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.VIEW_UPDATE_REQUESTS($collab_name);
```


### Step 3 — Generate Cleanup Script

Write `v2_cleanup.sql`:

```sql
-- =============================================================================
-- DCR V2 Cleanup — Collaboration API
-- Run to decommission the V2 collaboration from both accounts.
-- =============================================================================
-- LEAVE and TEARDOWN are two-call async operations:
--   1st call → status becomes LEAVING (consumer) or DROPPING (provider)
--   Wait ~60 seconds
--   Check GET_STATUS → status becomes LOCAL_DROP_PENDING
--   2nd call → completes cleanup (status: LEFT / DROPPED)
-- Consumer must LEAVE before provider runs TEARDOWN.
-- =============================================================================

-- === CONSUMER ACCOUNT ===
-- Run on: <consumer_account>

USE ROLE SAMOOHA_APP_ROLE;
USE SECONDARY ROLES NONE;

-- First LEAVE call (async — moves to LEAVING)
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.LEAVE('<collaboration_name>');

-- Verify status: expect LEAVING
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.GET_STATUS('<collaboration_name>');

-- Wait ~60 seconds, then check again.
-- When status = LOCAL_DROP_PENDING, run the second LEAVE call:
-- CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.LEAVE('<collaboration_name>');

USE ROLE ACCOUNTADMIN;
DROP DATABASE IF EXISTS <consumer_db>;  -- Only if created for V2 (not V1 reuse)


-- === PROVIDER ACCOUNT ===
-- Run on: <provider_account>
-- Run only after consumer has fully LEFT.

USE ROLE SAMOOHA_APP_ROLE;
USE SECONDARY ROLES NONE;

-- First TEARDOWN call (async — moves to DROPPING)
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.TEARDOWN('<collaboration_name>');

-- Verify status: expect DROPPING
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.GET_STATUS('<collaboration_name>');

-- Wait ~60 seconds, then check again.
-- When status = LOCAL_DROP_PENDING, run the second TEARDOWN call:
-- CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.TEARDOWN('<collaboration_name>');

-- NOTE: Do NOT drop the provider DB — it contains the original data used by V1 and V2.
-- Registry entries (data offerings and templates) are automatically cleaned up
-- when the SAMOOHA app is uninstalled. Do not try to delete them individually —
-- no DELETE_DATA_OFFERING or DELETE_TEMPLATE procedures exist in this version.
```


### Step 4 — Write Files

Get today's date and confirm the output directory:

```bash
date +%Y%m%d
```

> "I will generate the following files in `<YYYYMMDD>_V1_to_V2_Output/`:
> - `v2_provider_setup.sql` (Steps 1–6 + STOP)
> - `v2_consumer_setup.sql` (Steps 1–6)
> - `v2_cleanup.sql` (LEAVE/TEARDOWN)
> - `mapping_report.md` (V1→V2 mapping decisions table)
>
> Shall I proceed?"

After writing, list the files:

```
Files generated:
  <output_dir>/v2_provider_setup.sql    — <N> lines
  <output_dir>/v2_consumer_setup.sql    — <N> lines
  <output_dir>/v2_cleanup.sql           — <N> lines
  <output_dir>/mapping_report.md        — <N> lines
```

Also write `mapping_report.md` capturing all V1→V2 decisions from Phase 2 as a table.


## Template Conversion Reference

### V1 → V2 JinjaSQL Conversion Rules

| V1 Pattern | V2 Replacement | Notes |
|---|---|---|
| `IDENTIFIER({{ source_table[0] }}) p` | `IDENTIFIER({{ source_table[0] }}) p` | Unchanged |
| `IDENTIFIER({{ my_table[0] }}) c` | `IDENTIFIER({{ my_table[0] }}) c` | Unchanged |
| `IDENTIFIER({{ source_table[N] }})` | `IDENTIFIER({{ source_table[N] }})` | Unchanged for N ≥ 1 |
| `ON IDENTIFIER({{ provider_id \| join_policy }}) = IDENTIFIER({{ consumer_id \| join_policy }})` | `ON p.<join_col> = c.<join_col>` | Hardcode the join column from `view_join_policy` |
| `WHERE p.COL = {{ param }}` | `WHERE p.COL = {{ param }}` | Unchanged — engine auto-quotes strings |
| `'{{ param }}'` | `{{ param }}` | Remove outer quotes — they cause double-quoting in V2 |
| `{{ param \| sqlsafe }}` | `{{ param }}` | Remove `sqlsafe` — incompatible with V2 Snowpark binding |
| `GROUP BY`, `HAVING`, `ORDER BY` | Keep as-is | Aggregation logic unchanged |
| `DIV0`, `SHA2`, `COUNT DISTINCT` | Keep as-is | Snowflake functions unchanged |

### Critical Conversion Notes

1. **Remove the `join_policy` filter.** V1 templates use `{{ provider_id | join_policy }}` and
   `{{ consumer_id | join_policy }}` to enforce which column the consumer must join on. V2 has
   no `join_policy` filter. Replace the entire `IDENTIFIER({{ ... | join_policy }}) = IDENTIFIER({{ ... | join_policy }})`
   expression with the actual column name discovered in `view_join_policy`:
   ```sql
   -- V1: join_policy filter (runtime-enforced column lookup)
   ON IDENTIFIER({{ provider_id | join_policy }}) = IDENTIFIER({{ consumer_id | join_policy }})

   -- V2: hardcoded column (discovered from view_join_policy)
   ON p.EMAIL_HASH = c.EMAIL_HASH
   ```

2. **Remove `provider_id` and `consumer_id` from RUN args.** V1 callers passed
   `'provider_id', 'p.EMAIL_HASH', 'consumer_id', 'c.EMAIL_HASH'` in `object_construct`.
   V2 `COLLABORATION.RUN` does not use these — omit them entirely.

3. **Bare `{{ param }}` for string filters.** Both V1 and V2 use bare `{{ param }}`. The V2 Jinja
   engine auto-quotes string values during substitution. Do **not** add outer quotes
   (`'{{ param }}'` causes double-quoting `''value''`) or use `| sqlsafe` (breaks Snowpark binding).

4. **Arg 3/4 are reversed between V1 and V2.** This is the most error-prone migration step:
   - V1 `run_analysis`: arg 3 = consumer tables → `my_table[]`, arg 4 = provider tables → `source_table[]`
   - V2 `COLLABORATION.RUN`: arg 3 = provider source_tables, arg 4 = consumer my_tables

5. **Template ID format.** V1 template name is plain (`overlap_analysis`). V2 template ID is
   `name_version` (`overlap_analysis_YYYYMMDD_V1`), returned by `REGISTER_TEMPLATE`.

6. **3-part RUN ID format.** V2 `source_tables` and `my_tables` args use:
   `COLLABORATOR_ALIAS.DATA_OFFERING_ID.DATASET_ALIAS`
   Example: `'owner_account.provider_customer_spend_v_YYYYMMDD_V1.customer_spend_v'`

### Example: overlap_analysis V1 → V2

**V1 template body (`add_custom_sql_template`):**
```sql
SELECT
    p.SEGMENT, p.AGE_GROUP, p.REGION,
    COUNT(DISTINCT p.EMAIL_HASH) AS OVERLAP_COUNT
FROM IDENTIFIER({{ source_table[0] }}) p
INNER JOIN IDENTIFIER({{ my_table[0] }}) c
    ON IDENTIFIER({{ provider_id | join_policy }}) = IDENTIFIER({{ consumer_id | join_policy }})
GROUP BY p.SEGMENT, p.AGE_GROUP, p.REGION
ORDER BY p.SEGMENT, p.AGE_GROUP, p.REGION
```

**V2 template body (`REGISTER_TEMPLATE` YAML `template:` field):**
```sql
SELECT
    p.SEGMENT, p.AGE_GROUP, p.REGION,
    COUNT(DISTINCT p.EMAIL_HASH) AS OVERLAP_COUNT
FROM IDENTIFIER({{ source_table[0] }}) p
JOIN IDENTIFIER({{ my_table[0] }}) c
    ON p.EMAIL_HASH = c.EMAIL_HASH
GROUP BY p.SEGMENT, p.AGE_GROUP, p.REGION
HAVING COUNT(DISTINCT p.EMAIL_HASH) >= 1
```

**V1 `run_analysis` call:**
```sql
CALL samooha_by_snowflake_local_db.consumer.run_analysis(
    $cleanroom_name, 'overlap_analysis',
    ['DCR_V1_CONSUMER_DB.DATA.CUSTOMERS'],       -- consumer = arg 3 in V1
    ['DCR_V1_PROVIDER_DB.DATA.CUSTOMER_SPEND_V'], -- provider = arg 4 in V1
    object_construct('provider_id', 'p.EMAIL_HASH', 'consumer_id', 'c.EMAIL_HASH')
);
```

**V2 `COLLABORATION.RUN` call:**
```sql
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.RUN(
    $collab_name,
    'overlap_analysis_YYYYMMDD_V1',
    ['owner_account.provider_customer_spend_v_YYYYMMDD_V1.customer_spend_v'],  -- provider = arg 3 in V2
    ['collab_account.consumer_customers_YYYYMMDD_V1.customers'],               -- consumer = arg 4 in V2
    OBJECT_CONSTRUCT()   -- no provider_id/consumer_id
);
```

### Example: reach_analysis V1 → V2 (with string param)

**V1 template body:**
```sql
SELECT
    p.SEGMENT,
    COUNT(DISTINCT p.EMAIL_HASH) AS TOTAL_PROVIDER,
    COUNT(DISTINCT CASE WHEN c.EMAIL_HASH IS NOT NULL THEN p.EMAIL_HASH END) AS REACHABLE,
    DIV0(...) AS REACH_RATE
FROM IDENTIFIER({{ source_table[0] }}) p
LEFT JOIN IDENTIFIER({{ my_table[0] }}) c
    ON IDENTIFIER({{ provider_id | join_policy }}) = IDENTIFIER({{ consumer_id | join_policy }})
WHERE p.SEGMENT = {{ segment }}
GROUP BY p.SEGMENT
```

**V2 template body:**
```sql
SELECT
    p.SEGMENT,
    COUNT(DISTINCT p.EMAIL_HASH) AS TOTAL_PROVIDER,
    COUNT(DISTINCT CASE WHEN c.EMAIL_HASH IS NOT NULL THEN p.EMAIL_HASH END) AS REACHABLE,
    DIV0(...) AS REACH_RATE
FROM IDENTIFIER({{ source_table[0] }}) p
LEFT JOIN IDENTIFIER({{ my_table[0] }}) c
    ON p.EMAIL_HASH = c.EMAIL_HASH
WHERE p.SEGMENT = {{ segment }}    -- bare param unchanged; engine auto-quotes the string value
GROUP BY p.SEGMENT
```

**V2 `COLLABORATION.RUN` call:**
```sql
CALL SAMOOHA_BY_SNOWFLAKE_LOCAL_DB.COLLABORATION.RUN(
    $collab_name,
    'reach_analysis_YYYYMMDD_V1',
    ['owner_account.provider_customer_spend_v_YYYYMMDD_V1.customer_spend_v'],
    ['collab_account.consumer_customers_YYYYMMDD_V1.customers'],
    OBJECT_CONSTRUCT('segment', 'Premium')  -- no provider_id/consumer_id
);
```


## Common Issues

| Issue | Resolution |
|---|---|
| Collaboration name has spaces | DCR V2 names cannot contain spaces — replace with underscores in `collab_name` SET and YAML spec |
| `$var` in GRANT fails with "unexpected '$var'"  | Use `IDENTIFIER($var)` in GRANT statements and `USE WAREHOUSE IDENTIFIER($var)` |
| Template uses `\| join_policy` | Replace with hardcoded column: `ON p.<col> = c.<col>` |
| Template has `\| sqlsafe` | Remove filter entirely — `{{ param }}` is the correct V2 pattern |
| Template has `'{{ param }}'` (quoted) | Remove outer quotes — causes double-quoting `''value''` |
| V2 template version already registered | Bump version string (e.g., `YYYYMMDD_V2`); or use `ADD_TEMPLATE_REQUEST` + `APPROVE_UPDATE_REQUEST` for a live collaboration |
| Multiple consumer accounts in V1 | Add all consumers in `collaborator_identifier_aliases` and `analysis_runners` (one entry per runner) |
| Consumer reusing existing V1 DB | Skip CREATE/INSERT in Step 2; only add SAMOOHA_APP_ROLE grants |
| `LINK_LOCAL_DATA_OFFERING` fails | Confirm consumer has successfully JOINED before calling |
| `COLLABORATION.RUN` returns `Object does not exist` | Call `LINK_LOCAL_DATA_OFFERING` first |
| `InvalidDataOfferingIdFormat` on RUN | Verify 3-part ID format: `ALIAS.OFFERING_ID.DATASET_ALIAS` |
| `COLLABORATION.INITIALIZE` fails | Confirm REFERENCE_USAGE WITH GRANT OPTION is granted before calling |
