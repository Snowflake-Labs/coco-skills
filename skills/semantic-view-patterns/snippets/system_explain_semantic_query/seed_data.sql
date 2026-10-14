-- SYSTEM$EXPLAIN_SEMANTIC_QUERY: Seed Data

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

INSERT INTO customers VALUES
    (1, 'Acme Corp',      'enterprise'),
    (2, 'Riverside LLC',  'mid-market'),
    (3, 'Tiny Co',        'smb'),
    (4, 'Global Inc',     'enterprise');

INSERT INTO support_tickets VALUES
    ( 1, 1, '2024-01-10', 'P1', 120000.00),
    ( 2, 1, '2024-02-14', 'P2',  80000.00),
    ( 3, 1, '2024-03-05', 'P3',  50000.00),
    ( 4, 2, '2024-01-22', 'P1',  45000.00),
    ( 5, 2, '2024-03-18', 'P2',  30000.00),
    ( 6, 3, '2024-02-01', 'P3',   5000.00),
    ( 7, 3, '2024-04-09', 'P2',   8000.00),
    ( 8, 4, '2024-01-30', 'P1',  95000.00),
    ( 9, 4, '2024-02-20', 'P1', 110000.00),
    (10, 4, '2024-04-15', 'P2',  60000.00);
