# Scoped Dataset (SQL Query as Logical Table)

> ⚠️ **Private Preview feature** — available only to selected accounts. Contact your Snowflake account team to enable.

## The Problem

Your source table contains data for multiple lines of business, regions, or tenants. You want to create a Semantic View that is **pre-scoped** to one subset — so that users of the SV only see data for their specific LOB/region, without adding a WHERE clause to every query. No intermediate view or additional table required.

A related use case: **pre-joining two tables** into a single logical entity inside the SV, so downstream consumers see it as one flat entity.

## How You Might Express This Need

- "I have a single `sales_transactions` table with a `lob` column. I want one SV for 'Retail' and one for 'Enterprise' — without creating separate physical tables."
- "My orders and order_items tables should look like one entity to analysts — join them inline before exposing."
- "Don't show SMB data in the EMEA team's SV."

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **SQL** | `CREATE VIEW retail_sales AS SELECT * FROM sales WHERE lob = 'Retail'` |
| **LookML** | `sql_table_name: (SELECT * FROM sales WHERE lob = 'Retail') ;;` in a view |
| **dbt** | Separate model with `WHERE` filter |
| **Power BI** | Row-level security or filtered Import table |

## The SV Approach

Use `AS (SELECT ...)` in the TABLES clause:

```sql
CREATE SEMANTIC VIEW retail_orders_sv
TABLES (
    -- The alias 'orders' is required when using AS (...)
    orders AS (
        SELECT * FROM sales_transactions WHERE lob = 'Retail'
    ) PRIMARY KEY (transaction_id)
)
...
```

You can also **pre-join tables** into a single logical entity:
```sql
customer_info AS (
    SELECT * FROM customers JOIN addresses
    ON customers.id = addresses.customer_id
) PRIMARY KEY (id)
```

## Key Rules (from docs)

- The alias for the logical table is **required** when using `AS (...)`
- Session variables (`$var`) cannot be used in the inline query
- Same limitations as `CREATE VIEW` — no DDL, no DML, no transactions
- The filter is embedded in the SV DDL — changing it requires `CREATE OR REPLACE SEMANTIC VIEW`
- `DESCRIBE SEMANTIC VIEW` shows the inline query in a `DEFINITION` property (not `BASE_TABLE_NAME`)

## Two SVs from One Table Pattern

A powerful use: create **separate SVs for each LOB** from a single source table. Each SV has its own metrics and dimension scoping:

```
sales_transactions (all LOBs)
    ↓                ↓
retail_sv         enterprise_sv
(lob='Retail')   (lob='Enterprise')
```

## Docs

- [Using an SQL query as a logical table in a semantic view ⚠️ Private Preview](https://docs.snowflake.com/en/LIMITEDACCESS/semantic-views-inline-view)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `sales_transactions` table with `lob` column |
| `seed_data.sql` | Transactions across Retail, Enterprise, and SMB LOBs |
| `semantic_view.sql` | Two separate SVs scoped by LOB + a join-inline example |
| `queries.sql` | Queries against each scoped SV; DESCRIBE to verify inline filter |
