-- Semi-Additive Metric Example: Queries
-- Run schema.sql, seed_data.sql, and semantic_view.sql first.

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- WORKING QUERIES
-- ============================================================

-- 1. Total balance across all accounts as of a specific date
--    Include balance_date as a dimension so NON ADDITIVE BY sums correctly.
--
--    Expected: 2024-05-31 → $7,500.00  (1250 + 5300 + 950)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ACCOUNT_BALANCES_SV
    DIMENSIONS balances.balance_date
    METRICS balances.total_balance
    WHERE balances.balance_date = '2024-05-31'
);


-- 2. Balance by account as of a specific date
--
--    Expected (2024-05-31):
--      A001  Checking Account  $1,250
--      A002  Business Reserve  $5,300
--      A003  Savings Account   $  950
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ACCOUNT_BALANCES_SV
    DIMENSIONS balances.account_id, balances.account_name, balances.balance_date
    METRICS balances.total_balance
    WHERE balances.balance_date = '2024-05-31'
)
ORDER BY total_balance DESC;


-- 3. Total balance per date (all months — shows the portfolio growing over time)
--
--    Expected:
--      2024-01-31  $6,800
--      2024-02-29  $6,900
--      2024-03-31  $7,150
--      2024-04-30  $7,400
--      2024-05-31  $7,500
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ACCOUNT_BALANCES_SV
    DIMENSIONS balances.balance_date
    METRICS balances.total_balance
)
ORDER BY balance_date;


-- 4. Average monthly balance per account (trend analysis)
--    Uses avg_daily_balance — correct for this question.
--
--    Expected:
--      A002  Business Reserve  $5,080
--      A001  Checking Account  $1,170
--      A003  Savings Account   $  900
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ACCOUNT_BALANCES_SV
    DIMENSIONS balances.account_id, balances.account_name
    METRICS balances.avg_daily_balance
)
ORDER BY avg_daily_balance DESC;


-- 5. Average monthly balance over Q1 only
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.ACCOUNT_BALANCES_SV
    DIMENSIONS balances.account_id
    METRICS balances.avg_daily_balance
    WHERE balances.balance_date BETWEEN '2024-01-01' AND '2024-03-31'
)
ORDER BY account_id;


-- ============================================================
-- THE MISTAKE THIS PATTERN PREVENTS
-- ============================================================

-- WRONG: Naive SUM across all rows
-- Counts each account's balance once per snapshot date → 5× overcount.
-- A $5,000 balance that existed for 5 months looks like $25,000.
--
-- Raw SQL that gives the wrong answer:
SELECT
    account_id,
    SUM(balance_usd) AS wrong_total   -- $5,000 × 5 months = $25,000 for A002
FROM SNIPPETS.PUBLIC.ACCOUNT_BALANCES
GROUP BY 1
ORDER BY 2 DESC;
-- A002 shows $25,400 instead of the correct $5,300 (latest) or $5,080 (average)


-- ALSO WRONG: Grand total across all rows
SELECT SUM(balance_usd) AS wrong_grand_total FROM SNIPPETS.PUBLIC.ACCOUNT_BALANCES;
-- Returns $35,750 — the sum of all 15 snapshot rows.
-- Correct answers: $7,500 (latest point-in-time) or ~$4,383 (average per account per month)


-- ============================================================
-- WHAT DOESN'T WORK IN SEMANTIC_VIEW()
-- ============================================================

-- NOTE: Querying total_balance WITHOUT a balance_date dimension or filter
-- will not produce a meaningful single total.
-- The NON ADDITIVE BY clause causes the engine to return per-date subtotals
-- rather than a collapsed grand total, preventing silent overcounting.
--
-- If you want a single scalar total, always filter: WHERE balance_date = '2024-05-31'
-- If you want a collapsed average, use avg_daily_balance instead.

-- ALSO NOTE: You cannot use AVG(total_balance) — total_balance is already an
-- aggregated metric. To get the average, use the dedicated avg_daily_balance metric.
-- Attempting to nest metric references is not supported.
