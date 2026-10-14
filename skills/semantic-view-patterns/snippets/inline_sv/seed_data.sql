-- Inline SV: Seed Data

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

INSERT INTO inline_customers VALUES
    (1, 'Alice Martin', 'premium'),
    (2, 'Bob Chen',     'standard'),
    (3, 'Carol White',  'premium'),
    (4, 'Dan Patel',    'standard');

INSERT INTO inline_orders VALUES
    (101, 1, 1200.00, 'completed'),
    (102, 1,  800.00, 'completed'),
    (103, 2,  500.00, 'completed'),
    (104, 2,  300.00, 'refunded'),
    (105, 3,  950.00, 'completed'),
    (106, 4,  200.00, 'pending');
