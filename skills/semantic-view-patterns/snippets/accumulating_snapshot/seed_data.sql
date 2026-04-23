-- Accumulating Snapshot: Seed Data
--
-- 12 loan applications modeled after a B2C lender (SoFi-style).
-- Funnel: 12 applied → 10 reviewed → 7 decisions → 5 funded
-- Conversion rates: review 83%, decision 58%, funding 42%
--
-- Three months of applications (Jan–Mar 2025), 4 per month.
-- Some applications are still in-flight (NULL milestones) — that's the point.

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- DIM_DATE — one row per distinct date referenced in LOAN_APPLICATIONS
-- ============================================================

INSERT INTO DIM_DATE (date_key, month_num, month_name, quarter, year) VALUES
  -- January 2025
  ('2025-01-06', 1, 'January',  'Q1', 2025),
  ('2025-01-08', 1, 'January',  'Q1', 2025),
  ('2025-01-10', 1, 'January',  'Q1', 2025),
  ('2025-01-13', 1, 'January',  'Q1', 2025),
  ('2025-01-15', 1, 'January',  'Q1', 2025),
  ('2025-01-16', 1, 'January',  'Q1', 2025),
  ('2025-01-18', 1, 'January',  'Q1', 2025),
  ('2025-01-22', 1, 'January',  'Q1', 2025),
  ('2025-01-25', 1, 'January',  'Q1', 2025),
  -- February 2025
  ('2025-02-03', 2, 'February', 'Q1', 2025),
  ('2025-02-06', 2, 'February', 'Q1', 2025),
  ('2025-02-10', 2, 'February', 'Q1', 2025),
  ('2025-02-11', 2, 'February', 'Q1', 2025),
  ('2025-02-13', 2, 'February', 'Q1', 2025),
  ('2025-02-17', 2, 'February', 'Q1', 2025),
  ('2025-02-18', 2, 'February', 'Q1', 2025),
  ('2025-02-20', 2, 'February', 'Q1', 2025),
  ('2025-02-24', 2, 'February', 'Q1', 2025),
  -- March 2025
  ('2025-03-03', 3, 'March',    'Q1', 2025),
  ('2025-03-06', 3, 'March',    'Q1', 2025),
  ('2025-03-10', 3, 'March',    'Q1', 2025),
  ('2025-03-12', 3, 'March',    'Q1', 2025),
  ('2025-03-13', 3, 'March',    'Q1', 2025),
  ('2025-03-17', 3, 'March',    'Q1', 2025),
  ('2025-03-20', 3, 'March',    'Q1', 2025),
  ('2025-03-24', 3, 'March',    'Q1', 2025);

-- ============================================================
-- LOAN_APPLICATIONS — 12 rows across 3 months
-- NULL milestone = not yet reached
-- ============================================================

INSERT INTO LOAN_APPLICATIONS (
    application_id, loan_product, state, channel,
    application_date, review_date, decision_date, funding_date,
    requested_amount, funded_amount
) VALUES
  -- January cohort (4 apps) — mostly complete
  (1,  'Personal Loan', 'CA', 'Organic',     '2025-01-06', '2025-01-08', '2025-01-13', '2025-01-15', 25000, 25000),
  (2,  'Personal Loan', 'TX', 'Paid Search', '2025-01-10', '2025-01-13', '2025-01-16', '2025-01-18', 15000, 15000),
  (3,  'Student Refi',  'CA', 'Organic',     '2025-01-15', '2025-01-18', '2025-01-22', NULL,         45000, NULL),   -- denied
  (4,  'Personal Loan', 'FL', 'Referral',    '2025-01-22', '2025-01-25', NULL,          NULL,         12000, NULL),   -- in decision

  -- February cohort (4 apps) — partially complete
  (5,  'Home Equity',   'NY', 'Direct Mail', '2025-02-03', '2025-02-06', '2025-02-11', '2025-02-13', 80000, 80000),
  (6,  'Personal Loan', 'CA', 'Paid Search', '2025-02-10', '2025-02-13', '2025-02-18', '2025-02-20', 20000, 20000),
  (7,  'Student Refi',  'TX', 'Organic',     '2025-02-17', '2025-02-20', NULL,          NULL,         55000, NULL),   -- in decision
  (8,  'Personal Loan', 'WA', 'Referral',    '2025-02-24', NULL,          NULL,          NULL,          8000, NULL),   -- just applied

  -- March cohort (4 apps) — early stage
  (9,  'Personal Loan', 'CA', 'Organic',     '2025-03-03', '2025-03-06', '2025-03-10', '2025-03-12', 18000, 18000),
  (10, 'Home Equity',   'FL', 'Paid Search', '2025-03-10', '2025-03-13', '2025-03-17', NULL,         120000, NULL),   -- denied
  (11, 'Student Refi',  'NY', 'Direct Mail', '2025-03-17', '2025-03-20', NULL,          NULL,         40000, NULL),   -- in decision
  (12, 'Personal Loan', 'TX', 'Organic',     '2025-03-24', NULL,          NULL,          NULL,         10000, NULL);  -- just applied

-- Funnel summary:
--   Applied:   12 (all rows have application_date)
--   Reviewed:  10 (rows 1-7, 9-11 have review_date; 8 and 12 are NULL)
--   Decided:    7 (rows 1-3, 5-6, 9-10 have decision_date)
--   Funded:     5 (rows 1, 2, 5, 6, 9 have funding_date)
