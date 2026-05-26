---
name: manage-zerocopy-sapbdc
title: SAP BDC Zero-Copy Connector
summary: "Manage the lifecycle of the SAP BDC zero-copy connector: create, enroll, consume, publish, analyze, and troubleshoot."
description: >-
  Use when consuming SAP BDC data products in Snowflake, publishing Snowflake
  databases back to SAP BDC, analyzing shared SAP data, or troubleshooting SAP
  BDC connector issues. Covers the end-to-end lifecycle of the Snowflake and
  SAP BDC zero-copy integration. Triggers: SAP BDC, SAP connector, zerocopy
  connector, SAP data product, SAP BDC Connect, SAP publish, SAP share, SAP
  troubleshoot, catalog-linked database, LINKED_ZEROCOPY_CONNECTOR, SAP BDC
  share back.
tools:
  - snowflake_sql_execute
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
prompt: "$manage-zerocopy-sapbdc create a new zero-copy connector and enroll it with SAP BDC"
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
demo-url: ""
---

# SAP BDC <=> Snowflake Zero-Copy Integration

## Overview

This skill manages the full lifecycle of the SAP BDC <=> Snowflake zero-copy connector: create, enroll, consume shared SAP data products, publish Snowflake data back to SAP BDC, analyze mounted data, and troubleshoot connector states. Use it when working with `ZEROCOPY CONNECTOR` objects whose `PARTNER = SAP_BDC`.

Do NOT use this skill for general Snowflake data sharing (non-SAP), SAP HANA direct connections, or SAP BTP non-BDC services.

## Prerequisites

- An ORGADMIN has accepted the SAP® BDC Connect for Snowflake Terms (Admin » Terms » Snowflake Marketplace in Snowsight). One-time per Snowflake organization.
- SAP Business Data Cloud (BDC) tenant is set up.
- Your role has `CREATE ZEROCOPY CONNECTOR` on the target schema.
- For publishing: `CREATE SHARE` on the account.

## Workflow

### Step 1: Ask user intent

Always present this menu when the skill is invoked:

```
What would you like to do with the SAP BDC <=> Snowflake zero-copy connector?

1. Create a new zero-copy connector
2. Consume shared SAP BDC data products
3. Publish a Snowflake data product back to SAP BDC
4. Analyze shared data already mounted in Snowflake
5. Troubleshoot connector state, privileges, or connectivity
```

Route based on selection:
- Option 1 → load `<SKILL_DIR>/create-connector/INSTRUCTIONS.md`
- Option 2 → load `<SKILL_DIR>/consume/INSTRUCTIONS.md`
- Option 3 → load `<SKILL_DIR>/publish/INSTRUCTIONS.md`
- Option 4 → load `<SKILL_DIR>/analyze/INSTRUCTIONS.md`
- Option 5 → load `<SKILL_DIR>/troubleshoot/INSTRUCTIONS.md`

⚠️ STOPPING POINT: Wait for the user's selection before loading any sub-flow.

## Sub-flows

- `<SKILL_DIR>/create-connector/INSTRUCTIONS.md` — create and enroll a new connector
- `<SKILL_DIR>/consume/INSTRUCTIONS.md` — mount SAP data products as catalog-linked databases
- `<SKILL_DIR>/publish/INSTRUCTIONS.md` — publish Snowflake data back to SAP BDC
- `<SKILL_DIR>/analyze/INSTRUCTIONS.md` — query and join mounted SAP data
- `<SKILL_DIR>/troubleshoot/INSTRUCTIONS.md` — diagnose connector errors

## Connector States

| State | Allowed Actions |
|-------|-----------------|
| NEW | CONNECT, DROP |
| CONNECTING | wait |
| CONNECTED | DISCONNECT (after disabling share-back and dropping CLDs), create CLDs, publish |
| CONNECT_ERROR | CONNECT (retry), DROP |
| DISCONNECTING | wait |
| DISCONNECTED | CONNECT (reconnect), DROP |
| DISCONNECT_ERROR | DISCONNECT (retry), DROP |
| DELETED | none (no UNDROP) |

## Key SQL Commands

| Command | Purpose |
|---------|---------|
| `CREATE ZEROCOPY CONNECTOR <name> PARTNER = SAP_BDC` | Create connector |
| `SELECT CURRENT_ORGANIZATION_NAME() \|\| '-' \|\| CURRENT_ACCOUNT_NAME()` | Derive account URL for SAP for Me registration |
| `ALTER ZEROCOPY CONNECTOR <name> CONNECT WITH CONFIG = (INVITATION_LINK = '...')` | Establish connection |
| `DESC ZEROCOPY CONNECTOR <name>` | Check connector state |
| `SHOW ZEROCOPY CONNECTORS IN SCHEMA <db>.<schema>` | List connectors |
| `SELECT SYSTEM$ZEROCOPY_CONNECTOR_LIST_SHARES('<connector>')` | List available SAP data products |
| `ALTER ZEROCOPY CONNECTOR <name> SET SHARE_BACK = TRUE` | Enable publishing |
| `ALTER ZEROCOPY CONNECTOR <name> ADD SHARE <share>` | Associate share for publishing |
| `ALTER ZEROCOPY CONNECTOR <name> DISCONNECT` | Disconnect |
| `DROP ZEROCOPY CONNECTOR <name>` | Drop (only when NEW / CONNECT_ERROR / DISCONNECT_ERROR / DISCONNECTED) |

## Required Privileges

| Privilege | Scope | Purpose |
|-----------|-------|---------|
| CREATE ZEROCOPY CONNECTOR | Schema | Create connector |
| OPERATE | Connector | Connect, disconnect, publish (SYSTEM$SAP_PUBLISH_DATA_PRODUCT) |
| USAGE | Connector | Create CLD, add/remove share |
| MODIFY | Connector | Set properties (share_back, comment) |
| MONITOR | Connector | Describe / show / list shares |
| OWNERSHIP | Connector | Rename or drop |
| CREATE DATABASE | Account | Create catalog-linked database |
| CREATE SHARE | Account | Publish data back to SAP BDC |

## Common Mistakes

- Trying to `DISCONNECT` while CLDs still exist or share-back is enabled. Drop CLDs and `SET SHARE_BACK = FALSE` first.
- Calling `DROP ZEROCOPY CONNECTOR` while CONNECTED. Disconnect first; only NEW, CONNECT_ERROR, DISCONNECT_ERROR, or DISCONNECTED states allow DROP.
- Skipping the one-time ORGADMIN acceptance of the SAP BDC Connect terms. The connector will fail to connect.
- Using a stale or already-consumed `INVITATION_LINK`. Each link is single-use; regenerate from SAP for Me.
- Granting only `MONITOR` and expecting to create a CLD. CLD creation needs `USAGE` on the connector and `CREATE DATABASE` on the account.

## Stopping Points

- Step 1 — wait for the user to choose an option (1–5) before loading any sub-flow.
- Sub-flows contain their own stopping points before destructive actions (`DISCONNECT`, `DROP`, `ADD SHARE`, publishing data products). Do not run those commands without explicit user confirmation.

## Output

Depends on the selected sub-flow — see each `INSTRUCTIONS.md` for outputs.
