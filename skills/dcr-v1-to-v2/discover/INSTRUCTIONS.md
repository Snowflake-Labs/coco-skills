
# Phase 1 — V1 Discovery

## When to Use

Load this sub-skill when executing **Step 1 — Discovery** of the `dcr-v1-to-v2` migration skill.
Use it to:
- Inventory an existing DCR V1 (SAMOOHA Provider/Consumer API) setup before migration
- Document all linked datasets, join policies, templates, and consumer accounts
- Produce a discovery report for user confirmation before mapping begins

Runs read-only SAMOOHA API calls and SQL queries against the V1 provider account.

## Prerequisites

- Provider connection confirmed in **Step 0** (`provider_connection`)
- All queries must use `connection: <provider_connection>`
- Role with access to `SAMOOHA_APP_ROLE` (for SAMOOHA introspection) and `ACCOUNTADMIN` (for table/view DDL)

## Workflow

### Step 1 — List Clean Rooms

```sql
-- List all V1 clean rooms on this provider account
USE ROLE SAMOOHA_APP_ROLE;
CALL samooha_by_snowflake_local_db.provider.view_cleanrooms();
```

For each clean room, record:
- Name
- Provider account (org.account)
- State (`CREATED` = active and published)
- Consumer accounts
- Is Published

If no clean rooms are found, stop and inform the user — this account is not a V1 provider.
If multiple clean rooms are found, ask the user which one to migrate.

### Step 2 — Inventory Linked Datasets

For the selected clean room:

```sql
USE ROLE SAMOOHA_APP_ROLE;
CALL samooha_by_snowflake_local_db.provider.view_provider_datasets($cleanroom_name);
```

For each linked dataset, record the fully-qualified name (`database.schema.object`).

Then determine whether each object is a secure view or a raw table:

```sql
USE ROLE ACCOUNTADMIN;
-- Check if the object is a secure view
SHOW VIEWS LIKE '<object_name>' IN SCHEMA <db>.<schema>;
-- IS_SECURE = YES → secure view; not found in SHOW VIEWS → it is a table
```

For each **table**, capture column info:

```sql
DESCRIBE TABLE <db>.<schema>.<table_name>;
```

For each **secure view**, capture the definition:

```sql
SELECT GET_DDL('VIEW', '<db>.<schema>.<view_name>');
```

Record:
- Column names and data types
- Likely join key (typically hashed ID column: `EMAIL_HASH`, `CUSTOMER_ID`, etc.)
- Whether it is a raw table (flag for secure view recommendation) or secure view (safe to link directly)

### Step 3 — Inventory Join Policy

```sql
USE ROLE SAMOOHA_APP_ROLE;
CALL samooha_by_snowflake_local_db.provider.view_join_policy($cleanroom_name);
```

Record for each entry:
- Dataset FQN
- Join column name

This join column is what V2 templates will hardcode in place of the `join_policy` filter.

### Step 4 — Inventory Templates

```sql
USE ROLE SAMOOHA_APP_ROLE;
CALL samooha_by_snowflake_local_db.provider.view_added_templates($cleanroom_name);
```

This returns template names. To retrieve template bodies, attempt to query the clean room app package.
The app package name follows the pattern `SAMOOHA_CLEANROOM_APP_<CLEANROOM_NAME>`:

```sql
USE ROLE ACCOUNTADMIN;
SHOW TABLES IN APPLICATION SAMOOHA_CLEANROOM_APP_<CLEANROOM_NAME>;
```

Then try to read the custom SQL templates table (schema name may vary by SAMOOHA version):

```sql
SELECT * FROM SAMOOHA_CLEANROOM_APP_<CLEANROOM_NAME>.SHARED.CUSTOM_SQL_TEMPLATES LIMIT 50;
-- If that fails, try:
SELECT * FROM SAMOOHA_CLEANROOM_APP_<CLEANROOM_NAME>.TEMPLATES.CUSTOM_SQL_TEMPLATES LIMIT 50;
```

If the template body cannot be retrieved from the app package, ask the user:

> "I could not retrieve the template bodies from the clean room app package. Please provide
> the JinjaSQL body for each template: `<list_of_template_names>`. You can find them in
> the original setup scripts used to create the clean room."

For each template body, classify as:
- **Auto-portable**: simple SELECT with `IDENTIFIER({{ source_table[N] }})` / `IDENTIFIER({{ my_table[N] }})` references — can be converted automatically
- **Stub-required**: contains complex subqueries, JavaScript/Python UDFs, or procedural logic — generate stub with `-- TODO` markers

