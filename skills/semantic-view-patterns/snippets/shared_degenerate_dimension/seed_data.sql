-- Shared Degenerate Dimension: Seed Data

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

INSERT INTO store_orders VALUES
    (1,  '2024-01-05', 'North', 'Electronics', 1200),
    (2,  '2024-01-12', 'South', 'Furniture',    450),
    (3,  '2024-02-01', 'East',  'Electronics',  800),
    (4,  '2024-02-15', 'North', 'Apparel',       95),
    (5,  '2024-03-10', 'West',  'Electronics', 1500),
    (6,  '2024-03-22', 'South', 'Apparel',      120);

INSERT INTO web_orders VALUES
    (1,  '2024-01-08', 'North', 'mobile',  300),
    (2,  '2024-01-20', 'East',  'desktop', 750),
    (3,  '2024-02-05', 'West',  'mobile',  420),
    (4,  '2024-02-18', 'South', 'desktop', 680),
    (5,  '2024-03-01', 'North', 'mobile',  210),
    (6,  '2024-03-14', 'East',  'desktop', 940),
    (7,  '2024-03-28', 'West',  'mobile',  380);

-- Verify the union view contains exactly the 4 regions from both fact tables
-- SELECT * FROM region_dim ORDER BY region;
-- → East, North, South, West
