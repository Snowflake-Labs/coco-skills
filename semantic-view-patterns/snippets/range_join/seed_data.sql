-- Range Join Example: Seed Data
-- Run schema.sql first.

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- Customer segment history (SCD2, EXCLUSIVE end dates)
--
-- C001: Free → Growth → Enterprise
-- C002: Growth → Enterprise
-- C003: Free (never upgraded)
INSERT INTO CUSTOMER_SEGMENTS VALUES
    (1,  'C001', 'Free',       '2024-01-01', '2024-04-01'),
    (2,  'C001', 'Growth',     '2024-04-01', '2024-07-01'),
    (3,  'C001', 'Enterprise', '2024-07-01', '9999-12-31'),
    (4,  'C002', 'Growth',     '2024-01-01', '2024-06-01'),
    (5,  'C002', 'Enterprise', '2024-06-01', '9999-12-31'),
    (6,  'C003', 'Free',       '2024-01-01', '9999-12-31');

-- Orders
--
-- Expected tier at time of purchase:
--   O001: C001 ordered 2024-01-15  → Free     (valid 2024-01-01 to 2024-04-01)
--   O002: C001 ordered 2024-04-20  → Growth   (valid 2024-04-01 to 2024-07-01)
--   O003: C001 ordered 2024-09-10  → Enterprise (valid 2024-07-01+)
--   O004: C002 ordered 2024-02-05  → Growth   (valid 2024-01-01 to 2024-06-01)
--   O005: C002 ordered 2024-07-03  → Enterprise (valid 2024-06-01+)
--   O006: C003 ordered 2024-03-12  → Free
--   O007: C003 ordered 2024-06-08  → Free
--   O008: C003 ordered 2024-10-01  → Free
INSERT INTO ORDERS VALUES
    (1, 'C001', '2024-01-15', 49.00),
    (2, 'C001', '2024-04-20', 149.00),
    (3, 'C001', '2024-09-10', 499.00),
    (4, 'C002', '2024-02-05', 149.00),
    (5, 'C002', '2024-07-03', 499.00),
    (6, 'C003', '2024-03-12', 49.00),
    (7, 'C003', '2024-06-08', 49.00),
    (8, 'C003', '2024-10-01', 49.00);

-- Verify: Revenue by correct historical segment should be:
--   Free:       O001 + O006 + O007 + O008 = 49 + 49 + 49 + 49 = $196
--   Growth:     O002 + O004              = 149 + 149           = $298
--   Enterprise: O003 + O005              = 499 + 499           = $998
--   Total:                                                        $1,492
