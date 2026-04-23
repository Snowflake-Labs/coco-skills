-- SV Diagnostics: Verification Queries
--
-- Each section triggers a specific error on the BROKEN SV, states the exact
-- error message, then demonstrates the FIX on the corrected SV.

-- ══════════════════════════════════════════════════════════════════════════════
-- SCENARIO 1: AMBIGUOUS PATH RELATIONSHIP
-- ══════════════════════════════════════════════════════════════════════════════

-- Step 1a: Confirm the broken SV appears healthy — non-date queries work fine.
--          This is why the bug is insidious: many queries succeed before anyone
--          tries a time-series breakdown.
SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.DEALS_AMBIGUOUS_PATH_SV
    DIMENSIONS deals.product
    METRICS    deals.total_amount
)
ORDER BY product;
-- | PRODUCT         | TOTAL_AMOUNT |
-- |-----------------|--------------|
-- | Analytics       | 240000.00    |
-- | Data Pipelines  |  73500.00    |

-- Step 1b: Trigger the ambiguous path error — group by a date dimension.
--          ANY metric + ANY date dimension fires this error.
SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.DEALS_AMBIGUOUS_PATH_SV
    DIMENSIONS date_dim.year, date_dim.month_name
    METRICS    deals.total_amount
)
ORDER BY year, month_name;
-- ERROR: SQL compilation error:
-- Invalid dimension specified: Multi-path relationship between the dimension
-- entity 'DATE_DIM' and the base metric or dimension entity 'DEALS' is not supported.

-- Step 1c: Fixed SV — USING on each metric picks the correct date path.
--          deal_count_created: buckets by creation date (all 12 deals visible)
--          deal_count_closed:  buckets by close date (5 open deals → NULL row)
SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.DEALS_FIXED_SV
    DIMENSIONS date_dim.year, date_dim.month_num, date_dim.month_name
    METRICS    deals.deal_count_created, deals.deal_count_closed
)
ORDER BY year, month_num;
-- | YEAR | MONTH_NUM | MONTH_NAME | DEAL_COUNT_CREATED | DEAL_COUNT_CLOSED |
-- |------|-----------|------------|--------------------|-------------------|
-- | 2025 |         1 | January    |                  4 |                 1 |
-- | 2025 |         2 | February   |                  4 |                 2 |
-- | 2025 |         3 | March      |                  4 |                 4 |
-- | NULL |      NULL |            |               NULL |                 5 |


-- ══════════════════════════════════════════════════════════════════════════════
-- SCENARIO 2: FAN TRAP
-- ══════════════════════════════════════════════════════════════════════════════

-- Step 2a: Confirm the broken SV works for dimensions at or above DEALS grain.
--          Rep and stage are fine — they join directly to DEALS.
SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.DEALS_FAN_TRAP_SV
    DIMENSIONS rep_dim.rep_name
    METRICS    deals.total_amount
)
ORDER BY rep_name;
-- | REP_NAME      | TOTAL_AMOUNT |
-- |---------------|--------------|
-- | Alice Nguyen  | 160000.00    |
-- | Bob Torres    |  45000.00    |
-- | Carol Kim     |  80000.00    |
-- | David Osei    |  28500.00    |

-- Step 2b: Trigger the fan trap — group by a product dimension.
--          products.category is only reachable via DEAL_ITEMS (many-per-deal),
--          which is at a finer grain than DEALS. The SV engine catches this.
SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.DEALS_FAN_TRAP_SV
    DIMENSIONS products.category
    METRICS    deals.total_amount
)
ORDER BY category;
-- ERROR: SQL compilation error:
-- Invalid dimension specified: The dimension entity 'PRODUCTS' must be related
-- to and have an equal or lower level of granularity compared to the base metric
-- or dimension entity 'DEALS'.

-- Step 2c: Fixed SV — metric moved to DEAL_ITEMS grain.
--          Total revenue by product category, no fan trap.
SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.DEALS_FAN_TRAP_FIXED_SV
    DIMENSIONS products.category
    METRICS    deal_items.total_revenue, deal_items.item_count
)
ORDER BY category;
-- | CATEGORY        | TOTAL_REVENUE | ITEM_COUNT |
-- |-----------------|---------------|------------|
-- | Analytics       |    200666.66  |         10 |
-- | Data Pipelines  |    112833.34  |          8 |


-- ══════════════════════════════════════════════════════════════════════════════
-- SCENARIO 3: TABLE WITH NO RELATIONSHIP
-- ══════════════════════════════════════════════════════════════════════════════

