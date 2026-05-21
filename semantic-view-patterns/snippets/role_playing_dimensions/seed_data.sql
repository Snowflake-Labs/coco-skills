-- Role-Playing Dimensions: Seed Data
--
-- 8 orders spanning Nov 2024 – Feb 2025.
-- Four orders ship in a different month than they were placed —
-- that's what makes the role-playing demo interesting.
--
-- Revenue by ORDER_MONTH:  Nov=$1,300  Dec=$1,500  Jan=$1,350  Feb=$1,750  (total $5,900)
-- Revenue by SHIP_MONTH:   Nov=$500    Dec=$1,100  Jan=$1,650  Feb=$1,550  Mar=$1,100

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- DIM_DATE — one row for every date referenced by ORDERS
-- ============================================================

INSERT INTO DIM_DATE (date_key, month_num, month_name, quarter, year) VALUES
  -- November 2024 dates
  ('2024-11-15', 11, 'November', 'Q4', 2024),
  ('2024-11-20', 11, 'November', 'Q4', 2024),
  ('2024-11-28', 11, 'November', 'Q4', 2024),
  -- December 2024 dates
  ('2024-12-01', 12, 'December', 'Q4', 2024),
  ('2024-12-03', 12, 'December', 'Q4', 2024),
  ('2024-12-05', 12, 'December', 'Q4', 2024),
  ('2024-12-20', 12, 'December', 'Q4', 2024),
  -- January 2025 dates
  ('2025-01-04',  1, 'January',  'Q1', 2025),
  ('2025-01-10',  1, 'January',  'Q1', 2025),
  ('2025-01-15',  1, 'January',  'Q1', 2025),
  ('2025-01-25',  1, 'January',  'Q1', 2025),
  -- February 2025 dates
  ('2025-02-02',  2, 'February', 'Q1', 2025),
  ('2025-02-14',  2, 'February', 'Q1', 2025),
  ('2025-02-20',  2, 'February', 'Q1', 2025),
  ('2025-02-28',  2, 'February', 'Q1', 2025),
  -- March 2025 dates
  ('2025-03-05',  3, 'March',    'Q1', 2025);

-- ============================================================
-- ORDERS — 8 rows; 4 cross-month shippers marked with ←
-- ============================================================

INSERT INTO ORDERS (order_id, customer_name, order_date, ship_date, amount) VALUES
  (1, 'Acme Corp',    '2024-11-15', '2024-11-20',  500.00),  -- same month
  (2, 'Beta LLC',     '2024-11-28', '2024-12-03',  800.00),  -- ← Nov order, Dec ship
  (3, 'Gamma Inc',    '2024-12-01', '2024-12-05',  300.00),  -- same month
  (4, 'Delta Co',     '2024-12-20', '2025-01-04', 1200.00),  -- ← Dec order, Jan ship (crosses year!)
  (5, 'Acme Corp',    '2025-01-10', '2025-01-15',  450.00),  -- same month
  (6, 'Epsilon Ltd',  '2025-01-25', '2025-02-02',  900.00),  -- ← Jan order, Feb ship
  (7, 'Beta LLC',     '2025-02-14', '2025-02-20',  650.00),  -- same month
  (8, 'Gamma Inc',    '2025-02-28', '2025-03-05', 1100.00);  -- ← Feb order, Mar ship
