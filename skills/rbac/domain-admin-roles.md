---
name: domain-admin-roles
description: "Create Domain Admin roles for federated team administration. Use when: teams own multiple data products, federated admin model, need to delegate object ownership within databases managed by a higher level."
---

# Domain Admin Roles

Account-level roles that provide delegated administration for a business domain (team) that owns multiple data products within databases and schemas managed by a higher level in the hierarchy.

## When You Need This

Domain Admin Roles are required when:
- A team owns **multiple data products**
- You have a **federated admin model** (not centralized)
- You want teams to manage their own objects without owning the infrastructure

**Skip this layer if**:
- A team owns only **one data product** - use Data Product Admin roles instead (Level 4)
- You have **centralized administration** - go directly to Database/Schema roles

## Key Distinction from Environment Admins

| Aspect | Environment Admin | Domain Admin |
|--------|-------------------|--------------|
| **Owns** | Databases, schemas, warehouses | Schema-bound objects only (tables, views, tasks, etc.) |
| **Infrastructure** | Creates and controls | Uses what's provisioned for them |
| **Scope** | All objects in environment | Objects within their domain's schemas |

Domain admins work **within** infrastructure owned by a higher level (Account or Environment roles).

## Role Structure

For each domain, create:

| Role | Purpose |
|------|---------|
| `<DOMAIN>_SYSADMIN` | Owns schema-bound objects (tables, views, procedures, tasks, etc.). Does NOT own databases, schemas, or warehouses. |
| `<DOMAIN>_RBAC` | Manages grants and database role hierarchy within the domain |
| `<DOMAIN>_READER` | Read-only access path. Granted DB_R roles. Warehouse access for domain's own users. SYSADMIN inherits this. |

If using environment separation (Options A or C), include environment in the name:

| Role | Example |
|------|---------|
| `<ENV>_<DOMAIN>_SYSADMIN` | `DEV_SALES_SYSADMIN`, `PRD_SALES_SYSADMIN` |
| `<ENV>_<DOMAIN>_RBAC` | `DEV_SALES_RBAC`, `PRD_SALES_RBAC` |
| `<ENV>_<DOMAIN>_READER` | `DEV_SALES_READER`, `PRD_SALES_READER` |

## Role Hierarchy

```
ENV_SYSADMIN (or SYSADMIN if no env layer)
    ↓ owns databases, schemas, warehouses
ENV_DOMAIN_SYSADMIN
    ↓ granted
ENV_DOMAIN_READER
    ↓ granted DB_R roles and warehouse access
    ↓ allows prod read-only usage without write permissions

ENV_RBAC (or SECURITYADMIN if no env layer)
    ↓ grants role management to domain
ENV_DOMAIN_RBAC
    ↓ owns database roles (<schema>_R, <schema>_RW, <schema>_C, DB_R, DB_RW, DB_C)
    ↓ manages grants within domain
```

**Why READER?** In production, domain teams often need read-only access. SYSADMIN inherits READER, so users with write needs use SYSADMIN, while read-only users use READER directly.

---

## SQL Templates

### Pattern A: Domain Under Environment (Single Account, Multi-Env)

Domain roles grant up to environment roles.

```sql
-- Variables
SET env = 'DEV';
SET domain = 'SALES';

SET domainSysadmin = $env || '_' || $domain || '_SYSADMIN';
SET domainRbac = $env || '_' || $domain || '_RBAC';
SET domainReader = $env || '_' || $domain || '_READER';

-- Parent roles (environment level)
SET parentSysadmin = $env || '_SYSADMIN';
SET parentRbac = $env || '_RBAC';
SET parentReader = $env || '_READER';

-- Create domain roles
USE ROLE USERADMIN;

CREATE ROLE IF NOT EXISTS IDENTIFIER($domainSysadmin);
CREATE ROLE IF NOT EXISTS IDENTIFIER($domainRbac);
CREATE ROLE IF NOT EXISTS IDENTIFIER($domainReader);

-- Build role hierarchy
USE ROLE SECURITYADMIN;

-- READER granted to SYSADMIN (SYSADMIN inherits read access)
GRANT ROLE IDENTIFIER($domainReader) TO ROLE IDENTIFIER($domainSysadmin);

-- Domain roles grant up to environment level
GRANT ROLE IDENTIFIER($domainSysadmin) TO ROLE IDENTIFIER($parentSysadmin);
GRANT ROLE IDENTIFIER($domainRbac) TO ROLE IDENTIFIER($parentRbac);
GRANT ROLE IDENTIFIER($domainReader) TO ROLE IDENTIFIER($parentReader);

-- Grant account-level access roles
GRANT ROLE _AR_EXEC_TASK TO ROLE IDENTIFIER($domainSysadmin);
GRANT ROLE _AR_VIEW_AUSG TO ROLE IDENTIFIER($domainSysadmin);
GRANT ROLE _AR_APPLY_TAG TO ROLE IDENTIFIER($domainSysadmin);
```

