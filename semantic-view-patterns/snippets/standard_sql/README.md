# Standard SQL on Semantic Views

## The Problem

Not every consumer of a Semantic View uses Cortex Analyst or the `SEMANTIC_VIEW()` function. Data analysts in SQL clients, BI tools, and dbt models often need to query a SV like a regular table or view using **plain SQL syntax**.

Snowflake allows querying a SV with a regular SELECT — but with some important rules around aggregate functions.

## How You Might Express This Need

- "Can I connect Tableau directly to a Semantic View without using SEMANTIC_VIEW()?"
- "I want to query the SV in a dbt model using SELECT ... FROM ... WHERE"
- "How do I use a window function on top of SV output?"
- "I just want to see distinct dates from the SV — no aggregation needed"

## The Rules

When using standard SQL on a SV (not SEMANTIC_VIEW()):

| Scenario | Rule |
|----------|------|
| SELECT metric + other columns | Wrap metric in `ANY_VALUE()`, `MIN()`, or `MAX()` |
| SELECT metric only | No wrapper needed |
| SELECT dimensions only | No wrapper, no GROUP BY needed — returns distinct values |
| WHERE clause | Works normally on dimensions |
| ORDER BY, LIMIT | Work normally |
| JOIN another table/SV | Works normally |

### Why `ANY_VALUE()`?
Because SVs are not regular tables — they are aggregated semantic objects. When combined with non-metric columns, the engine needs an aggregate function to resolve the grouping. `ANY_VALUE` is the idiomatic "I know this is functionally deterministic for this group" wrapper.

## Example

```sql
SELECT
    month,
    ANY_VALUE(total_revenue) AS revenue
FROM SNIPPETS.PUBLIC.CHANNEL_SALES_SV
WHERE year = 2024
GROUP BY ALL
ORDER BY month;
```

## Docs

- [Querying semantic views — standard SQL FROM clause](https://docs.snowflake.com/en/user-guide/views-semantic/querying#specifying-the-name-of-the-semantic-view-in-the-from-clause)
- [Querying semantic views — SEMANTIC_VIEW() clause](https://docs.snowflake.com/en/user-guide/views-semantic/querying#specifying-the-semantic-view-clause-in-the-from-clause)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | Reference note — uses `derived_metrics` SV |
| `queries.sql` | Standard SQL patterns: ANY_VALUE, metric-only, dim-only, JOINs, window on top |

**Prerequisites:** Deploy `derived_metrics/semantic_view.sql` first (creates `SNIPPETS.PUBLIC.CHANNEL_SALES_SV`).
