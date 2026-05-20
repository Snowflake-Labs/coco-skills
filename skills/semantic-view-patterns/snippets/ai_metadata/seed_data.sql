-- AI Metadata: Seed Data

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

INSERT INTO ai_customers VALUES
    (1, 'Alice Martin', 'West'),
    (2, 'Bob Chen',     'East'),
    (3, 'Carol White',  'West');

INSERT INTO ai_orders VALUES
    (101, 1, 1200.00, 'completed', '2024-01-10'),
    (102, 1,  800.00, 'completed', '2024-02-15'),
    (103, 2,  500.00, 'completed', '2024-01-12'),
    (104, 2,  300.00, 'refunded',  '2024-03-01'),
    (105, 3,  950.00, 'completed', '2024-02-20'),
    (106, 1, 1500.00, 'pending',   '2024-03-30');
