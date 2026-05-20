---
name: rbac
title: Snowflake RBAC Patterns
summary: Snowflake Role-Based Access Control patterns for designing role hierarchies, access roles, and policy-driven access.
description: "Use when designing RBAC architecture, creating roles, granting access, setting up role hierarchies, or planning multi-environment access control. Triggers: rbac, role hierarchy, access roles, role design, grant access, schema access role, database access role, secondary roles, policy roles, functional roles."
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
prompt: Help me design an RBAC hierarchy for my Snowflake account.
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# Snowflake RBAC Patterns

## Overview

Router skill for Snowflake Role-Based Access Control design. Routes requests to the right sub-skill for hierarchy design, access role creation, and policy integration. Start with `architecture-patterns` if you are new to RBAC.

## When to Use

Use this skill when you need to:

- Design a new role hierarchy from scratch
- Translate job functions or team structures into Snowflake roles
- Create database, schema, or warehouse access roles
- Decide between functional roles and access roles
- Wire roles into masking or row access policies
- Set up secondary roles for cross-domain access

## When NOT to Use This Skill

This skill covers role hierarchy design and access patterns only. Delegate elsewhere for:

| Topic | Delegate To |
|-------|-------------|
| Multi-account strategy, org-level governance | `organization-management` |
| Cross-account data sharing | `internal-marketplace-org-listing` |
| Masking / row access policy implementation | `data-governance` |
| Declarative sharing / app packages | `declarative-sharing` |

This skill tells you which roles to reference. Bundled skills above implement the policies.

## Full Role Hierarchy

```
1. OOB Account Roles (ACCOUNTADMIN, SYSADMIN, etc.)
   └── 2. Environment Admin Roles (multi-env in single account)
       └── 3. Business Domain Admins (federated/hub-spoke)
           └── 4. Data Product Admins (discrete teams)
               └── 5. Database Access Roles (DB_R, DB_RW, DB_C)
                   └── 6. Schema Access Roles (<schema>_R, <schema>_RW, <schema>_C)
```

Not every layer applies. Use `architecture-patterns` to decide.

## Intent Routing

| Intent | Sub-Skill |
|--------|-----------|
| Starting RBAC design | `architecture-patterns` |
| Single vs multi-account, centralized vs federated | `architecture-patterns` |
| Translate job functions to roles | `personas` |
| Functional vs access roles | `personas` |
| Schema R/RW/C roles, MANAGED ACCESS, future grants | `schema-access-roles` |
| Aggregate schema roles into DB-wide roles | `database-access-roles` |
| Warehouse access role patterns | `warehouse-access-roles` |
| Multi-role aggregation, cross-domain queries | `secondary-roles` |
| `IS_ROLE_IN_SESSION` vs `CURRENT_ROLE` in policies | `policy-roles` |
| Role-based masking and tiered access | `policy-roles` |

## Sub-Skills by Layer

| Layer | Skill |
|-------|-------|
| Architecture decisions | `architecture-patterns` |
| 1. OOB account roles | `oob-account-roles` |
| 2. Environment admin roles | `environment-admin-roles` |
| 3-4. Domain & product admins | `domain-admin-roles` |
| 5. Database access roles | `database-access-roles` |
| 6. Schema access roles | `schema-access-roles` |
| Cross-cutting: personas | `personas` |
| Cross-cutting: warehouses | `warehouse-access-roles` |
| Cross-cutting: secondary roles | `secondary-roles` |
| Cross-cutting: policies | `policy-roles` |

## Workflow

1. New to RBAC? Start with `architecture-patterns`.
2. Identify which layers apply to your organization.
3. Implement top-down: highest layer first, then work down.
4. Route to the sub-skill that matches the current layer.

## Common Mistakes

- **Granting privileges directly to users.** Always grant to roles, then grant roles to users. Direct user grants break audit and offboarding.
- **Skipping access roles and granting on objects directly to functional roles.** This couples permissions to job titles and breaks when teams reorg. Use the access-role layer (`DB_R`, `<schema>_RW`, etc.) and grant access roles to functional roles.
- **Forgetting future grants.** Grants apply only to existing objects unless you also grant on `FUTURE` objects in the schema. Without future grants, new tables silently fail access.
- **Using `CURRENT_ROLE()` in row access policies.** It only matches the primary role and ignores secondary roles. Use `IS_ROLE_IN_SESSION()` so users with the role as either primary or secondary pass the check.
- **Putting everything under `ACCOUNTADMIN`.** Reserve `ACCOUNTADMIN` for break-glass. Object ownership and day-to-day grants belong under `SYSADMIN` and its descendants.
- **Creating one role per user.** Roles model job functions or access patterns, not people. One-role-per-user defeats the point of RBAC.
