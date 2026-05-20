---
name: policy-roles
description: "Roles in data policies (Row Access, Masking, Projection, Aggregation). Use when: writing policy conditions, choosing between role-based and attribute-based policies, understanding IS_ROLE_IN_SESSION vs CURRENT_ROLE."
---

# Roles in Data Policies

How roles interact with Row Access Policies, Masking Policies, and other policy types.

---

## Core Principle: Policy Roles as Attributes, Not Access

**Policy roles should have NO direct access to data.**

The ability to query tables is encapsulated by **schema access roles** (`<schema>_R`, `<schema>_RW`). Policy roles exist solely as **user attributes** that modify runtime behaviour when checked by policies.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  SEPARATION OF CONCERNS                                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Schema Access Role (RBAC)          Policy Role (ABAC)                  │
│  ─────────────────────────          ────────────────────                │
│  • CUSTOMERS_R                      • PII_READER                        │
│  • Grants: SELECT on tables         • Grants: NONE (no data access)    │
│  • Purpose: CAN query               • Purpose: HOW query behaves       │
│                                                                          │
│  User queries table via CUSTOMERS_R                                     │
│  Policy checks PII_READER to determine masking level                    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Why This Matters

1. **Clear separation**: Access roles grant access; policy roles modify behaviour
2. **SCIM integration**: Policy roles can be provisioned via IdP (Okta/Entra) as user attributes
3. **Audit clarity**: Access audit shows WHO accessed data; policy roles show WHAT they saw
4. **Composability**: Same data access + different policy roles = different views of data

### Anti-Pattern: Policy Roles with Data Access

```sql
-- WRONG: Policy role that also grants table access
CREATE ROLE PII_READER;
GRANT SELECT ON customers TO ROLE PII_READER;  -- Don't do this!

-- CORRECT: Policy role is purely an attribute
CREATE ROLE PII_READER;  -- No grants to data objects
-- Access comes from schema role: CUSTOMERS_R
```

### RBAC + ABAC Combined

Policy roles enable **Attribute Based Access Control (ABAC)** layered on classic RBAC:

| Layer | Role Type | Purpose | Grants Data Access? |
|-------|-----------|---------|---------------------|
| RBAC | Schema access role (`CUSTOMERS_R`) | Permission to query | **Yes** |
| ABAC | Policy role (`PII_READER`) | Attribute checked by policy | **No** |

```sql
-- User: jane.doe
-- Roles: CUSTOMERS_R (schema access), PII_READER (policy attribute)
-- 
-- CUSTOMERS_R → Can SELECT from customers table
-- PII_READER  → Masking policy sees this role, returns unmasked PII
--
-- Without PII_READER: Same access, but PII columns masked
```

---

## Policy Types Overview

| Policy Type | Purpose | Common Role Usage |
|-------------|---------|-------------------|
| **Row Access Policy** | Filter rows based on context | Role determines which rows visible |
| **Masking Policy** | Transform column values | Role determines masking level |
| **Projection Policy** | Control which columns queryable | Role determines column visibility |
| **Aggregation Policy** | Require MIN_GROUP_SIZE | Role may bypass aggregation requirement |

---

## Choosing the Right Role Function

Four functions check roles in policies. Choosing the right one is critical.

| Function | Checks | Use When |
|----------|--------|----------|
| `CURRENT_ROLE()` | Primary role only | Almost never - legacy only |
| `IS_ROLE_IN_SESSION()` | Active account roles (primary + secondary) | Account roles, secondary roles enabled |
| `IS_DATABASE_ROLE_IN_SESSION()` | Active database roles | Schema/database access roles, data sharing |
| `CURRENT_AVAILABLE_ROLES()` | All granted roles | Check grants regardless of session state |

**Note:** `IS_DATABASE_ROLE_IN_SESSION()` is the only function that works across data shares and aligns with the `<schema>_R`, `<schema>_RW`, `DB_R`, `DB_RW` access role pattern.

