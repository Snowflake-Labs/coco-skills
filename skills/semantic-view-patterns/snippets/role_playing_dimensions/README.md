# Role-Playing Dimensions

## The Problem

You have a fact table with multiple foreign keys that all reference the same physical dimension table. A classic example: an `ORDERS` table with both an `ORDER_DATE` and a `SHIP_DATE`, both of which should join to the same `DIM_DATE` calendar table.

The naive approach — one dimension table, two relationships, shared columns — hits an immediate ambiguity. The SV engine can't tell which relationship path to use when you group by a dimension like `year` or `month`, because that column belongs to both roles simultaneously.

**How You Might Express This Need:**
- "I want to see revenue both by when orders were placed *and* by when they shipped."
- "My DIM_DATE has 50 columns. I don't want to duplicate it just to get two date roles."
- "In Power BI I'd just create two date table relationships. How do I do that in a Semantic View?"
- "I need ORDER_YEAR and SHIP_YEAR as independent dimensions in the same report."

## The Solution: Alias the Dimension Table Twice

In the `TABLES` clause, list the same physical table under two different logical names. The SV engine treats each alias as a completely separate entity — separate joins, separate dimension columns, no ambiguity.

```sql
TABLES (
  orders         AS ORDERS    PRIMARY KEY (ORDER_ID),
  order_date_dim AS DIM_DATE  PRIMARY KEY (DATE_KEY),  -- role 1
  ship_date_dim  AS DIM_DATE  PRIMARY KEY (DATE_KEY)   -- role 2, same physical table
)
RELATIONSHIPS (
  orders_to_order_date AS orders(ORDER_DATE) REFERENCES order_date_dim(DATE_KEY),
  orders_to_ship_date  AS orders(SHIP_DATE)  REFERENCES ship_date_dim(DATE_KEY)
)
DIMENSIONS (
  -- logical_name AS physical_column — each role gets unique logical names
  order_date_dim.order_year       AS YEAR,        -- logical: order_year  → physical: YEAR
  order_date_dim.order_month_name AS MONTH_NAME,  -- logical: order_month_name → physical: MONTH_NAME
  ship_date_dim.ship_year         AS YEAR,        -- logical: ship_year   → physical: YEAR (same col, different role)
  ship_date_dim.ship_month_name   AS MONTH_NAME   -- logical: ship_month_name  → physical: MONTH_NAME
)
```

Each role gets its own uniquely named dimensions. No `USING` clause is needed. You can use `ORDER_YEAR` and `SHIP_YEAR` independently or together in the same query.

## What the Demo Shows

This snippet uses 8 orders placed between November 2024 and February 2025. Four of them **ship in a different month than they were placed** — that's what makes the role distinction meaningful:

| Order | Customer | Order Date | Ship Date | Amount | Cross-month? |
|-------|----------|-----------|-----------|--------|--------------|
| 1 | Acme Corp | Nov 15, 2024 | Nov 20, 2024 | $500 | — |
| 2 | Beta LLC | Nov 28, 2024 | Dec 3, 2024 | $800 | ← Nov order, Dec ship |
| 3 | Gamma Inc | Dec 1, 2024 | Dec 5, 2024 | $300 | — |
| 4 | Delta Co | Dec 20, 2024 | Jan 4, 2025 | $1,200 | ← Dec order, **Jan ship (crosses year!)** |
| 5 | Acme Corp | Jan 10, 2025 | Jan 15, 2025 | $450 | — |
| 6 | Epsilon Ltd | Jan 25, 2025 | Feb 2, 2025 | $900 | ← Jan order, Feb ship |
| 7 | Beta LLC | Feb 14, 2025 | Feb 20, 2025 | $650 | — |
| 8 | Gamma Inc | Feb 28, 2025 | Mar 5, 2025 | $1,100 | ← Feb order, Mar ship |

**Revenue by ORDER month** (4 rows):
```
November 2024   $1,300   2 orders
December 2024   $1,500   2 orders
January  2025   $1,350   2 orders
February 2025   $1,750   2 orders
```

**Revenue by SHIP month** (5 rows — same $5,900, different distribution):
```
November 2024     $500   1 order
December 2024   $1,100   2 orders
January  2025   $1,650   2 orders   ← Order 4 (Dec) shows up here
February 2025   $1,550   2 orders
March    2025   $1,100   1 order
```

