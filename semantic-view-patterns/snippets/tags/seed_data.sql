-- Tags: Seed Data

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

INSERT INTO tag_dim_date VALUES
    (1, '2024-01-01', 2024, 1),
    (2, '2024-02-01', 2024, 2),
    (3, '2024-03-01', 2024, 3),
    (4, '2024-04-01', 2024, 4);

INSERT INTO tag_store_sales VALUES
    (1, 1, 5000, 50), (2, 2, 6000, 60), (3, 3, 7000, 70), (4, 4, 4500, 45);

INSERT INTO tag_web_sales VALUES
    (1, 1, 2000, 25), (2, 2, 2500, 30), (3, 3, 3000, 35), (4, 4, 3500, 40);