### CURRENT_ROLE() - Avoid

Returns only the **primary role**. With secondary roles enabled (the default), this misses most effective privileges.

```sql
-- WRONG: Only checks primary role
CREATE OR REPLACE ROW ACCESS POLICY sales_rap
AS (region STRING) RETURNS BOOLEAN ->
  CURRENT_ROLE() IN ('SALES_ADMIN', 'SALES_MANAGER')
  OR region = 'PUBLIC';

-- User with SALES_MANAGER as secondary role: DENIED (incorrect)
```

### IS_ROLE_IN_SESSION() - Recommended

Checks if a role is **currently active** as either primary OR secondary.

```sql
-- CORRECT: Checks all active roles
CREATE OR REPLACE ROW ACCESS POLICY sales_rap
AS (region STRING) RETURNS BOOLEAN ->
  IS_ROLE_IN_SESSION('SALES_ADMIN')
  OR IS_ROLE_IN_SESSION('SALES_MANAGER')
  OR region = 'PUBLIC';

-- User with SALES_MANAGER as secondary role: ALLOWED (correct)
```

**Use this when**: Secondary roles are enabled (default configuration). This is the recommended approach for most deployments.

### CURRENT_AVAILABLE_ROLES() - Granted Roles

Returns all roles **granted to the user**, regardless of whether they are currently active in the session.

```sql
-- Check if user has been granted the role (active or not)
CREATE OR REPLACE ROW ACCESS POLICY grants_based_rap
AS (region STRING) RETURNS BOOLEAN ->
  ARRAY_CONTAINS('SALES_MANAGER'::VARIANT, CURRENT_AVAILABLE_ROLES())
  OR region = 'PUBLIC';
```

**Use this when**: 
- You want policy to reflect **granted access**, not session state
- Session policies may restrict secondary roles but you want to honour the underlying grant
- Users may not have activated all their roles but should still have access

### Decision Matrix

| Scenario | Function |
|----------|----------|
| Account roles, secondary roles enabled | `IS_ROLE_IN_SESSION()` |
| Schema/database access roles | `IS_DATABASE_ROLE_IN_SESSION()` |
| Policies on shared data | `IS_DATABASE_ROLE_IN_SESSION()` |
| Honour grants regardless of session | `CURRENT_AVAILABLE_ROLES()` |
| Secondary roles disabled by policy | `IS_ROLE_IN_SESSION()` (respects restriction) |
| Legacy - primary role only | `CURRENT_ROLE()` (avoid if possible) |

### Key Difference: Session State vs Grants

```sql
-- User: jane.doe
-- Granted: SALES_MANAGER, FINANCE_READER
-- Session: Primary=SALES_MANAGER, Secondary roles=NONE (disabled by session policy)

CURRENT_ROLE()             → 'SALES_MANAGER'
IS_ROLE_IN_SESSION('FINANCE_READER') → FALSE (not active)
ARRAY_CONTAINS('FINANCE_READER'::VARIANT, CURRENT_AVAILABLE_ROLES()) → TRUE (granted)
```

If your organisation has **secondary roles enabled** (recommended), `IS_ROLE_IN_SESSION()` and `CURRENT_AVAILABLE_ROLES()` will behave similarly. The difference matters when:
- Session policies restrict secondary roles for specific users
- You want to check potential access vs current access

---

## IS_DATABASE_ROLE_IN_SESSION - Database & Schema Access Roles

For database roles (`<schema>_R`, `<schema>_RW`, `DB_R`, `DB_RW`), use `IS_DATABASE_ROLE_IN_SESSION()`.

### Why Database Roles in Policies?

1. **Data Sharing**: Only function that works across shares - consumer's database role grants flow through
2. **Access Role Alignment**: Directly checks the schema/database access roles you've already provisioned
3. **Encapsulation**: Policy logic stays within the database, portable with the data

### Basic Usage

