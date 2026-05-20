---
name: personas
description: "Persona-based access design framework. Use when: translating business responsibilities to roles, designing functional roles, choosing between persona-aligned vs data-product-aligned approaches, onboarding patterns."
---

# Personas

## What Is a Persona?

A **Persona** is a conceptual "hat" a human or service account wears - NOT a Snowflake object. It describes *how* someone interacts with the platform.

- One human may have **multiple personas** (e.g., Admin + Analyst)
- A service account typically has a **single persona**

Personas translate business responsibilities into access requirements **before** mapping to physical Snowflake roles.

---

## Persona Categories

### Maintainer Personas (Least Privilege)

Focus on **platform and data product operation**. Guiding principle: **minimum rights necessary**, accept granular roles and explicit role switching.

| Persona | Responsibilities |
|---------|-----------------|
| **Admin** | Account configuration, role hierarchy, warehouse provisioning |
| **Engineer** | Pipeline development, schema DDL, data loading/transformation |
| **Support** | Incident triage, query profiling, troubleshooting |
| **Governance** | Policy enforcement, tagging, masking, auditing |

### Analytic Personas (Least Effort)

Focus on **consuming and analyzing data**. Guiding principle: **minimize friction**, provide clear defaults (role, warehouse, namespace).

| Persona | Responsibilities |
|---------|-----------------|
| **Report Viewer** | Run pre-built dashboards; read-only |
| **Data Analyst** | Ad-hoc SQL, BI tool exploration |
| **Power Analyst / Data Scientist** | Advanced analytics, ML, sandbox write-back |
| **Governance Viewer** | Read-only audit logs, lineage, classification |

For analytic personas, use an **aggregate Functional Role** bundling all needed access - users "log in and run queries" without role switching.

---

## Two Design Approaches

### Approach A: Persona-Aligned Functional Roles

Each persona maps 1:1 to a Snowflake functional role containing all access roles the persona needs.

```
FUNCTIONAL_ROLE: FR_DATA_ANALYST
  ├── AR_FINANCE_READ
  ├── AR_MARKETING_READ
  ├── AR_WAREHOUSE_ANALYST_WH
  └── SANDBOX.DB_RW
```

| Pros | Cons |
|------|------|
| Business legibility - names match job functions | Custom bundles require hand-curation |
| Simple SCIM mapping (1 group = 1 role) | Persona splitting causes proliferation |
| Least-effort for consumers | Drift risk - stale/broad grants accumulate |
| Clear audit trail per persona | Tight coupling to org structure |

### Approach B: Data-Product-Aligned Functional Roles

Functional roles organized around **data products/domains**, not personas. Users granted specific domain roles they need.

```
USER: jane.doe
  ├── DR_FINANCE_ANALYST
  ├── DR_MARKETING_ANALYST
  └── DR_SANDBOX_POWERUSER
```

| Pros | Cons |
|------|------|
| Standardized, reusable building blocks | Multiple SCIM groups per user |
| Additive access - one grant per new domain | Requires secondary roles for cross-domain |
| Reduced proliferation (scales with domains) | Higher friction for consumers |
| Clear ownership by domain teams | Harder to audit "what can Jane do?" |

---

## Side-by-Side Comparison

| Dimension | Persona-Aligned (A) | Data-Product-Aligned (B) |
|-----------|---------------------|--------------------------|
| Role granularity | Coarse (per persona) | Fine (per domain) |
| SCIM complexity | Low (1:1) | Higher (N:N) |
| Maintenance burden | Higher (custom bundles) | Lower (templates) |
| Role proliferation | Higher over time | Lower, scales with domains |
| Consumer experience | Frictionless | Requires secondary roles |
| Cross-domain queries | Built-in | Requires aggregation |
| Auditability | Strong (named role) | Spread across roles |
| Exception handling | Low (forces forking) | High (grant/revoke) |

---

## Recommended Hybrid Approach

