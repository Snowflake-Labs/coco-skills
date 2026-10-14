-- Materialization: Seed Data

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

INSERT INTO mat_customers VALUES
    (1, 'Acme Corp',    'Enterprise'),
    (2, 'Globex LLC',   'SMB'),
    (3, 'Initech',      'Enterprise'),
    (4, 'Umbrella Co',  'SMB'),
    (5, 'Soylent Corp', 'Enterprise');

INSERT INTO mat_orders VALUES
    (101, 1, '2023-01-10', 1200, 'West'),
    (102, 1, '2023-03-15',  800, 'West'),
    (103, 1, '2024-01-20', 1500, 'West'),
    (104, 2, '2023-02-14',  300, 'East'),
    (105, 2, '2023-08-22',  450, 'East'),
    (106, 3, '2023-04-01', 2100, 'South'),
    (107, 3, '2024-02-28', 1800, 'South'),
    (108, 4, '2023-06-10',  150, 'North'),
    (109, 4, '2023-11-05',  200, 'North'),
    (110, 5, '2023-07-07', 3400, 'West'),
    (111, 5, '2024-03-12', 2900, 'West'),
    (112, 1, '2024-04-18',  950, 'East');