-- Step 3a: Confirm the broken SV works for connected dimensions.
--          rep_dim has a relationship — its dimensions work fine.
SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.DEALS_NO_REL_SV
    DIMENSIONS rep_dim.region
    METRICS    deals.total_amount
)
ORDER BY region;
-- | REGION | TOTAL_AMOUNT |
-- |--------|--------------|
-- | East   | 108500.00    |
-- | West   | 205000.00    |

-- Step 3b: Trigger the no-relationship error — use the orphaned dim_region table.
--          Note: IDENTICAL error message to the fan trap (Scenario 2).
--          Distinguishing factor: check RELATIONSHIPS clause for a missing entry.
SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.DEALS_NO_REL_SV
    DIMENSIONS dim_region.region_name
    METRICS    deals.total_amount
);
-- ERROR: SQL compilation error:
-- Invalid dimension specified: The dimension entity 'DIM_REGION' must be related
-- to and have an equal or lower level of granularity compared to the base metric
-- or dimension entity 'DEALS'.

-- Step 3c: Fixed SV — relationship added: deals → rep_dim → dim_region.
SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.DEALS_NO_REL_FIXED_SV
    DIMENSIONS dim_region.region_name
    METRICS    deals.total_amount
)
ORDER BY region_name;
-- | REGION_NAME     | TOTAL_AMOUNT |
-- |-----------------|--------------|
-- | Eastern Region  | 108500.00    |
-- | Western Region  | 205000.00    |


-- ══════════════════════════════════════════════════════════════════════════════
-- SCENARIO 4A: DUPLICATE LOGICAL NAME — DEPLOY-TIME ERROR
-- ══════════════════════════════════════════════════════════════════════════════

-- Reproducing this error requires attempting to CREATE the broken SV.
-- See semantic_view.sql — the DEALS_DUPE_NAME_SV definition is commented out.
-- Uncomment and run to observe the deploy-time error:
--
-- ERROR: SQL compilation error: error line N at position N
-- invalid identifier '<duplicate_name>'
--
-- Fix: ensure every dimension and metric has a globally unique logical name
-- within the SV. Use entity-scoped names (rep_segment, product_segment) when
-- the same concept appears on multiple entities.


-- ══════════════════════════════════════════════════════════════════════════════
-- SCENARIO 4B: OVERLAPPING SYNONYMS — CORTEX ANALYST AMBIGUITY
-- ══════════════════════════════════════════════════════════════════════════════
-- These queries are run via the Cortex Analyst API, not SEMANTIC_VIEW().
-- Results are shown as CA responses.

-- Q4b-1: "What is total revenue by segment?"
-- → CA response on DEALS_AMBIGUOUS_NAMES_SV:
--   "The term 'segment' is ambiguous. It could refer to 'product_segment'
--    (product category/segment) or 'rep_segment' (rep region/territory/segment).
--    Could you clarify which segment you mean?"
--
-- → CA response on DEALS_CLEAR_NAMES_SV:
--   "The term 'segment' is ambiguous. The closest dimensions are 'product_category'
--    or 'rep_territory'. Could you clarify which 'segment' you mean?"
--   (Still can't answer — "segment" was deliberately removed from all synonyms.)

-- Q4b-2: "What is total revenue by area?"
-- → CA response on DEALS_AMBIGUOUS_NAMES_SV:
--   "The term 'area' is ambiguous — it matches both 'product_segment' and
--    'rep_segment'. Could you clarify?"

-- Q4b-3: "What is total revenue?"
-- → CA response on DEALS_AMBIGUOUS_NAMES_SV:
--   "'total revenue' can refer to: (1) 'total_amount' — deal-level total, or
--    (2) 'total_revenue' — line-item level total. These may produce different
--    results. Could you clarify?"

-- Q4b-4: "What is product revenue by product category?"
-- → CA on DEALS_AMBIGUOUS_NAMES_SV: answers correctly (unambiguous phrasing)
-- → CA on DEALS_CLEAR_NAMES_SV:     answers correctly ✓

-- Q4b-5: "What is total deal value by rep territory?"
-- → CA on DEALS_CLEAR_NAMES_SV: answers correctly ✓
--   Generated SQL routes to deals.deal_value metric + rep_dim.rep_territory dimension


-- ══════════════════════════════════════════════════════════════════════════════
-- SCENARIO 5A: REVERSED RELATIONSHIP DIRECTION — DEPLOY-TIME ERROR
-- ══════════════════════════════════════════════════════════════════════════════

-- Reproducing this error requires attempting to CREATE the broken SV.
-- See semantic_view.sql — DEALS_REVERSED_REL_SV is commented out.
-- Uncomment and run to observe:
--
-- ERROR: SQL compilation error:
-- The referenced key in the relationship 'REP_DIM REFERENCES DEALS' must be
-- the primary or unique key of the referenced entity.
--
-- This fires because DEALS.REP_ID is not a declared PK or UK of DEALS.
-- The SV engine enforces that the RHS of REFERENCES must be a PK/UK — this
-- catches reversed-direction mistakes whenever the FK column is not also unique.
--
-- Note: this guard only works when the FK column is NOT the PK of its table.
-- If both the left and right columns happen to be declared PKs (Scenario 5b),
-- the engine cannot detect the cardinality lie and the model deploys silently.


-- ══════════════════════════════════════════════════════════════════════════════
-- SCENARIO 5B: WRONG CARDINALITY — SILENT WRONG RESULTS
-- ══════════════════════════════════════════════════════════════════════════════

-- Step 5b-1: Confirm the wrong-cardinality SV looks healthy for safe queries.
--            Line-item metrics by product work correctly because the join path
--            is the same regardless of the declared PK.
SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.DEALS_BOTH_UNIQUE_SV
    DIMENSIONS products.category
    METRICS    deal_items.total_revenue, deal_items.item_count
)
ORDER BY category;
-- | CATEGORY        | TOTAL_REVENUE | ITEM_COUNT |
-- |-----------------|---------------|------------|
-- | Analytics       | 221666.66     |         10 |   ← correct ✓
-- | Data Pipelines  |  91833.34     |          8 |   ← correct ✓