```sql
CREATE OR REPLACE ROW ACCESS POLICY schema_rap
AS (sensitivity STRING) RETURNS BOOLEAN ->
  IS_DATABASE_ROLE_IN_SESSION('MYDB', 'SENSITIVE_R')
  OR sensitivity = 'PUBLIC';
```

### With Schema Access Roles

```sql
-- Policy aligned with <schema>_R, <schema>_RW pattern
CREATE OR REPLACE ROW ACCESS POLICY sales.customer_rap
AS (region STRING) RETURNS BOOLEAN ->
  IS_DATABASE_ROLE_IN_SESSION('SALES_DB', 'CUSTOMERS_R')    -- Full read access
  OR IS_DATABASE_ROLE_IN_SESSION('SALES_DB', 'CUSTOMERS_RW') -- ETL access
  OR region = 'PUBLIC';
```

### Data Sharing Scenario

```sql
-- Provider account: Policy on shared table
CREATE OR REPLACE ROW ACCESS POLICY shared_data_rap
AS (tenant_id STRING) RETURNS BOOLEAN ->
  IS_DATABASE_ROLE_IN_SESSION('SHARED_DB', tenant_id || '_READER');

-- Consumer account: Database role granted via share
-- Policy automatically enforces tenant isolation
```

This checks if the database role is granted to any active role (primary or secondary) in the session.

---

## Role-Based vs Attribute-Based Policies

### Role-Based Policies

Policy conditions check roles directly:

```sql
CREATE OR REPLACE MASKING POLICY pii_mask
AS (val STRING) RETURNS STRING ->
  CASE
    WHEN IS_ROLE_IN_SESSION('PII_READER') THEN val
    WHEN IS_ROLE_IN_SESSION('ANALYST') THEN SHA2(val)
    ELSE '***MASKED***'
  END;
```

| Pros | Cons |
|------|------|
| Simple to understand | Policy must list every role |
| Direct mapping to RBAC | Adding roles requires policy update |
| Easy to audit | Can become unwieldy at scale |

### Attribute-Based Policies

Policy conditions check user/session attributes or mapping tables:

```sql
CREATE OR REPLACE ROW ACCESS POLICY region_rap
AS (region STRING) RETURNS BOOLEAN ->
  region IN (
    SELECT allowed_region 
    FROM access_control.user_regions 
    WHERE user_name = CURRENT_USER()
  );
```

| Pros | Cons |
|------|------|
| Scales better | Requires mapping table maintenance |
| Policy unchanged when access changes | Harder to audit (check table, not policy) |
| Supports complex logic | Query in policy can impact performance |

### Hybrid: Role-to-Attribute Mapping

Best of both worlds - policy checks roles, but role membership determines attribute access:

```sql
CREATE OR REPLACE ROW ACCESS POLICY region_rap
AS (region STRING) RETURNS BOOLEAN ->
  EXISTS (
    SELECT 1 
    FROM access_control.role_regions rr
    WHERE rr.region = region
      AND IS_ROLE_IN_SESSION(rr.role_name)
  );
```

The mapping table:
```sql
CREATE TABLE access_control.role_regions (
  role_name STRING,
  region STRING
);

INSERT INTO access_control.role_regions VALUES
  ('SALES_EMEA_READER', 'EMEA'),
  ('SALES_APAC_READER', 'APAC'),
  ('SALES_AMER_READER', 'AMER'),
  ('SALES_GLOBAL_READER', 'EMEA'),
  ('SALES_GLOBAL_READER', 'APAC'),
  ('SALES_GLOBAL_READER', 'AMER');
```

---

## Row Access Policy Patterns

### Pattern 1: Simple Role Check

```sql
CREATE OR REPLACE ROW ACCESS POLICY sensitive_data_rap
AS (sensitivity_level STRING) RETURNS BOOLEAN ->
  CASE sensitivity_level
    WHEN 'PUBLIC' THEN TRUE
    WHEN 'INTERNAL' THEN IS_ROLE_IN_SESSION('INTERNAL_READER')
    WHEN 'CONFIDENTIAL' THEN IS_ROLE_IN_SESSION('CONFIDENTIAL_READER')
    WHEN 'RESTRICTED' THEN IS_ROLE_IN_SESSION('RESTRICTED_READER')
    ELSE FALSE
  END;
```

