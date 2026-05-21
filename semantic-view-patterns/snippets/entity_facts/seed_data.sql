-- Entity Facts: Seed Data

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

INSERT INTO customers VALUES
    (1, 'Alice Martin', 1988),
    (2, 'Bob Chen',     1975),
    (3, 'Carol White',  1995),
    (4, 'Dan Patel',    2000);

-- Alice:  4 orders totaling $4200  → "high value" (>$3000)
-- Bob:    3 orders totaling $1700  → "medium value" ($1000-$3000)
-- Carol:  2 orders totaling $600   → "low value" (<$1000)
-- Dan:    1 order  totaling $200   → "low value"
INSERT INTO orders VALUES
    (101, 1, '2024-01-10', 1200),
    (102, 1, '2024-02-15', 800),
    (103, 1, '2024-03-20', 1500),
    (104, 1, '2024-04-05', 700),
    (105, 2, '2024-01-12', 900),
    (106, 2, '2024-03-18', 500),
    (107, 2, '2024-05-01', 300),
    (108, 3, '2024-02-22', 400),
    (109, 3, '2024-04-30', 200),
    (110, 4, '2024-03-15', 200);
