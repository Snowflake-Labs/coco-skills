-- SV Diagnostics: Seed Data

USE DATABASE SEMANTIC_SKILLS;
USE SCHEMA SNIPPETS;

-- ── DIM_DATE (shared calendar) ────────────────────────────────────────────────
INSERT INTO DIM_DATE (date_key, month_num, month_name, quarter, year)
SELECT column1, column2, column3, column4, column5
FROM VALUES
    ('2025-01-06'::DATE, 1, 'January',  'Q1', 2025),
    ('2025-01-15'::DATE, 1, 'January',  'Q1', 2025),
    ('2025-01-22'::DATE, 1, 'January',  'Q1', 2025),
    ('2025-01-28'::DATE, 1, 'January',  'Q1', 2025),
    ('2025-01-31'::DATE, 1, 'January',  'Q1', 2025),
    ('2025-02-03'::DATE, 2, 'February', 'Q1', 2025),
    ('2025-02-10'::DATE, 2, 'February', 'Q1', 2025),
    ('2025-02-14'::DATE, 2, 'February', 'Q1', 2025),
    ('2025-02-20'::DATE, 2, 'February', 'Q1', 2025),
    ('2025-02-28'::DATE, 2, 'February', 'Q1', 2025),
    ('2025-03-05'::DATE, 3, 'March',    'Q1', 2025),
    ('2025-03-12'::DATE, 3, 'March',    'Q1', 2025),
    ('2025-03-19'::DATE, 3, 'March',    'Q1', 2025),
    ('2025-03-25'::DATE, 3, 'March',    'Q1', 2025),
    ('2025-03-31'::DATE, 3, 'March',    'Q1', 2025)
WHERE NOT EXISTS (SELECT 1 FROM DIM_DATE WHERE date_key = column1);

-- ── DIM_REP ───────────────────────────────────────────────────────────────────
DELETE FROM DIM_REP;
INSERT INTO DIM_REP VALUES
    (1, 'Alice Nguyen', 'West', 'Enterprise'),
    (2, 'Bob Torres',   'West', 'SMB'),
    (3, 'Carol Kim',    'East', 'Enterprise'),
    (4, 'David Osei',   'East', 'SMB');

-- ── DIM_PRODUCT ───────────────────────────────────────────────────────────────
DELETE FROM DIM_PRODUCT;
INSERT INTO DIM_PRODUCT VALUES
    (1, 'Analytics Cloud',   'Analytics'),
    (2, 'Data Pipeline Pro', 'Data Pipelines'),
    (3, 'ML Workbench',      'Analytics'),
    (4, 'Connector Suite',   'Data Pipelines');

-- ── DIM_REGION (region_code must match DIM_REP.region values exactly) ─────────
DELETE FROM DIM_REGION;
INSERT INTO DIM_REGION VALUES
    ('West', 'Western Region'),
    ('East', 'Eastern Region');

-- ── DEALS ─────────────────────────────────────────────────────────────────────
DELETE FROM DEALS;
-- 12 deals Q1 2025. created_date and close_date often differ by weeks —
-- that gap makes the ambiguous-path error meaningful (Scenario 1).
-- AMOUNT is at header grain — used to demonstrate the fan trap (Scenario 2).
INSERT INTO DEALS VALUES
--  id  rep  created_date    close_date      amount     product          stage
    (1,  1, '2025-01-06', '2025-01-31',  45000.00, 'Analytics',      'Closed Won'),
    (2,  2, '2025-01-15', '2025-02-20',  12000.00, 'Data Pipelines', 'Closed Won'),
    (3,  3, '2025-01-22', '2025-02-28',  30000.00, 'Analytics',      'Closed Lost'),
    (4,  4, '2025-01-28', NULL,           8500.00, 'Data Pipelines', 'Open'),
    (5,  1, '2025-02-03', '2025-03-12',  55000.00, 'Analytics',      'Closed Won'),
    (6,  2, '2025-02-10', '2025-03-25',  18000.00, 'Data Pipelines', 'Closed Won'),
    (7,  3, '2025-02-14', '2025-03-31',  22000.00, 'Analytics',      'Closed Won'),
    (8,  4, '2025-02-20', NULL,          11000.00, 'Data Pipelines', 'Open'),
    (9,  1, '2025-03-05', '2025-03-31',  60000.00, 'Analytics',      'Closed Won'),
    (10, 2, '2025-03-12', NULL,          15000.00, 'Data Pipelines', 'Open'),
    (11, 3, '2025-03-19', NULL,          28000.00, 'Analytics',      'Open'),
    (12, 4, '2025-03-25', NULL,           9000.00, 'Data Pipelines', 'Open');

-- ── DEAL_ITEMS ────────────────────────────────────────────────────────────────
-- Each deal links to 1–3 products. LINE_AMOUNT is the per-product share of
-- the deal amount (evenly split). Deals with multiple items (1, 3, 5, 6, 7, 9)
-- are the ones that would double/triple-count if header AMOUNT were used.
DELETE FROM DEAL_ITEMS;
INSERT INTO DEAL_ITEMS VALUES
--  id  deal  product  line_amount
    (1,  1, 1, 22500.00),  -- Deal 1 ($45k): Analytics Cloud     (split 2 ways)
    (2,  1, 3, 22500.00),  -- Deal 1 ($45k): ML Workbench
    (3,  2, 2, 12000.00),  -- Deal 2 ($12k): Data Pipeline Pro   (single product)
    (4,  3, 1, 15000.00),  -- Deal 3 ($30k): Analytics Cloud     (split 2 ways)
    (5,  3, 3, 15000.00),  -- Deal 3 ($30k): ML Workbench
    (6,  4, 2,  8500.00),  -- Deal 4 ($8.5k): Data Pipeline Pro  (single product)
    (7,  5, 1, 18333.33),  -- Deal 5 ($55k): Analytics Cloud     (split 3 ways)
    (8,  5, 3, 18333.33),  -- Deal 5 ($55k): ML Workbench
    (9,  5, 4, 18333.34),  -- Deal 5 ($55k): Connector Suite
    (10, 6, 2,  9000.00),  -- Deal 6 ($18k): Data Pipeline Pro   (split 2 ways)
    (11, 6, 4,  9000.00),  -- Deal 6 ($18k): Connector Suite
    (12, 7, 1, 22000.00),  -- Deal 7 ($22k): Analytics Cloud     (single product)
    (13, 8, 2, 11000.00),  -- Deal 8 ($11k): Data Pipeline Pro   (single product)
    (14, 9, 1, 30000.00),  -- Deal 9 ($60k): Analytics Cloud     (split 2 ways)
    (15, 9, 3, 30000.00),  -- Deal 9 ($60k): ML Workbench
    (16,10, 4, 15000.00),  -- Deal 10 ($15k): Connector Suite    (single product)
    (17,11, 1, 28000.00),  -- Deal 11 ($28k): Analytics Cloud    (single product)
    (18,12, 2,  9000.00);  -- Deal 12 ($9k):  Data Pipeline Pro  (single product)
