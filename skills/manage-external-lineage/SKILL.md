---
name: manage-external-lineage
title: Manage External Lineage
summary: Create and delete OpenLineage events to connect external systems to Snowflake's lineage graph.
description: "Use when you need to connect external data sources (Postgres, MySQL, S3, Kafka, etc.) to Snowflake's lineage graph via the OpenLineage REST API, or when you need to delete external lineage relationships. Triggers: external lineage, openlineage event, send lineage, establish lineage, delete lineage, create lineage event, connect postgres to snowflake lineage, connect mysql to snowflake lineage, connect s3 to snowflake lineage, track data flow, document data pipeline, lineage api, ingest lineage."
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Bash
  - Read
  - Write
  - Edit
prompt: Create an external lineage event linking my Postgres source table to a Snowflake table.
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Manage External Lineage

## Overview

This skill creates and deletes OpenLineage `COMPLETE` events through Snowflake's external lineage REST API so external systems (Postgres, MySQL, S3, Kafka, DB2, Trino, etc.) appear in Snowsight's lineage graph alongside Snowflake objects. Use it to document cross-platform pipelines, show upstream sources feeding Snowflake tables, or show downstream destinations Snowflake feeds.

## Prerequisites

- `INGEST LINEAGE` privilege on the account (and `DELETE LINEAGE` for deletes).
- Active Snowflake connection in your `cortex` session, OR a Programmatic Access Token (PAT) / JWT.
- Python deps: `requests`, `snowflake-connector-python`.

## Workflow

### 1. Verify privileges

```sql
SHOW GRANTS ON ACCOUNT;
-- GRANT INGEST LINEAGE ON ACCOUNT TO ROLE <role_name>;
```

### 2. Verify the Snowflake target exists

```sql
DESCRIBE TABLE <database>.<schema>.<table_name>;
```

### 3. Build the payload

```json
{
  "eventType": "COMPLETE",
  "eventTime": "<ISO8601>",
  "job": {"namespace": "<job_namespace>", "name": "<job_name>"},
  "run": {"runId": "<UUID>"},
  "producer": "https://github.com/OpenLineage/OpenLineage/blob/v1-0-0/client",
  "schemaURL": "https://openlineage.io/spec/0-0-1/OpenLineage.json",
  "inputs":  [{"namespace": "<source_ns>", "name": "<source_object>"}],
  "outputs": [{"namespace": "snowflake://<ORG>-<ACCOUNT>", "name": "<DB>.<SCHEMA>.<TABLE>"}]
}
```

Stop and show the payload to the user before sending.

### 4. Send the event

Recommended (uses your active `cortex` connection, no token wrangling):

```bash
SNOWFLAKE_CONNECTION_NAME=<connection> \
  python <SKILL_DIR>/send_lineage_via_connection.py -p payload.json
```

PAT/JWT alternative:

```bash
<SKILL_DIR>/send_lineage.sh -a <ACCOUNT> -t /path/to/token.txt -p payload.json
```

### 5. Verify in Snowsight

Catalog → Database Explorer → your table → **Lineage** tab. May take 1–2 minutes to reflect.

## Deleting external lineage

| Scenario | Params | Effect |
|---|---|---|
| Break specific edge | source + target | Removes that edge only |
| Break all downstream | source only | Removes source → all targets |
| Remove from graph | target only | Removes target regardless of source |

```bash
curl --globoff -X DELETE \
  -H "Authorization: Bearer $API_KEY" \
  "https://<ACCOUNT>.snowflakecomputing.com/api/v2/lineage/external-lineage?sourceNamespace=<SRC_NS>&sourceName=<SRC>&sourceDatasetType=External%20Node&targetName=<DB>.<SCHEMA>.<TABLE>&targetDatasetType=TABLE"
```

DELETE always returns HTTP 200 — confirm in Snowsight.

## Example: Postgres + MySQL → Snowflake

```json
{
  "eventType": "COMPLETE",
  "eventTime": "2026-02-20T19:00:00.000Z",
  "job": {"namespace": "external-etl", "name": "customer_data_pipeline"},
  "run": {"runId": "f47ac10b-58cc-4372-a567-0e02b2c3d479"},
  "producer": "https://github.com/OpenLineage/OpenLineage/blob/v1-0-0/client",
  "schemaURL": "https://openlineage.io/spec/0-0-1/OpenLineage.json",
  "inputs": [
    {"namespace": "postgres://prod-db.example.com:5432", "name": "public.customer_signups"},
    {"namespace": "mysql://warehouse.example.com:3306", "name": "raw.customer_raw"}
  ],
  "outputs": [
    {"namespace": "snowflake://<ORG>-<ACCOUNT>", "name": "<DB>.<SCHEMA>.<TABLE>"}
  ]
}
```

## Common Mistakes

- **Wrong `eventType`.** Only `COMPLETE` is processed; `START`, `RUNNING`, `FAIL` are silently ignored.
- **Including `facets` on external objects.** Omit them — externals render as "External Node" automatically.
- **Underscores in the account identifier.** Use `ORG-ACCOUNT`, not `ORG_ACCOUNT`.
- **Not using `--globoff` with curl.** Without it, curl re-encodes `External%20Node` and the DELETE matches nothing.
- **Trusting HTTP 200 from DELETE.** It always returns 200; verify in Snowsight.
- **Case-mismatched namespaces.** Namespace and name values are case-sensitive; a typo creates a new orphan node.
- **Mismatched delete direction.** If lineage was created with the external object as `inputs`, the delete `source` must be that same external node.
- **Expecting external nodes in `GET_LINEAGE`.** They only appear in the Snowsight UI.

## Limits

- 1-year retention
- 10,000 events per account
- 1000-char max FQN
- No column-level lineage

## Reference files

- `namespace_conventions.md` — namespace formats per source type
- `token_setup.md` — PAT setup
- `troubleshooting.md` — 401/403/404 fixes
- `send_lineage_via_connection.py` — recommended sender
- `send_lineage.sh`, `generate_payload.sh` — PAT-based alternatives
