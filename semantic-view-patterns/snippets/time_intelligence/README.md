# Time Intelligence (SPLY, YoY, MoM)

## The Problem

BI tools like Power BI, Tableau, and Looker have built-in time intelligence functions (`PREVIOUSYEAR`, `SAMEPERIODLASTYEAR`, `DATEADD`). In a semantic layer you need an equivalent pattern that lets users ask "how does this month compare to last year?" without writing any SQL.

**Example in this snippet**: Monthly sales revenue compared to the same period last month (SPLM) and same period last year (SPLY), with MoM% and YoY% derived metrics.

## How You Might Express This Need

- "Show me revenue this month vs last month"
- "What's our year-over-year growth rate?"
- "Compare Q3 2024 to Q3 2023"
- "Revenue this year vs same period last year, broken down by region"
- "I want PREVIOUSMONTH and SAMEPERIODLASTYEAR like we had in Power BI"

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **Power BI / DAX** | `CALCULATE([Revenue], SAMEPERIODLASTYEAR(Calendar[Date]))` — time intelligence functions on a Date table |
| **LookML** | `type: yesno` + `offset_periods` parameter, or `required_access_grants` + derived table with shifted dates |
| **Tableau** | `LOOKUP(SUM([Revenue]), -1)` for period-over-period in table calcs; `DATE_TRUNC` + FIXED LOD for explicit prior periods |
| **Raw SQL** | `LAG(revenue, 12) OVER (PARTITION BY region ORDER BY month)` for 12-month lag on monthly data |

Snowflake Semantic Views handle this with a **role-playing alias + computed FACT as a shifted join key** — no window functions, no ETL views, no pre-aggregated tables required.

## The SV Approach: Role-Playing Aliases with Shifted Keys

The core idea: create **multiple logical table aliases pointing to the same physical table**, each with a different join key that "shifts" those rows into the current period bucket.

### Three Parts

**1. Role-playing table alias** in TABLES:
```sql
, sales_ly AS SNIPPETS.PUBLIC.FACT_SALES
    PRIMARY KEY (ROW_ID)
    COMMENT = 'SPLY alias: same rows, shifted +1 year in the calendar join'
```

**2. Computed FACT as the shifted join key** in FACTS:
```sql
, sales_ly.sale_month_shifted_ly AS DATEADD('year', 1, SALE_MONTH)
    COMMENT = 'Computed FK: SALE_MONTH + 1 year'
```
The expression `DATEADD('year', 1, SALE_MONTH)` is a **scalar expression on the physical column**. The SV evaluates it per row to produce the join key.

**3. Relationship using the computed key** in RELATIONSHIPS:
```sql
, sales_ly_to_calendar AS sales_ly(sale_month_shifted_ly) REFERENCES calendar(MONTH)
```

### Why This Works

When you query `calendar.MONTH = '2024-03-01'`:

| Entity | Join condition | Rows returned |
|--------|---------------|---------------|
| `sales` | `SALE_MONTH = '2024-03-01'` | March 2024 rows |
| `sales_ly` | `DATEADD('year',1, SALE_MONTH) = '2024-03-01'` → `SALE_MONTH = '2023-03-01'` | March 2023 rows |
| `sales_lm` | `DATEADD('month',1, SALE_MONTH) = '2024-03-01'` → `SALE_MONTH = '2024-02-01'` | February 2024 rows |

The "shift" happens entirely in the join evaluation — no extra rows, no UNION ALL, no pre-built view.

### Cross-Entity Derived Metrics

Once you have LY and LM metrics, YoY% and MoM% are just derived metrics referencing both entities:

```sql
, yoy_pct AS DIV0(sales.revenue - sales_ly.revenue_ly, sales_ly.revenue_ly) * 100
    COMMENT = 'Revenue % change vs same period last year'
```

No table prefix on the left side (`yoy_pct`) — these are **global derived metrics** that reference metrics from different entities.

## What Works

| Pattern | Query |
|---------|-------|
| Monthly SPLY comparison | `DIMENSIONS calendar.year, calendar.month METRICS sales.revenue, sales_ly.revenue_ly` |
| Annual YoY totals | `DIMENSIONS calendar.year METRICS sales.revenue, sales_ly.revenue_ly, yoy_pct` |
| MoM % by region | `DIMENSIONS calendar.year, calendar.month, sales.region METRICS mom_pct` |
| Full dashboard row | All metrics together grouped by month |

## What Doesn't Work

**YTD / QTD / MTD** — this pattern gives point-in-time period comparisons, not cumulative running totals. For YTD use `SUM OVER (PARTITION BY year ORDER BY date ROWS UNBOUNDED PRECEDING)`. See `window_metrics/`.

**NULL for boundary periods** — `revenue_ly` is NULL for all 2023 rows (no 2022 data in dataset). `revenue_lm` is NULL for January 2023. Handle with `COALESCE(revenue_ly, 0)` in a `standard_sql` wrapper.

**Quarter/period-to-date breakdowns** — the shift is a full period (1 month or 1 year). Partial periods (e.g., "Q1 to date" for a mid-quarter query) require additional calendar filtering logic outside the SV.

## Comparison with `window_metrics/`

| | `window_metrics` | `time_intelligence` |
|-|-----------------|---------------------|
| Pattern | `LAG(n)` / `SUM() OVER (...)` window functions on a single entity | Role-playing aliases + shifted join keys across entities |
| Best for | Daily grain, rolling averages, YTD accumulators | Monthly/quarterly grain, SPLY, SPLM, YoY% |
| Requires calendar table | No | Yes |
| Cross-period filters | Limited (by row offset) | Natural — filter on calendar dimensions |
| NULL behavior | First N rows are NULL | First period in dataset is NULL |

## Docs

- [CREATE SEMANTIC VIEW — FACTS (scalar expressions)](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view#label-create-semantic-view-facts)
- [Cross-table (derived) metrics](https://docs.snowflake.com/en/user-guide/views-semantic/sql#defining-cross-table-metrics)
- [Role-playing logical tables](https://docs.snowflake.com/en/user-guide/views-semantic/sql#defining-role-playing-logical-tables)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `DIM_CALENDAR` and `FACT_SALES` table DDL |
| `seed_data.sql` | 24 calendar months (2023–2024) + 48 sales rows (East/West × 24 months) |
| `semantic_view.sql` | SV with 3 logical table aliases, computed-FK FACTS, and 8 metrics |
| `queries.sql` | SPLY comparison, MoM%, YoY by region, full dashboard row — plus gotchas |