## Combining Both Roles in One Query

Because `order_date_dim` and `ship_date_dim` are independent entities, you can group by both simultaneously. The result is a cross-tab showing the joint distribution of order date and ship date — useful for fulfillment lag analysis:

```
ORDER_MONTH     SHIP_MONTH     REVENUE
November 2024 → November 2024   $500    (same-month)
November 2024 → December 2024   $800    (1-month lag)
December 2024 → December 2024   $300    (same-month)
December 2024 → January  2025  $1,200   (crosses year)
...
```

## Role-Playing Dimensions vs. Multi-Path Metrics

Both patterns handle a single physical table reached via two join paths. The choice depends on what you want to expose:

| | Role-Playing Dimensions (this snippet) | Multi-Path Metrics (`multi_path_metrics`) |
|--|--|--|
| Aliases in TABLES | Two aliases of the dim table | One alias; two relationships point to it |
| Disambiguation | None needed — each alias has unique dim names | `USING` clause on each metric |
| Dimensions | `ORDER_YEAR`, `SHIP_YEAR` — independently named | Single shared column (e.g. `weather_condition`) |
| Use both in one query? | Yes — produces cross-tab | No — USING locks each metric to one path |
| Best for | Multiple date roles, multiple geography roles | Weather-at-departure vs weather-at-arrival style analysis |

Use **role-playing dimensions** when each role needs its own independently named columns.
Use **multi-path metrics** when the dimension column is shared and disambiguation is needed at the metric level.

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **Power BI** | Mark the same date table as active for one relationship, inactive for the others; use `USERELATIONSHIP()` in DAX measures. Requires a separate DAX expression per relationship — role-playing is implicit, not structural. |
| **Tableau** | Duplicate the date dimension data source, rename it, join each copy to the appropriate date FK. Doubles the data loaded into the extract. |
| **LookML** | `view: order_date { from: dim_date }` and `view: ship_date { from: dim_date }` — exact equivalent. LookML pioneered the from-alias pattern; SV TABLES aliasing follows the same idea. |
| **dbt** | No semantic layer equivalent; handled in SQL via aliased CTEs or multiple joins. Query author must remember which date column to use. |
| **Raw SQL** | `LEFT JOIN dim_date AS order_date_dim ON ... LEFT JOIN dim_date AS ship_date_dim ON ...` — the SV pattern encodes this join structure once in the model definition. |

## What Doesn't Work

- **Using a single alias with two relationships (multi-path without USING)**: If you define only one `date_dim` alias but two relationships pointing to it, any metric grouped by `date_dim.year` will error with "multi-path relationship". The engine can't resolve which path to use without explicit `USING` disambiguation.

- **Expecting a simple list when combining both roles**: Grouping by `ORDER_MONTH_NAME` and `SHIP_MONTH_NAME` together produces a cross-tab (one row per unique combination), not a flat list. This is correct behavior, but can produce many rows if orders span many months.

- **Sparse DIM_DATE**: The SV uses LEFT JOINs. An `ORDER_DATE` with no matching row in `DIM_DATE` will produce NULL values for all order date dimensions (`ORDER_YEAR`, `ORDER_MONTH_NAME`, etc.). Populate `DIM_DATE` for the full date range of the fact table.

- **Column name collisions**: If both aliases expose a dimension with the same name (e.g., `year AS YEAR`), the SV will fail to deploy — dimension names must be globally unique. Always prefix them per role (`ORDER_YEAR`, `SHIP_YEAR`).

## Docs

- [Semantic View — TABLES clause (logical table aliases)](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view)
- [Semantic View — RELATIONSHIPS clause](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view#relationships)
- [SEMANTIC_VIEW() table function](https://docs.snowflake.com/en/sql-reference/functions/semantic_view)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `DIM_DATE` calendar table + `ORDERS` fact table with two date FKs |
| `seed_data.sql` | 16 calendar dates + 8 orders (4 with cross-month ship dates) |
| `semantic_view.sql` | `ORDERS_RPD_SV` — DIM_DATE aliased as `order_date_dim` and `ship_date_dim` |
| `queries.sql` | Revenue by order month, revenue by ship month, fulfillment lag cross-tab, gotchas |