### Pattern 2: Hierarchical Roles

Higher roles can see lower sensitivity levels:

```sql
CREATE OR REPLACE ROW ACCESS POLICY tiered_rap
AS (tier INT) RETURNS BOOLEAN ->
  (tier <= 1)  -- PUBLIC
  OR (tier <= 2 AND IS_ROLE_IN_SESSION('TIER2_READER'))
  OR (tier <= 3 AND IS_ROLE_IN_SESSION('TIER3_READER'))
  OR IS_ROLE_IN_SESSION('FULL_ACCESS');
```

### Pattern 3: Owner Sees All

Data owners bypass restrictions:

```sql
CREATE OR REPLACE ROW ACCESS POLICY ownership_rap
AS (owner_domain STRING) RETURNS BOOLEAN ->
  IS_ROLE_IN_SESSION(owner_domain || '_SYSADMIN')
  OR IS_ROLE_IN_SESSION(owner_domain || '_READER')
  OR owner_domain = 'SHARED';
```

---

## Masking Policy Patterns

### Pattern 1: Tiered Masking

```sql
CREATE OR REPLACE MASKING POLICY email_mask
AS (val STRING) RETURNS STRING ->
  CASE
    WHEN IS_ROLE_IN_SESSION('PII_FULL') THEN val
    WHEN IS_ROLE_IN_SESSION('PII_PARTIAL') THEN 
      REGEXP_REPLACE(val, '^(.{2}).*(@.*)$', '\\1***\\2')
    ELSE '***@***.***'
  END;

-- PII_FULL sees: john.smith@company.com
-- PII_PARTIAL sees: jo***@company.com
-- Others see: ***@***.***
```

### Pattern 2: Conditional Unmasking

```sql
CREATE OR REPLACE MASKING POLICY ssn_mask
AS (val STRING) RETURNS STRING ->
  CASE
    WHEN IS_ROLE_IN_SESSION('HR_ADMIN') THEN val
    WHEN IS_ROLE_IN_SESSION('HR_VIEWER') THEN 'XXX-XX-' || RIGHT(val, 4)
    ELSE 'XXX-XX-XXXX'
  END;
```

### Pattern 3: Context-Aware Masking

Mask based on both role AND data context:

```sql
CREATE OR REPLACE MASKING POLICY salary_mask
AS (val NUMBER, department STRING) RETURNS NUMBER ->
  CASE
    WHEN IS_ROLE_IN_SESSION('FINANCE_ADMIN') THEN val
    WHEN IS_ROLE_IN_SESSION(department || '_MANAGER') THEN val
    ELSE NULL
  END;
```

---

## Projection Policy Patterns

Projection policies control whether a column can be included in query results.

### Pattern: Role-Based Column Access

```sql
CREATE OR REPLACE PROJECTION POLICY salary_projection
AS () RETURNS PROJECTION_CONSTRAINT ->
  CASE
    WHEN IS_ROLE_IN_SESSION('HR_ADMIN') THEN PROJECTION_CONSTRAINT(ALLOW => TRUE)
    WHEN IS_ROLE_IN_SESSION('MANAGER') THEN PROJECTION_CONSTRAINT(ALLOW => TRUE)
    ELSE PROJECTION_CONSTRAINT(ALLOW => FALSE)
  END;
```

---

## Aggregation Policy Patterns

Aggregation policies require results to have a minimum group size (k-anonymity).

### Pattern: Role Bypass

```sql
CREATE OR REPLACE AGGREGATION POLICY min_group_policy
AS () RETURNS AGGREGATION_CONSTRAINT ->
  CASE
    WHEN IS_ROLE_IN_SESSION('RESEARCH_FULL') THEN AGGREGATION_CONSTRAINT(MIN_GROUP_SIZE => 0)
    ELSE AGGREGATION_CONSTRAINT(MIN_GROUP_SIZE => 10)
  END;
```

