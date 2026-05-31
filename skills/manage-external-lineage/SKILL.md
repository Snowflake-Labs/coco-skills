---
name: manage-external-lineage
title: Manage External Lineage
summary: Send and delete OpenLineage COMPLETE events to connect external systems to Snowflake's lineage graph.
description: |
  Use when you need to surface external systems (Postgres, MySQL, S3, Kafka, etc.) in Snowflake's lineage view, send OpenLineage COMPLETE events via REST, or remove existing external lineage links. Triggers: external lineage, openlineage event, send lineage, establish lineage, delete lineage, connect postgres to snowflake lineage, connect mysql to snowflake lineage, connect s3 to snowflake lineage, document data pipeline, lineage api, ingest lineage.
tools:
  - snowflake_sql_execute
  - Bash
  - Read
  - Write
  - Edit
prompt: Create a COMPLETE external lineage event from postgres://prod-db:5432 public.orders into MYDB.PUBLIC.ORDERS.
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Manage External Lineage

## Overview

Snowflake's lineage graph natively tracks objects inside the account. To show data flowing in from (or out to) external systems — Postgres, MySQL, S3, Kafka, DB2, Trino, etc. — you POST OpenLineage `COMPLETE` events to the external lineage REST endpoint. Once accepted, those external nodes appear in Snowsight under **Catalog → Database Explorer → [Table] → Lineage**.

This skill helps you:

- Build a valid OpenLineage payload
- Send it using your existing Snowflake connection (no token juggling)
- Delete external lineage relationships when sources are retired

## Prerequisites

- `INGEST LINEAGE` privilege on the account (and `DELETE LINEAGE` for deletes)
- An active `cortex` connection, OR a Programmatic Access Token (PAT)
- Python deps: `requests`, `snowflake-connector-python`

## Workflow

### Step 1: Verify privileges and target

```sql
SHOW GRANTS ON ACCOUNT;
-- Look for INGEST LINEAGE granted to your role
DESCRIBE TABLE <db>.<schema>.<table>;
```

If missing: `GRANT INGEST LINEAGE ON ACCOUNT TO ROLE <role>;`

### Step 2: Build the payload

```json
{
  "eventType": "COMPLETE",
  "eventTime": "2026-02-20T19:00:00.000Z",
  "job": {"namespace": "external-etl", "name": "orders_pipeline"},
  "run": {"runId": "f47ac10b-58cc-4372-a567-0e02b2c3d479"},
  "producer": "https://github.com/OpenLineage/OpenLineage/blob/v1-0-0/client",
  "schemaURL": "https://openlineage.io/spec/0-0-1/OpenLineage.json",
  "inputs": [
    {"namespace": "postgres://prod-db:5432", "name": "public.orders"}
  ],
  "outputs": [
    {"namespace": "snowflake://<ORG>-<ACCOUNT>", "name": "<DB>.<SCHEMA>.<TABLE>"}
  ]
}
```

Rules:
- `eventType` must be `COMPLETE` — other types are ignored.
- `inputs` and `outputs` must mix Snowflake and external objects.
- Do NOT include `facets` for external objects — they render as "External Node" by default.
- See `namespace_conventions.md` for per-source namespace formats.

⚠️ STOPPING POINT: Show the payload to the user and wait for confirmation before sending.

### Step 3: Send the event

Preferred — use your Cortex Code connection:

```bash
SNOWFLAKE_CONNECTION_NAME=<connection> python <SKILL_DIR>/send_lineage_via_connection.py -p payload.json
```

Or generate + send in one go:

```bash
<SKILL_DIR>/generate_payload.sh -a <ACCOUNT> -o <DB>.<SCHEMA>.<TABLE> \
  -i 'postgres://host:5432::db.schema.source' -f /tmp/payload.json
SNOWFLAKE_CONNECTION_NAME=<connection> python <SKILL_DIR>/send_lineage_via_connection.py -p /tmp/payload.json
```

PAT alternative: `<SKILL_DIR>/send_lineage.sh -a <ACCOUNT> -t token.txt -p payload.json`

### Step 4: Verify

Open Snowsight → Catalog → Database Explorer → your table → Lineage tab. Allow 1–2 minutes for propagation.

### Step 5: Delete external lineage (optional)

⚠️ STOPPING POINT: Confirm the source/target before sending DELETE. The endpoint always returns HTTP 200 — verify removal in Snowsight.

```bash
curl --globoff -X DELETE \
  -H "Authorization: Bearer $API_KEY" \
  "https://<ACCOUNT>.snowflakecomputing.com/api/v2/lineage/external-lineage?sourceNamespace=<NS>&sourceName=<NAME>&sourceDatasetType=External%20Node&targetName=<DB>.<SCHEMA>.<TABLE>&targetDatasetType=TABLE"
```

Delete scopes:
- Source + target → break that one link
- Source only → break all downstream from that source
- Target only → strip the target from the graph

## Common Mistakes

- Using `eventType` other than `COMPLETE` — silently dropped.
- Underscores in the account URL — use hyphens (`ORG-ACCOUNT`).
- Forgetting `--globoff` on curl — it mangles `External%20Node`.
- Including `facets` on external nodes — breaks the "External Node" rendering.
- Treating DELETE's `200` as success — always verify in Snowsight.
- Mismatched delete direction — if external was the INPUT on create, it must be the source on delete.
- Case-insensitive matching — namespaces and names are case-sensitive.

## Limitations

- 1-year retention, 10,000 events per account
- 1000-char max FQN
- No column-level lineage
- External lineage isn't returned by `GET_LINEAGE`

## Stopping Points

- Step 2 — wait for payload review before sending
- Step 5 — confirm targets before DELETE
- Step 5 — verify in Snowsight (HTTP 200 does not confirm deletion)

## Reference files

- `namespace_conventions.md` — namespace formats per source
- `token_setup.md` — creating a PAT
- `troubleshooting.md` — 401 / 403 / 404 fixes
- `send_lineage_via_connection.py` — recommended sender
- `send_lineage.sh` — PAT-based sender
- `generate_payload.sh` — payload builder
