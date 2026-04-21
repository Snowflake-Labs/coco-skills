# Caller Rights — Semantic View Access Control

## The Problem

A data platform team wants to expose a governed Semantic View to analysts, but needs to ensure that:
1. Analysts can only query the SV if they also have direct access to the underlying base tables.
2. Granting SELECT on the SV alone is **not** enough — there is no privilege escalation path through the SV layer.

This is the opposite of how standard Snowflake views work. Regular views use **owner rights** by default: a user with SELECT on the view can read data even without SELECT on the base tables. Semantic views use **caller rights**: the query runs with the calling user's privileges, and the user must have access to both the SV *and* every base table it references.

## Owner Rights vs Caller Rights

| | Standard View (default) | Semantic View |
|--|------------------------|---------------|
| Query runs with | **Owner's** privileges | **Caller's** privileges |
| User needs SELECT on view? | Yes | Yes |
| User needs SELECT on base tables? | **No** | **Yes** |
| Privilege escalation possible? | Yes — view owner can expose data the caller can't see | **No** — caller must already have access |
| Analogy | Stored procedure with `EXECUTE AS OWNER` | Stored procedure with `EXECUTE AS CALLER` |

## How You Might Express This Need

- "We want the SV to be an additional access gate, not a bypass around table-level permissions"
- "Our base tables have row-level security / column masking — we need to make sure those policies still apply"
- "Can a user with SELECT on the SV read data they don't have SELECT on in the base tables?"
- "How do we design roles for a semantic layer — who owns the SV vs who queries it?"

## The Four-Role Pattern

This snippet demonstrates the minimal role structure for a production semantic layer:

| Role | What it can do | Has base table SELECT? | Has SV SELECT? |
|------|---------------|----------------------|----------------|
| `SV_CREATOR` | Creates SVs in `SV` schema | Yes (needed to define the SV) | Implicitly (creator) |
| `SV_OWNER` | Owns SVs (via future grant), grants SELECT on them | No | Owns |
| `SV_USER` | Queries the SV | **Yes** | **Yes** → **succeeds** |
| `SV_USER_NO_BASE_SELECT` | Has SV SELECT but no base table access | **No** | Yes → **fails** |

The key insight: `SV_USER_NO_BASE_SELECT` has SELECT on the SV but cannot query it because the engine needs to resolve the base tables with the *caller's* privileges. The error is immediate and clear.

## Schema Layout

Two separate schemas enforce the separation of concerns:

```
SV_CALLER_TEST.SV    ← semantic view lives here (SV_CREATOR creates, SV_OWNER owns)
SV_CALLER_TEST.DATA  ← base tables live here (SV_USER can see, SV_USER_NO_BASE_SELECT cannot)
```

## What Doesn't Work

- **Granting SELECT on the SV is not sufficient** — `SV_USER_NO_BASE_SELECT` fails even with SV SELECT because it lacks USAGE on `DATA` schema and SELECT on the base tables. This is the intended behavior.
- **`USE SECONDARY ROLES ALL` can unexpectedly grant access** — if a user has secondary roles that include base table access, the query may succeed. The snippet uses `USE SECONDARY ROLES NONE` to isolate role-specific privileges during testing.
- **Column masking and row access policies on base tables are respected** — because the query runs with caller rights, any policies applied to base table columns apply to SV queries too.

## Cleanup

Run the cleanup block at the bottom of `queries.sql` to remove all objects created by this snippet (roles, warehouse, database).

## Docs

- [Semantic view privileges](https://docs.snowflake.com/en/user-guide/views-semantic/privileges)
- [CREATE SEMANTIC VIEW — access control](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view#access-control-requirements)
- [GRANT privilege on semantic view](https://docs.snowflake.com/en/sql-reference/sql/grant-privilege)
- [Understanding caller's rights and owner's rights](https://docs.snowflake.com/en/developer-guide/stored-procedure/stored-procedures-rights)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | Roles, warehouse, DB/schemas, tables, and all privilege grants |
| `seed_data.sql` | Customer, address, and order data |
| `semantic_view.sql` | SV creation (as SV_CREATOR) + SELECT grants (as SV_OWNER) |
| `queries.sql` | Succeeding query (SV_USER), failing query (SV_USER_NO_BASE_SELECT), cleanup |

> ⚠️ **Requires ACCOUNTADMIN** (or both SECURITYADMIN and SYSADMIN). This snippet creates roles, a warehouse, and a dedicated database (`SV_CALLER_TEST`). It does **not** use the `--db` / `--schema` arguments from `run_snippet.py` — all objects are created under `SV_CALLER_TEST`.
