---
name: schema-access-roles
description: "Create and manage Schema Access Roles - Database Roles providing tiered Read/Write/Create access to a schema. Use when: setting up schema permissions, creating database roles for schema access, implementing RBAC at schema level."
---

# Schema Access Roles

Database Roles that provide three tiers of access to a schema's objects.

## Why Database Roles (Not Account Roles)

Schema Access Roles must be Database Roles, not Account Roles:

| Benefit | Explanation |
|---------|-------------|
| **Scoped by design** | Database Roles cannot see outside their database - enforces least-privilege automatically |
| **Clone-friendly** | Cloning a database clones its Database Roles - no role rebuilding required |
| **No UI clutter** | Database Roles don't appear in the account role list, keeping it clean |
| **Ownership safety** | Cannot be used with USE ROLE, preventing accidental object ownership via "creator owns" default |

**CRITICAL: Database Roles must NEVER own objects.** Because Database Roles cannot see outside their database, they cannot own:
- Views referencing other databases
- Dynamic tables (require warehouse, which lives outside the database)
- Tasks (require warehouse)
- Any object with cross-database dependencies

This was always suboptimal but now makes certain patterns completely impossible. Objects should be owned by an Account Role (typically a dedicated admin role), not the Database Role that has CREATE privileges.

**Why Schema level?** Object-level permissions are too granular (unmanageable at scale), database-level is too broad (violates least-privilege). Schema is the sweet spot - logical groupings of related objects with cohesive access needs.

## Access Tiers

| Tier | Prefix | Privileges | Use Case |
|------|--------|------------|----------|
| READ | `<schema>_R` | SELECT on data objects | Analysts, reporting |
| READ-WRITE | `<schema>_RW` | INSERT, UPDATE, DELETE + operational | ETL, applications |
| CREATE | `<schema>_C` | DDL (CREATE objects, but NOT ownership) | Developers, admins |

## Role Hierarchy

```
<schema>_C  (Create - highest)
    ↓ inherits
<schema>_RW  (Read-Write)
    ↓ inherits
<schema>_R  (Read - lowest)
```

## Workflow

### Step 1: Gather Requirements
Ask user for:
- Database name
- Schema name (new or existing)

**MANAGED ACCESS is required.** Always create schemas with MANAGED ACCESS. Without it, any user with CREATE privileges can grant access to objects they create, bypassing centralized access control. Only in rare legacy migration scenarios should non-MANAGED ACCESS be considered.

### Step 2: Generate SQL
Use the template below, substituting database and schema names.

### Step 3: Execute
Run SQL in Snowflake with appropriate admin privileges.

---

## SQL Template

### Variables
```sql
SET dbNm = '<DATABASE_NAME>';
SET scNm = '<SCHEMA_NAME>';
SET sarR = $scNm || '_R';
SET sarRW = $scNm || '_RW';
SET sarC = $scNm || '_C';
USE DATABASE IDENTIFIER($dbNm);
```

### Create Schema (MANAGED ACCESS)
```sql
CREATE SCHEMA IF NOT EXISTS IDENTIFIER($scNm) WITH MANAGED ACCESS;
```

### READ Role (<schema>_R)
```sql
CREATE DATABASE ROLE IF NOT EXISTS IDENTIFIER($sarR);
GRANT USAGE, MONITOR ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);

-- Current objects: Tables and table-like
GRANT SELECT ON ALL TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);
GRANT SELECT ON ALL VIEWS IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);
GRANT SELECT ON ALL MATERIALIZED VIEWS IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);
GRANT SELECT ON ALL EXTERNAL TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);
GRANT SELECT ON ALL DYNAMIC TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);
GRANT SELECT ON ALL ICEBERG TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);
GRANT SELECT ON ALL EVENT TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);
GRANT SELECT ON ALL STREAMS IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);

-- Current objects: Executable/callable
GRANT USAGE ON ALL FUNCTIONS IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);

-- Future objects: Tables and table-like
GRANT SELECT ON FUTURE TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);
GRANT SELECT ON FUTURE VIEWS IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);
GRANT SELECT ON FUTURE MATERIALIZED VIEWS IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);
GRANT SELECT ON FUTURE EXTERNAL TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);
GRANT SELECT ON FUTURE DYNAMIC TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);
GRANT SELECT ON FUTURE ICEBERG TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);
GRANT SELECT ON FUTURE EVENT TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);
GRANT SELECT ON FUTURE STREAMS IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);

-- Future objects: Executable/callable
GRANT USAGE ON FUTURE FUNCTIONS IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarR);
```

