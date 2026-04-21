# Shared Degenerate Dimension

## The Problem

You have two (or more) fact tables that each contain a low-cardinality categorical column — like `region`, `country`, or `status` — but there is **no dedicated dimension table** for that column. The same concept exists on both facts, and you want a single dimension that works across all of them.

This is a **degenerate dimension**: a dimension attribute that lives on the fact table itself rather than in a separate dimension table. Making it "shared" so both facts can group by it requires creating a synthetic dimension entity that aggregates the distinct values from all sources.

## How You Might Express This Need

- "Both `store_orders` and `web_orders` have a `region` column. How do I create one `region` dimension that works for both?"
- "My three fact tables all have a `status` column. I want to slice any metric by `status` without picking one fact table's version."
- "I don't have a region lookup table — the values are baked into the facts."

## The Core Pattern

```
store_orders.region  ─┐
                       ├→ UNION → region_dim → regions.region (shared dimension)
web_orders.region    ─┘
```

**Step 1:** Create a helper that UNIONs distinct values from all fact tables:
```sql
CREATE VIEW region_dim AS
    SELECT DISTINCT region FROM store_orders
    UNION
    SELECT DISTINCT region FROM web_orders;
```

**Step 2:** Reference it as a `UNIQUE` entity in TABLES:
```sql
TABLES (
    regions AS region_dim UNIQUE (region),
    store_orders,
    web_orders
)
```

**Step 3:** Create relationships from each fact to the shared dim:
```sql
RELATIONSHIPS (
    store_to_region AS store_orders(region) REFERENCES regions,
    web_to_region   AS web_orders(region)   REFERENCES regions
)
```

Now `regions.region` is a single dimension that can be used with metrics from either fact.

## Physical View vs Inline SQL

| | Physical helper view | Inline SQL in TABLES |
|--|---------------------|---------------------|
| Syntax | `CREATE VIEW region_dim AS ...` | `regions AS (SELECT DISTINCT ... UNION ...) UNIQUE (region)` |
| Reusable across SVs | Yes | No — re-declare in each SV |
| Governance/discovery | Yes — view appears in catalog | No separate object |
| Best for | Production SVs | Quick prototyping |

## When Values Differ Across Facts

If `store_orders` has `region = 'Pacific'` but `web_orders` has no `'Pacific'` rows, the UNION ensures `'Pacific'` is still in `region_dim`. Web revenue will show 0 or NULL for `'Pacific'` — which is correct and expected.

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **dbt** | `ref('region_seed')` — a standalone seed or model with the dimension values |
| **LookML** | Role-playing dimension / one-to-one relationship from each explore |
| **Star schema** | Add a physical `dim_region` table to the warehouse layer |
| **Power BI** | Common "Region" table referenced by both fact tables |

## Docs

- [Identifying the relationships between logical tables](https://docs.snowflake.com/en/user-guide/views-semantic/sql#identifying-the-relationships-between-logical-tables)
- [CREATE SEMANTIC VIEW — RELATIONSHIPS clause](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view#label-create-semantic-view-relationships)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `store_orders`, `web_orders`, and `CREATE VIEW region_dim` (UNION helper) |
| `seed_data.sql` | 6 store orders and 7 web orders across 4 regions |
| `semantic_view.sql` | Two SV variants: physical helper view + inline SQL UNION |
| `queries.sql` | Shared region dimension queries, side-by-side channel comparison |
