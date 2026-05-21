---
name: architecture-patterns
description: "Determine your Snowflake RBAC architecture based on account structure, environment strategy, and admin model. Use when: starting RBAC design, deciding how many role layers needed, choosing between centralized and federated admin."
---

# RBAC Architecture Patterns

Before implementing roles, determine which layers of the RBAC hierarchy your organization needs. Not every layer applies to every organization.

## The Full Hierarchy

```
1. OOB Account Roles
   └── 2. Environment Admin Roles
       └── 3. Business Domain Admins
           └── 4. Data Product Admins
               └── 5. Database Roles
                   └── 6. Schema Roles
```

Most organizations will **skip** one or more layers based on their architecture decisions.

---

## Decision 1: Account Strategy

The most fundamental decision. Account boundaries provide the strongest isolation - everything else is logical separation via grants.

### Option A: Single Account

All environments (Dev, Test, Prod) and all business domains in one account.

| Pros | Cons |
|------|------|
| **Simplicity** - one account to manage | **Logical separation only** - environments separated by grants, not hard boundaries |
| **No replication setup** - no cross-account data sharing or replication needed | **Compliance risk** - some orgs won't accept prod/non-prod in same account |
| **Single security perimeter** - one set of network policies, one Private Link config | **Blast radius** - misconfiguration affects everything |
| **Cloning** - clone entire databases for on-demand dev/UAT environments | **Shared limits** - account-level limits shared across all workloads |

**Best for**: Smaller organizations, centralized IT, rapid development cycles where cloning is valuable.

**Requires**: Environment Admin Roles (Level 2) to logically separate Dev/Test/Prod.

### Option B: Account Per Environment

Separate accounts for each environment tier (e.g., one for Dev/Test, one for Prod).

| Pros | Cons |
|------|------|
| **Hard separation** - prod data physically isolated from non-prod | **Replication required** - need cross-account sharing for data promotion |
| **Compliance friendly** - satisfies auditors wanting environment isolation | **Multiple security configs** - network policies, Private Link per account |
| **Independent scaling** - prod limits unaffected by dev activity | **No cross-account cloning** - can't clone prod to dev for testing |
| **Cleaner prod** - no dev/test clutter in production account | **More accounts to manage** - multiplied operational overhead |

**Best for**: Regulated industries, organizations with strict prod/non-prod separation requirements.

**Skip**: Environment Admin Roles (Level 2) - environment isolation handled by account boundaries.

### Option C: Account Per Business Unit

Large or mature business units get their own account(s), potentially with their own environment separation.

| Pros | Cons |
|------|------|
| **Full autonomy** - BUs configure their account as they see fit | **Significant overhead** - many accounts to manage |
| **Cost isolation** - clear billing separation by BU | **Data sharing complexity** - cross-BU data requires explicit sharing |
| **Independent governance** - each BU owns their security posture | **Inconsistent patterns** - BUs may diverge in RBAC approach |
| **Blast radius contained** - BU issues don't affect others | **Central visibility harder** - monitoring across accounts more complex |

**Best for**: Large enterprises, holding companies, organizations with autonomous business units.

**Skip**: Business Domain Admin Roles (Level 3) - domain isolation handled by account boundaries.

### Option D: Account Per Business Unit AND Environment

The most granular option - separate accounts for each combination (e.g., PROD_FINANCE, DEV_FINANCE, PROD_SALES, DEV_SALES).

| Pros | Cons |
|------|------|
| **Maximum isolation** - hard boundaries for both env and BU | **Account sprawl** - many accounts to manage |
| **Full autonomy + compliance** - BUs independent AND prod protected | **Complex data flows** - cross-account sharing for everything |
| **Clearest cost attribution** - billing by BU and environment | **Operational burden** - multiplied by both dimensions |

**Best for**: Large regulated enterprises with autonomous business units.

**Skip**: Both Environment Admin Roles (Level 2) AND Business Domain Admin Roles (Level 3).

### Hybrid Approaches

These options represent maximum configurations. In practice, hybrid approaches are common:

- **Mostly single account, large domains separate** - 80% of domains share accounts, but 1-2 large/sensitive domains (e.g., HR, Finance) get their own
- **Shared non-prod, separate prod** - Dev and Test share an account, Prod is separate
- **Core vs satellite** - Central data platform in shared accounts, autonomous BUs in their own

The key is consistency within each boundary - don't mix patterns arbitrarily.

### Decision Matrix

| Factor | Single Account | Per Environment | Per BU | Per BU + Env |
|--------|---------------|-----------------|--------|--------------|
| Compliance needs hard separation | ❌ | ✓ | ✓ | ✓ |
| Want cloning for dev/test | ✓ | ❌ | ❌ | ❌ |
| Centralized IT team | ✓ | ✓ | ❌ | ❌ |
| Autonomous business units | ❌ | ❌ | ✓ | ✓ |
| Minimize operational overhead | ✓ | Moderate | Low | ❌ |
| Simple data sharing | ✓ | Moderate | Low | ❌ |

---

## Decision 2: Admin Model

