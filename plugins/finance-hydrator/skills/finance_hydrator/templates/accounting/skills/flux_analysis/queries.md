# Query Reference — Flux Analysis

All physical table/column references are resolved at runtime against the customer's Snowflake account. This file describes query types and logic patterns — no SQL, no FQNs.

---

## Data Shape (resolved at runtime)

**Primary data sources:**
- Income statement activity table (one row per journal line per fiscal day)
- Balance sheet balance table (one row per account per period-end date)
- Fiscal calendar reference (one row per calendar date mapping to fiscal periods)
- (Optional) Pre-computed results/state table for batch run caching

---

## Query Type 1 — Account Enumeration

**Purpose**: List all GL accounts within a use case's filter scope, with current and prior period totals, to assess materiality.

**Logic**:
1. Filter the GL table by the use case's account filter (e.g., all accounts in a specific FSLI grouping)
2. Join to fiscal calendar to identify which rows fall in current vs prior period
3. GROUP BY account identifier
4. SUM amounts per period per account
5. Compute variance (current - prior) and variance percentage

**Output grain**: One row per GL account with current balance, prior balance, variance, and variance %.

---

## Query Type 2 — Activity Flux (Income Statement)

**Purpose**: Period-over-period variance by configurable dimensions for income statement accounts.

**Logic**:
1. Filter the IS activity table by account and period range
2. Join to fiscal calendar to classify each transaction into Current Period or Prior Period
3. GROUP BY configured dimension columns (e.g., supplier, cost center, spend category)
4. SUM amounts per dimension per period
5. Pivot to columns: dimension values, current period total, prior period total
6. Compute change amount, change %, absolute change
7. Rank by absolute change descending
8. Compute cumulative % of total variance (for top-N driver identification)

**Output grain**: One row per unique dimension combination, ranked by variance contribution.

---

## Query Type 3 — Balance Flux (Balance Sheet)

**Purpose**: Point-in-time balance comparison at period-end dates for balance sheet accounts.

**Logic**:
1. Filter the BS table by account and period-end flag (quarter-end for QoQ, month-end for MoM)
2. Select rows where date matches current period-end OR prior period-end
3. GROUP BY configured dimension columns
4. SUM balance amounts per dimension per period-end date
5. Pivot, compute change, rank by absolute change (same as Query Type 2)

**Key difference from Activity Flux**: Balance sheet uses point-in-time snapshots at specific dates, not activity totals across a date range.

**Output grain**: One row per dimension combination at the two comparison dates.

---

## Query Type 4 — Dimension Probe

**Purpose**: Discover which dimensional columns have meaningful data for a specific account before running the full flux query.

**Logic**:
1. Filter the GL table to the target account and recent periods
2. For each candidate dimension column, compute:
   - COUNT(DISTINCT values) — cardinality
   - COUNT(rows WHERE value IS NOT NULL AND value != '') — population rate
   - Whether the top value accounts for > 80% of rows (dominance check)
3. Score each dimension: useful if cardinality >= 2, cardinality <= 500, population > 50%, and no single value dominates > 95%
4. Return ranked list of useful dimensions

**Output grain**: One row per candidate dimension with cardinality, population %, and usefulness score.

---

## Query Type 5 — Theme/Memo Extraction

**Purpose**: Extract business themes from journal line descriptions when dimensional analysis produces sparse or blank results.

**Logic**:
1. Filter GL table to target account and comparison periods
2. Extract keywords/patterns from the line description/memo field
3. Classify each line into a business theme using pattern matching (e.g., "amortization", "accrual reversal", "cash receipt", "reclassification")
4. GROUP BY theme and period
5. SUM amounts per theme per period
6. Compute net change per theme (current - prior)
7. Rank themes by absolute net change

**Output grain**: One row per identified theme with current total, prior total, and net change.

**When to use**: Always for balance sheet accounts. For income statement accounts, only when the dimension probe indicates sparse data (population < 50% or cardinality < 2 for configured dimensions).

---

## Query Type 6 — Journal Detail Drill-Down

**Purpose**: Raw transaction details for sparse accounts (5 or fewer rows) or on-demand deep-dive into a specific driver.

**Logic**:
1. Filter GL table to target account and period
2. Return all available columns: date, amount, line description/memo, all dimensional attributes
3. ORDER BY absolute amount descending
4. No aggregation — show individual transactions

**Output grain**: One row per journal line/transaction.

**When to use**: When the dimension probe returns total_rows <= 5 (skip aggregation entirely, show raw data). Also used on-demand when user asks "show me the detail behind X driver."

---

## Pitfalls

1. **IS vs BS query family** — Income statement accounts use activity totals (sum of transactions within a period). Balance sheet accounts use point-in-time balances at period-end dates. Using the wrong family produces nonsensical results.

2. **Fiscal calendar is mandatory** — Never hardcode fiscal periods. The fiscal calendar binding determines period boundaries, quarter definitions (Feb-start vs Jan-start vs custom), and period-end dates. Always join transaction dates to the fiscal calendar.

3. **Period-end flags for BS** — Balance sheet queries must filter to period-end dates only. Without this filter, you may get intra-period balances that don't represent the true period-end position.

4. **Currency mixing** — If the GL table contains multiple currencies, all amounts must be in reporting currency (translated). Never sum across currencies. If a reporting-currency column exists, use it; otherwise group by currency.

5. **Fluxed-together accounts** — When accounts are configured to be analyzed as a group, the enumeration query must sum their balances together for the materiality check. Individual flux queries then run against the combined set.

6. **Dimension column names differ by table** — The IS and BS tables may use different column names for the same concept (e.g., "company" on IS vs "legal_entity" on BS). The correct column names for each table binding are resolved at runtime.

7. **Sparse accounts** — Accounts with very few transactions (<=5 rows) should skip aggregation entirely and use the Journal Detail query. Trying to compute variance percentages or rank drivers with 2-3 rows produces misleading results.
