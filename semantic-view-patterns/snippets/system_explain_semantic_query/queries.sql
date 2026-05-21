-- SYSTEM$EXPLAIN_SEMANTIC_QUERY: Queries

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- HOW TO USE
-- ============================================================
--
-- SYSTEM$EXPLAIN_SEMANTIC_QUERY(sv_name, query_string)
--   sv_name      : fully qualified semantic view name (string literal)
--   query_string : a SEMANTIC_VIEW() query in $$...$$ dollar-quoting
--
-- Returns: the SQL the engine would generate — without executing it.
-- Safe to call even for queries that would fail at runtime.


-- ============================================================
-- 1. Simple metric + dimension
--    Shows: generated SELECT, GROUP BY, and join to customers table
-- ============================================================

SELECT SYSTEM$EXPLAIN_SEMANTIC_QUERY(
    'SNIPPETS.PUBLIC.SUPPORT_ANALYTICS_SV',
    $$
    SELECT sv.*
    FROM SEMANTIC_VIEW(
        SNIPPETS.PUBLIC.SUPPORT_ANALYTICS_SV
        METRICS support_tickets.total_tickets
        DIMENSIONS customers.tier
    ) AS sv
    $$
);


-- ============================================================
-- 2. Derived dimension from PRIVATE fact
--    Shows: the CASE expression for value_segment is inlined directly
--    into the generated SQL — the PRIVATE fact is never exposed as
--    a column; it appears as a subexpression inside the CASE.
-- ============================================================

SELECT SYSTEM$EXPLAIN_SEMANTIC_QUERY(
    'SNIPPETS.PUBLIC.SUPPORT_ANALYTICS_SV',
    $$
    SELECT sv.*
    FROM SEMANTIC_VIEW(
        SNIPPETS.PUBLIC.SUPPORT_ANALYTICS_SV
        METRICS support_tickets.total_tickets, support_tickets.total_revenue
        DIMENSIONS customers.value_segment
    ) AS sv
    $$
);


-- ============================================================
-- 3. Multi-metric cross-table query
--    Shows: how two metrics from different granularities
--    (ticket-level count + customer-level count) are combined
-- ============================================================

SELECT SYSTEM$EXPLAIN_SEMANTIC_QUERY(
    'SNIPPETS.PUBLIC.SUPPORT_ANALYTICS_SV',
    $$
    SELECT sv.*
    FROM SEMANTIC_VIEW(
        SNIPPETS.PUBLIC.SUPPORT_ANALYTICS_SV
        METRICS support_tickets.total_tickets, customers.customer_count
        DIMENSIONS customers.tier
    ) AS sv
    $$
);


-- ============================================================
-- 4. Using EXPLAIN to diagnose a failing query BEFORE running it
--    The query below mixes FACTS and METRICS — which is illegal.
--    EXPLAIN shows you the intended SQL so you can spot the issue
--    without waiting for a runtime error.
-- ============================================================

-- First, see what EXPLAIN shows for the mixed query:
SELECT SYSTEM$EXPLAIN_SEMANTIC_QUERY(
    'SNIPPETS.PUBLIC.SUPPORT_ANALYTICS_SV',
    $$
    SELECT sv.*
    FROM SEMANTIC_VIEW(
        SNIPPETS.PUBLIC.SUPPORT_ANALYTICS_SV
        FACTS support_tickets.ticket_amount
        METRICS support_tickets.total_tickets    -- mixing FACTS + METRICS
    ) AS sv
    $$
);

-- Then confirm the actual runtime error:
-- SELECT sv.*
-- FROM SEMANTIC_VIEW(
--     SNIPPETS.PUBLIC.SUPPORT_ANALYTICS_SV
--     FACTS support_tickets.ticket_amount
--     METRICS support_tickets.total_tickets
-- ) AS sv;
-- → Error: Cannot specify FACTS and METRICS in the same SEMANTIC_VIEW clause


-- ============================================================
-- RUNNING QUERIES (for comparison with EXPLAIN output)
-- ============================================================

-- Q1: What EXPLAIN showed in query 1 — tickets by tier
SELECT sv.*
FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.SUPPORT_ANALYTICS_SV
    METRICS support_tickets.total_tickets
    DIMENSIONS customers.tier
) AS sv
ORDER BY sv.total_tickets DESC;


-- Q2: What EXPLAIN showed in query 2 — revenue by value segment
SELECT sv.*
FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.SUPPORT_ANALYTICS_SV
    METRICS support_tickets.total_tickets, support_tickets.total_revenue
    DIMENSIONS customers.value_segment
) AS sv
ORDER BY sv.value_segment;


-- ============================================================
-- HOW SYSTEM$EXPLAIN_SEMANTIC_QUERY WORKS:
-- The function compiles the SEMANTIC_VIEW() call against the SV's
-- metadata — resolving metric expressions, join paths, and dimension
-- derivations — then serializes the resulting logical plan as SQL.
-- No tables are scanned; no query is executed. Use it freely as a
-- debugging and learning tool.
