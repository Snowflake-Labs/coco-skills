# Multi-Fact Table

## The Problem

A single business domain has **multiple independent fact tables** that should all be queryable through one semantic view — sharing common dimensions (product, date) but with separate metrics. You also want **cross-fact derived metrics** (e.g. net revenue = store + web − returns).

## How You Might Express This Need

- "I have store_sales, web_sales, and returns tables — I want them all in one SV sharing a product and date dimension"
- "Total revenue should include both channels. Net revenue subtracts returns."
- "SHOW DIMENSIONS for store_sales shouldn't require including web_sales columns"

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **SQL** | Multiple CTEs, LEFT JOINs on shared dims; fan-out/funnel query pattern |
| **LookML** | Multiple explores joined to a single shared dimension view |
| **dbt** | `metrics.yml` with multiple models; union or join at reporting layer |
| **Power BI** | Multiple fact tables in a star schema with shared dim tables |
| **Tableau** | Multi-source Relationships or data blending. Each fact connects to shared dimension tables; cross-fact comparisons require blends or custom SQL. |

## The SV Approach

Each fact table is declared independently and joined to the **shared dimensions**:
```sql
TABLES (
    dim_product PRIMARY KEY (product_id),
    channel_dim_date PRIMARY KEY (date_id),
    channel_store_sales,
    channel_web_sales,
    channel_returns
)
RELATIONSHIPS (
    store_to_date    AS channel_store_sales(date_id)    REFERENCES channel_dim_date,
    store_to_product AS channel_store_sales(product_id) REFERENCES dim_product,
    web_to_date      AS channel_web_sales(date_id)      REFERENCES channel_dim_date,
    ...
)
```

Cross-fact derived metrics reference both fact entities:
```sql
total_gross_revenue AS channel_store_sales.store_revenue + channel_web_sales.web_revenue
net_revenue AS total_gross_revenue - channel_returns.total_returns
```

## Key Behavior

- Querying only `store_revenue` does **not** join `channel_web_sales` — the engine is selective
- `SHOW SEMANTIC DIMENSIONS FOR METRIC store_revenue` will show only dims reachable via store_sales's relationships
- Cross-fact derived metrics trigger a join/aggregation across all referenced facts when queried
- Each fact can have its own set of fact-specific metrics; they coexist in the same SV

## Docs

- [Using SQL commands to create and manage semantic views](https://docs.snowflake.com/en/user-guide/views-semantic/sql)
- [Defining derived metrics (cross-fact totals)](https://docs.snowflake.com/en/user-guide/views-semantic/sql#defining-derived-metrics)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | Product dim, date dim, 3 fact tables |
| `seed_data.sql` | 3 products × 6 months across all 3 facts |
| `semantic_view.sql` | SV with 3 facts sharing 2 dimensions + cross-fact derived metrics |
| `queries.sql` | Channel comparison, net revenue, return rate |
