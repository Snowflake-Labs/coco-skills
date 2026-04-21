# Variables (Parameterized Semantic Views)

## The Problem

You have a metric or dimension whose calculation depends on a **user-defined threshold, weight, or date window** that shouldn't be hard-coded. Different business users or use cases need different values — but you don't want to create a separate SV for each configuration.

**Variables** let you define adjustable parameters in the SV DDL with optional defaults, then override them at query time.

## How You Might Express This Need

- "Our data science team wants to adjust the weights in our composite score model without changing the SV"
- "We want 'premium' to mean >$500 in Q1 but >$400 in Q2 promotions — without duplicating the SV"
- "Let users define the 'lookback window' for recency without touching the DDL"
- "Dynamic thresholds, dynamic scoring — same SV, different parameters"

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **SQL** | Query parameters (`$1`, `%s`) or Jinja templates (`{{ var('threshold') }}`) |
| **LookML** | Liquid parameters: `{% parameter threshold %}` |
| **dbt** | `{{ var('premium_threshold', 500) }}` in model SQL |
| **Power BI** | What-if parameters / Power Query parameters |

## The SV Approach

**Declare variables with types and defaults in the DDL:**
```sql
VARIABLES (
    premium_threshold NUMBER(10,2) DEFAULT 500.00,
    rating_weight     NUMBER(3,2)  DEFAULT 0.6
)
```

**Reference by name in DIMENSIONS or METRICS expressions:**
```sql
DIMENSIONS (
    product_sales.price_tier AS (
        CASE WHEN unit_price >= premium_threshold THEN 'premium' ... END
    )
)
METRICS (
    product_sales.performance_score AS (
        rating_weight * AVG(customer_rating) / 5.0 + ...
    )
)
```

**Override at query time with `VARIABLES key => value`:**
```sql
SELECT * FROM SEMANTIC_VIEW(
    product_performance
    DIMENSIONS price_tier
    METRICS total_sales
    VARIABLES premium_threshold => 400.00
)
```

## Key Rules

- Variables can only be used in **DIMENSIONS, METRICS, FACTS** expressions
- Variables **cannot** be used in TABLES or RELATIONSHIPS clauses
- `DEFAULT` is optional — if omitted, the variable **must** be supplied at every query call
- Value must be coercible to the declared type (e.g. integer `1` for `NUMBER(3,2)` works)
- Variables are not exposed as queryable dimensions or metrics

## Docs

- [CREATE SEMANTIC VIEW — VARIABLES clause](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view#variables)
- [SEMANTIC_VIEW clause — VARIABLES at query time](https://docs.snowflake.com/en/sql-reference/constructs/semantic_view#variables)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `product_sales` table |
| `seed_data.sql` | 8 product sale rows across 3 categories |
| `semantic_view.sql` | SV with 6 variables: scoring weights, price tier thresholds, date windows |
| `queries.sql` | Default vs override for each variable pattern |
