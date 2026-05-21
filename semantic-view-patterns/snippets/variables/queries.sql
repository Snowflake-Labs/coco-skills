-- Variables: Queries

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- WORKING QUERIES
-- ============================================================

-- 1. Default scoring weights: price=0.4, rating=0.6
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.PRODUCT_PERFORMANCE_SV
    DIMENSIONS product_name, category
    METRICS total_sales, avg_rating, performance_score
)
ORDER BY performance_score DESC;


-- 2. Override to rating-only weighting (price_weight=0, rating_weight=1)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.PRODUCT_PERFORMANCE_SV
    DIMENSIONS product_name, category
    METRICS total_sales, avg_rating, performance_score
    VARIABLES price_weight => 0, rating_weight => 1
)
ORDER BY performance_score DESC;


-- 3. Price tier breakdown using default thresholds (budget<$100, mid $100-$500, premium>$500)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.PRODUCT_PERFORMANCE_SV
    DIMENSIONS price_tier
    METRICS total_sales, total_revenue
)
ORDER BY price_tier;


-- 4. Adjust tier thresholds at query time
--    New tiers: budget <$200, mid-range $200-$400, premium >$400
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.PRODUCT_PERFORMANCE_SV
    DIMENSIONS price_tier
    METRICS total_sales, total_revenue
    VARIABLES premium_threshold => 400.00, budget_threshold => 200.00
)
ORDER BY price_tier;


-- 5. "Recent products" flag with custom analysis window
--    Only items sold in the last 60 days (from a specific reference date)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.PRODUCT_PERFORMANCE_SV
    DIMENSIONS product_name, is_recent
    METRICS total_sales
    VARIABLES recent_days => 60, analysis_date => '2024-03-31'
)
ORDER BY is_recent DESC, total_sales DESC;


-- ============================================================
-- VARIABLE RULES
-- ============================================================

-- Variables can ONLY be used in:
--   DIMENSIONS, METRICS, FACTS calculation expressions

-- Variables CANNOT be used in:
--   TABLES clause, RELATIONSHIPS clause

-- At query time: VARIABLES key => value
--   All unspecified variables use their DEFAULT value
--   If a variable has no DEFAULT, specifying it at query time is REQUIRED

-- Type coercion: the supplied value must be coercible to the declared type
-- (e.g. passing integer 1 for a DECIMAL(3,2) variable works fine)