**Question**: Who administers databases and access?

| Choice | Implication |
|--------|-------------|
| **Centralized** | One team manages all databases. Skip Levels 3-4. Go directly from OOB roles (or Env Admins) to Database/Schema roles. |
| **Federated** | Domains have their own admins. Need Level 3 (Business Domain Admins), possibly Level 4 (Data Product Admins). |

Federated models range from "hub-and-spoke" (central platform team + domain admins) to "fully autonomous" (domains operate independently). From an RBAC perspective, these are the same - both require Domain Admin roles. The difference is organizational, not technical.

---

## Decision 3: Domain vs Data Product Admin

This decision only applies if you chose **Federated** admin model.

**Question**: How are teams organized within your domains?

| Scenario | Implication |
|----------|-------------|
| **Team owns many data products** | The team IS the domain. Use Domain Admin roles only. Skip Data Product Admin roles (Level 4). |
| **Team owns one data product** | Need Data Product Admin roles (Level 4). Domain Admin may be unnecessary or just an aggregation point. |
| **Multiple teams under one domain** | Need both. Domain Admin for shared access/governance across the domain, Data Product Admins for each team's assets. Example: HR domain with common access requirements but separate teams for Payroll, Recruitment, Benefits. |

---

## Decision 4: Database Scope

**This decision only applies if you chose Single Account (Option A) or Account Per Business Unit (Option C).**

If you have environments in separate accounts (Options B or D), cloning across accounts is not possible. Without cloning as a driver, there's little reason to constrain yourself to a single database - you will almost certainly have multiple databases per environment.

**Single database setups only make sense when environments share an account** where you want to maximize cloning benefits (clone entire environment in one operation).

**Question**: How many databases per environment?

| Choice | Implication |
|--------|-------------|
| **Single database** | Skip Database Access Roles. Schema roles grant directly to functional roles. Maximizes cloning simplicity. |
| **Multiple databases** | Need Database Access Roles (DB_R, DB_RW, DB_C) to aggregate schema access. |

---

## Common Patterns

### Pattern A: Single Account, Centralized IT
```
OOB Roles (SYSADMIN, SECURITYADMIN)
    └── Environment Admins (DEV_ADMIN, TST_ADMIN, PRD_ADMIN)
        └── Database Access Roles
            └── Schema Access Roles (<schema>_R, <schema>_RW, <schema>_C)
```
- Single account with Dev/Test/Prod environments
- Centralized team, no domain delegation
- Cloning available for on-demand environments
- Layers needed: 1, 2, 5, 6

### Pattern B: Single Account, Federated Domains
```
OOB Roles
    └── Environment Admins (DEV_ADMIN, TST_ADMIN, PRD_ADMIN)
        └── Domain Admins (SALES_ADMIN, FINANCE_ADMIN)
            └── Database Access Roles
                └── Schema Access Roles
```
- Single account with Dev/Test/Prod environments
- Federated domain administration
- Cloning available for on-demand environments
- Layers needed: 1, 2, 3, 5, 6

### Pattern C: Multi-Account by Environment, Federated Domains
```
OOB Roles (per account)
    └── Domain Admins (SALES_ADMIN, FINANCE_ADMIN)
        └── Database Access Roles
            └── Schema Access Roles
```
- Separate accounts for Dev, Test, Prod (skip Level 2)
- Multiple business domains per account
- Layers needed: 1, 3, 5, 6

### Pattern D: Large Domain with Data Product Teams
```
OOB Roles
    └── Domain Admin (HR_ADMIN)
        └── Data Product Admins (PAYROLL_ADMIN, RECRUITMENT_ADMIN)
            └── Database Access Roles
                └── Schema Access Roles
```
- Single domain with multiple teams producing different data assets
- Shared access requirements at domain level
- Layers needed: 1, 3, 4, 5, 6

### Pattern E: Maximum Complexity
```
OOB Roles
    └── Environment Admins
        └── Domain Admins
            └── Data Product Admins
                └── Database Access Roles
                    └── Schema Access Roles
```
- Single account, multi-environment
- Federated domains with discrete data product teams
- Layers needed: All

---

## Workflow

1. Answer the four decisions above
2. Identify which pattern (A-D) most closely matches
3. Note which layers you need
4. Implement top-down: start with highest layer, work down

## Next Steps by Layer

| Layer | Skill |
|-------|-------|
| 1. OOB Account Roles | `oob-account-roles` |
| 2. Environment Admin Roles | `environment-admin-roles` |
| 3. Business Domain Admins | `domain-admin-roles` |
| 4. Data Product Admins | `data-product-admin-roles` |
| 5. Database Access Roles | `database-access-roles` |
| 6. Schema Access Roles | `schema-access-roles` |

---

## Key Principles

- **Skip layers that don't apply** - unnecessary hierarchy adds complexity without value
- **Account boundaries are strongest isolation** - use multi-account when hard separation required
- **Centralized control vs federated autonomy** - pick one, don't mix inconsistently
- **Start simple, add layers as needed** - easier to add hierarchy than remove it