Record for each template:
- Template name
- Full JinjaSQL body (or note if user must provide)
- Parameters passed in `object_construct` (filter conditions like `segment`, `category`, etc.)
- Tables referenced (`source_table[N]`, `my_table[N]`)
- Join columns (from `join_policy` filter in the template body)
- Portability classification

### Step 5 — Identify Consumer Accounts

From the `view_cleanrooms` output (Step 1), extract consumer account identifiers (org.account format).

To check whether consumers have linked datasets:

```sql
-- On provider account — shows consumer-side linked datasets if available
USE ROLE SAMOOHA_APP_ROLE;
CALL samooha_by_snowflake_local_db.consumer.view_consumer_datasets($cleanroom_name);
```

If the user also has access to the consumer account, optionally gather consumer DB info:

```sql
-- On consumer account
USE ROLE ACCOUNTADMIN;
SHOW DATABASES;
SHOW TABLES IN SCHEMA <consumer_db>.<schema>;
```

Record for each consumer:
- Account identifier (org.account)
- Consumer DB name (if known)
- Consumer linked tables (from `view_consumer_datasets` or user input)

### Step 6 — Provider DB Inventory

```sql
USE ROLE ACCOUNTADMIN;
SHOW DATABASES;
```

Identify:
- The provider data database (contains the source data referenced by linked datasets)
- Any other databases referenced by the linked datasets

For the provider data DB:

```sql
SHOW SCHEMAS IN DATABASE <provider_db>;
SHOW TABLES IN SCHEMA <provider_db>.<schema>;
SHOW VIEWS IN SCHEMA <provider_db>.<schema>;
```

Confirm all linked objects exist and are accessible.

## Output

Generate `discovery_report.md` with this structure:

```markdown
# DCR V1 Discovery Report

Generated: <date>
Provider account: <account>
Connection: <connection_name>
Clean room: <cleanroom_name>
Distribution: EXTERNAL / INTERNAL
State: <state>
Published: Yes / No

## Consumer Accounts

| Account | Status |
|---|---|
| <org.account> | Added as consumer |

## Linked Datasets (Provider)

| Object FQN | Type | Join Column | Notes |
|---|---|---|---|
| DCR_V1_PROVIDER_DB.DATA.CUSTOMER_SPEND_V | Secure View | EMAIL_HASH | Pre-aggregated spend profile |
| DCR_V1_PROVIDER_DB.DATA.TRANSACTIONS | Table | EMAIL_HASH | Raw transactions |

## Join Policy

| Dataset | Join Column |
|---|---|
| DCR_V1_PROVIDER_DB.DATA.CUSTOMER_SPEND_V | EMAIL_HASH |
| DCR_V1_PROVIDER_DB.DATA.TRANSACTIONS | EMAIL_HASH |

## Templates

| Template Name | Portability | Parameters | Notes |
|---|---|---|---|
| overlap_analysis | Auto-portable | provider_id, consumer_id | Standard join template |
| reach_analysis | Auto-portable | provider_id, consumer_id, segment | String filter param |
| transaction_overlap | Auto-portable | provider_id, consumer_id | Aggregation by category |
| spend_analysis | Auto-portable | provider_id, consumer_id | AVG/SUM aggregation |

### Template Bodies

For each template, include the full JinjaSQL body as found in the app package or provided by the user.

## Provider Database

| Database | Schema | Tables | Views |
|---|---|---|---|
| DCR_V1_PROVIDER_DB | DATA | CUSTOMERS, TRANSACTIONS | CUSTOMER_SPEND_V (secure) |

## Consumer Datasets

| Consumer Account | Consumer DB | Linked Table |
|---|---|---|
| SFPSCOGS.WLIN_AWS_W2 | DCR_V1_CONSUMER_DB | DATA.CUSTOMERS |
```

## Stopping Point

Present the discovery report to the user. Ask:

> "Here is the V1 inventory. Please confirm this is complete and correct before I
> proceed with the V1→V2 mapping. Are there any objects missing or corrections needed?"

Do not proceed to Step 2 (Mapping) until the user confirms.

## Common Issues

| Issue | Resolution |
|---|---|
| `view_cleanrooms` returns empty | Confirm you are on the provider account and using SAMOOHA_APP_ROLE |
| Cannot retrieve template body from app package | Ask user to provide from original setup scripts |
| Multiple clean rooms found | Ask user which one to migrate |
| Template uses JavaScript/Python UDF | Mark as stub-required — cannot auto-convert |
| Consumer datasets not visible | Optionally gather from consumer account or ask user |
| Raw table linked (no secure view) | Flag in report — recommend creating a secure view before migration |
