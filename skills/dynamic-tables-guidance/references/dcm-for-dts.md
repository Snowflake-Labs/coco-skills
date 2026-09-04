# DCM for Dynamic Tables

DCM (Database Change Management) provides **git-native infrastructure-as-code** for DT pipelines. Define your DTs declaratively, version them in git, and deploy with `snow dcm plan` → `snow dcm deploy`.

## Why DCM for DTs

- **Version controlled** — DT definitions live in git alongside your other infrastructure
- **Repeatable deployments** — same definitions deploy to dev/staging/prod via templating
- **Schema evolution** — change a DT definition, redeploy, DCM handles the diff
- **Full pipeline IaC** — database, schema, warehouses, tables, DTs, roles, and grants in one project

## DCM DT Syntax (DEFINE)

```sql
DEFINE DYNAMIC TABLE {{ database }}.{{ schema }}.BRONZE_EVENTS
TARGET_LAG = DOWNSTREAM
WAREHOUSE = {{ database }}_DT_WH
AS
  SELECT
    record_content:event_id::STRING AS event_id,
    record_content:event_type::STRING AS event_type,
    record_content:user_id::STRING AS user_id,
    record_content:timestamp::TIMESTAMP_NTZ AS event_ts,
    record_content:payload AS payload
  FROM {{ database }}.{{ schema }}.RAW_EVENTS_TOPIC;

DEFINE DYNAMIC TABLE {{ database }}.{{ schema }}.GOLD_HOURLY_SALES
TARGET_LAG = '5 minutes'
WAREHOUSE = {{ database }}_DT_WH
AS
  SELECT
    DATE_TRUNC('hour', event_ts) AS sales_hour,
    category,
    COUNT(DISTINCT event_id) AS order_count,
    SUM(line_total) AS revenue
  FROM {{ database }}.{{ schema }}.SILVER_PURCHASES
  GROUP BY 1, 2;
```

## DCM Manifest (manifest.yml)

```yaml
manifest_version: 2
type: DCM_PROJECT
default_target: 'DEV'
targets:
  DEV:
    project_name: '{{DATABASE}}.{{SCHEMA}}.MY_PROJECT'
    project_owner: SYSADMIN
    templating_config: 'DEV'
templating:
  defaults:
    database: 'MY_DB'
    schema: 'PUBLIC'
  configurations:
    DEV:
      database: 'MY_DB_DEV'
    PROD:
      database: 'MY_DB_PROD'
```

## DCM Workflow

```bash
snow dcm raw-analyze dcm/ -c <connection>
snow dcm plan dcm/ -c <connection> --save-output
snow dcm deploy dcm/ -c <connection> --alias "v1-initial"
```

**Tip:** Put all DT definitions in a single `dynamic_tables.sql` file within `dcm/definitions/`. DCM processes all `.sql` files in that directory. Use Jinja templating (`{{ database }}`, `{{ schema }}`) for environment portability.
