# Row Access Policies with Semantic Views

## The Problem

You have a fact table and a dimension table. You want users to see only metrics for the dimensions they are authorized to access — for example, a regional sales analyst should see only their region's revenue, with no visibility into other regions.

The natural instinct is to apply a Row Access Policy (RAP) to the dimension table. But in a Semantic View, this creates an unexpected result: fact rows for filtered-out dimensions still appear — they just show up with **NULL dimension values**. The aggregated metrics from those rows are visible in the NULL row, leaking both the existence and magnitude of data the user should not see.

**Example in this snippet**: Four sales regions (Northeast, Southeast, Northwest, Southwest). `REGION_A_ANALYST` should see only Northeast and Southeast. With a RAP on the dimension table only, they instead see:

```
Northeast  $1,250  2 orders   ← correct
Southeast    $750  2 orders   ← correct
NULL       $2,600  4 orders   ← should not exist
```

The NULL row reveals that $2,600 of revenue exists in other regions — even though those region names are hidden.

## Why This Happens

The SV engine generates a **LEFT JOIN** between the fact table and the dimension table. The RAP filters rows in the dimension table, but LEFT JOIN semantics mean unmatched fact rows survive — they just receive NULL for every dimension column. Those rows are then grouped under a single NULL dimension value in the result.

A RAP on the dimension table controls **what dimension data is visible**, but it does not control **which fact rows are included**.

## Two Workarounds

### Workaround 1: Helper view with inner join

Create a SQL view that **inner-joins** the fact table to the dimension table, and use that view as the fact entity in the Semantic View instead of the raw fact table:

```sql
CREATE OR REPLACE VIEW ORDERS_FILTERED AS
    SELECT o.order_id, o.region_id, o.order_date, o.amount
    FROM ORDERS o
    INNER JOIN SALES_REGIONS r ON o.region_id = r.region_id;
```

When the RAP hides a `SALES_REGIONS` row, the `INNER JOIN` also drops the corresponding `ORDERS` rows from the view. No orphaned fact rows reach the SV — no NULL rows appear.

**Best for**: Situations where you cannot or should not alter the physical fact table (e.g., it is shared by other SVs or queries that must not be filtered).

**Trade-off**: Adds an intermediate view object to manage; the filter lives in two places (view DDL + RAP on dimension table).

### Workaround 2: Apply the RAP to the fact table

Apply the same Row Access Policy directly to the fact table:

```sql
ALTER TABLE ORDERS
    ADD ROW ACCESS POLICY region_access_policy ON (REGION_ID);
```

Now fact rows are filtered at the source — before any join occurs. The original SV (without the helper view) works correctly. No NULL rows, no leaked metrics.

**Best for**: Most cases. Simpler than the helper view approach and more robust — the filter is enforced regardless of how the data is queried (SV, direct SQL, etc.).

**Trade-off**: Modifies the underlying table. All queries against `ORDERS` — not just through the SV — will be subject to the RAP.

## Comparison

| | RAP on dimension only (anti-pattern) | Workaround 1: helper view | Workaround 2: RAP on fact |
|--|--|--|--|
| NULL rows in results? | **Yes — data leakage** | No | No |
| Modifies underlying fact table? | No | No | **Yes** |
| Intermediate view required? | No | **Yes** | No |
| Applies outside the SV too? | No | No | **Yes** |
| Simpler to maintain? | — | Moderate | **Yes** |

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **Power BI** | Row-level security (RLS) defined on the semantic model. Applied at the model layer, not the table. Fan-out/NULL behavior depends on report layout and cross-filter direction. |
| **Tableau** | User filters or row-level security via data source filters or `USERNAME()` / `ISMEMBEROF()` calculations. Must be applied to every data source that joins to a sensitive table. |
| **dbt** | No native row-level security. Typically handled at the warehouse layer via Snowflake policies or views, then modeled in dbt. |
| **LookML** | `access_filter` or `sql_where` on an Explore. Looker applies this as a WHERE clause on the SQL query, effectively filtering both the dimension and any joined facts. |
| **Raw SQL** | Requires a WHERE clause or JOIN condition on every query — no enforcement guarantee. |

Snowflake RAPs give you centralized, policy-driven enforcement at the storage layer. The challenge is understanding _where_ in the data pipeline to apply them when using the semantic layer.

## What Doesn't Work

- **RAP on dimension table alone**: Causes NULL rows in SEMANTIC_VIEW() results for any fact rows whose dimension join is blocked. This is the core anti-pattern this snippet addresses.
- **Filtering in AI_SQL_GENERATION instructions**: Instructing Cortex Analyst to "only return results for the user's region" is not a security boundary — it can be bypassed and is not enforced by the engine.
- **Relying on SEMANTIC_VIEW() WHERE clause**: A caller can omit or override WHERE conditions. Policy enforcement must live at the table or view layer.

## Docs

- [Row Access Policies — Snowflake Documentation](https://docs.snowflake.com/en/user-guide/security-row-intro)
- [ALTER TABLE — ADD ROW ACCESS POLICY](https://docs.snowflake.com/en/sql-reference/sql/alter-table)
- [Semantic Views — Overview](https://docs.snowflake.com/en/user-guide/views-semantic)

## Files

> **Note**: This snippet creates a dedicated environment (`RAP_TEST` database, `REGION_A_ANALYST` / `REGION_B_ANALYST` roles). The `--db` / `--schema` arguments to `run_snippet.py` are ignored — all objects are hardcoded to `RAP_TEST.PUBLIC`. The analyst roles need USAGE on an existing warehouse (Tutorial mode handles this automatically). Run the cleanup block in `queries.sql` when done.

| File | Description |
|------|-------------|
| `schema.sql` | `ORDERS` and `SALES_REGIONS` tables; roles `REGION_A_ANALYST` and `REGION_B_ANALYST`; RAP applied to dimension table (anti-pattern setup) |
| `seed_data.sql` | 4 regions + 8 orders (2 per region) with known per-region totals |
| `semantic_view.sql` | Anti-pattern SV + helper view + workaround 1 SV |
| `queries.sql` | Role-switching demo: NULL-row problem → workaround 1 → workaround 2 → cleanup |
