# Materialization

> ⚠️ **Private Preview feature** — available only to selected accounts. Contact your Snowflake account team to enable.

## The Problem

Semantic view queries scan the underlying base tables and re-aggregate on every request. For large datasets or high-query-volume analytics, this can be slow and expensive. **Materialization** pre-computes selected dimension/metric combinations and stores them, so queries can read from the pre-aggregated result instead of scanning base tables.

## How You Might Express This Need

- "Our revenue-by-customer query runs on 100M rows every time — can we pre-aggregate it?"
- "We have historical data from 3 years ago that never changes — can we freeze-materialize it?"
- "The SV is fast for small queries but slow for the full rollup our CFO dashboard runs daily"

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **SQL** | Materialized views; pre-aggregated summary tables |
| **dbt** | `+materialized: table` on summary models; incremental models |
| **LookML** | Aggregate awareness / persistent derived tables (PDTs) |
| **Power BI** | Aggregations feature on Import tables |
| **Tableau** | Materialized extracts |

## How It Works

**Step 1:** Set `MAX_STALENESS` on the SV (minimum 120 seconds):
```sql
CREATE SEMANTIC VIEW my_sv
  ...
  MAX_STALENESS = '1 hour';
```

**Step 2:** Grant the materialization privilege to your role:
```sql
GRANT ADD SEMANTIC VIEW MATERIALIZATION ON SCHEMA db.schema TO ROLE my_role;
```

**Step 3:** Add a materialization for the dimensions/metrics you want pre-aggregated:
```sql
ALTER SEMANTIC VIEW my_sv ADD MATERIALIZATION revenue_by_customer
  WAREHOUSE = my_wh
  AS
    DIMENSIONS mat_customers.customer_name, mat_orders.order_year
    METRICS mat_orders.total_revenue;
```

Queries automatically use the materialization — no change to query syntax required.

## Reaggregation: Additive vs Non-Additive

A materialization on `(customer, year)` can serve a query for just `(customer)` — by summing across year values.

| Metric type | Reaggregatable? | Notes |
|-------------|----------------|-------|
| `SUM` | ✅ Yes | Sum across extra dimensions |
| `COUNT` | ✅ Yes | Sum across extra dimensions |
| `MIN` / `MAX` | ✅ Yes | Re-apply MIN/MAX |
| `AVG` | ❌ No | Weighted average can't be derived from group averages |
| `COUNT(DISTINCT ...)` | ❌ No | Can't re-count distinct from a pre-counted result |
| `MEDIAN`, `PERCENTILE` | ❌ No | Non-decomposable statistics |

## IMMUTABLE WHERE — Incremental Refresh

Without `IMMUTABLE WHERE`, every refresh recomputes the entire materialization (expensive for large SVs).

```sql
ALTER SEMANTIC VIEW my_sv ADD MATERIALIZATION historical_revenue
  WAREHOUSE = my_wh
  IMMUTABLE WHERE (order_date < '2024-01-01')   -- only rows AFTER this date are refreshed
  AS ...
```

**Strongly recommended** for historical data that doesn't change.

## What Cannot Be Materialized

- Window function metrics (LAG, rolling AVG, YTD)
- Semi-additive metrics (`NON ADDITIVE BY`)
- Metrics with `USING` clause (multi-path disambiguation)

## Docs

- [Materializing dimensions and metrics in semantic views ⚠️ Private Preview](https://docs.snowflake.com/en/LIMITEDACCESS/semantic-views-materialization)
- [ALTER SEMANTIC VIEW — ADD / REFRESH / DROP MATERIALIZATION](https://docs.snowflake.com/en/sql-reference/sql/alter-semantic-view)
- [SEMANTIC_VIEW_MATERIALIZATION_REFRESH_HISTORY](https://docs.snowflake.com/en/sql-reference/functions/semantic_view_materialization_refresh_history)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `mat_orders` + `mat_customers` |
| `seed_data.sql` | 5 customers, 12 orders spanning 2023-2024 |
| `semantic_view.sql` | SV creation + ADD MATERIALIZATION + all operational commands |
| `queries.sql` | Queries showing when materialization is used, reaggregation, fallback |