### Pattern B: Domain Under Account (Multi-Account by Env, No Env Layer)

Domain roles grant directly to OOB account roles.

```sql
-- Variables (no env prefix needed - environments are separate accounts)
SET domain = 'SALES';

SET domainSysadmin = $domain || '_SYSADMIN';
SET domainRbac = $domain || '_RBAC';
SET domainReader = $domain || '_READER';

-- Parent roles (OOB account roles)
SET parentSysadmin = 'SYSADMIN';
SET parentRbac = 'SECURITYADMIN';

-- Create domain roles
USE ROLE USERADMIN;

CREATE ROLE IF NOT EXISTS IDENTIFIER($domainSysadmin);
CREATE ROLE IF NOT EXISTS IDENTIFIER($domainRbac);
CREATE ROLE IF NOT EXISTS IDENTIFIER($domainReader);

-- Build role hierarchy
USE ROLE SECURITYADMIN;

-- READER granted to SYSADMIN (SYSADMIN inherits read access)
GRANT ROLE IDENTIFIER($domainReader) TO ROLE IDENTIFIER($domainSysadmin);

-- Grant to account level

GRANT ROLE IDENTIFIER($domainSysadmin) TO ROLE IDENTIFIER($parentSysadmin);
GRANT ROLE IDENTIFIER($domainRbac) TO ROLE IDENTIFIER($parentRbac);
GRANT ROLE IDENTIFIER($domainReader) TO ROLE SYSADMIN;  -- No parent reader at OOB level

-- Grant account-level access roles
GRANT ROLE _AR_EXEC_TASK TO ROLE IDENTIFIER($domainSysadmin);
GRANT ROLE _AR_VIEW_AUSG TO ROLE IDENTIFIER($domainSysadmin);
GRANT ROLE _AR_APPLY_TAG TO ROLE IDENTIFIER($domainSysadmin);
```

---

## Connecting to Database Roles

**Schema ownership always sits at Environment or Account level** - never at Domain or Data Product level.

### Data Product Level (Object Ownership)
Data Product SYSADMIN:
- Receives CREATE privileges on schemas via database role hierarchy (DB_C ← <schema>_C)
- **Owns the objects they create** (tables, views, procedures, etc.)
- Does NOT own the schema itself

Data Product READER:
- Receives READ access via DB_R
- Warehouse access (direct or via access roles) granted here
- SYSADMIN inherits through being granted READER

```
DataProduct_SYSADMIN
    ↓ granted
DataProduct_READER
    ↓ granted
DB_R (read access) + Warehouse access
```

```
DataProduct_SYSADMIN
    ↓ granted
DB_C (Database Role)
    ↓ granted
<schema>_C (Database Role)
    ↓ has CREATE privileges on
Schema (owned by ENV_SYSADMIN or SYSADMIN)
    ↓ creates
Objects (owned by DataProduct_SYSADMIN)
```

### Domain Level (Inherited Access)
Domain SYSADMIN:
- Granted Data Product SYSADMIN roles
- **Inherits object ownership** and **read/warehouse access** through the hierarchy
- Has **no direct schema-level privileges**

```
Domain_SYSADMIN
    ↓ granted
Domain_READER
    ↓ granted
DataProduct_READER (inherits DB_R)
```