-- Step 5b-2: The dangerous query — header-level metric by fine-grain dimension.
--            On a correctly-declared SV this errors (fan trap caught).
--            On the wrong-cardinality SV it runs and silently inflates numbers.
--
--            Why: declaring PRIMARY KEY (DEAL_ID) on DEAL_ITEMS tells the engine
--            the relationship is 1:1. It believes no fan-out can occur and skips
--            the cardinality check. Every deal with multiple items gets its
--            AMOUNT counted once per item.
SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.DEALS_BOTH_UNIQUE_SV
    DIMENSIONS products.category
    METRICS    deals.total_amount
)
ORDER BY category;
-- | CATEGORY        | TOTAL_AMOUNT  |
-- |-----------------|---------------|
-- | Analytics       | 430000.00     |   ← WRONG: should be ~$240k (multi-item deals counted 2-3×)
-- | Data Pipelines  | 146500.00     |   ← WRONG: should be ~$73.5k

-- Step 5b-3: Prove the correctly-declared SV would catch this as a fan trap error.
SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.DEALS_FAN_TRAP_SV
    DIMENSIONS products.category
    METRICS    deals.total_amount
)
ORDER BY category;
-- ERROR: SQL compilation error:
-- Invalid dimension specified: The dimension entity 'PRODUCTS' must be related
-- to and have an equal or lower level of granularity compared to the base metric
-- or dimension entity 'DEALS'.
--
-- The guard is disabled by the wrong PK declaration. Same model structure,
-- same wrong query — one errors (safe), one silently returns garbage (dangerous).

-- Step 5b-4: Detection heuristic — compare totals against raw SQL.
--            If a SV metric total doesn't match a direct table aggregate, the
--            model likely has a cardinality lie somewhere in the TABLES clause.
SELECT SUM(amount) AS raw_total FROM SEMANTIC_SKILLS.SNIPPETS.DEALS;
-- | RAW_TOTAL   |
-- |-------------|
-- | 313500.00   |   ← correct deal total; $430k + $146.5k = $576.5k in the SV ≠ this


-- ══════════════════════════════════════════════════════════════════════════════
-- SCENARIO 6: FORGOTTEN SEMI-ADDITIVE BEHAVIOR — CHECKLIST ONLY
-- ══════════════════════════════════════════════════════════════════════════════
--
-- No query to run — this is a model review heuristic, not a detectable error.
--
-- Ask this question for every FACT and METRIC in your model:
--   "Does this column represent a SNAPSHOT (balance, headcount, inventory level)
--    or a FLOW (revenue, quantity sold, events)?"
--
--   FLOW   → SUM is correct. Adding up revenue across time periods is meaningful.
--   SNAPSHOT → SUM across time is almost certainly wrong. Summing daily account
--              balances across 30 days gives a number 30× too large.
--
-- Examples of snapshot metrics that should NOT be SUM'd across time:
--   - Account balance (bank, savings, investment)
--   - Headcount / employee count
--   - Inventory on hand
--   - Active subscriptions
--   - Open pipeline value (the same deal counted every day it's open)
--
-- The correct aggregation for snapshots is either:
--   - LAST_VALUE (closing balance, end-of-period headcount)
--   - AVG (average daily balance, average inventory)
--   - MAX/MIN (peak/trough)
--
-- Snowflake SVs support this via NON ADDITIVE BY — see the `semi_additive_metric`
-- snippet for the full pattern. Use the question above as your trigger to go look
-- at that snippet before defining a SUM on any snapshot column.
