---
name: rbac
title: Snowflake RBAC Patterns
summary: Router skill for designing Snowflake Role-Based Access Control hierarchies and access role patterns.
description: |
  Use when designing or refactoring Snowflake RBAC: choosing a role hierarchy, creating database/schema access roles, setting up functional roles, deciding between primary and secondary roles, or referencing roles in masking and row access policies. Routes to focused sub-flows for each layer of the hierarchy. Triggers: rbac, role hierarchy, access roles, functional roles, schema access role, database access role, warehouse access role, secondary roles, policy roles, role design, grant, managed access schema, future grants
tools:
  - snowflake_sql_execute
  - Read
  - Write
  - Edit
  - Grep
  - Glob
prompt: Help me design an RBAC hierarchy for my Snowflake account.
language: en
status: Published
author: Snowflake Solutions Team
type: community
---

# Snowflake RBAC Patterns

## Overview

This skill is a router for designing Snowflake Role-Based Access Control. It helps you pick a role hierarchy that fits your account, then routes to focused sub-flows for each layer (account roles, environment admins, domain admins, database access roles, schema access roles) and cross-cutting concerns (personas, warehouses, secondary roles, policy roles).

If you are new to RBAC, start with `architecture-patterns/INSTRUCTIONS.md` to decide which layers your organization actually needs. Most accounts do not need all six.

## When to Use

Use this skill when you need to:

- Design a fresh RBAC hierarchy for a new Snowflake account.
- Refactor an existing role mess into a clean access-role pattern.
- Create read/write/create roles for databases or schemas.
- Decide between functional (persona) roles and data-product access roles.
- Reference roles correctly in masking or row access policies.

## When NOT to Use

Delegate to bundled skills for non-RBAC concerns:

| Topic | Delegate to |
|---|---|
| Multi-account strategy, org-level governance | `organization-management` |
| Cross-account data sharing via listings | `internal-marketplace-org-listing` |
| Writing masking / row access policy SQL | `data-governance` |
| Declarative sharing or application packages | `declarative-sharing` |

This skill tells you which roles to reference. Those skills handle policy implementation and sharing mechanics.

## Full Role Hierarchy

```
1. OOB Account Roles (ACCOUNTADMIN, SYSADMIN, USERADMIN, SECURITYADMIN)
   └── 2. Environment Admin Roles (DEV_ADMIN, PROD_ADMIN)
       └── 3. Business Domain Admins (federated / hub-spoke)
           └── 4. Data Product Admins (per team)
               └── 5. Database Access Roles (DB_R, DB_RW, DB_C)
                   └── 6. Schema Access Roles (<schema>_R, _RW, _C)
```

## Sub-Flows

| Layer / Topic | File |
|---|---|
| Pick a hierarchy | `architecture-patterns/INSTRUCTIONS.md` |
| Persona-aligned roles | `personas/INSTRUCTIONS.md` |
| OOB account roles | `oob-account-roles/INSTRUCTIONS.md` |
| Environment admin roles | `environment-admin-roles/INSTRUCTIONS.md` |
| Domain & data product admins | `domain-admin-roles/INSTRUCTIONS.md` |
| Database access roles | `database-access-roles/INSTRUCTIONS.md` |
| Schema access roles | `schema-access-roles/INSTRUCTIONS.md` |
| Warehouse access roles | `warehouse-access-roles/INSTRUCTIONS.md` |
| Secondary roles | `secondary-roles/INSTRUCTIONS.md` |
| Roles inside policies | `policy-roles/INSTRUCTIONS.md` |

## Workflow

1. Run `architecture-patterns/INSTRUCTIONS.md` to choose which layers apply.
2. Implement top-down: account → environment → domain → product → DB → schema.

⚠️ STOPPING POINT: After identifying which layers the user needs, confirm the plan before loading any sub-flow. Do not chain into implementation without explicit user approval.

3. For each layer, open the matching sub-flow and follow it.
4. Validate grants with `SHOW GRANTS TO ROLE <name>` before handing access to users.

## Common Mistakes

- **Granting object privileges directly to users or functional roles.** Always grant to access roles first, then grant access roles to functional roles. This keeps the hierarchy refactorable.
- **Skipping `MANAGED ACCESS` on schemas.** Without it, object owners can grant access independently and bypass your hierarchy.
- **Forgetting future grants.** Without `GRANT ... ON FUTURE` plus `GRANT ... ON ALL`, new objects silently lose access.
- **Using `CURRENT_ROLE()` in row access policies when secondary roles are enabled.** Use `IS_ROLE_IN_SESSION()` so users with multiple authorized roles see the data they should. See `policy-roles/INSTRUCTIONS.md`.
- **Creating warehouse access roles when not needed.** Most accounts can grant `USAGE` on warehouses directly to functional roles.
- **Letting `ACCOUNTADMIN` own objects.** Object ownership should land on `SYSADMIN` or a domain admin, never `ACCOUNTADMIN`.

## Stopping Points

- After Step 1 — confirm the chosen layers and implementation plan before loading any sub-flow

This router skill issues no SQL on its own. Each sub-flow that creates roles, applies grants, or alters schemas defines its own stopping points. The general rule across all sub-flows:

⚠️ STOPPING POINT: Before running any `CREATE ROLE`, `GRANT`, `REVOKE`, `ALTER SCHEMA … ENABLE MANAGED ACCESS`, or `DROP ROLE` statement, show the planned SQL to the user and wait for explicit confirmation. RBAC changes are easy to apply but painful to reverse once users start depending on them.
