# Derived Metrics

## The Problem

You have metrics defined on separate entities (e.g. store sales, web sales, catalog sales) and want to combine them into **cross-entity derived metrics**: totals, ratios, and % of total — all maintained in one place without duplicating SQL.

## How You Might Express This Need

- "What's our total revenue across all channels? And what % does each channel contribute?"
- "Show net revenue = gross revenue minus returns"
- "Store is growing — what's its share of total sales vs last quarter?"
- "Derive a metric from two other metrics without writing a new SQL model"

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **SQL** | `store_revenue + web_revenue AS total_revenue` in a SELECT or CTE |
| **LookML** | `measure: total_revenue { type: number; sql: ${store_revenue} + ${web_revenue} }` |
| **dbt** | Calculated metric in metrics YAML or derived model |
| **Power BI** | DAX `TOTAL_REVENUE = [STORE_REVENUE] + [WEB_REVENUE]` |

## The SV Approach

Derived metrics reference other metric names by logical name — **no table prefix**:
```sql
METRICS (
    store_sales.store_revenue AS SUM(revenue),
    web_sales.web_revenue     AS SUM(revenue),

    -- Cross-table derived: NO table prefix on the derived metric name
    total_revenue AS store_sales.store_revenue + web_sales.web_revenue,

    -- Ratio: derives from the derived metric itself
    store_pct AS store_sales.store_revenue / total_revenue
)
```

## Key Rules

- Derived metric names **must not** have a table prefix — they are not scoped to an entity
- A derived metric can reference other derived metrics as building blocks
- Division returns a decimal (0.0–1.0) — multiply × 100 in standard SQL wrapping for display as percent
- All referenced metrics must be reachable via the same set of relationships/dimensions in the query
- Derived metrics are additive by default — they do not support NON ADDITIVE BY

## Docs

- [Defining derived metrics](https://docs.snowflake.com/en/user-guide/views-semantic/sql#defining-derived-metrics)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | Three channel fact tables + date dimension |
| `seed_data.sql` | 6 months × 3 channels |
| `semantic_view.sql` | SV with per-channel metrics, total, and % of total |
| `queries.sql` | Channel mix, quarterly comparison, standard SQL for display |