Most mature deployments combine both:

1. **Define personas conceptually** - for requirements, documentation, SCIM group naming
2. **Build data-product-aligned access roles** as atomic building blocks (Approach B)
3. **Create persona functional roles** for high-volume, well-defined populations (Approach A convenience layer)
4. **Enable secondary roles** for power users crossing domain boundaries
5. **Establish governance cadence** - quarterly reviews, grant diffing, clear escalation paths

This provides persona legibility for common cases while keeping architecture modular for exceptions.

---

## Mapping to RBAC Hierarchy

| Persona Category | Maps To |
|------------------|---------|
| Admin | Environment/Domain SYSADMIN + RBAC roles |
| Engineer | Data Product SYSADMIN roles |
| Analyst | READER roles (aggregated via functional role or secondary roles) |
| Governance | Account-level governance roles |

### Pattern: Analyst Persona with Secondary Roles
```sql
-- Grant domain READER roles
GRANT ROLE SALES_READER TO USER analyst_jane;
GRANT ROLE MARKETING_READER TO USER analyst_jane;
GRANT ROLE FINANCE_READER TO USER analyst_jane;

-- Enable secondary roles for seamless cross-domain access
ALTER USER analyst_jane SET DEFAULT_SECONDARY_ROLES = ('ALL');
```

### Pattern: Analyst Persona with Functional Role
```sql
-- Create aggregate functional role
CREATE ROLE FR_BUSINESS_ANALYST;

-- Grant all required access roles
GRANT ROLE SALES_READER TO ROLE FR_BUSINESS_ANALYST;
GRANT ROLE MARKETING_READER TO ROLE FR_BUSINESS_ANALYST;
GRANT ROLE FINANCE_READER TO ROLE FR_BUSINESS_ANALYST;
GRANT ROLE ANALYST_WH_USER TO ROLE FR_BUSINESS_ANALYST;

-- Grant functional role to user
GRANT ROLE FR_BUSINESS_ANALYST TO USER analyst_jane;

-- Set as default
ALTER USER analyst_jane SET DEFAULT_ROLE = 'FR_BUSINESS_ANALYST';
```

---

## Decision Guide

| If... | Then... |
|-------|---------|
| Well-defined, stable user populations | Approach A (persona-aligned) |
| Frequent exceptions / varied access needs | Approach B (data-product-aligned) + secondary roles |
| Simple SCIM integration priority | Approach A |
| Domain team ownership priority | Approach B (requires secondary roles for cross-domain) |

### On Disabling Secondary Roles

**Disabling secondary roles is an outlier, not a legitimate design choice.**

If your organisation has disabled secondary roles via session policy, this should be treated as a legacy constraint to work around - not a security posture to aspire to. Disabling secondary roles:
- Provides **no security benefit** (see `secondary-roles` skill for myth-busting)
- Blocks personal databases, sandbox schemas, and other self-service patterns
- Forces Approach A or complex hybrid workarounds
- Creates friction that drives users toward shadow IT alternatives

If secondary roles are disabled: Approach A or hybrid is **required**, not recommended.

---

## Data Product Access Provisioning (Approach B in Detail)

The other half of the access story: instead of provisioning by job role, **provision by Data Product**. Users request access to specific Data Products, not personas.

### The Model

```
Data Product Team                    Identity Provider (Okta/Entra)
       │                                        │
       ▼                                        ▼
┌─────────────────┐                   ┌─────────────────┐
│ SALES_READER    │ ◄──── SCIM ────► │ SNOW-SALES-READ │
│ (Snowflake Role)│                   │ (IdP Group)     │
└─────────────────┘                   └─────────────────┘
       │
       ▼
┌─────────────────┐
│ SALES.DB_R      │
│ (Database Role) │
└─────────────────┘
```

Each Data Product publishes:
1. A **Snowflake account role** (e.g., `SALES_READER`) that holds access to its database roles
2. A corresponding **IdP group** (e.g., `SNOW-SALES-READ`) mapped via SCIM

