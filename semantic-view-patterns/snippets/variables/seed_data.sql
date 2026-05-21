-- Variables: Seed Data

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

INSERT INTO product_sales VALUES
    (1, 101, 'Laptop Pro',    'Electronics', '2024-01-15', 2, 1200.00, 4.5),
    (2, 102, 'Desk Chair',    'Furniture',   '2024-01-16', 5,  150.00, 4.0),
    (3, 101, 'Laptop Pro',    'Electronics', '2024-02-20', 1, 1200.00, 4.8),
    (4, 103, 'Mouse Pad',     'Accessories', '2024-01-17', 10,  15.00, 3.5),
    (5, 104, 'Standing Desk', 'Furniture',   '2024-02-10', 3,  450.00, 4.9),
    (6, 102, 'Desk Chair',    'Furniture',   '2024-03-05', 2,  150.00, 3.8),
    (7, 103, 'Mouse Pad',     'Accessories', '2024-01-25', 8,   15.00, 3.2),
    (8, 105, 'Monitor 4K',    'Electronics', '2024-03-10', 4,  600.00, 4.7);
