# Window Metrics (LAG, Rolling Average, YTD)

## The Problem

You need metrics that **span time** — comparing today to a prior period, smoothing daily noise into a rolling average, or accumulating a year-to-date total. These require window functions that operate over ordered rows, not simple aggregations.

## How You Might Express This Need

- "Show me revenue with a 7-day rolling average to smooth out weekend dips"
- "Compare today's sales to the same day 30 days ago"
- "I want a running YTD total that resets each January 1st"
- "What was the 7-day rolling average 30 days ago, for period-over-period comparison?"

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **SQL** | `AVG(revenue) OVER (ORDER BY date RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW)` |
| **LookML** | `measure: rolling_7d { type: running_total ... }` (limited) |
| **dbt** | Window functions in model SQL; must be pre-materialized |
| **Power BI** | DAX `DATESINPERIOD()`, `TOTALYTD()` |
| **Tableau** | Table calculations: `WINDOW_AVG` for rolling, `LOOKUP(SUM([metric]), -1)` for LAG, `RUNNING_SUM` for YTD. Limited to dimensions present in the current view. |

## Three Window Metric Patterns

### 1. Rolling Average (RANGE INTERVAL)
```sql
STORESALES.rolling_7d_avg AS
  AVG(total_revenue)
  OVER (PARTITION BY EXCLUDING daily_sales.date
        ORDER BY daily_sales.date
        RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW)
```
`PARTITION BY EXCLUDING` = "partition by all dimensions in the query **except** the ORDER BY dim." If channel is requested, each channel gets its own independent window.

### 2. LAG — Prior Period Comparison
```sql
daily_sales.revenue_30d_ago AS
  LAG(total_revenue, 30)
  OVER (PARTITION BY EXCLUDING daily_sales.date
        ORDER BY daily_sales.date)
```
Returns the value of `total_revenue` from 30 rows (days) earlier in the same partition. NULL for the first 30 rows.

### 3. YTD Cumulative Sum
```sql
daily_sales.ytd_revenue AS
  SUM(total_revenue)
  OVER (PARTITION BY daily_sales.year
        ORDER BY daily_sales.date
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
```
`PARTITION BY year` (not EXCLUDING) explicitly partitions by year — the running total resets at each year boundary.

## Key Rules

- Window metrics must include their ORDER BY dimension in the DIMENSIONS clause of the query
- `PARTITION BY EXCLUDING <dim>` partitions by all other query dimensions — adding more dimensions (e.g. channel) automatically applies the window per-group
- `PARTITION BY <dim>` (without EXCLUDING) partitions explicitly by that dimension only
- `LAG(n)` returns NULL for the first n rows — expected behavior

## What Doesn't Work

### PARTITION BY EXCLUDING on FACT-based metrics

If you declare a measure column in the `FACTS` clause and then use it in a base metric, `PARTITION BY EXCLUDING` will fail:

```sql
FACTS (fact_table.revenue AS revenue)  -- ← declares revenue as a FACT

METRICS (
  fact_table.total_revenue AS SUM(fact_table.revenue),  -- ← references a FACT

  fact_table.rolling_avg AS
    AVG(total_revenue)
    OVER (PARTITION BY EXCLUDING fact_table.date  -- ← FAILS
          ORDER BY fact_table.date
          RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW)
)
```
**Error:** `PARTITION BY EXCLUDING is not allowed when the window function operates over a row-level expression.`

The engine classifies any metric whose expression references a FACT column as "row-level" — even though it's wrapped in `SUM()`. The fix is to **not declare measure columns in FACTS**. Leave them as plain table columns and reference them by bare physical name in the metric:

```sql
-- No FACTS clause for revenue

METRICS (
  fact_table.total_revenue AS SUM(revenue),  -- ← bare physical column name, no entity prefix

  fact_table.rolling_avg AS
    AVG(total_revenue)
    OVER (PARTITION BY EXCLUDING fact_table.date  -- ← works
          ORDER BY fact_table.date
          RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW)
)
```

### ROWS BETWEEN without PARTITION BY EXCLUDING

Dropping `PARTITION BY EXCLUDING` entirely and using bare `ORDER BY` with `ROWS BETWEEN <n> PRECEDING` also fails:

```sql
fact_table.rolling AS
  SUM(total_revenue)
  OVER (ORDER BY fact_table.date
        ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)  -- ← FAILS
```
**Error:** `Unsupported expression in the definition of derived metric.`

Always include `PARTITION BY EXCLUDING` (or an explicit `PARTITION BY`) with window metrics.

### Entity prefix in metric expressions

Window metrics must use the **entity prefix on the metric name** (matching the snippet style `entity.metric_name AS ...`). Metrics defined without the entity prefix (`total_revenue AS SUM(revenue)`) may fail to resolve correctly in window function context. Always use:
```sql
fact_table.total_revenue AS SUM(revenue)
fact_table.rolling_avg AS AVG(total_revenue) OVER (...)
```

## Docs

- [Defining and querying window function metrics](https://docs.snowflake.com/en/user-guide/views-semantic/querying#defining-and-querying-window-function-metrics)
- [CREATE SEMANTIC VIEW — window function syntax](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view#label-create-semantic-view-window-function-syntax)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `daily_sales` table |
| `seed_data.sql` | 35 days of daily sales |
| `semantic_view.sql` | SV with rolling avg, LAG, and YTD metrics |
| `queries.sql` | Each window pattern queried independently + combined |
