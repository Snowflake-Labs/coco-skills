# SYSTEM$EXPLAIN_SEMANTIC_QUERY

## The Problem

A `SEMANTIC_VIEW()` query fails — or returns unexpected results — and you need to understand what SQL the engine is actually generating. The error message points to a column name that doesn't exist, or a result looks wrong, but the SV definition looks correct. Without seeing the generated SQL, debugging is guesswork.

`SYSTEM$EXPLAIN_SEMANTIC_QUERY` solves this: given a semantic view name and a `SEMANTIC_VIEW()` query string, it returns the exact SQL the engine would generate and execute — without running it.

## When to Use It

| Situation | What EXPLAIN tells you |
|-----------|----------------------|
| Query fails with `invalid identifier` | Which column or alias the engine generated that doesn't resolve |
| Unexpected metric values | Whether the aggregation, join, or GROUP BY is what you expect |
| Debugging PRIVATE facts | Whether an intermediate fact is inlined correctly into downstream expressions |
| Verifying a complex join path | Which tables are joined and in what order |
| Learning how SVs work | The generated SQL is plain, readable SELECT — see the "magic" |

## How You Might Express This Need

- "My SEMANTIC_VIEW query fails and I can't tell why — how do I debug it?"
- "I want to see the SQL the semantic view generates for a given query"
- "The metric value looks wrong — how can I verify the aggregation?"
- "How do I know which join path the engine is using?"

## The SV Approach

```sql
SELECT SYSTEM$EXPLAIN_SEMANTIC_QUERY(
    'SNIPPETS.PUBLIC.SUPPORT_ANALYTICS_SV',
    $$
    SELECT sv.*
    FROM SEMANTIC_VIEW(
        SNIPPETS.PUBLIC.SUPPORT_ANALYTICS_SV
        METRICS tickets.total_tickets
        DIMENSIONS customers.tier
    ) AS sv
    $$
);
```

The function returns a single string: the SQL that would be executed against the underlying tables. It does **not** run the query — it is safe to call even for queries that would fail at runtime.

## What Doesn't Work

- **Only accepts `SEMANTIC_VIEW()` query syntax** — you cannot pass a general SQL query. The inner string must use the `SEMANTIC_VIEW(... METRICS ... DIMENSIONS ...)` syntax.
- **Does not validate that the generated SQL is correct** — it shows what the engine *intends* to generate, but the result can still fail if the generated SQL references something that doesn't exist (that's the point: you can see *why* it fails).
- **Output is a single long string** — use `PARSE_JSON` or a `::`-cast and pretty-print in your client if needed.

## Docs

- [SYSTEM$EXPLAIN_SEMANTIC_QUERY](https://docs.snowflake.com/en/sql-reference/functions/system_explain_semantic_query)
- [Querying a semantic view](https://docs.snowflake.com/en/user-guide/views-semantic/querying)
- [DESCRIBE SEMANTIC VIEW](https://docs.snowflake.com/en/sql-reference/sql/desc-semantic-view) — complementary introspection command

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `customers`, `support_tickets` table DDL |
| `seed_data.sql` | 4 customers (tiers), 10 tickets |
| `semantic_view.sql` | SV with PRIVATE fact, derived dimension, cross-table metric |
| `queries.sql` | EXPLAIN calls for simple, cross-table, and PRIVATE-fact queries |
