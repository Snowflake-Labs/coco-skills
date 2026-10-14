-- Row Access Policy Example: Seed Data
-- Run schema.sql first.

USE DATABASE RAP_TEST;
USE SCHEMA PUBLIC;

-- Four sales regions — two accessible per analyst role
INSERT INTO SALES_REGIONS VALUES
    ('R001', 'Northeast', 'Alice Johnson'),
    ('R002', 'Southeast', 'Bob Smith'),
    ('R003', 'Northwest', 'Carol Davis'),
    ('R004', 'Southwest', 'David Wilson');

-- Eight orders, two per region
INSERT INTO ORDERS VALUES
    (1, 'R001', '2024-01-15',  500.00),
    (2, 'R001', '2024-02-20',  750.00),
    (3, 'R002', '2024-01-10',  300.00),
    (4, 'R002', '2024-03-05',  450.00),
    (5, 'R003', '2024-01-25',  600.00),
    (6, 'R003', '2024-02-14',  800.00),
    (7, 'R004', '2024-01-08',  250.00),
    (8, 'R004', '2024-03-20',  950.00);

-- Expected totals by region:
--   R001  Northeast  $1,250   ← REGION_A_ANALYST can see
--   R002  Southeast    $750   ← REGION_A_ANALYST can see
--   R003  Northwest  $1,400   ← REGION_B_ANALYST can see
--   R004  Southwest  $1,200   ← REGION_B_ANALYST can see
--
-- REGION_A total:  $2,000 across 4 orders
-- REGION_B total:  $2,600 across 4 orders
-- Full total:      $4,600 across 8 orders
