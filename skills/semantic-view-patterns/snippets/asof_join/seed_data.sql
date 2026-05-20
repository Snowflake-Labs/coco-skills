-- ASOF Join Example: Seed Data

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- Cust001 moved addresses twice; Cust002 stayed put
INSERT INTO Customer_address VALUES
    ('Cust001', 90001, '100 First St.',  '2024-01-01'),
    ('Cust001', 90002, '200 First St.',  '2024-04-01'),
    ('Cust001', 90003, '300 First St.',  '2024-07-01'),
    ('Cust002', 90010, '10 Second St.',  '2024-01-01');

INSERT INTO Customer_name VALUES
    ('Cust001', 'Mary', 'Smith'),
    ('Cust002', 'Bill', 'Wilson');

-- Expected address at time of order:
--   Ord100 Feb 01 → Cust001 zip 90001 (moved Apr 01)
--   Ord101 Feb 02 → Cust001 zip 90001
--   Ord102 May 01 → Cust001 zip 90002 (moved Apr 01, not yet Jul 01)
--   Ord103 May 02 → Cust001 zip 90002
--   Ord104 Aug 01 → Cust001 zip 90003 (moved Jul 01)
--   Ord105 Aug 02 → Cust002 zip 90010 (never moved)
INSERT INTO Orders VALUES
    ('Ord100', 'Cust001', '2024-02-01', 100),
    ('Ord101', 'Cust001', '2024-02-02', 200),
    ('Ord102', 'Cust001', '2024-05-01', 300),
    ('Ord103', 'Cust001', '2024-05-02', 400),
    ('Ord104', 'Cust001', '2024-08-01', 500),
    ('Ord105', 'Cust002', '2024-08-02', 600);
