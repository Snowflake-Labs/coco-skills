# ASOF Join

## The Problem

You need to join a fact to the dimension record that was active **at the time of the event**, but your dimension table only has a `start_date` — no explicit end date. You want "the most recent record whose start date is on or before the event date."

This is an **ASOF join** (as-of join): "give me the record that was in effect *as of* this date."

**Example in this snippet**: A customer moves addresses over time. Each order should be attributed to the address the customer lived at when they placed the order.

## ASOF vs BETWEEN EXCLUSIVE (When to Use Which)

| | ASOF Join | Range Join (BETWEEN EXCLUSIVE) |
|--|-----------|-------------------------------|
| **Dimension has** | Only a start date | Explicit start + end date |
| **Semantics** | "Latest record on or before the event date" | "Record whose range contains the event date" |
| **NULL valid_to handling** | Automatic — no sentinel needed | Requires sentinel (e.g. `9999-12-31`) |
| **Syntax** | `REFERENCES dim(id, ASOF start_date)` | `REFERENCES dim(id, BETWEEN start AND end EXCLUSIVE)` |
| **Use when** | Address history, price lists, org hierarchy changes | SCD2 with explicit validity windows |

## How You Might Express This Need

- "Join orders to the address the customer was at when they ordered"
- "Show revenue by the price tier that was in effect at purchase time, but we don't have explicit end dates"
- "My dimension table has a `valid_from` but no `valid_to` — how do I join?"
- "Which account manager owned this customer when the deal closed?"

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **SQL** | `JOIN dim ON id = id AND dim.start_date = (SELECT MAX(start_date) FROM dim WHERE id = fact.id AND start_date <= fact.date)` |
| **dbt** | No native ASOF; requires the subquery pattern above in a Jinja model |
| **LookML** | No native support; pre-join at ETL time |
| **Power BI** | CALCULATE + FILTER to find latest active record |
| **Tableau** | FIXED LOD `{ FIXED [ID]: MAX([start_date]) }` filtered to dates ≤ event timestamp; or blend a date-filtered extract. No native ASOF join. |

## The SV Approach

Two things are required:

**1. Declare UNIQUE on the entity key + start date** (no end date needed):
```sql
Customer_address UNIQUE (ca_custid, ca_start_date)
```

**2. Reference with ASOF**:
```sql
Orders(o_custid, o_orddate) REFERENCES Customer_address(ca_custid, ASOF ca_start_date)
```

This automatically finds the `Customer_address` row with the largest `ca_start_date` that is ≤ `o_orddate` for the same `ca_custid`.

## Docs

- [Using a date, time, timestamp, or numeric range to join logical tables (ASOF)](https://docs.snowflake.com/en/user-guide/views-semantic/sql#using-a-date-time-timestamp-or-numeric-range-to-join-logical-tables)
- [ASOF JOIN syntax reference](https://docs.snowflake.com/en/sql-reference/constructs/asof-join)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `Customer_address`, `Customer_name`, `Orders` table DDL |
| `seed_data.sql` | Address history for 2 customers, 6 orders |
| `semantic_view.sql` | SV using ASOF relationship |
| `queries.sql` | Revenue by zip code per month + comparison with naive SQL mistake |