Users request membership in IdP groups. SCIM provisions the role grants. Secondary roles aggregate access at runtime.

### SCIM Integration Pattern

```
IdP Group                    Snowflake Role              Grants
─────────────────────────────────────────────────────────────────
SNOW-SALES-READ         →    SALES_READER           →    SALES.DB_R
SNOW-SALES-ETL          →    SALES_ETL              →    SALES.DB_RW
SNOW-MARKETING-READ     →    MARKETING_READER       →    MARKETING.DB_R
SNOW-FINANCE-READ       →    FINANCE_READER         →    FINANCE.DB_R
```

### Why This Works

| Benefit | Explanation |
|---------|-------------|
| **Self-service** | Users request access via IdP portal, no Snowflake admin involvement |
| **Data Product ownership** | Each team controls who gets access to their product |
| **Standardised** | Every Data Product follows the same pattern |
| **Auditable** | IdP provides access request history; Snowflake shows current grants |
| **Scalable** | Adding a new Data Product = new role + new IdP group |

### Setup: Data Product Side

```sql
-- Data Product team creates their READER role
CREATE ROLE SALES_READER;

-- Grant database role to account role
GRANT DATABASE ROLE SALES.DB_R TO ROLE SALES_READER;

-- NO WAREHOUSE GRANT HERE
-- Consumers bring their own compute via their domain's READER role

-- Transfer ownership to RBAC role for governance
GRANT OWNERSHIP ON ROLE SALES_READER TO ROLE SALES_RBAC COPY CURRENT GRANTS;
```

**Critical**: Data Product READER roles grant **data access only**, never warehouse access. Bundling compute with data access creates a perverse incentive - popular data products would drive consumption costs to the producing domain, discouraging data sharing.

### Setup: IdP Side (Okta Example)

1. Create group `SNOW-SALES-READ` in Okta
2. Configure SCIM provisioning to map group → Snowflake role `SALES_READER`
3. Users request group membership via Okta access request workflow
4. Approval (manual or automated) grants membership
5. SCIM syncs membership to Snowflake role grant

### User Experience

**Jane is a Marketing analyst who needs Sales and Marketing data:**

```sql
-- Jane's domain (Marketing) gives her warehouse access via her domain role:
--   GRANT ROLE MARKETING_ANALYST TO USER jane.doe;  (includes MARKETING_WH access)

-- Jane requests access to Sales data via IdP:
--   SCIM provisions: GRANT ROLE SALES_READER TO USER jane.doe;

-- Jane logs in with DEFAULT_SECONDARY_ROLES = ('ALL')
-- Secondary roles aggregate:
--   - MARKETING_ANALYST: warehouse access + Marketing data
--   - SALES_READER: Sales data access only (no warehouse)

-- She can query both using her Marketing warehouse:
SELECT * FROM SALES.CORE.CUSTOMERS;      -- data via SALES_READER, compute via MARKETING_ANALYST
SELECT * FROM MARKETING.CAMPAIGNS.DATA;  -- both via MARKETING_ANALYST
```

**Key point**: Jane's domain provides compute. Sales provides data. The cost of Jane's queries is borne by Marketing, not Sales - creating the right incentives for data sharing.

### Comparison: Persona vs Data Product Provisioning

| Aspect | Persona Provisioning | Data Product Provisioning |
|--------|---------------------|--------------------------|
| Access request | "I need Analyst access" | "I need Sales data access" |
| Approval | Central team decides what Analyst means | Data Product owner approves |
| IdP groups | Few (one per persona) | Many (one per Data Product) |
| Snowflake roles | Custom bundles | Standardised per product |
| Adding new data | Update persona role | User requests new product |
| Removing access | Complex (which products?) | Remove from specific group |
| Secondary roles | Optional | Required |
| Warehouse access | Bundled in persona role | Consumer's domain provides compute |
