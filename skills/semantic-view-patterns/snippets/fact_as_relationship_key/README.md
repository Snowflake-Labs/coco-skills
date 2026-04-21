# Fact as Relationship Key

## The Problem

You need to join two tables, but the join key doesn't exist as a physical column on the fact table — it has to be **computed** from columns that are there. There's no way to add the derived key to the source table (read-only source, or it would be redundant denormalization).

**Example in this snippet**: A `sales` table stores individual transactions with a `sale_date`. A separate `fiscal_quarters` table stores quarterly budget targets, keyed by a string like `"2024-Q2"`. The sales table has no `fiscal_quarter_key` column — but you can derive it from `sale_date`. The goal: join every sale to its fiscal quarter budget without transforming the source data.

## How You Might Express This Need

- "I want to join my sales table to a quota/budget table by fiscal quarter, but there's no fiscal quarter column on sales"
- "My dimension table is keyed by a composite or computed value — how do I join to it from a fact that only has the raw components?"
- "I need to map events to lookup values using a derived key (e.g. region extracted from a longer code)"
- "Can I define a computed FK in a semantic view without adding a column to the source table?"

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **SQL** | `JOIN fiscal_quarters fq ON CONCAT(YEAR(sale_date), '-Q', QUARTER(sale_date)) = fq.fiscal_quarter_key` |
| **dbt** | Add a computed column in the staging model: `CONCAT(YEAR(sale_date), '-Q', QUARTER(sale_date)) AS fiscal_qtr_key` |
| **LookML** | `dimension: fiscal_qtr_key { sql: CONCAT(YEAR(${sale_date}), '-Q', QUARTER(${sale_date})) }` + `join` block |
| **Power BI** | Add a calculated column in Power Query: `Text.From(Date.Year([sale_date])) & "-Q" & Text.From(Date.QuarterOfYear([sale_date]))` |
| **Tableau** | Computed join field in the data source dialog, or a pre-joined extract |

All of these require either modifying the source table/model or writing the join expression directly in every query. The SV encodes it once in the model definition.

## The SV Approach

Two things are required:

**1. Define the computed key as a FACT on the source table:**
```sql
FACTS (
    sales.fiscal_qtr_key AS CONCAT(TO_VARCHAR(YEAR(sale_date)), '-Q', TO_VARCHAR(QUARTER(sale_date)))
)
```

**2. Reference that fact in the RELATIONSHIP:**
```sql
RELATIONSHIPS (
    sales(sales.fiscal_qtr_key) REFERENCES fiscal_quarters
)
```

The engine evaluates `fiscal_qtr_key` per row at query time and uses it as the FK — no physical column needed.

## What Doesn't Work

- **The computed fact is not a metric or dimension** — you can't query `sales.fiscal_qtr_key` in a `SEMANTIC_VIEW()` call directly. It exists only to power the join.
- **Aggregation expressions are not valid** — the fact used as a FK must be a scalar (row-level) expression. `SUM(...)`, `COUNT(...)`, etc. will fail.
- **The referenced table must have a matching PRIMARY KEY** — the right-hand side of `REFERENCES` must be the table's declared `PRIMARY KEY` (or omitted to use it implicitly).

## Docs

- [Defining facts, dimensions, and metrics](https://docs.snowflake.com/en/user-guide/views-semantic/sql#defining-facts-dimensions-and-metrics)
- [RELATIONSHIPS — using a fact as a foreign key](https://docs.snowflake.com/en/user-guide/views-semantic/sql#relationships)
- [CREATE SEMANTIC VIEW syntax reference](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `sales`, `fiscal_quarters`, `products` table DDL |
| `seed_data.sql` | 6 quarters of targets, 13 sales across 3 products |
| `semantic_view.sql` | SV with computed-FK fact + budget metrics |
| `queries.sql` | Revenue vs budget by quarter, attainment by category, gotchas |
