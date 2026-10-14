-- Caller Rights: Seed Data

USE ROLE SYSADMIN;
USE SCHEMA SV_CALLER_TEST.DATA;

INSERT INTO CUSTOMER VALUES
    ('cust001', 'Mary', 'Smith'),
    ('cust002', 'Bill', 'Wilson');

-- cust001 moved twice; cust002 moved once — used to test ASOF join resolves correct zip
INSERT INTO CUSTOMER_ADDRESS VALUES
    ('cust001', '94025', '100 Main Street', '2024-01-01', '2024-03-31'),
    ('cust001', '94026', '200 Main Street', '2024-04-01', '2024-06-30'),
    ('cust001', '94027', '300 Main Street', '2024-07-01', NULL),
    ('cust002', '94028', '400 Main Street', '2024-01-01', '2024-04-30'),
    ('cust002', '94029', '500 Main Street', '2024-05-01', '2024-07-31'),
    ('cust002', '94030', '600 Main Street', '2024-08-01', NULL);

-- Expected zip at order time (ASOF resolves to the address active on o_ord_date):
--   ord100 2024-02-01 cust001 → 94025   ord101 2024-02-02 cust001 → 94025
--   ord102 2024-05-01 cust001 → 94026   ord103 2024-05-02 cust001 → 94026
--   ord104 2024-08-01 cust001 → 94027   ord105 2024-08-02 cust001 → 94027
--   ord106 2024-03-01 cust002 → 94028   ord107 2024-03-02 cust002 → 94028
--   ord108 2024-06-01 cust002 → 94029   ord109 2024-06-02 cust002 → 94029
--   ord110 2024-09-01 cust002 → 94030   ord111 2024-09-02 cust002 → 94030
INSERT INTO ORDERS VALUES
    ('ord100', 'cust001', '2024-02-01', 100),
    ('ord101', 'cust001', '2024-02-02', 200),
    ('ord102', 'cust001', '2024-05-01', 300),
    ('ord103', 'cust001', '2024-05-02', 400),
    ('ord104', 'cust001', '2024-08-01', 500),
    ('ord105', 'cust001', '2024-08-02', 600),
    ('ord106', 'cust002', '2024-03-01', 100),
    ('ord107', 'cust002', '2024-03-02', 200),
    ('ord108', 'cust002', '2024-06-01', 300),
    ('ord109', 'cust002', '2024-06-02', 400),
    ('ord110', 'cust002', '2024-09-01', 500),
    ('ord111', 'cust002', '2024-09-02', 600);
