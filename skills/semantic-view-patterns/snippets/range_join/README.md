# Range Join (Temporal / SCD2 Join)

## The Problem

You have a fact table and a dimension table where the dimension data changes over time. When you join them, you need the version of the dimension that was **active at the time of the fact event** — not the current version.

**Example in this snippet**: A customer's subscription tier (Free → Growth → Enterprise) changes over time. When reporting revenue by tier, each order should be attributed to the tier the customer was on *at the time of purchase*.

## How You Might Express This Need

- "Show me revenue broken down by the subscription tier the customer was on at time of purchase"
- "What plan was each user on when they churned?"
- "Join each event to the pricing tier that was in effect at that time"
- "I want to use our SCD2 customer dimension — but the segment should reflect what it was historically, not what it is today"
- "My dimension table has `valid_from` / `valid_to` columns. How do I use those in the semantic layer?"

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **dbt** | `dbt snapshot` creates SCD2 history. Temporal join is done manually in SQL: `JOIN dim ON id = id AND event_ts BETWEEN dbt_valid_from AND dbt_valid_to` |
| **LookML** | No native SCD2 support. Typically denormalized at ETL time or handled in a Liquid-templated derived table. |
| **Power BI / DAX** | No native temporal join. Requires pre-built snapshot tables or complex CALCULATE + FILTER patterns. |
| **SSAS / Tabular** | No native temporal join. Denormalization is standard. |
| **Raw SQL** | `JOIN dim ON fact.id = dim.id AND fact.event_date BETWEEN dim.valid_from AND dim.valid_to` |

Snowflake Semantic Views handle this natively with a **range relationship** — no denormalization or ETL-time join needed.

## The SV Approach

Three things are required:

**1. Declare the time range on the dimension table** (`UNIQUE` + `CONSTRAINT DISTINCT RANGE`):
```sql
customer_segments AS DB.SCHEMA.CUSTOMER_SEGMENTS
    PRIMARY KEY (SEGMENT_ID)
    UNIQUE (CUSTOMER_ID, VALID_FROM, VALID_TO)
    CONSTRAINT segment_period DISTINCT RANGE BETWEEN VALID_FROM AND VALID_TO EXCLUSIVE
```

**2. Define the compound relationship** (entity key + temporal column → entity key + range):
```sql
orders_to_segment AS orders(CUSTOMER_ID, ORDER_DATE)
    REFERENCES customer_segments(CUSTOMER_ID, BETWEEN VALID_FROM AND VALID_TO EXCLUSIVE)
```

**3. Use dimensions from `customer_segments`** in your queries — they'll automatically resolve to the historically-correct record.

### EXCLUSIVE vs INCLUSIVE End Dates

This snippet uses **EXCLUSIVE** end dates: `valid_to` is the first day the record is *no longer* active.

| Customer | Segment | `valid_from` | `valid_to` (exclusive) | Active through |
|----------|---------|-------------|----------------------|----------------|
| C001 | Free | 2024-01-01 | 2024-04-01 | March 31 |
| C001 | Growth | 2024-04-01 | 2024-07-01 | June 30 |
| C001 | Enterprise | 2024-07-01 | 9999-12-31 | current |

An order on `2024-03-31` falls in `[2024-01-01, 2024-04-01)` → Free. ✓  
An order on `2024-04-01` falls in `[2024-04-01, 2024-07-01)` → Growth. ✓

> If your data uses **inclusive** end dates (`valid_to = 2024-03-31`), either convert them at load time or create a view with `valid_to + 1 day` before referencing in the SV.

### Type Compatibility

The fact's temporal FK column must be type-coercible to the dimension's range columns. If your order date is `DATE` and your segment dates are `TIMESTAMP_NTZ`, add a `PRIVATE` FACT to cast:

```sql
FACTS (
    PRIVATE orders.order_ts AS ORDER_DATE::TIMESTAMP_NTZ
)
RELATIONSHIPS (
    orders_to_segment AS orders(CUSTOMER_ID, order_ts)
        REFERENCES customer_segments(CUSTOMER_ID, BETWEEN VALID_FROM AND VALID_TO EXCLUSIVE)
)
```

## Entity Isolation (Key Gotcha)

The SV engine enforces **entity isolation across range join boundaries**. You cannot use a dimension from the range-joined entity (`customer_segments`) with a metric defined on a *different* entity that is only connected through that range join.

If you add a second fact table (e.g., `support_tickets`) that is NOT directly related to `customer_segments`, you cannot query `support_tickets` metrics broken down by `customer_segments.segment`. The dimension and metric must share a direct join path.

**Fix**: Add the dimension you need directly to the metric's entity table (if the physical column exists there), or establish a direct relationship from the second fact to the dimension.

## Docs

- [Joining logical tables that contain ranges of values](https://docs.snowflake.com/en/user-guide/views-semantic/sql#joining-logical-tables-that-contain-ranges-of-values)
- [CREATE SEMANTIC VIEW — CONSTRAINT / BETWEEN syntax](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view#label-create-semantic-view-tables-constraint)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `ORDERS` and `CUSTOMER_SEGMENTS` table DDL |
| `seed_data.sql` | 3 customers × segment history + 8 orders across different tier periods |
| `semantic_view.sql` | SV with range relationship between orders and segment history |
| `queries.sql` | Working queries + the naive SQL mistake this pattern prevents |
