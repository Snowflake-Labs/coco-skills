-- Semi-Additive Metric Example: Semantic View DDL
-- Run schema.sql and seed_data.sql first.

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.ACCOUNT_BALANCES_SV

  TABLES (
    balances AS SNIPPETS.PUBLIC.ACCOUNT_BALANCES
      PRIMARY KEY (BALANCE_ID)
  )

  FACTS (
    balances.balance_usd AS BALANCE_USD
      COMMENT = 'End-of-day account balance in USD'
  )

  DIMENSIONS (
    balances.account_id   AS ACCOUNT_ID
      WITH SYNONYMS ('account', 'account number')
      COMMENT = 'Account identifier',

    balances.account_name AS ACCOUNT_NAME
      WITH SYNONYMS ('account label', 'account description'),

    balances.balance_date AS BALANCE_DATE
      WITH SYNONYMS ('date', 'snapshot date', 'as of date', 'month end')
      COMMENT = 'The date this balance snapshot was recorded (end of day)'
  )

  METRICS (
    -- Point-in-time balance: additive across accounts, NOT across dates.
    -- NON ADDITIVE BY (balance_date) prevents summing across time periods.
    -- Use this when you want: "total balance as of [date]" or "balance by account on [date]"
    balances.total_balance NON ADDITIVE BY (balance_date) AS SUM(BALANCE_USD)
      WITH SYNONYMS ('current balance', 'balance as of date', 'snapshot balance',
                     'end of day balance', 'point in time balance', 'balance on hand')
      COMMENT = 'Sum of balances across accounts for a given date. Non-additive across time — always filter or group by balance_date to get a meaningful total.',

    -- Average balance over time: use for trend analysis, not point-in-time.
    -- Use this when you want: "average monthly balance over Q1" or "mean balance by account"
    balances.avg_daily_balance AS AVG(BALANCE_USD)
      WITH SYNONYMS ('average balance', 'average daily balance', 'mean balance',
                     'typical balance', 'balance trend', 'average monthly balance')
      COMMENT = 'Average balance across snapshot periods. Use for trend analysis, not point-in-time reporting.'
  )

  COMMENT = 'Daily account balance snapshots. Balances are semi-additive: sum across accounts is valid; sum across time periods double-counts. Use total_balance for point-in-time; avg_daily_balance for trends.'

  AI_SQL_GENERATION 'IMPORTANT: ACCOUNT_BALANCES is a snapshot table — each row is a balance at a point in time, not a transaction. 
- For point-in-time totals: use total_balance with balance_date as a dimension or filter. Summing total_balance across all dates is meaningless (double-counts).
- For trends over time: use avg_daily_balance — it averages across the snapshot dates.
- Never use total_balance without a balance_date dimension or WHERE filter on balance_date.';
