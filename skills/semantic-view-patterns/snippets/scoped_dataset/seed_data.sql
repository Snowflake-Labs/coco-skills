-- Scoped Dataset: Seed Data

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

INSERT INTO lob_customers VALUES
    (1,  'Acme Corp',      'Enterprise', 'Enterprise'),
    (2,  'TechStart LLC',  'Enterprise', 'Enterprise'),
    (3,  'Corner Store',   'SMB',        'SMB'),
    (4,  'Main St Deli',   'SMB',        'SMB'),
    (5,  'City Dept Store','Standard',   'Retail'),
    (6,  'Fashion Hub',    'Standard',   'Retail');

INSERT INTO sales_transactions VALUES
    -- Enterprise LOB
    (1,  1, '2024-01-10', 5000, 'West',  'Enterprise'),
    (2,  1, '2024-02-15', 3200, 'West',  'Enterprise'),
    (3,  2, '2024-01-20', 7500, 'East',  'Enterprise'),
    (4,  2, '2024-03-08', 4100, 'East',  'Enterprise'),
    -- SMB LOB
    (5,  3, '2024-01-12',  450, 'North', 'SMB'),
    (6,  3, '2024-02-22',  320, 'North', 'SMB'),
    (7,  4, '2024-01-30',  180, 'South', 'SMB'),
    (8,  4, '2024-03-15',  250, 'South', 'SMB'),
    -- Retail LOB
    (9,  5, '2024-01-05',  120, 'West',  'Retail'),
    (10, 5, '2024-02-10',   95, 'West',  'Retail'),
    (11, 6, '2024-01-25',  210, 'East',  'Retail'),
    (12, 6, '2024-03-20',  175, 'East',  'Retail');
