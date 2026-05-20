# Semi-Additive Metric

## The Problem

Your fact table represents a **snapshot in time** — each row records a value at a specific point (e.g., account balance at end of day, headcount at end of month, inventory on hand at midnight). 

Summing these snapshots **across time is mathematically wrong** — it double-counts. A balance of $1,000 on Monday and $1,000 on Tuesday is still $1,000, not $2,000. But summing across accounts on the *same* date is fine.

This kind of measure is called **semi-additive**: additive across some dimensions (accounts, regions), non-additive across others (time).

## How You Might Express This Need

- "What is the total account balance?" (wants a point-in-time sum across accounts)
- "Show me average daily balance by account over the last quarter"
- "How many employees did we have at the end of each month?"
- "What was our inventory level on hand last Friday?"
- "My numbers are way too high — I think I'm double-counting across dates"

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **Power BI / DAX** | `LASTNONBLANK`, `FIRSTNONBLANK`, `AVERAGEX` — semi-additive measures are a first-class concept |
| **SSAS / Tabular** | `FirstChild`, `LastChild`, `AverageOfChildren` measure types |
| **LookML** | `type: sum` with a date filter in the measure, or `type: average` — no native semi-additive guard |
| **dbt** | No native concept; requires window functions (`LAST_VALUE`, `SUM OVER`) in the model |
| **Raw SQL** | `WHERE balance_date = CURRENT_DATE` for point-in-time; `AVG(balance_usd)` for trend |
| **Tableau** | Fixed LOD for point-in-time: `{ FIXED [Date]: SUM([Balance]) }`; `WINDOW_AVG` table calculation for trends. Requires careful scope management to avoid double-counting. |

Snowflake Semantic Views handle this with `NON ADDITIVE BY` — it marks a metric as non-aggregatable across a specific time dimension, preventing accidental cross-date summing.

## The SV Approach

Define **two separate metrics** with non-overlapping synonyms — one for point-in-time, one for averages:

```sql
METRICS (
    -- NON ADDITIVE BY prevents summing across balance_date
    -- Use this when you want totals at a specific point in time
    balances.total_balance NON ADDITIVE BY (balance_date) AS SUM(BALANCE_USD)
        WITH SYNONYMS ('current balance', 'balance as of date', 'snapshot balance'),

    -- Use this when you want trends or averages across time
    balances.avg_daily_balance AS AVG(BALANCE_USD)
        WITH SYNONYMS ('average balance', 'average daily balance', 'mean balance over time')
)
```

### What `NON ADDITIVE BY` Does

When you include `balance_date` as a dimension in your query, `total_balance` correctly sums across accounts for that date. When you *don't* include `balance_date`, the SV engine refuses to sum across all dates — instead it returns the metric grouped by date internally (you'll see date-level values, not a single cross-date total).

### Why You Need Two Metrics (Not One)

You **cannot** apply `AVG()` to a `NON ADDITIVE` metric. They are separate operations on the underlying fact, not composable. Define:
- `total_balance NON ADDITIVE BY (balance_date) AS SUM(BALANCE_USD)` for "what is the total right now"
- `avg_daily_balance AS AVG(BALANCE_USD)` for "what is the typical balance over a period"

### Synonym Discipline

If both metrics mention "balance", the AI may pick the wrong one. Make synonyms explicitly intent-oriented:
- `total_balance`: *"current balance", "snapshot balance", "balance as of a date", "end of day balance"*
- `avg_daily_balance`: *"average balance", "mean balance", "typical balance", "balance trend"*

## Docs

- [Identifying the dimensions that should be non-additive for a metric](https://docs.snowflake.com/en/user-guide/views-semantic/sql#identifying-the-dimensions-that-should-be-non-additive-for-a-metric)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `ACCOUNT_BALANCES` snapshot table DDL |
| `seed_data.sql` | 3 accounts × 5 daily snapshots (15 rows) |
| `semantic_view.sql` | SV with both `NON ADDITIVE BY` and `AVG` metrics |
| `queries.sql` | Correct point-in-time and trend queries + the double-counting mistake |
