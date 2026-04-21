# Entity Facts and Calculated Dimensions

## The Problem

You need analytics at the **customer (entity) level**, not just the order level. For example:
- A customer's **lifetime value** (total spend across all orders)
- A **value tier** ("high", "medium", "low") derived from that LTV
- A **calculated age** dimension from a birth year column

These patterns require entity-level aggregation, derived dimensions, and expression-based dimensions — none of which require separate tables or pre-computed columns.

## How You Might Express This Need

- "Segment customers by total lifetime spend — show order volume per segment"
- "Each customer has a birth year. Compute their age and use it to filter/bucket."
- "I want VALUE_BUCKET to be derived dynamically from total spend, not stored in the DB"

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **SQL** | `SUM(amount) OVER (PARTITION BY customer_id)` as subquery for LTV; CASE in SELECT |
| **LookML** | `derived_table` + `dimension: value_segment { sql: CASE WHEN ... }` |
| **dbt** | `metrics.yml` customer_ltv + downstream dimension in model |
| **Power BI** | DAX CALCULATE + ALLEXCEPT for entity-level aggregation |

## Three Patterns in This Snippet

### 1. Entity-Level Aggregated Fact
```sql
FACTS (
    PRIVATE customers.lifetime_value AS SUM(orders.order_amount)
)
```
Aggregates `order_amount` up to the `customers` entity — produces one number per customer. `PRIVATE` means it's not directly queryable but can be used in DIMENSIONS expressions.

### 2. Derived Dimension from Aggregated Fact
```sql
DIMENSIONS (
    customers.value_segment AS (
        CASE
            WHEN customers.lifetime_value < 1000  THEN 'low'
            WHEN customers.lifetime_value <= 3000 THEN 'medium'
            ELSE                                       'high'
        END
    )
)
```
The CASE expression uses `lifetime_value` — which is a PRIVATE fact — to produce a queryable `value_segment` dimension. The LTV is never exposed directly; only the tier.

### 3. Calculated Dimension (Expression on Physical Column)
```sql
DIMENSIONS (
    customers.age AS (YEAR(CURRENT_DATE()) - birth_year)
)
```
Expression evaluated at query time. No stored column needed.

## PRIVATE vs Public Facts

| | PRIVATE fact | Public fact |
|--|-------------|-------------|
| Queryable as dimension | No | Yes |
| Usable in DIMENSIONS expressions | Yes | Yes |
| Shows in DESCRIBE / Cortex Analyst | No | Yes |
| Use when | Intermediate computation only | You want users to see and filter by the value |

## Docs

- [Defining facts, dimensions, and metrics](https://docs.snowflake.com/en/user-guide/views-semantic/sql#defining-facts-dimensions-and-metrics)
- [CREATE SEMANTIC VIEW — FACTS / DIMENSIONS syntax](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view#facts)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `customers` + `orders` |
| `seed_data.sql` | 4 customers, 10 orders with known LTV tiers |
| `semantic_view.sql` | SV with PRIVATE fact, derived segment, and age dimension |
| `queries.sql` | Revenue by segment, age filtering, per-order fact in WHERE |
