-- Semi-Additive Metric Example: Seed Data
-- Run schema.sql first.

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- 3 accounts, 5 daily snapshots each (15 rows total)
--
-- Point-in-time totals (sum across accounts per date):
--   2024-01-31:  1000 + 5000 +  800 =  6,800
--   2024-02-29:  1200 + 4800 +  900 =  6,900
--   2024-03-31:  1100 + 5200 +  850 =  7,150
--   2024-04-30:  1300 + 5100 + 1000 =  7,400
--   2024-05-31:  1250 + 5300 +  950 =  7,500
--
-- WRONG: naive SUM across all rows = 6,800+6,900+7,150+7,400+7,500 = 35,750
--        (5x overcounted — every balance counted once per month instead of once total)
--
-- AVG balance per account (across months):
--   A001: (1000+1200+1100+1300+1250) / 5 = 1,170
--   A002: (5000+4800+5200+5100+5300) / 5 = 5,080
--   A003: ( 800+ 900+ 850+1000+ 950) / 5 =   900
INSERT INTO ACCOUNT_BALANCES VALUES
    ( 1, 'A001', 'Checking Account',   '2024-01-31',  1000.00),
    ( 2, 'A001', 'Checking Account',   '2024-02-29',  1200.00),
    ( 3, 'A001', 'Checking Account',   '2024-03-31',  1100.00),
    ( 4, 'A001', 'Checking Account',   '2024-04-30',  1300.00),
    ( 5, 'A001', 'Checking Account',   '2024-05-31',  1250.00),
    ( 6, 'A002', 'Business Reserve',   '2024-01-31',  5000.00),
    ( 7, 'A002', 'Business Reserve',   '2024-02-29',  4800.00),
    ( 8, 'A002', 'Business Reserve',   '2024-03-31',  5200.00),
    ( 9, 'A002', 'Business Reserve',   '2024-04-30',  5100.00),
    (10, 'A002', 'Business Reserve',   '2024-05-31',  5300.00),
    (11, 'A003', 'Savings Account',    '2024-01-31',   800.00),
    (12, 'A003', 'Savings Account',    '2024-02-29',   900.00),
    (13, 'A003', 'Savings Account',    '2024-03-31',   850.00),
    (14, 'A003', 'Savings Account',    '2024-04-30',  1000.00),
    (15, 'A003', 'Savings Account',    '2024-05-31',   950.00);
