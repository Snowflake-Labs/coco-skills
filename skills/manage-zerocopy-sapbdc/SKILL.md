---
name: manage-zerocopy-sapbdc
title: SAP BDC Zero-Copy Connector
summary: Manage the lifecycle of the SAP BDC zero-copy connector: create, enroll, consume, publish, analyze, and troubleshoot.
description: >-
  Manage the end-to-end lifecycle of the Snowflake and SAP BDC Zero-Copy
  Integration and connector. Use when: consuming SAP data products in Snowflake,
  publishing Snowflake databases to SAP BDC, analyzing shared SAP data, or
  troubleshooting SAP BDC connector issues. Triggers: SAP BDC, SAP connector,
  zerocopy connector, SAP data product, SAP BDC Connect, SAP publish, SAP share,
  SAP troubleshoot, catalog-linked database, LINKED_ZEROCOPY_CONNECTOR, SAP BDC
  share back.
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
prompt: "$manage-zerocopy-sapbdc create a new zero-copy connector and enroll it with SAP BDC"
language: en
status: Published
author: Sanjay Nagamangalam
type: snowflake
demo-url: ""
---

# SAP BDC <=> Snowflake Zero-Copy Integration

Manages the full lifecycle of the SAP BDC <=> Snowflake zero-copy integration and connector: setup, consume, publish, analyze, and troubleshoot.

## When to Use

- User asks to create, connect, or enroll an SAP BDC zero-copy connector
- User wants to consume or mount SAP BDC shared data products in Snowflake
- User wants to publish Snowflake data back to SAP BDC as a data product
- User asks to explore, query, or join data from mounted SAP data products
- User needs to troubleshoot SAP BDC connector states, privileges, or connectivity
- Do NOT use for: general Snowflake data sharing (non-SAP), SAP HANA direct connections, or SAP BTP non-BDC services

## Prerequisites 4

- An ORGADMIN must have accepted the SAP® BDC Connect for Snowflake Terms (Admin » Terms » Snowflake Marketplace section in Snowsight). This only needs to be done once per Snowflake organization.
- SAP Business Data Cloud (BDC) setup must be complete
- Role must have `CREATE ZEROCOPY CONNECTOR` on the target schema
- For publishing: `CREATE SHARE` privilege on the account

## Workflow

### Step 1: Ask User Intent

**Every time this skill is invoked**, present this menu:

```
What would you like to do with the SAP BDC <=> Snowflake zero-copy connector?

1. Create a new zero-copy connector - Set up a new connector and enroll it with SAP BDC
2. Consume shared data products - Mount shared SAP BDC data products as catalog-linked databases in Snowflake
3. Publish a data product - Share a Snowflake database back to SAP BDC as a new data product
4. Analyze shared data - Explore, query, and join data from SAP BDC data products already mounted in Snowflake
5. Troubleshoot - Diagnose and fix connector state errors, privilege issues, and connectivity problems
```

**Route based on selection:**
- Option 1 -> **Load** `create-connector/INSTRUCTIONS.md`
- Option 2 -> **Load** `consume/INSTRUCTIONS.md`
- Option 3 -> **Load** `publish/INSTRUCTIONS.md`
- Option 4 -> **Load** `analyze/INSTRUCTIONS.md`
- Option 5 -> **Load** `troubleshoot/INSTRUCTIONS.md`

## Connector Quick Reference

### Connector States
| State | Description | Allowed Actions |
|-------|-------------|-----------------|
| NEW | Just created, no connection attempted | CONNECT, DROP |
| CONNECTING | Connection in progress (async) | Wait |
| CONNECTED | Active connection | DISCONNECT (must disable share-back and drop CLDs first), create CLDs, publish |
| CONNECT_ERROR | Connection failed | CONNECT (retry), DROP |
| DISCONNECTING | Disconnection in progress (async) | Wait |
| DISCONNECTED | Connection dropped | CONNECT (reconnect), DROP |
| DISCONNECT_ERROR | Disconnection failed | DISCONNECT (retry), DROP |
| DELETED | Connector has been dropped (permanent, no UNDROP) | None |

### Key SQL Commands
| Command | Purpose |
|---------|---------|
| `CREATE ZEROCOPY CONNECTOR <name> PARTNER = SAP_BDC` | Create connector |
| `SELECT CURRENT_ORGANIZATION_NAME() \|\| '-' \|\| CURRENT_ACCOUNT_NAME()` | Derive account URL for SAP for Me registration |
| `ALTER ZEROCOPY CONNECTOR <name> CONNECT WITH CONFIG = (INVITATION_LINK = '...')` | Establish connection |
| `DESC ZEROCOPY CONNECTOR <name>` | Check connector state |
| `SHOW ZEROCOPY CONNECTORS IN SCHEMA <db>.<schema>` | List all connectors |
| `SELECT SYSTEM$ZEROCOPY_CONNECTOR_LIST_SHARES('<connector>')` | List available SAP data products |
| `ALTER ZEROCOPY CONNECTOR <name> SET SHARE_BACK = TRUE` | Enable publishing |
| `ALTER ZEROCOPY CONNECTOR <name> ADD SHARE <share>` | Associate share for publishing |
| `ALTER ZEROCOPY CONNECTOR <name> DISCONNECT` | Disconnect (disable share-back and drop CLDs first) |
| `DROP ZEROCOPY CONNECTOR <name>` | Drop connector (must be NEW/CONNECT_ERROR/DISCONNECT_ERROR/DISCONNECTED) |

### Required Privileges
| Privilege | Scope | Purpose |
|-----------|-------|---------|
| CREATE ZEROCOPY CONNECTOR | Schema | Create connector |
| OPERATE | Connector | Connect, disconnect, and publish data product (SYSTEM$SAP_PUBLISH_DATA_PRODUCT) |
| USAGE | Connector | Create CLD (also requires CREATE DATABASE on account), add/remove share (also requires OWNERSHIP on share) |
| MODIFY | Connector | Set/unset properties (comment, share_back, etc.) |
| MONITOR | Connector | Describe connector, show connectors, list shares (any privilege on the connector is sufficient) |
| OWNERSHIP | Connector | Rename or drop the connector |
| CREATE DATABASE | Account | Create catalog-linked database (also requires USAGE on connector) |
| CREATE SHARE | Account | Publish data back to SAP BDC |

## Examples

### Example 1: Create and enroll a connector
User: `$manage-zerocopy-sapbdc create a new zero-copy connector and enroll it with SAP BDC`
Assistant: Asks for database/schema/connector name, runs CREATE ZEROCOPY CONNECTOR, provides Partner ID for SAP 4 Me registration, then connects with invitation link.

### Example 2: Consume a data product
User: `$manage-zerocopy-sapbdc mount the Workforce Persons data product from SAP`
Assistant: Lists available shares, creates a catalog-linked database, confirms tables are accessible and semantic views are generated.

### Example 3: Publish Snowflake data to SAP
User: `$manage-zerocopy-sapbdc publish my ANALYTICS_DB.SALES schema to SAP BDC`
Assistant: Enables share-back, creates Iceberg tables, generates CSN Interop JSON, creates share, publishes data product.

### Example 4: Troubleshoot
User: `$manage-zerocopy-sapbdc my connector is stuck in CONNECT_ERROR`
Assistant: Runs DESC ZEROCOPY CONNECTOR, checks privileges, validates invitation link, provides remediation steps.

## Stopping Points

- After Step 1: Route to selected sub-skill

## Output

Depends on selected use case — see sub-skill outputs.