---

## Performance Considerations

### Avoid Complex Subqueries

Policy conditions execute for every row. Complex subqueries can devastate performance.

**Slow:**
```sql
CREATE OR REPLACE ROW ACCESS POLICY slow_rap
AS (region STRING) RETURNS BOOLEAN ->
  EXISTS (
    SELECT 1 FROM complex_view v
    JOIN another_table t ON v.id = t.id
    WHERE t.region = region
      AND v.user = CURRENT_USER()
  );
```

**Better:**
```sql
-- Pre-compute access in a simple mapping table
CREATE OR REPLACE ROW ACCESS POLICY fast_rap
AS (region STRING) RETURNS BOOLEAN ->
  EXISTS (
    SELECT 1 FROM access_control.user_regions
    WHERE user_name = CURRENT_USER()
      AND allowed_region = region
  );
```

### IS_ROLE_IN_SESSION Performance

`IS_ROLE_IN_SESSION()` is highly optimised - it does not query the role hierarchy at runtime. Multiple calls in a single policy are acceptable.

---

## Policy Role Design Guidelines

### 1. Policy Roles Have NO Data Grants

Policy roles are attributes, not access roles. They receive **zero grants to data objects**.

```sql
-- Policy roles - NO data access grants
CREATE ROLE PII_READER;        -- Can see PII data unmasked
CREATE ROLE RESTRICTED_READER; -- Can see restricted rows
CREATE ROLE RESEARCH_FULL;     -- Bypasses aggregation

-- These roles have NO grants to tables, views, or schemas
-- Data access comes from schema access roles (<schema>_R, <schema>_RW)

-- Grant policy roles to users/functional roles as attributes
GRANT ROLE PII_READER TO ROLE HR_ANALYST;
GRANT ROLE RESTRICTED_READER TO ROLE COMPLIANCE_TEAM;
```

### 2. SCIM Provisioning for Policy Roles

Policy roles are ideal for IdP provisioning via SCIM:

```
IdP Group: "PII-Authorised"  →  Snowflake Role: PII_READER
IdP Group: "Research-Team"   →  Snowflake Role: RESEARCH_FULL
```

The IdP manages **who has the attribute**; the policy manages **what the attribute means**.

### 3. Policy Roles in RBAC Hierarchy

Policy roles should be:
- **Owned** by a central governance/RBAC role
- **Granted** to functional roles or directly via SCIM
- **Never** granted data access

```
GOVERNANCE_RBAC (owns policy roles)
    ↓
PII_READER, RESTRICTED_READER, etc. (NO data grants)
    ↓ granted to (as attributes)
HR_ANALYST, COMPLIANCE_TEAM, or directly to users via SCIM
    ↓
Users (access data via schema roles, modified by policy roles)
```

### 4. Audit Policy Role Grants

```sql
-- Who has PII access?
SELECT grantee_name, granted_on
FROM SNOWFLAKE.ACCOUNT_USAGE.GRANTS_TO_ROLES
WHERE role = 'PII_READER'
  AND deleted_on IS NULL;
```

---

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| **Policy roles with data grants** | Conflates access and attributes | Policy roles have NO data grants - access via schema roles only |
| Using `CURRENT_ROLE()` | Ignores secondary roles | Use `IS_ROLE_IN_SESSION()` or `CURRENT_AVAILABLE_ROLES()` |
| Wrong function choice | `IS_ROLE_IN_SESSION` vs `CURRENT_AVAILABLE_ROLES` confusion | See Decision Matrix - active session vs granted roles |
| Hardcoding role lists | Policy needs update for new roles | Use mapping table or role hierarchy |
| Complex policy subqueries | Performance degradation | Pre-compute access in simple tables |
| Admin roles in policies | Over-privileged, hard to audit | Create purpose-built policy roles |
| No default deny | Missing roles = full access | Always include `ELSE FALSE/MASKED` |

