---
name: warehouse-access-roles
description: "When to use (and not use) warehouse access roles. Challenges common over-application of this pattern."
---

# Warehouse Access Roles

## The Common Misconception

Many RBAC guides recommend creating warehouse access roles (`_WH_<warehouse>`) for every warehouse. **This is almost always unnecessary complexity.**

### Why This Pattern Gets Over-Applied
- Early Snowflake documentation showed access roles as a general pattern
- Consultants apply it universally without considering the actual requirement
- It feels "more secure" to have an intermediary role (it isn't)

### The Simple Rule
**If an access role contains a single grant, it shouldn't exist.**

Access roles exist to bucket multiple permissions together. A 1:1 mapping between access role and warehouse/privilege is just an extra layer of indirection with no benefit.

---

## When You DON'T Need Warehouse Access Roles

### Any Single Warehouse, Single Privilege Scenario

**Don't do this:**
```sql
CREATE ROLE _WH_FINANCE_TRNFRM;
GRANT USAGE ON WAREHOUSE FINANCE_TRNFRM TO ROLE _WH_FINANCE_TRNFRM;
GRANT ROLE _WH_FINANCE_TRNFRM TO ROLE FINANCE_SYSADMIN;
```

**Do this instead:**
```sql
GRANT USAGE ON WAREHOUSE FINANCE_TRNFRM TO ROLE FINANCE_SYSADMIN;
```

The access role adds nothing - it's a 1:1 wrapper around a single grant.

---

## The Cross-Domain Anti-Pattern

A common but **wrong** recommendation is to use warehouse access roles for cross-domain access.

**Example of what NOT to do:**
```
"Marketing needs to query Finance data, so grant them access to Finance's warehouse"
```

**Why this is wrong:**
- Creates an incentive AGAINST supplying useful data products
- Data producers become responsible for consumer compute costs
- Violates separation of concerns

**Correct approach:**
- Domains bring their own compute to data products
- Consumer grants themselves READ access to the data
- Consumer uses their OWN warehouse to query

```
MARKETING_ANALYST
    └── granted FINANCE.DB_R (read access to Finance data)
    └── uses MARKETING_QUERY warehouse (their own compute)
```

This creates the right incentives: data products are judged on data quality, not on providing free compute.

---

## When You DO Need Warehouse Access Roles

Access roles are warranted when **bucketing multiple permissions together**.

These roles must fit into the domain structure:
- **Ownership** transferred to the appropriate RBAC role (Domain or Data Product)
- **Granted** to the READER role at that level (SYSADMIN inherits through the hierarchy)

### Scenario 1: Suite of Specialized Warehouses
A set of Snowpark-optimized or high-compute warehouses available to a subset of users within a domain.

```sql
SET domain = 'DATASCIENCE';
SET domainRbac = $domain || '_RBAC';
SET domainReader = $domain || '_READER';
SET accessRole = $domain || '_WH_SNOWPARK_SUITE';

USE ROLE SECURITYADMIN;

CREATE ROLE IF NOT EXISTS IDENTIFIER($accessRole);

GRANT USAGE ON WAREHOUSE SNOWPARK_M TO ROLE IDENTIFIER($accessRole);
GRANT USAGE ON WAREHOUSE SNOWPARK_L TO ROLE IDENTIFIER($accessRole);
GRANT USAGE ON WAREHOUSE SNOWPARK_XL TO ROLE IDENTIFIER($accessRole);
GRANT USAGE ON WAREHOUSE SNOWPARK_HCM TO ROLE IDENTIFIER($accessRole);

GRANT OWNERSHIP ON ROLE IDENTIFIER($accessRole) TO ROLE IDENTIFIER($domainRbac);
GRANT ROLE IDENTIFIER($accessRole) TO ROLE IDENTIFIER($domainReader);
```

One access role bundles multiple warehouses, owned by the domain's RBAC role, granted to domain's READER (SYSADMIN inherits).

### Scenario 2: Multiple Privileges on a Warehouse
When users need more than USAGE - e.g., MONITOR and OPERATE for warehouse management.

```sql
SET dataProduct = 'ETL_PLATFORM';
SET dpRbac = $dataProduct || '_RBAC';
SET dpReader = $dataProduct || '_READER';
SET accessRole = $dataProduct || '_WH_OPS';

USE ROLE SECURITYADMIN;

CREATE ROLE IF NOT EXISTS IDENTIFIER($accessRole);

GRANT USAGE ON WAREHOUSE ETL_LOAD TO ROLE IDENTIFIER($accessRole);
GRANT MONITOR ON WAREHOUSE ETL_LOAD TO ROLE IDENTIFIER($accessRole);
GRANT OPERATE ON WAREHOUSE ETL_LOAD TO ROLE IDENTIFIER($accessRole);

GRANT OWNERSHIP ON ROLE IDENTIFIER($accessRole) TO ROLE IDENTIFIER($dpRbac);
GRANT ROLE IDENTIFIER($accessRole) TO ROLE IDENTIFIER($dpReader);
```

One access role bundles USAGE + MONITOR + OPERATE, owned by data product's RBAC role, granted to READER.

### Scenario 3: Combination - Multiple Warehouses, Multiple Privileges
High-compute cluster with full operational access for a specialized team within a domain.

```sql
SET domain = 'DATASCIENCE';
SET domainRbac = $domain || '_RBAC';
SET domainReader = $domain || '_READER';
SET accessRole = $domain || '_WH_HIGHCOMPUTE_OPS';

USE ROLE SECURITYADMIN;

CREATE ROLE IF NOT EXISTS IDENTIFIER($accessRole);

GRANT USAGE, MONITOR, OPERATE ON WAREHOUSE COMPUTE_4XL TO ROLE IDENTIFIER($accessRole);
GRANT USAGE, MONITOR, OPERATE ON WAREHOUSE COMPUTE_6XL TO ROLE IDENTIFIER($accessRole);

GRANT OWNERSHIP ON ROLE IDENTIFIER($accessRole) TO ROLE IDENTIFIER($domainRbac);
GRANT ROLE IDENTIFIER($accessRole) TO ROLE IDENTIFIER($domainReader);
```

---

## Decision Tree

```
How many grants would this access role contain?
│
├── ONE grant (single warehouse, single privilege)
│   └── DON'T create access role
│       └── Grant directly to the functional role
│
└── MULTIPLE grants
    ├── Multiple warehouses (suite/cluster)
    │   └── CREATE access role to bundle them
    │
    └── Multiple privileges (USAGE + MONITOR + OPERATE)
        └── CREATE access role to bundle them
```

---

## Warehouse Ownership

Warehouse **ownership** follows the infrastructure principle:

| Architecture | Warehouse Owner |
|--------------|-----------------|
| Single Account | SYSADMIN |
| Per Environment | ENV_SYSADMIN |
| Per Business Unit | BU_SYSADMIN or ENV_SYSADMIN |

Domain and Data Product roles **never own warehouses** - they receive USAGE only.

---

## Anti-Patterns

### 1. One Access Role Per Warehouse
Creating `_WH_<name>` for every warehouse regardless of need.
**Fix:** Only create when bucketing multiple grants.

### 2. Cross-Domain Warehouse Grants
Giving Domain B access to Domain A's warehouse.
**Fix:** Domain B brings their own compute.

### 3. Access Role with Single USAGE Grant
```sql
CREATE ROLE _WH_ANALYTICS;
GRANT USAGE ON WAREHOUSE ANALYTICS TO ROLE _WH_ANALYTICS;  -- Only one grant!
```
**Fix:** Grant USAGE directly to the functional role.

### 4. "Consistency" as Justification
Creating access roles because "we do it for all warehouses."
**Fix:** Consistency in unnecessary complexity is still unnecessary.

---

## Summary

| Situation | Use Access Role? |
|-----------|------------------|
| Single warehouse, USAGE only | **No** - grant directly |
| Cross-domain warehouse access | **No** - bring your own compute |
| Suite of specialized warehouses | **Yes** - bundles multiple warehouses |
| Multiple privileges (USAGE + MONITOR + OPERATE) | **Yes** - bundles multiple privileges |
| "Because best practice says so" | **No** |
