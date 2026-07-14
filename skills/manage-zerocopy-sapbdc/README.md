# SAP BDC <=> Snowflake Zero-Copy Integration - Cortex Code Skill

**Authors:** Amit Tapas, Kevin Poskitt (from SAP)

A Cortex Code skill that manages the end-to-end lifecycle of the [SAP and Snowflake Zero-Copy Integration](https://docs.snowflake.com/en/user-guide/data-integration/zero-copy/about-sap-snowflake) and connector - creating connectors, consuming data products from SAP BDC, publishing Snowflake data to SAP BDC, analyzing shared data, and troubleshooting issues, all through a conversational, step-by-step workflow.


---

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
  - [Step 1: Download the Skill](#step-1-download-the-skill)
  - [Step 2: Install](#step-2-install)
    - [Cortex Code (CLI)](#cortex-code-cli)
    - [Snowsight](#snowsight)
- [Getting Started](#getting-started)
- [Use Cases](#use-cases)
  - [Use Case 1: Create a New Zero-Copy Connector](#use-case-1-create-a-new-zero-copy-connector)
  - [Use Case 2: Consume Data Products](#use-case-2-consume-data-products)
  - [Use Case 3: Publish a Data Product](#use-case-3-publish-a-data-product)
  - [Use Case 4: Analyze Shared Data](#use-case-4-analyze-shared-data)
  - [Use Case 5: Troubleshoot](#use-case-5-troubleshoot)
- [SAP CSN Generator (Sub-Skill)](#sap-csn-generator-sub-skill)
- [Skill Structure](#skill-structure)
- [Key Concepts](#key-concepts)
  - [Connector States](#connector-states)
  - [Catalog-Linked Databases](#catalog-linked-databases)
  - [Publishing Requirements](#publishing-requirements)
- [Privileges](#privileges)
- [Key SQL Commands](#key-sql-commands)
- [Documentation Links](#documentation-links)

## Overview

The zero-copy connector enables seamless, bi-directional data sharing between SAP Business Data Cloud and Snowflake without copying data. SAP data products become queryable in Snowflake as **catalog-linked databases**, and Snowflake databases can be published back to SAP BDC as data products.

This skill provides five workflows accessible from a single menu:

| # | Workflow | Description |
|---|----------|-------------|
| 1 | **Create Connector** | Create a new zero-copy connector and enroll it with SAP BDC |
| 2 | **Consume** | Mount SAP BDC data products as catalog-linked databases |
| 3 | **Publish** | Share a Snowflake database back to SAP BDC as a data product |
| 4 | **Analyze** | Explore, query, and join data from mounted SAP data products |
| 5 | **Troubleshoot** | Diagnose and fix connector, CLD, and publishing issues |

## Prerequisites

Before using this skill, ensure:

1. **SAP BDC Connect for Snowflake Terms accepted** — An `ORGADMIN` must accept the SAP® BDC Connect for Snowflake Terms in Snowsight (Admin » Terms » Snowflake Marketplace section). This only needs to be done once per Snowflake organization.
2. **SAP-side setup complete** — Your SAP administrator has provisioned SAP Business Data Cloud and other associated SAP services
3. **Snowflake privileges** — Your role has the necessary privileges (see [Privileges](#privileges) below).

## Installation

### Step 1: Download the Skill

The skill lives in the Snowflake-Solutions internal repository. Clone the whole repo and locate the skill folder:

```
git clone git@github.com:Snowflake-Solutions/cortex-code-skills.git
cd cortex-code-skills/skills/manage-zerocopy-sapbdc
```

For external (Snowflake-Labs) distribution, the skill will be published separately at `https://github.com/Snowflake-Labs/cortex-code-skills` (forthcoming).

### Step 2: Install

#### Cortex Code (CLI)

Copy the `manage-zerocopy-sapbdc` folder (which includes the CSN Generator as a sub-skill) to install.

#### Option A: Project-local (current project only)

```
cp -r manage-zerocopy-sapbdc /path/to/project/.cortex/skills/
```

#### Option B: Global (all projects)

```
cp -r manage-zerocopy-sapbdc ~/.snowflake/cortex/skills/
```

Or if `$SNOWFLAKE_HOME` is set:

```
cp -r manage-zerocopy-sapbdc $SNOWFLAKE_HOME/cortex/skills/
```

#### Snowsight Workspace

1. In Snowsight, go to **Projects > Workspaces** in the left navigation.
2. Create a new workspace if you don't have one already.
3. Click the **Cortex Code** icon on the right side (the blue icon with the star) to open a chat panel.
4. Click the **+** icon in the chat panel and select **Upload Skill Folder(s)**.
5. Select the `manage-zerocopy-sapbdc` folder from your local machine.

The skill will be available via the same trigger phrases listed in [Getting Started](#getting-started).

## Getting Started

Simply mention any of these trigger phrases in Cortex Code:

- "SAP BDC connector"
- "SAP data product"
- "zerocopy connector"
- "SAP BDC Connect"
- "catalog-linked database"
- "publish to SAP"
- "SAP troubleshoot"
- "SAP share back"

The skill will present a menu with five options every time it is invoked.

## Use Cases

### Use Case 1: Create a New Zero-Copy Connector

**Purpose:** Create a new zerocopy connector and enroll it with SAP BDC.

A Snowflake account can have multiple zero-copy connectors (e.g., one per SAP BDC tenant or business unit).

**What it does:**
1. Creates the database, schema, and connector object
2. Derives the Snowflake account URL for SAP for Me registration
3. Connects the connector using the invitation link from SAP

**You will need:**
- A database and schema for the connector
- Access to SAP for Me to generate the invitation link

### Use Case 2: Consume Data Products

**Purpose:** Mount SAP BDC data products as catalog-linked databases in Snowflake.

**What it does:**
1. Selects an existing CONNECTED connector (or routes to Create if none exist)
2. Lists available SAP data products
3. Creates catalog-linked database(s) for selected data products
4. Previews the data

**You will need:**
- An active (CONNECTED) zerocopy connector
- The name(s) of data products you want to mount

### Use Case 3: Publish a Data Product

**Purpose:** Share a Snowflake database back to SAP BDC as a new data product.

**What it does:**
1. Verifies the connector is connected and share-back is enabled
2. Helps you identify which tables to publish
3. Converts FDN (standard Snowflake) tables to Iceberg V3 if needed, with two options:
   - **CTAS (one-time snapshot)** — creates static Iceberg copies
   - **Dynamic Iceberg Tables (automatic sync)** — creates dynamic tables that refresh from the source on a configurable target lag
4. Verifies all Iceberg table prerequisites (`CATALOG = 'SNOWFLAKE'`, `STORAGE_SERIALIZATION_POLICY = 'COMPATIBLE'`, `ENABLE_ICEBERG_MERGE_ON_READ = FALSE`)
5. Generates a minimal CSN Interop v1.0 document via the **SAP CSN Generator** sub-skill (or accepts a user-provided CSN file)
6. Creates a Snowflake share with proper grants and associates it with the connector
7. Generates ORD metadata (title, description) with intelligent defaults from table inspection
8. Publishes the data product with ORD metadata and CSN schema

**You will need:**
- An active (CONNECTED) zerocopy connector
- Iceberg V3 tables with copy-on-write enabled (Snowflake-managed catalog), or standard FDN tables that the skill will convert
- ORD metadata (title, description) for the data product — the skill proposes defaults
- A CSN (Core Schema Notation) JSON document — the skill can generate a minimal CSN Interop v1.0 document via the CSN Generator

### Use Case 4: Analyze Shared Data

**Purpose:** Explore, query, and analyze SAP data products already mounted in Snowflake.

**What it does:**
1. Discovers catalog-linked databases and their tables
2. Explores table schemas and sample data
3. Checks for auto-generated Semantic Views in the `snowflake$` schema (created from SAP CSN)
4. Offers Cortex Analyst natural-language querying via Semantic Views, or direct SQL
5. Writes and runs analytical queries (including cross-database joins)
6. Optionally persists results as native Snowflake tables (CTAS)

**You will need:**
- At least one catalog-linked database already created
- A business question or analysis goal

### Use Case 5: Troubleshoot

**Purpose:** Diagnose and fix connector, CLD, and publishing issues.

**What it covers:**
- `CONNECT_ERROR` / `DISCONNECT_ERROR` resolution
- Catalog-linked database creation failures
- CLD shows no tables (grant/role verification, sync not complete, or SAP-side not provisioned)
- Missing `snowflake$` schema (SAP data product published without CSN document)
- `snowflake$` schema exists but no Semantic View (CSN document incomplete/malformed)
- Publishing / share-back issues (Iceberg prerequisites, share grants, ORD/CSN validation)
- Data freshness / sync problems (CLD status check, SUSPEND/RESUME DISCOVERY, per-table Iceberg metadata refresh, sync interval tuning)
- Privilege and access denied errors
- General diagnostic collection for Snowflake Support escalation

## SAP CSN Generator (Sub-Skill)

The **csn-generator** sub-skill generates minimal [SAP CSN Interop v1.0](https://sap.github.io/csn-interop-specification/) JSON files that match the SAP BDC Connect SDK output format, maximizing acceptance when publishing to SAP Datasphere/BDC. It is invoked by the Publish workflow when the user needs a CSN document.

**Key capabilities:**
- Generates CSN Interop v1.0 (SDK-compatible) — no options, reviews, or validation loops
- Core structure only: `definitions`, `kind`, `elements`, primary keys (`key: true`)
- Type mappings follow the Snowflake → Iceberg → CSN chain (e.g. `INTEGER → cds.Integer`, `BIGINT → cds.Integer64`, `NUMBER(p,s) → cds.Decimal(p,s)`, `FLOAT → cds.Double`, `TIMESTAMP_*(6) → cds.Timestamp`, strings → `cds.String` with no length); `TIME`, nanosecond timestamps, `BINARY`, `VARIANT`, and complex types are unsupported
- Foreign-key associations only when PK/FK constraints are available (`@ObjectModel.foreignKey.association`); no heuristic inference
- Auto-adds `@PersonalData.*` annotations when PII columns are detected (no interactive review)
- `kind: context` namespacing where the context is the schema name; UPPERCASE identifiers matching Snowflake; empty `i18n: {}`
- Intentionally omits display labels, i18n translations, semantic/analytical annotations, and entity classification
- Result: ~300–500 bytes per entity (vs 3–5 KB for full CSN)

See `csn-generator/README.md` for full documentation.

## Skill Structure

```
manage-zerocopy-sapbdc/            # Connector lifecycle skill
├── SKILL.md                              # Main router — presents use case menu
├── create-connector/
│   └── INSTRUCTIONS.md                   # Use Case 1: Create & enroll connector
├── consume/
│   └── INSTRUCTIONS.md                   # Use Case 2: Consume data products
├── publish/
│   └── INSTRUCTIONS.md                   # Use Case 3: Publish to SAP BDC
├── analyze/
│   └── INSTRUCTIONS.md                   # Use Case 4: Analyze shared data
├── troubleshoot/
│   └── INSTRUCTIONS.md                   # Use Case 5: Troubleshoot issues
├── csn-generator/                        # Minimal CSN Interop v1.0 generator sub-routine
│   ├── INSTRUCTIONS.md                   # Sub-routine definition (source of truth)
│   ├── README.md                         # Full documentation
│   ├── skill.json                        # Sub-skill manifest
│   └── references/
│       └── type-mapping-sdk.md           # Snowflake → CDS type mapping (SDK-compatible)
├── README.md                             # This file
└── LICENSE                               # Snowflake Skills License
```

## Key Concepts

### Connector States

| State | Meaning |
|-------|---------|
| `NEW` | Created but not connected |
| `CONNECTING` | Connection in progress |
| `CONNECTED` | Active — can create CLDs and publish |
| `CONNECT_ERROR` | Connection failed — check `connection_error` |
| `DISCONNECTING` | Disconnection in progress |
| `DISCONNECTED` | Disconnected — can reconnect |
| `DISCONNECT_ERROR` | Disconnection failed |
| `DELETED` | Connector has been dropped (permanent, no UNDROP) |

### Catalog-Linked Databases

When you mount an SAP data product, Snowflake creates a catalog-linked database (CLD). Key details:
- A read-only `snowflake$` schema is auto-created with **Semantic Views** from the SAP CSN (if the data product includes a CSN document)
- Semantic Views enable **Cortex Analyst** natural-language querying of SAP data
- `SYNC_INTERVAL_SECONDS` controls how often schema changes are discovered (30–86400 seconds)
- CLDs do **not** support `UNDROP`
- Share-back must be disabled and all CLDs must be dropped before disconnecting a connector
- Use `SYSTEM$CATALOG_LINK_STATUS` and `SYSTEM$GET_CATALOG_LINKED_DATABASE_CONFIG` to check CLD health

### Publishing Requirements

To publish Snowflake data back to SAP BDC:
- Tables must be **Iceberg V3 with copy-on-write** enabled (`ENABLE_ICEBERG_MERGE_ON_READ = FALSE`)
- Tables must use **Snowflake as the Iceberg catalog** (`CATALOG = 'SNOWFLAKE'`)
- `STORAGE_SERIALIZATION_POLICY` must be set to `'COMPATIBLE'`
- Each data product maps to a single dedicated database
- A CSN Interop v1.0 JSON document describing the schema is required
- Standard FDN tables can be converted to Iceberg V3 via CTAS or Dynamic Iceberg Tables (the skill guides this)

## Privileges

| Privilege | Scope | Required For |
|-----------|-------|-------------|
| `CREATE ZEROCOPY CONNECTOR` | Schema | Creating a connector |
| `OPERATE` | Connector | Connecting, disconnecting, and publishing data product (SYSTEM$SAP_PUBLISH_DATA_PRODUCT) |
| `USAGE` | Connector | Creating CLDs (also requires CREATE DATABASE on account), adding/removing shares (also requires OWNERSHIP on share) |
| `MODIFY` | Connector | Setting properties (e.g., SHARE_BACK) |
| `MONITOR` | Connector | Describing connector, showing connectors, listing shares |
| `OWNERSHIP` | Connector | Renaming or dropping |
| `CREATE DATABASE` | Account | Creating catalog-linked databases (also requires USAGE on connector) |
| `CREATE SHARE` | Account | Publishing data to SAP BDC |

## Key SQL Commands

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
| `SELECT SYSTEM$SAP_PUBLISH_DATA_PRODUCT(...)` | Publish data product |
| `SELECT SYSTEM$CATALOG_LINK_STATUS('<cld_name>')` | Check CLD link status |
| `SELECT SYSTEM$GET_CATALOG_LINKED_DATABASE_CONFIG('<cld_name>')` | Check CLD configuration |
| `ALTER DATABASE <cld_name> SUSPEND DISCOVERY` | Suspend CLD auto-discovery |
| `ALTER DATABASE <cld_name> RESUME DISCOVERY` | Resume CLD auto-discovery (forces immediate sync) |
| `ALTER DATABASE <cld_name> UPDATE LINKED_CATALOG SET SYNC_INTERVAL_SECONDS = <n>` | Change CLD sync interval |
| `ALTER ICEBERG TABLE <table> REFRESH` | Refresh individual Iceberg table metadata |
| `ALTER ZEROCOPY CONNECTOR <name> DISCONNECT` | Disconnect (disable share-back and drop CLDs first) |
| `DROP ZEROCOPY CONNECTOR <name>` | Drop connector |

## Documentation Links

- [About SAP and Snowflake Zero-Copy Integration](https://docs.snowflake.com/en/user-guide/data-integration/zero-copy/about-sap-snowflake)
- [Setup Tasks](https://docs.snowflake.com/en/user-guide/data-integration/zero-copy/sap-sql/setup-tasks)
- [Set Up Zerocopy Connector](https://docs.snowflake.com/en/user-guide/data-integration/zero-copy/sap-sql/setup)
- [SAP Snowflake (Greenfield)](https://docs.snowflake.com/en/user-guide/data-integration/zero-copy/sap-sql/setup-sap-snowflake)
- [SAP BDC Connect for Snowflake (Brownfield)](https://docs.snowflake.com/en/user-guide/data-integration/zero-copy/sap-sql/setup-sap-bdc)
- [Explore Data Products](https://docs.snowflake.com/en/user-guide/data-integration/zero-copy/sap-sql/explore-data-products)
- [Publish Data](https://docs.snowflake.com/en/user-guide/data-integration/zero-copy/sap-sql/publish-data)
- [Security & Privileges](https://docs.snowflake.com/en/user-guide/data-integration/zero-copy/sap-sql/security)
- [CSN Interop Specification](https://sap.github.io/csn-interop-specification/)
- [ALTER DATABASE (catalog-linked)](https://docs.snowflake.com/en/sql-reference/sql/alter-database-catalog-linked)
- [ALTER ICEBERG TABLE ... REFRESH](https://docs.snowflake.com/en/sql-reference/sql/alter-iceberg-table-refresh)
