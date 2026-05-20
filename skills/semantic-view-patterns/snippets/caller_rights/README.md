# Caller Rights — Semantic View Access Control

## The Problem

By default, both standard Snowflake views and semantic views execute with **owner rights**: when a user queries the view, it runs with the *view owner's* privileges. This means a user with SELECT on the view can read data even if they have no SELECT on the underlying tables — the owner's access covers them.

Sometimes that's exactly what you want. But for a governed semantic layer, it creates a problem: you may want to ensure that users can only query the semantic view if they also have *direct* access to the underlying base tables — so that row-level security policies, column masking, and schema-level access controls still apply to the caller.

## The Trick — Ownership Separation

The key is a single design decision: **make the SV owner a role that has no access to the base tables.**

```
SV_CREATOR  creates the SV  (needs base table access to define it)
     ↓  future grant transfers ownership immediately
SV_OWNER    owns the SV     (deliberately has NO base table access)
```

Because the SV runs with the *owner's* rights (`SV_OWNER`), and `SV_OWNER` cannot access the base tables, the query cannot succeed on owner rights alone. The only way it can succeed is if the **caller** brings their own base table access. This converts the effective execution model to caller rights — without any special DDL clause.

The critical line that makes this work:
```sql
GRANT OWNERSHIP ON FUTURE SEMANTIC VIEWS IN SCHEMA SV_CALLER_TEST.SV TO ROLE SV_OWNER;
```

`SV_OWNER` can grant SELECT on the SV to users — but granting SELECT on the SV alone is not enough. The caller must also have USAGE on the DATA schema and SELECT on every base table.

## How This Compares to a Standard View

| | Standard view (owner has table access) | This SV pattern (owner has NO table access) |
|--|---------------------------------------|---------------------------------------------|
| Executes with | Owner's rights | Owner's rights |
| Owner has SELECT on base tables? | Yes | **No — deliberately** |
| User needs SELECT on view? | Yes | Yes |
| User needs SELECT on base tables? | **No** — owner provides it | **Yes** — owner can't provide it |
| Effective execution model | Owner rights | Effectively caller rights |

## How You Might Express This Need

- "We want the SV to be an additional access gate, not a bypass around table-level permissions"
- "Our base tables have row-level security / column masking — we need the caller's policies to apply, not the owner's"
- "Can a user with SELECT on the SV read data they don't have SELECT on in the base tables?"
- "How do we design roles for a semantic layer so that base table access is still required?"

## The Four-Role Pattern

| Role | Creates SVs? | Owns SVs? | DATA schema access? | SELECT on SV? | Can query? |
|------|-------------|-----------|---------------------|----------------|------------|
| `SV_CREATOR` | Yes | No (future grant hands off) | **Yes** | Implicitly | Yes |
| `SV_OWNER` | No | **Yes** | **No** | Owns | N/A (grants, doesn't query) |
| `SV_USER` | No | No | **Yes** | **Yes** | ✅ Yes |
| `SV_USER_NO_BASE_SELECT` | No | No | **No** | **Yes** | ❌ Fails |

`SV_USER_NO_BASE_SELECT` has SELECT on the SV but the query fails because `SV_OWNER` (the view executor) has no base table access, and neither does the caller. The error is immediate and clear.

## Schema Layout

Two separate schemas reinforce the boundary:

```
SV_CALLER_TEST.SV    ← semantic view lives here (SV_CREATOR creates, SV_OWNER owns)
SV_CALLER_TEST.DATA  ← base tables live here (SV_USER can see, SV_OWNER cannot)
```

## What Doesn't Work

- **`USE SECONDARY ROLES ALL` can unexpectedly grant access** — if a user has secondary roles that include DATA schema access, the query may succeed. Always use `USE SECONDARY ROLES NONE` when testing access boundaries.
- **The trick only works if the owner truly lacks table access** — if `SV_OWNER` accidentally gets USAGE on the DATA schema (e.g. via a future grant or role inheritance), the whole pattern breaks and the SV reverts to effectively owner-rights behavior.
- **Column masking and row access policies on base tables are respected** — because the query can only succeed when the *caller* has base table access, any policies on those tables apply to the caller's role.

## Cleanup

Run the cleanup block at the bottom of `queries.sql` to remove all objects created by this snippet (roles, warehouse, database).

## Docs

- [Semantic view privileges](https://docs.snowflake.com/en/user-guide/views-semantic/privileges)
- [CREATE SEMANTIC VIEW — access control](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view#access-control-requirements)
- [GRANT privilege on semantic view](https://docs.snowflake.com/en/sql-reference/sql/grant-privilege)
- [GRANT OWNERSHIP on future objects](https://docs.snowflake.com/en/sql-reference/sql/grant-ownership)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | Roles, warehouse, DB/schemas, tables, and all privilege grants |
| `seed_data.sql` | Customer, address, and order data |
| `semantic_view.sql` | SV creation (as SV_CREATOR) + SELECT grants (as SV_OWNER) |
| `queries.sql` | Succeeding query (SV_USER), failing query (SV_USER_NO_BASE_SELECT), cleanup |

> ⚠️ **Requires ACCOUNTADMIN** (or both SECURITYADMIN and SYSADMIN). This snippet creates roles, a warehouse, and a dedicated database (`SV_CALLER_TEST`). It does **not** use the `--db` / `--schema` arguments from `run_snippet.py` — all objects are created under `SV_CALLER_TEST`.