See `database-access-roles` and `schema-access-roles` for the SQL templates that connect these layers.

---

## Warehouse Access

Warehouses are owned by a higher level. Warehouse access is granted to the **domain's own READER** role for consumption within that domain. This is critical:

**Consumers bring their own compute.** When users from Domain A query data from Domain B:
- Domain B's READER grants **data access only** (DB_R)
- Domain A's READER provides **warehouse access**
- Secondary roles aggregate both at runtime

Bundling warehouse access with data product access creates a perverse incentive - popular data products would drive costs to the producing domain, discouraging data sharing.

```sql
-- Domain's own warehouse for its own users
GRANT USAGE ON WAREHOUSE DEV_SALES_TRNFRM TO ROLE DEV_SALES_READER;
```

For multiple warehouses or multiple privileges, use an access role granted to READER - see `warehouse-access-roles`.

---

## Data Product Admin Roles (Level 4)

The same pattern applies when you need an additional layer **below** domain for individual data products.

### When to Use Data Product Admins

| Scenario | Use |
|----------|-----|
| Team owns many data products | Domain Admin only - the team IS the domain |
| Team owns one data product, no larger domain | Domain Admin only - call it whatever fits |
| Multiple teams under one domain | Both - Domain Admin for shared concerns, Data Product Admin per team |

### Structure

Data Product Admins are identical to Domain Admins in structure:
- `<ENV>_<DOMAIN>_<PRODUCT>_SYSADMIN` - owns schema-bound objects
- `<ENV>_<DOMAIN>_<PRODUCT>_RBAC` - manages grants

They sit below Domain Admins in the hierarchy:
```
ENV_DOMAIN_SYSADMIN
    ↓ granted (inherits object ownership)
ENV_DOMAIN_PRODUCT_SYSADMIN
    ↓ owns objects in those schemas
```

### Example: HR Domain with Product Teams

```
PRD_HR_SYSADMIN (domain level)
    ├── PRD_HR_PAYROLL_SYSADMIN (product team)
    ├── PRD_HR_RECRUITMENT_SYSADMIN (product team)
    └── PRD_HR_BENEFITS_SYSADMIN (product team)
```

Each product team owns objects in their schemas. The domain admin inherits across all products and can access all product areas when needed.

### Data Product SQL Templates

#### Pattern A: Data Product Under Domain (with Environment Layer)

```sql
-- Variables
SET env = 'DEV';
SET domain = 'HR';
SET product = 'PAYROLL';

SET productSysadmin = $env || '_' || $domain || '_' || $product || '_SYSADMIN';
SET productRbac = $env || '_' || $domain || '_' || $product || '_RBAC';
SET productReader = $env || '_' || $domain || '_' || $product || '_READER';

-- Parent roles (domain level)
SET parentSysadmin = $env || '_' || $domain || '_SYSADMIN';
SET parentRbac = $env || '_' || $domain || '_RBAC';
SET parentReader = $env || '_' || $domain || '_READER';

-- Create data product roles
USE ROLE USERADMIN;

CREATE ROLE IF NOT EXISTS IDENTIFIER($productSysadmin);
CREATE ROLE IF NOT EXISTS IDENTIFIER($productRbac);
CREATE ROLE IF NOT EXISTS IDENTIFIER($productReader);

-- Build role hierarchy
USE ROLE SECURITYADMIN;

-- READER granted to SYSADMIN (SYSADMIN inherits read access)
GRANT ROLE IDENTIFIER($productReader) TO ROLE IDENTIFIER($productSysadmin);

-- Grant to domain level
GRANT ROLE IDENTIFIER($productSysadmin) TO ROLE IDENTIFIER($parentSysadmin);
GRANT ROLE IDENTIFIER($productRbac) TO ROLE IDENTIFIER($parentRbac);
GRANT ROLE IDENTIFIER($productReader) TO ROLE IDENTIFIER($parentReader);

-- Grant account-level access roles
GRANT ROLE _AR_EXEC_TASK TO ROLE IDENTIFIER($productSysadmin);
GRANT ROLE _AR_VIEW_AUSG TO ROLE IDENTIFIER($productSysadmin);
```

