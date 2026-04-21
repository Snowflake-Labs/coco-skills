# Introspection Commands

## The Problem

You need to discover what's in a Semantic View, understand metric-dimension compatibility, or trace data lineage — without reading the raw DDL.

## Commands Covered

### `DESCRIBE SEMANTIC VIEW`
Full DDL round-trip inspection — returns every table, relationship, dimension, metric, fact, VQR, and AI metadata block.
```sql
DESCRIBE SEMANTIC VIEW SNIPPETS.PUBLIC.MULTI_CHANNEL_SV;
```

### `SHOW SEMANTIC VIEWS`
List all SVs in a schema (with optional pattern matching).
```sql
SHOW SEMANTIC VIEWS IN SNIPPETS.PUBLIC;
SHOW SEMANTIC VIEWS LIKE '%CHANNEL%' IN SNIPPETS.PUBLIC;
```

### `SHOW SEMANTIC METRICS`
List all metrics in a SV — logical name, expression, synonyms, tags.
```sql
SHOW SEMANTIC METRICS IN SNIPPETS.PUBLIC.MULTI_CHANNEL_SV;
```

### `SHOW SEMANTIC DIMENSIONS FOR METRIC`
Critical for multi-fact SVs: which dimensions are **compatible** with a specific metric?
```sql
SHOW SEMANTIC DIMENSIONS IN SNIPPETS.PUBLIC.MULTI_CHANNEL_SV
FOR METRIC CHANNEL_STORE_SALES.STORE_REVENUE;
```
Different metrics may have different dimension availability — this command tells you exactly what can be paired without errors.

### `snowflake.core.get_lineage()`
Table function for upstream (source tables) and downstream (reports, agents) dependency tracing.
```sql
SELECT * FROM TABLE(
    SNOWFLAKE.CORE.GET_LINEAGE(
        'SNIPPETS.PUBLIC.MULTI_CHANNEL_SV', 'SEMANTIC_VIEW', 'UPSTREAM', 5
    )
);
```

## When to Use Each

| Task | Command |
|------|---------|
| Read the SV definition | `DESCRIBE SEMANTIC VIEW` |
| Find all SVs in a schema | `SHOW SEMANTIC VIEWS` |
| Discover available metrics | `SHOW SEMANTIC METRICS` |
| Check metric-dim compatibility | `SHOW SEMANTIC DIMENSIONS FOR METRIC` |
| Find which tables feed this SV | `GET_LINEAGE ... 'UPSTREAM'` |
| Find what depends on this SV | `GET_LINEAGE ... 'DOWNSTREAM'` |

## Docs

- [DESCRIBE SEMANTIC VIEW](https://docs.snowflake.com/en/sql-reference/sql/desc-semantic-view)
- [SHOW SEMANTIC VIEWS](https://docs.snowflake.com/en/sql-reference/sql/show-semantic-views)
- [SHOW SEMANTIC METRICS](https://docs.snowflake.com/en/sql-reference/sql/show-semantic-metrics)
- [SHOW SEMANTIC DIMENSIONS FOR METRIC](https://docs.snowflake.com/en/sql-reference/sql/show-semantic-dimensions-for-metric)
- [GET_LINEAGE function](https://docs.snowflake.com/en/sql-reference/functions/get_lineage)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | Reference note — uses `multi_fact_table` SV |
| `queries.sql` | All introspection commands with annotations |

**Prerequisites:** Deploy `multi_fact_table/semantic_view.sql` first (creates `SNIPPETS.PUBLIC.MULTI_CHANNEL_SV`).
