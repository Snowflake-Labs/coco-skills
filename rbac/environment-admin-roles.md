---
name: environment-admin-roles
description: "Create Environment Admin roles for logical separation of Dev/Test/Prod within a single account. Use when: single account with multiple environments, account-per-domain setup, need to delegate environment administration."
---

# Environment Admin Roles

Account-level roles that provide logical separation between environments (Dev/Test/Prod) when they share the same Snowflake account.

## When You Need This

Environment Admin Roles are required when:
- **Option A**: Single account with all environments
- **Option C**: Account per business domain (environments share within each BU's account)

Skip this layer if environments are already in separate accounts (Options B or D).

## Purpose

Environment Admin Roles provide:
- **Logical isolation** - Dev admins can't accidentally modify Prod
- **Delegated administration** - Environment teams manage their own space
- **Clear boundaries** - All objects in an environment share a naming prefix

```
SYSADMIN
    ↓ creates DB/WH, transfers ownership
DEV_SYSADMIN / TST_SYSADMIN / PRD_SYSADMIN
    ↓ owns and manages
Environment databases, warehouses, roles
```

## Role Structure

For each environment, create a parallel set of admin roles:

| Role | Purpose |
|------|---------|
| `<ENV>_SYSADMIN` | Owns databases and warehouses for the environment. Does NOT manage roles. |
| `<ENV>_RBAC` | Manages grants and role hierarchy within the environment |
| `<ENV>_READER` | Read-only access path. Granted DB_R roles. Warehouse access for users within this environment. SYSADMIN inherits this. |

Common environment prefixes:
- `DEV` or `D` - Development
- `TST` or `T` - Test
- `UAT` or `U` - User Acceptance Testing
- `PRD` or `P` - Production

## Role Hierarchy

```
SYSADMIN ─────────────────────────────────────────────────────┐
    ↓ granted                                                 │
PRD_SYSADMIN ← TST_SYSADMIN ← DEV_SYSADMIN                   │
    ↓ granted                                                 │
PRD_READER ← TST_READER ← DEV_READER (DB_R + own warehouse)  │
                                                              │
SECURITYADMIN ────────────────────────────────────────────────┘
    ↓ granted
PRD_RBAC ← TST_RBAC ← DEV_RBAC
```

**Note**: Environment roles roll up to their OOB counterparts so central admins retain access when needed. Cross-environment access requires escalating to the OOB role (SYSADMIN, SECURITYADMIN) - environment admin roles should never grant across environment boundaries.

---

## SQL Template

### Variables
```sql
SET env = 'DEV';  -- Change for each environment: DEV, TST, PRD

SET envSysadmin = $env || '_SYSADMIN';
SET envRbac = $env || '_RBAC';
SET envReader = $env || '_READER';
```

### Create Environment Admin Roles
```sql
USE ROLE USERADMIN;

CREATE ROLE IF NOT EXISTS IDENTIFIER($envSysadmin);
CREATE ROLE IF NOT EXISTS IDENTIFIER($envRbac);
CREATE ROLE IF NOT EXISTS IDENTIFIER($envReader);
```

### Grant to OOB Roles
```sql
USE ROLE SECURITYADMIN;

-- READER granted to SYSADMIN (SYSADMIN inherits read + warehouse access)
GRANT ROLE IDENTIFIER($envReader) TO ROLE IDENTIFIER($envSysadmin);

-- Environment roles grant up to OOB roles
GRANT ROLE IDENTIFIER($envSysadmin) TO ROLE SYSADMIN;
GRANT ROLE IDENTIFIER($envRbac) TO ROLE SECURITYADMIN;
GRANT ROLE IDENTIFIER($envReader) TO ROLE SYSADMIN;  -- Central admin inherits all read access
```

### Grant Account-Level Access Roles
Environment admins typically need certain account-level privileges. Grant via the access roles created in `oob-account-roles`:

```sql
USE ROLE SECURITYADMIN;

GRANT ROLE _AR_EXEC_TASK TO ROLE IDENTIFIER($envSysadmin);
GRANT ROLE _AR_VIEW_AUSG TO ROLE IDENTIFIER($envSysadmin);
```

---

## Creating Environment Objects

Once environment admin roles exist, SYSADMIN creates objects and transfers ownership:

### Database Creation Pattern
```sql
SET env = 'DEV';
SET dbName = $env || '_SALES_RAW';
SET envSysadmin = $env || '_SYSADMIN';

USE ROLE SYSADMIN;

CREATE DATABASE IF NOT EXISTS IDENTIFIER($dbName);

GRANT OWNERSHIP ON DATABASE IDENTIFIER($dbName) TO ROLE IDENTIFIER($envSysadmin) COPY CURRENT GRANTS;
```

### Warehouse Creation Pattern
```sql
SET env = 'DEV';
SET whName = $env || '_SALES_INGEST';
SET envSysadmin = $env || '_SYSADMIN';

USE ROLE SYSADMIN;

CREATE WAREHOUSE IF NOT EXISTS IDENTIFIER($whName)
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE;

GRANT OWNERSHIP ON WAREHOUSE IDENTIFIER($whName) TO ROLE IDENTIFIER($envSysadmin) COPY CURRENT GRANTS;
```

---

## Naming Convention

All objects within an environment should be prefixed with the environment code:

| Object Type | Pattern | Example |
|-------------|---------|---------|
| Database | `<ENV>_<DOMAIN>_<ZONE>` | `DEV_SALES_RAW` |
| Warehouse | `<ENV>_<DOMAIN>_<WORKLOAD>` | `DEV_SALES_INGEST` |
| Functional Role | `<ENV>_<DOMAIN>_<ROLE>` | `DEV_SALES_CREATE` |

This ensures:
- Objects sort together by environment in UI lists
- Clear visual identification of environment
- Prevents accidental cross-environment operations

---

## Key Points

- **Logical separation only** - Environment admin roles don't provide hard isolation; that requires separate accounts
- **SYSADMIN creates, then transfers** - Central admin creates databases/warehouses, transfers ownership to environment admin
- **Naming prefix is critical** - All objects must include environment in name to maintain clarity
- **Roll up to OOB roles** - Environment admins granted to their OOB counterparts so central team retains access
- **Grant access roles, not privileges** - Use `_AR_` roles from `oob-account-roles`, don't grant ACCOUNTADMIN privileges directly
