-- Shared Degenerate Dimension: Queries

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- WORKING QUERIES
-- Using CHANNEL_BY_REGION_SV (physical helper view approach)
-- ============================================================

-- 1. Total revenue by region across both channels
--    regions.region is shared — works with metrics from EITHER fact table
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.CHANNEL_BY_REGION_SV
    DIMENSIONS regions.region
    METRICS total_revenue
)
ORDER BY total_revenue DESC;


-- 2. Store revenue only by region
--    regions.region is reachable from store_orders via store_to_region
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.CHANNEL_BY_REGION_SV
    DIMENSIONS regions.region
    METRICS store_orders.store_revenue
)
ORDER BY region;


-- 3. Web revenue only by region
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.CHANNEL_BY_REGION_SV
    DIMENSIONS regions.region
    METRICS web_orders.web_revenue
)
ORDER BY region;


-- 4. Side-by-side channel comparison by region
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.CHANNEL_BY_REGION_SV
    DIMENSIONS regions.region
    METRICS store_orders.store_revenue, web_orders.web_revenue, total_revenue
)
ORDER BY total_revenue DESC;


-- 5. Store revenue by category (fact-specific dimension — NOT shared)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.CHANNEL_BY_REGION_SV
    DIMENSIONS store_orders.category
    METRICS store_orders.store_revenue
)
ORDER BY store_revenue DESC;


-- 6. Web revenue by channel and region
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.CHANNEL_BY_REGION_SV
    DIMENSIONS regions.region, web_orders.channel
    METRICS web_orders.web_revenue
)
ORDER BY region, channel;


-- ============================================================
-- INLINE APPROACH (same queries, different SV name)
-- ============================================================

SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.CHANNEL_BY_REGION_INLINE_SV
    DIMENSIONS regions.region
    METRICS store_orders.store_revenue, web_orders.web_revenue, total_revenue
)
ORDER BY total_revenue DESC;


-- ============================================================
-- WHAT DOESN'T WORK (WITHOUT THIS PATTERN)
-- ============================================================

-- If you tried to use store_orders.region and web_orders.region as separate dimensions,
-- there is no relationship between them — they're just independent columns on separate facts.
-- A query asking "revenue by region" would have to pick one fact's region dimension,
-- and the other fact's metrics would not be groupable by the same region concept.

-- The union helper creates a SINGLE authoritative entity that both facts reference,
-- enabling consistent region-level analytics across both channels.

-- Also note: if one fact had a region value the other didn't (e.g. 'Pacific' only
-- in store_orders), UNION ensures 'Pacific' is still in the region_dim so it can
-- serve as the outer join anchor for web_revenue = 0 in that region.