### READ-WRITE Role (<schema>_RW)
```sql
CREATE DATABASE ROLE IF NOT EXISTS IDENTIFIER($sarRW);

-- DML on tables
GRANT INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT INSERT, UPDATE, DELETE, TRUNCATE ON ALL ICEBERG TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT INSERT ON ALL EVENT TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);

-- Operational: Sequences, formats, stages
GRANT USAGE ON ALL SEQUENCES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT USAGE ON ALL FILE FORMATS IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT USAGE ON ALL STAGES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT READ, WRITE ON ALL STAGES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);

-- Operational: Pipelines and automation
GRANT OPERATE, MONITOR ON ALL TASKS IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT OPERATE ON ALL DYNAMIC TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT OPERATE ON ALL ALERTS IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT MONITOR, OPERATE ON ALL PIPES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);

-- Operational: Secrets and Git
GRANT USAGE ON ALL SECRETS IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT READ ON ALL GIT REPOSITORIES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);

-- Operational: SPCS (Snowpark Container Services)
GRANT READ ON ALL IMAGE REPOSITORIES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT OPERATE, MONITOR ON ALL SERVICES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);

-- Future DML on tables
GRANT INSERT, UPDATE, DELETE, TRUNCATE ON FUTURE TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT INSERT, UPDATE, DELETE, TRUNCATE ON FUTURE ICEBERG TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT INSERT ON FUTURE EVENT TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);

-- Future operational: Sequences, formats, stages
GRANT USAGE ON FUTURE SEQUENCES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT USAGE ON FUTURE FILE FORMATS IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT USAGE ON FUTURE STAGES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT READ, WRITE ON FUTURE STAGES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);

-- Future operational: Pipelines and automation
GRANT OPERATE, MONITOR ON FUTURE TASKS IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT OPERATE ON FUTURE DYNAMIC TABLES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT OPERATE ON FUTURE ALERTS IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT MONITOR, OPERATE ON FUTURE PIPES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);

-- Future operational: Secrets and Git
GRANT USAGE ON FUTURE SECRETS IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT READ ON FUTURE GIT REPOSITORIES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);

-- Future operational: SPCS
GRANT READ ON FUTURE IMAGE REPOSITORIES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT OPERATE, MONITOR ON FUTURE SERVICES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);

-- Procedures (can modify data under owner's rights)
GRANT USAGE ON ALL PROCEDURES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT USAGE ON FUTURE PROCEDURES IN SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarRW);
```

### CREATE Role (<schema>_C)
**NOTE:** This role grants the ability to CREATE objects, but the Database Role must NEVER own them. Objects created while using an Account Role that has this Database Role granted will be owned by that Account Role, not the Database Role.

```sql
CREATE DATABASE ROLE IF NOT EXISTS IDENTIFIER($sarC);

-- Tables and table-like objects
GRANT CREATE TABLE ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE VIEW ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE MATERIALIZED VIEW ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE EXTERNAL TABLE ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE DYNAMIC TABLE ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE ICEBERG TABLE ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE EVENT TABLE ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE STREAM ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);

-- Code objects
GRANT CREATE FUNCTION ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE PROCEDURE ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);

-- Pipeline and automation
GRANT CREATE TASK ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE ALERT ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE PIPE ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);

-- Data loading infrastructure
GRANT CREATE STAGE ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE FILE FORMAT ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE SEQUENCE ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);

-- Git and secrets
GRANT CREATE SECRET ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE GIT REPOSITORY ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);

-- SPCS (Snowpark Container Services)
GRANT CREATE IMAGE REPOSITORY ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE SERVICE ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE SNAPSHOT ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);

-- Applications
GRANT CREATE STREAMLIT ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE NOTEBOOK ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);

-- AI/ML and Cortex
GRANT CREATE CORTEX SEARCH SERVICE ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE SEMANTIC VIEW ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE MODEL ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE DATASET ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);

-- Governance/policy (optional - may restrict to admin schemas)
GRANT CREATE NETWORK RULE ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE TAG ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE MASKING POLICY ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
GRANT CREATE ROW ACCESS POLICY ON SCHEMA IDENTIFIER($scNm) TO DATABASE ROLE IDENTIFIER($sarC);
```

### Establish Hierarchy
```sql
GRANT DATABASE ROLE IDENTIFIER($sarR) TO DATABASE ROLE IDENTIFIER($sarRW);
GRANT DATABASE ROLE IDENTIFIER($sarRW) TO DATABASE ROLE IDENTIFIER($sarC);
```

---

## Key Points

- **MANAGED ACCESS**: Required. Only schema owner can grant access, not object owners. Without MANAGED ACCESS, access control becomes fragmented and unauditable.
- **Future Grants**: Essential for new objects to automatically receive proper permissions.
- **Hierarchy**: Create role inherits Read-Write, Read-Write inherits Read. Grant at lowest needed level.
- **Naming**: `<schema>_R`, `<schema>_RW`, `<schema>_C` suffixes make roles self-documenting.
- **CREATE ≠ OWN**: The CREATE role grants DDL privileges, but Database Roles must NEVER own objects. Ownership stays with an Account Role.
