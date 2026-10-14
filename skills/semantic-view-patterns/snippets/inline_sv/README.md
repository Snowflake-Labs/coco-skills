# Inline Semantic View and SQL Subquery as Table

> ⚠️ **Private Preview feature** — the inline SV syntax (`WITH ... AS SEMANTIC VIEW`) is not yet generally available. Contact your Snowflake account team to enable.

## Two Related Patterns

Both patterns let you work with Semantic Views **without creating a persistent named object**.

---

## Pattern 1: SQL Subquery as Table Definition

Use a SQL query as the source for a table in the TABLES clause. The SV definition is persisted (via CREATE), but one of its "tables" is actually an inline SQL expression.

**Use when:** You want to filter source data, exclude certain rows, or combine tables before exposing them through the SV — without creating an intermediate view.

```sql
CREATE SEMANTIC VIEW my_sv
TABLES (
    orders,
    customers AS (
        SELECT * FROM customers WHERE tier = 'premium'
    ) UNIQUE (customer_id)
)
...
```

The subquery filters to only premium customers. The SV consumers see a "customers" entity that already has the filter applied.

---

## Pattern 2: Inline / Ad-Hoc Semantic View (SV CTE)

Define and query a SV in a single statement — no CREATE needed. The SV exists only for the duration of the query.

**Use when:** Testing DDL before committing, writing dbt unit tests, rapid prototyping.

```sql
WITH adhoc_sv AS SEMANTIC VIEW
TABLES (
    orders,
    customers UNIQUE (customer_id)
)
RELATIONSHIPS (
    orders(customer_id) REFERENCES customers
)
DIMENSIONS (
    customers.customer_name AS customer_name
)
METRICS (
    orders.total_revenue AS SUM(amount)
)
SELECT * FROM SEMANTIC_VIEW(
    adhoc_sv
    DIMENSIONS customers.customer_name
    METRICS orders.total_revenue
);
```

---

## Pattern Comparison

| | SQL Subquery in TABLES | Ad-Hoc SV (WITH ... AS SEMANTIC VIEW) |
|--|------------------------|--------------------------------------|
| Persisted | Yes (CREATE SEMANTIC VIEW) | No — exists for one query only |
| Usable by Cortex Analyst | Yes | No |
| Use for testing | Limited | Ideal — no DDL pollution |
| dbt unit testing | No | Yes |
| Filter source data | Yes | Yes |

---

## Docs

- [Using an SQL query as a logical table in a semantic view ⚠️ Private Preview](https://docs.snowflake.com/en/LIMITEDACCESS/semantic-views-inline-view)
- [WITH ... AS SEMANTIC VIEW (inline SV)](https://docs.snowflake.com/en/sql-reference/constructs/semantic_view)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `inline_orders` + `inline_customers` |
| `seed_data.sql` | 4 customers (2 premium, 2 standard), 6 orders |
| `semantic_view.sql` | Both patterns with full working SQL |