#### Pattern B: Data Product Under Environment (No Domain Layer)

When a single team owns one data product, skip the domain layer.

```sql
-- Variables
SET env = 'DEV';
SET product = 'PAYROLL';

SET productSysadmin = $env || '_' || $product || '_SYSADMIN';
SET productRbac = $env || '_' || $product || '_RBAC';
SET productReader = $env || '_' || $product || '_READER';

-- Parent roles (environment level)
SET parentSysadmin = $env || '_SYSADMIN';
SET parentRbac = $env || '_RBAC';
SET parentReader = $env || '_READER';

-- Create data product roles
USE ROLE USERADMIN;

CREATE ROLE IF NOT EXISTS IDENTIFIER($productSysadmin);
CREATE ROLE IF NOT EXISTS IDENTIFIER($productRbac);
CREATE ROLE IF NOT EXISTS IDENTIFIER($productReader);

-- Build role hierarchy
USE ROLE SECURITYADMIN;

-- READER granted to SYSADMIN (SYSADMIN inherits read access)
GRANT ROLE IDENTIFIER($productReader) TO ROLE IDENTIFIER($productSysadmin);

-- Grant to environment level
GRANT ROLE IDENTIFIER($productSysadmin) TO ROLE IDENTIFIER($parentSysadmin);
GRANT ROLE IDENTIFIER($productRbac) TO ROLE IDENTIFIER($parentRbac);
GRANT ROLE IDENTIFIER($productReader) TO ROLE IDENTIFIER($parentReader);

-- Grant account-level access roles
GRANT ROLE _AR_EXEC_TASK TO ROLE IDENTIFIER($productSysadmin);
GRANT ROLE _AR_VIEW_AUSG TO ROLE IDENTIFIER($productSysadmin);
```

#### Pattern C: Data Product Under Account (No Domain, No Environment Layer)

Multi-account by environment setup with single team per data product.

```sql
-- Variables (no env or domain prefix)
SET product = 'PAYROLL';

SET productSysadmin = $product || '_SYSADMIN';
SET productRbac = $product || '_RBAC';
SET productReader = $product || '_READER';

-- Parent roles (OOB account roles)
SET parentSysadmin = 'SYSADMIN';
SET parentRbac = 'SECURITYADMIN';

-- Create data product roles
USE ROLE USERADMIN;

CREATE ROLE IF NOT EXISTS IDENTIFIER($productSysadmin);
CREATE ROLE IF NOT EXISTS IDENTIFIER($productRbac);
CREATE ROLE IF NOT EXISTS IDENTIFIER($productReader);

-- Build role hierarchy
USE ROLE SECURITYADMIN;

-- READER granted to SYSADMIN (SYSADMIN inherits read access)
GRANT ROLE IDENTIFIER($productReader) TO ROLE IDENTIFIER($productSysadmin);

-- Grant to account level
GRANT ROLE IDENTIFIER($productSysadmin) TO ROLE IDENTIFIER($parentSysadmin);
GRANT ROLE IDENTIFIER($productRbac) TO ROLE IDENTIFIER($parentRbac);
GRANT ROLE IDENTIFIER($productReader) TO ROLE SYSADMIN;  -- No parent reader at OOB level

-- Grant account-level access roles
GRANT ROLE _AR_EXEC_TASK TO ROLE IDENTIFIER($productSysadmin);
GRANT ROLE _AR_VIEW_AUSG TO ROLE IDENTIFIER($productSysadmin);
```

---

## Key Points

- **Domain owns objects, not infrastructure** - tables, views, tasks, procedures, but NOT databases, schemas, or warehouses
- **Infrastructure provisioned by higher level** - Environment or Account admin creates DBs, schemas, WHs
- **CREATE grants enable object ownership** - Domain sysadmin creates and owns objects via granted CREATE privileges
- **RBAC manages access roles** - Domain RBAC owns and manages the <schema>_R, <schema>_RW, <schema>_C, DB_R, DB_RW, DB_C database roles
- **Cross-domain access requires escalation** - To access another domain's objects, escalate to higher level role
- **Data Product Admins follow same pattern** - Just an additional layer below domain when needed for discrete teams
