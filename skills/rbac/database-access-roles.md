---
name: database-access-roles
description: "Create and manage Database Access Roles - Database Roles aggregating schema-level access across an entire database. Use when: setting up database-wide permissions, aggregating schema access roles."
---

# Database Access Roles

Database Roles that aggregate schema-level access across an entire database, providing a single point of grant for consumers.

## Relationship to Schema Access Roles

Database Access Roles sit **above** Schema Access Roles in the hierarchy:

```
Account Functional Roles (AFR)
    ↓ granted
Database Access Roles (DB_R, DB_RW, DB_C)  ← This skill
    ↓ granted
Schema Access Roles (<schema>_R, <schema>_RW, <schema>_C)  ← schema-access-roles skill
    ↓ privileges on
Schema Objects (tables, views, etc.)
```

Database Access Roles **inherit** permissions from Schema Access Roles - they receive no direct grants themselves.

## Why Database Access Roles?

| Problem | Solution |
|---------|----------|
| Granting access to many schemas individually is tedious | Aggregate all schema roles into one database role |
| Account roles shouldn't hold privileges directly | Database roles wrap privileges; account roles hold database roles |
| Consumers need a single "reader" entrypoint | DB_R provides read access to entire database |
| Clone-friendly access | Database roles clone with the database |

## Access Tiers

| Tier | Role Name | Aggregates | Use Case |
|------|-----------|------------|----------|
| READ | DB_R | All <schema>_R roles in database | Data consumers, analysts |
| READ-WRITE | DB_RW | All <schema>_RW roles in database | ETL processes, applications |
| CREATE | DB_C | All <schema>_C roles in database | Developers, CI/CD |

## Role Hierarchy

```
DB_C  (Create - highest)
  ↓ inherits
DB_RW  (Read-Write)
  ↓ inherits
DB_R  (Read - lowest)
  ↓ aggregates
<schema1>_R, <schema2>_R, ...
```

## Integration with Account Functional Roles

Database Access Roles bridge the gap between schema-level Database Roles and account-level Functional Roles:

| Account Functional Role | Database Role Granted | Purpose |
|------------------------|----------------------|---------|
| `<PREFIX>_READER` | DB_R | Database consumers |
| `<PREFIX>_ETL` | DB_RW | Data engineers |
| `<PREFIX>_SYSADMIN` | DB_C | Deployment/CI-CD |
| `<PREFIX>_ADMIN` | (owns database) | Delegated administration |
| `<PREFIX>_RBAC` | (owns DB roles) | Access governance |

**Naming Convention:**
- `<PREFIX>` = Derived from database name components (e.g., `ENT_SALES` for database `ENT_SALES_RAW`)

## Workflow

Database Access Roles are created **with the database**, before any schemas exist. Schema Access Roles are then granted up to them as schemas are added.

### Step 1: Gather Requirements
Ask user for:
- Database name
- Associated Account Functional Role names (or derive from naming convention)

### Step 2: Generate SQL
Use the template below.

### Step 3: Execute
Run as the Delegated Admin role that owns the database.

### Step 4: As Schemas Are Added
When schemas are created (see `schema-access-roles` skill), their Schema Access Roles get granted up to these Database Access Roles.

---

## SQL Template

### Variables
```sql
SET dbNm = '<DATABASE_NAME>';
SET prefixNm = '<PREFIX>';  -- For deriving Account Functional Role names

SET dbrR = 'DB_R';
SET dbrRW = 'DB_RW';
SET dbrC = 'DB_C';



SET afrAdmin  = $prefixNm || '_SYSADMIN';
SET afrCreate = $prefixNm || '_SYSADMIN';
SET afrETL    = $prefixNm || '_ETL';
SET afrRbac   = $prefixNm || '_RBAC';
SET afrReader = $prefixNm || '_READER';
```

### Review Configuration
```sql
SELECT 
   $dbNm       AS "Database Name"
  ,$afrAdmin   AS "Delegated Admin Role"
  ,$afrCreate  AS "Deploy/Create Role"
  ,$afrETL     AS "ETL Role"
  ,$afrRbac    AS "RBAC Owner Role"
  ,$afrReader  AS "Data Product Reader Role"
;
```

### Create Database Access Roles
```sql
USE ROLE IDENTIFIER($afrAdmin);
USE DATABASE IDENTIFIER($dbNm);

CREATE DATABASE ROLE IF NOT EXISTS IDENTIFIER($dbrR);
CREATE DATABASE ROLE IF NOT EXISTS IDENTIFIER($dbrRW);
CREATE DATABASE ROLE IF NOT EXISTS IDENTIFIER($dbrC);
```

### Transfer Ownership to RBAC Role
```sql
GRANT OWNERSHIP ON DATABASE ROLE IDENTIFIER($dbrR) TO ROLE IDENTIFIER($afrRbac) COPY CURRENT GRANTS;
GRANT OWNERSHIP ON DATABASE ROLE IDENTIFIER($dbrRW) TO ROLE IDENTIFIER($afrRbac) COPY CURRENT GRANTS;
GRANT OWNERSHIP ON DATABASE ROLE IDENTIFIER($dbrC) TO ROLE IDENTIFIER($afrRbac) COPY CURRENT GRANTS;
```

### Grant Database Roles to Account Functional Roles
```sql
GRANT DATABASE ROLE IDENTIFIER($dbrR) TO ROLE IDENTIFIER($afrReader);
GRANT DATABASE ROLE IDENTIFIER($dbrRW) TO ROLE IDENTIFIER($afrETL);
GRANT DATABASE ROLE IDENTIFIER($dbrC) TO ROLE IDENTIFIER($afrCreate);
```

### Aggregate Schema Access Roles (per schema)
For each schema in the database, grant its Schema Access Roles to the corresponding Database Access Role:

```sql
SET scNm = '<SCHEMA_NAME>';
SET sarR = $scNm || '_R';
SET sarRW = $scNm || '_RW';
SET sarC = $scNm || '_C';

GRANT DATABASE ROLE IDENTIFIER($sarR) TO DATABASE ROLE IDENTIFIER($dbrR);
GRANT DATABASE ROLE IDENTIFIER($sarRW) TO DATABASE ROLE IDENTIFIER($dbrRW);
GRANT DATABASE ROLE IDENTIFIER($sarC) TO DATABASE ROLE IDENTIFIER($dbrC);
```

Repeat the above block for each schema.

---

## Ownership Model

| Object | Owner | Rationale |
|--------|-------|-----------|
| Database | `<PREFIX>_SYSADMIN` | Delegated admin controls database settings |
| Database Access Roles | `<PREFIX>_RBAC` | Separates access governance from administration |
| Schema Access Roles | `<PREFIX>_RBAC` | Consistent ownership for all access roles |

**Why separate ADMIN and RBAC roles?**
- ADMIN handles structural changes (DDL, database settings)
- RBAC handles access governance (who can read/write what)
- Separation of duties - the person deploying code shouldn't control who accesses data

## Key Points

- **No Direct Grants**: Database Access Roles receive no privilege grants directly - they only aggregate Schema Access Roles.
- **Single Entrypoint**: Consumers get one database role (DB_R) that provides read access to the entire database.
- **Clone-Friendly**: When you clone a database, all Database Roles and their grants clone with it.
- **RBAC Ownership**: The RBAC role owns the access roles, enabling governance teams to manage access without admin privileges.
- **Account Functional Roles**: These are created separately (by USERADMIN/SECURITYADMIN) and receive the database roles as grants.