---

## SQL Templates

### Create Policy Role Structure

```sql
-- Variables
SET policyDomain = 'DATA_GOVERNANCE';
SET rbacRole = $policyDomain || '_RBAC';

-- Create policy roles
USE ROLE SECURITYADMIN;

CREATE ROLE IF NOT EXISTS PII_NONE;      -- No PII access (default)
CREATE ROLE IF NOT EXISTS PII_PARTIAL;   -- Partial PII (last 4 digits, etc.)
CREATE ROLE IF NOT EXISTS PII_FULL;      -- Full PII access

CREATE ROLE IF NOT EXISTS RESTRICTED_READER;
CREATE ROLE IF NOT EXISTS CONFIDENTIAL_READER;

-- Establish hierarchy
GRANT ROLE PII_NONE TO ROLE PII_PARTIAL;
GRANT ROLE PII_PARTIAL TO ROLE PII_FULL;

-- Transfer ownership to governance
GRANT OWNERSHIP ON ROLE PII_NONE TO ROLE IDENTIFIER($rbacRole) COPY CURRENT GRANTS;
GRANT OWNERSHIP ON ROLE PII_PARTIAL TO ROLE IDENTIFIER($rbacRole) COPY CURRENT GRANTS;
GRANT OWNERSHIP ON ROLE PII_FULL TO ROLE IDENTIFIER($rbacRole) COPY CURRENT GRANTS;
GRANT OWNERSHIP ON ROLE RESTRICTED_READER TO ROLE IDENTIFIER($rbacRole) COPY CURRENT GRANTS;
GRANT OWNERSHIP ON ROLE CONFIDENTIAL_READER TO ROLE IDENTIFIER($rbacRole) COPY CURRENT GRANTS;
```

### Standard Masking Policy

```sql
CREATE OR REPLACE MASKING POLICY governance.pii_string_mask
AS (val STRING) RETURNS STRING ->
  CASE
    WHEN IS_ROLE_IN_SESSION('PII_FULL') THEN val
    WHEN IS_ROLE_IN_SESSION('PII_PARTIAL') THEN 
      CASE 
        WHEN LENGTH(val) > 4 THEN REPEAT('*', LENGTH(val) - 4) || RIGHT(val, 4)
        ELSE REPEAT('*', LENGTH(val))
      END
    ELSE '***MASKED***'
  END;
```

### Standard Row Access Policy

```sql
CREATE OR REPLACE ROW ACCESS POLICY governance.sensitivity_rap
AS (sensitivity STRING) RETURNS BOOLEAN ->
  sensitivity = 'PUBLIC'
  OR (sensitivity = 'INTERNAL' AND IS_ROLE_IN_SESSION('INTERNAL_READER'))
  OR (sensitivity = 'CONFIDENTIAL' AND IS_ROLE_IN_SESSION('CONFIDENTIAL_READER'))
  OR (sensitivity = 'RESTRICTED' AND IS_ROLE_IN_SESSION('RESTRICTED_READER'))
  OR IS_ROLE_IN_SESSION('DATA_GOVERNANCE_ADMIN');
```

---

## Summary

| Do | Don't |
|----|-------|
| **Policy roles = attributes with NO data grants** | Grant data access to policy roles |
| Use schema access roles for data access | Mix access and attributes in one role |
| Use `IS_ROLE_IN_SESSION()` for active session checks | Use `CURRENT_ROLE()` |
| Use `IS_DATABASE_ROLE_IN_SESSION()` for schema/DB roles | Use account role functions for database roles |
| Use `CURRENT_AVAILABLE_ROLES()` for grant-based checks | Confuse active vs granted roles |
| Provision policy roles via SCIM as user attributes | Manually manage policy role membership |
| Include default deny (`ELSE FALSE`) | Assume missing condition = deny |
| Keep policy logic simple | Put complex joins in policies |
| Own policy roles under governance | Let policy roles float unowned |
