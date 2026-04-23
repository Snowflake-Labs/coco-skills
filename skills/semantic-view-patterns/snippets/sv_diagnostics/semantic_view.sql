-- SV Diagnostics: All Semantic View DDL
--
-- Six diagnostic scenarios — each has a BROKEN version (to trigger the error)
-- and a FIXED version (the correct model). Read together with queries.sql and README.md.
--
-- Scenario 1: Ambiguous path relationship     → DEALS_AMBIGUOUS_PATH_SV  / DEALS_FIXED_SV
-- Scenario 2: Fan trap                         → DEALS_FAN_TRAP_SV        / DEALS_FAN_TRAP_FIXED_SV
-- Scenario 3: Table with no relationship       → DEALS_NO_REL_SV          / DEALS_NO_REL_FIXED_SV
-- Scenario 4: Duplicate name / ambiguous synos → DEALS_DUPE_NAME_SV (deploy error)
--                                                DEALS_AMBIGUOUS_NAMES_SV / DEALS_CLEAR_NAMES_SV
-- Scenario 5: Wrong relationship direction     → deploy-time error (reversed FK/PK)
--             Wrong cardinality (lying PK)     → DEALS_BOTH_UNIQUE_SV bypasses fan trap guard
-- Scenario 6: Forgotten semi-additive metric   → checklist only, no SV DDL

USE DATABASE SEMANTIC_SKILLS;
USE SCHEMA SNIPPETS;

-- ══════════════════════════════════════════════════════════════════════════════
-- SCENARIO 1: AMBIGUOUS PATH RELATIONSHIP
-- ══════════════════════════════════════════════════════════════════════════════
--
-- PROBLEM: DEALS has two date FKs (CREATED_DATE, CLOSE_DATE) both referencing
-- DIM_DATE. Two relationships exist with no disambiguation. The SV deploys
-- without error, but any query that uses a date dimension fails at runtime:
--
--   "Multi-path relationship between the dimension entity 'DATE_DIM' and the
--    base metric or dimension entity 'DEALS' is not supported."
--
-- KEY INSIGHT: Queries that don't touch date dimensions work fine — the bug
-- hides until an analyst tries to do time-series analysis.
--
-- FIX: Add USING (relationship) to every metric to declare which date path
-- that metric should use. Each metric independently picks its own date path.

-- ── BROKEN ────────────────────────────────────────────────────────────────────
CREATE OR REPLACE SEMANTIC VIEW DEALS_AMBIGUOUS_PATH_SV
  TABLES (
    deals    AS SEMANTIC_SKILLS.SNIPPETS.DEALS    PRIMARY KEY (DEAL_ID)
    , date_dim AS SEMANTIC_SKILLS.SNIPPETS.DIM_DATE  PRIMARY KEY (DATE_KEY)
    , rep_dim  AS SEMANTIC_SKILLS.SNIPPETS.DIM_REP   PRIMARY KEY (REP_ID)
  )
  RELATIONSHIPS (
    deals_to_created_date AS deals(CREATED_DATE) REFERENCES date_dim(DATE_KEY)
    , deals_to_close_date AS deals(CLOSE_DATE)   REFERENCES date_dim(DATE_KEY)
    , deals_to_rep        AS deals(REP_ID)        REFERENCES rep_dim(REP_ID)
  )
  FACTS   ( deals.amount AS AMOUNT )
  DIMENSIONS (
    deals.product     AS PRODUCT
    , deals.stage     AS STAGE
    , rep_dim.rep_name  AS REP_NAME
    , rep_dim.region    AS REGION
    , date_dim.year       AS YEAR
    , date_dim.month_num  AS MONTH_NUM
    , date_dim.month_name AS MONTH_NAME
  )
  METRICS (
    -- No USING → ambiguous which date path to use at query time
    deals.total_amount AS SUM(AMOUNT)
    , deals.deal_count AS COUNT(DEAL_ID)
  );

-- ── FIXED ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE SEMANTIC VIEW DEALS_FIXED_SV
  TABLES (
    deals    AS SEMANTIC_SKILLS.SNIPPETS.DEALS    PRIMARY KEY (DEAL_ID)
    , date_dim AS SEMANTIC_SKILLS.SNIPPETS.DIM_DATE  PRIMARY KEY (DATE_KEY)
    , rep_dim  AS SEMANTIC_SKILLS.SNIPPETS.DIM_REP   PRIMARY KEY (REP_ID)
  )
  RELATIONSHIPS (
    deals_to_created_date AS deals(CREATED_DATE) REFERENCES date_dim(DATE_KEY)
    , deals_to_close_date AS deals(CLOSE_DATE)   REFERENCES date_dim(DATE_KEY)
    , deals_to_rep        AS deals(REP_ID)        REFERENCES rep_dim(REP_ID)
  )
  FACTS   ( deals.amount AS AMOUNT )
  DIMENSIONS (
    deals.product     AS PRODUCT
    , deals.stage     AS STAGE
    , rep_dim.rep_name  AS REP_NAME
    , rep_dim.region    AS REGION
    , date_dim.year       AS YEAR
    , date_dim.month_num  AS MONTH_NUM
    , date_dim.month_name AS MONTH_NAME
  )
  METRICS (
    -- USING declares which date relationship each metric uses
    -- Syntax: entity.logical_name USING (relationship) AS physical_expression
    deals.total_amount_created USING (deals_to_created_date) AS SUM(AMOUNT)
      COMMENT = 'Total deal value, dated by when the deal was created'
    , deals.deal_count_created USING (deals_to_created_date) AS COUNT(DEAL_ID)
      COMMENT = 'Count of deals, dated by creation date'
    , deals.total_amount_closed USING (deals_to_close_date)  AS SUM(AMOUNT)
      COMMENT = 'Total deal value, dated by close date (excludes open deals)'
    , deals.deal_count_closed  USING (deals_to_close_date)   AS COUNT(DEAL_ID)
      COMMENT = 'Count of closed deals, dated by close date'
  );


-- ══════════════════════════════════════════════════════════════════════════════
-- SCENARIO 2: FAN TRAP
-- ══════════════════════════════════════════════════════════════════════════════
--
-- PROBLEM: Revenue (AMOUNT) lives at the DEALS header level — one row per deal.
-- DEAL_ITEMS is a bridge table: each deal maps to one or more products.
-- Defining a metric on DEALS and grouping by a dimension from DIM_PRODUCT
-- requires routing through DEAL_ITEMS, which fans out DEALS rows.
-- The SV engine catches this and errors at query time:
--
--   "The dimension entity 'PRODUCTS' must be related to and have an equal or
--    lower level of granularity compared to the base metric or dimension
--    entity 'DEALS'."
--
-- NOTE: This same error appears for Scenario 3 (no relationship). The fix
-- is different — here the relationship exists but at the wrong grain; in
-- Scenario 3 the relationship is simply missing entirely.
--
-- FIX: Move the metric to DEAL_ITEMS.LINE_AMOUNT (line-item grain). A metric
-- defined on DEAL_ITEMS can be grouped by DIM_PRODUCT because DEAL_ITEMS
-- directly references DIM_PRODUCT — same or lower granularity. ✓

-- ── BROKEN ────────────────────────────────────────────────────────────────────
CREATE OR REPLACE SEMANTIC VIEW DEALS_FAN_TRAP_SV
  TABLES (
    deals        AS SEMANTIC_SKILLS.SNIPPETS.DEALS       PRIMARY KEY (DEAL_ID)
    , deal_items AS SEMANTIC_SKILLS.SNIPPETS.DEAL_ITEMS  PRIMARY KEY (ITEM_ID)
    , products   AS SEMANTIC_SKILLS.SNIPPETS.DIM_PRODUCT PRIMARY KEY (PRODUCT_ID)
    , rep_dim    AS SEMANTIC_SKILLS.SNIPPETS.DIM_REP     PRIMARY KEY (REP_ID)
  )
  RELATIONSHIPS (
    items_to_deals      AS deal_items(DEAL_ID)    REFERENCES deals(DEAL_ID)
    , items_to_products AS deal_items(PRODUCT_ID) REFERENCES products(PRODUCT_ID)
    , deals_to_rep      AS deals(REP_ID)           REFERENCES rep_dim(REP_ID)
  )
  FACTS   ( deals.amount AS AMOUNT )  -- metric at DEALS grain
  DIMENSIONS (
    deals.stage               AS STAGE
    , rep_dim.rep_name        AS REP_NAME
    , products.product_name   AS PRODUCT_NAME  -- dimension at PRODUCTS grain
    , products.category       AS CATEGORY
  )
  METRICS (
    -- AMOUNT is at DEALS grain. PRODUCTS is reachable only via DEAL_ITEMS.
    -- Grouping by PRODUCTS fanout multiplies DEALS rows → fan trap.
    deals.total_amount AS SUM(AMOUNT)
    , deals.deal_count AS COUNT(DEAL_ID)
  );

-- ── FIXED ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE SEMANTIC VIEW DEALS_FAN_TRAP_FIXED_SV
  TABLES (
    deals        AS SEMANTIC_SKILLS.SNIPPETS.DEALS       PRIMARY KEY (DEAL_ID)
    , deal_items AS SEMANTIC_SKILLS.SNIPPETS.DEAL_ITEMS  PRIMARY KEY (ITEM_ID)
    , products   AS SEMANTIC_SKILLS.SNIPPETS.DIM_PRODUCT PRIMARY KEY (PRODUCT_ID)
    , rep_dim    AS SEMANTIC_SKILLS.SNIPPETS.DIM_REP     PRIMARY KEY (REP_ID)
  )
  RELATIONSHIPS (
    items_to_deals      AS deal_items(DEAL_ID)    REFERENCES deals(DEAL_ID)
    , items_to_products AS deal_items(PRODUCT_ID) REFERENCES products(PRODUCT_ID)
    , deals_to_rep      AS deals(REP_ID)           REFERENCES rep_dim(REP_ID)
  )
  FACTS (
    deal_items.line_amount AS LINE_AMOUNT  -- metric moved to DEAL_ITEMS grain
  )
  DIMENSIONS (
    deals.stage               AS STAGE
    , rep_dim.rep_name        AS REP_NAME
    , products.product_name   AS PRODUCT_NAME
    , products.category       AS CATEGORY
  )
  METRICS (
    -- LINE_AMOUNT is at DEAL_ITEMS grain — same level as DIM_PRODUCT.
    -- Grouping by product category is now safe. ✓
    deal_items.total_revenue AS SUM(LINE_AMOUNT)
      COMMENT = 'Revenue at line-item grain — safe to group by product or category'
    , deal_items.item_count  AS COUNT(ITEM_ID)
  );


-- ══════════════════════════════════════════════════════════════════════════════
-- SCENARIO 3: TABLE WITH NO RELATIONSHIP
-- ══════════════════════════════════════════════════════════════════════════════
--
-- PROBLEM: DIM_REGION is listed in the TABLES clause but has no RELATIONSHIP
-- connecting it to any fact. The SV deploys without error. At query time,
-- using any dimension from DIM_REGION triggers the same error as a fan trap:
--
--   "The dimension entity 'DIM_REGION' must be related to and have an equal or
--    lower level of granularity compared to the base metric or dimension
--    entity 'DEALS'."
--
-- The error message is identical to the fan trap — the diagnostic difference
-- is that here there is NO relationship at all, whereas in a fan trap there IS
-- a relationship but at the wrong grain.
--
-- FIX: Either add the missing relationship, or remove the orphaned table from
-- the TABLES clause if it was included by mistake.

-- ── BROKEN ────────────────────────────────────────────────────────────────────
CREATE OR REPLACE SEMANTIC VIEW DEALS_NO_REL_SV
  TABLES (
    deals      AS SEMANTIC_SKILLS.SNIPPETS.DEALS      PRIMARY KEY (DEAL_ID)
    , rep_dim  AS SEMANTIC_SKILLS.SNIPPETS.DIM_REP    PRIMARY KEY (REP_ID)
    , dim_region AS SEMANTIC_SKILLS.SNIPPETS.DIM_REGION PRIMARY KEY (REGION_CODE)
  )
  RELATIONSHIPS (
    deals_to_rep AS deals(REP_ID) REFERENCES rep_dim(REP_ID)
    -- dim_region has no relationship — orphaned table
  )
  FACTS   ( deals.amount AS AMOUNT )
  DIMENSIONS (
    deals.stage           AS STAGE
    , rep_dim.rep_name    AS REP_NAME
    , rep_dim.region      AS REGION
    , dim_region.region_name AS REGION_NAME  -- will error at query time
  )
  METRICS ( deals.total_amount AS SUM(AMOUNT) );

-- ── FIXED ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE SEMANTIC VIEW DEALS_NO_REL_FIXED_SV
  TABLES (
    deals        AS SEMANTIC_SKILLS.SNIPPETS.DEALS      PRIMARY KEY (DEAL_ID)
    , rep_dim    AS SEMANTIC_SKILLS.SNIPPETS.DIM_REP    PRIMARY KEY (REP_ID)
    , dim_region AS SEMANTIC_SKILLS.SNIPPETS.DIM_REGION PRIMARY KEY (REGION_CODE)
  )
  RELATIONSHIPS (
    deals_to_rep    AS deals(REP_ID)    REFERENCES rep_dim(REP_ID)
    , rep_to_region AS rep_dim(REGION)  REFERENCES dim_region(REGION_CODE)
    -- dim_region is now reachable: deals → rep_dim → dim_region ✓
  )
  FACTS   ( deals.amount AS AMOUNT )
  DIMENSIONS (
    deals.stage              AS STAGE
    , rep_dim.rep_name       AS REP_NAME
    , rep_dim.region         AS REGION
    , dim_region.region_name AS REGION_NAME
  )
  METRICS ( deals.total_amount AS SUM(AMOUNT) );


-- ══════════════════════════════════════════════════════════════════════════════
-- SCENARIO 4A: DUPLICATE LOGICAL NAME — DEPLOY-TIME ERROR
-- ══════════════════════════════════════════════════════════════════════════════
--
-- PROBLEM: Two dimensions in the same SV share the same logical name, even
-- across different entities. The SV engine enforces globally unique logical
-- names within a SV — this fails immediately at CREATE time:
--
--   "SQL compilation error: invalid identifier '<name>'"
--
-- Attempting to run this will fail. It is included here to show the error.
--
-- FIX: Give each dimension a unique logical name that reflects its entity context.
-- If two dimensions represent the same concept from different join paths,
-- consider whether they should be in separate SVs or use distinct names like
-- rep_segment vs product_segment.

-- Uncomment to reproduce the deploy-time error:
-- CREATE OR REPLACE SEMANTIC VIEW DEALS_DUPE_NAME_SV
--   TABLES (
--     deals    AS SEMANTIC_SKILLS.SNIPPETS.DEALS   PRIMARY KEY (DEAL_ID)
--     , rep_dim  AS SEMANTIC_SKILLS.SNIPPETS.DIM_REP PRIMARY KEY (REP_ID)
--     , products AS SEMANTIC_SKILLS.SNIPPETS.DIM_PRODUCT PRIMARY KEY (PRODUCT_ID)
--     , deal_items AS SEMANTIC_SKILLS.SNIPPETS.DEAL_ITEMS PRIMARY KEY (ITEM_ID)
--   )
--   RELATIONSHIPS (
--     deals_to_rep      AS deals(REP_ID)       REFERENCES rep_dim(REP_ID)
--     , items_to_deals  AS deal_items(DEAL_ID)    REFERENCES deals(DEAL_ID)
--     , items_to_products AS deal_items(PRODUCT_ID) REFERENCES products(PRODUCT_ID)
--   )
--   FACTS ( deals.amount AS AMOUNT )
--   DIMENSIONS (
--     deals.stage           AS STAGE
--     , rep_dim.rep_name    AS REP_NAME
--     , rep_dim.segment     AS REGION    -- logical name: "segment"
--     , products.segment    AS CATEGORY  -- logical name: "segment" ← DUPLICATE → error
--   )
--   METRICS ( deals.total_amount AS SUM(AMOUNT) );


-- ══════════════════════════════════════════════════════════════════════════════
-- SCENARIO 4B: OVERLAPPING SYNONYMS — CORTEX ANALYST AMBIGUITY
-- ══════════════════════════════════════════════════════════════════════════════
--
-- PROBLEM: Distinct logical names but overlapping synonyms. The SV deploys
-- and all SQL queries work correctly. However Cortex Analyst cannot disambiguate:
-- when the user asks "what is total revenue by segment?" CA refuses to answer
-- because "revenue" matches two metrics and "segment" matches two dimensions.
--
-- FIX: Give each dimension and metric a synonym set that is unique and scoped
-- to its entity context. Avoid sharing high-value terms like "revenue", "count",
-- "total", "segment" across multiple definitions.

-- ── BROKEN (deploys, but CA refuses ambiguous queries) ───────────────────────
CREATE OR REPLACE SEMANTIC VIEW DEALS_AMBIGUOUS_NAMES_SV
  TABLES (
    deals        AS SEMANTIC_SKILLS.SNIPPETS.DEALS       PRIMARY KEY (DEAL_ID)
    , deal_items AS SEMANTIC_SKILLS.SNIPPETS.DEAL_ITEMS  PRIMARY KEY (ITEM_ID)
    , products   AS SEMANTIC_SKILLS.SNIPPETS.DIM_PRODUCT PRIMARY KEY (PRODUCT_ID)
    , rep_dim    AS SEMANTIC_SKILLS.SNIPPETS.DIM_REP     PRIMARY KEY (REP_ID)
  )
  RELATIONSHIPS (
    items_to_deals      AS deal_items(DEAL_ID)    REFERENCES deals(DEAL_ID)
    , items_to_products AS deal_items(PRODUCT_ID) REFERENCES products(PRODUCT_ID)
    , deals_to_rep      AS deals(REP_ID)          REFERENCES rep_dim(REP_ID)
  )
  FACTS (
    deals.amount             AS AMOUNT
    , deal_items.line_amount AS LINE_AMOUNT
  )
  DIMENSIONS (
    deals.stage        AS STAGE
    , rep_dim.rep_name AS REP_NAME
    -- "segment", "area" claimed by both → CA can't resolve either
    , rep_dim.rep_segment      AS REGION   WITH SYNONYMS ('segment', 'region', 'territory', 'area')
    , products.product_segment AS CATEGORY WITH SYNONYMS ('segment', 'category', 'product type', 'area')
  )
  METRICS (
    -- "revenue", "total revenue" claimed by both → CA can't resolve either
    deals.total_amount AS SUM(AMOUNT)
      WITH SYNONYMS ('revenue', 'total revenue', 'sales')
      COMMENT = 'Total deal value at header level — not suitable for product breakdowns'
    , deal_items.total_revenue AS SUM(LINE_AMOUNT)
      WITH SYNONYMS ('revenue', 'total revenue', 'product revenue')
      COMMENT = 'Revenue at line-item level — use this when grouping by product'
  );

-- ── FIXED ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE SEMANTIC VIEW DEALS_CLEAR_NAMES_SV
  TABLES (
    deals        AS SEMANTIC_SKILLS.SNIPPETS.DEALS       PRIMARY KEY (DEAL_ID)
    , deal_items AS SEMANTIC_SKILLS.SNIPPETS.DEAL_ITEMS  PRIMARY KEY (ITEM_ID)
    , products   AS SEMANTIC_SKILLS.SNIPPETS.DIM_PRODUCT PRIMARY KEY (PRODUCT_ID)
    , rep_dim    AS SEMANTIC_SKILLS.SNIPPETS.DIM_REP     PRIMARY KEY (REP_ID)
  )
  RELATIONSHIPS (
    items_to_deals      AS deal_items(DEAL_ID)    REFERENCES deals(DEAL_ID)
    , items_to_products AS deal_items(PRODUCT_ID) REFERENCES products(PRODUCT_ID)
    , deals_to_rep      AS deals(REP_ID)          REFERENCES rep_dim(REP_ID)
  )
  FACTS (
    deals.amount             AS AMOUNT
    , deal_items.line_amount AS LINE_AMOUNT
  )
  DIMENSIONS (
    deals.stage          AS STAGE
    , rep_dim.rep_name   AS REP_NAME
    -- Each dimension owns a non-overlapping synonym set
    , rep_dim.rep_territory    AS REGION   WITH SYNONYMS ('rep territory', 'sales territory', 'rep region')
    , products.product_category AS CATEGORY WITH SYNONYMS ('product category', 'product type', 'product line')
  )
  METRICS (
    -- Each metric owns a non-overlapping synonym set
    deals.deal_value AS SUM(AMOUNT)
      WITH SYNONYMS ('deal value', 'total deal value', 'closed deal value', 'pipeline value')
      COMMENT = 'Total deal value at header level — use for deal-level analysis by rep, stage, or time'
    , deal_items.product_revenue AS SUM(LINE_AMOUNT)
      WITH SYNONYMS ('product revenue', 'revenue by product', 'line item revenue')
      COMMENT = 'Revenue at line-item level — use when grouping by product or product category'
  );


-- ══════════════════════════════════════════════════════════════════════════════
-- SCENARIO 5A: REVERSED RELATIONSHIP DIRECTION — DEPLOY-TIME ERROR
-- ══════════════════════════════════════════════════════════════════════════════
--
-- PROBLEM: The REFERENCES direction is flipped — the dimension (one side) is
-- placed on the left of REFERENCES, pointing to the fact (many side) on the right.
-- The SV engine enforces that the right-hand side of REFERENCES must be a
-- declared primary or unique key. Since DEALS.REP_ID is not a PK, this fails
-- immediately at CREATE time:
--
--   "The referenced key in the relationship 'REP_DIM REFERENCES DEALS' must be
--    the primary or unique key of the referenced entity."
--
-- Attempting to run this will fail. It is included here to show the error.
--
-- FIX: Always write relationships as many_side(FK) REFERENCES one_side(PK).
--      The right-hand side must be the primary key of the dimension/parent table.

-- Uncomment to reproduce the deploy-time error:
-- CREATE OR REPLACE SEMANTIC VIEW DEALS_REVERSED_REL_SV
--   TABLES (
--     deals    AS SEMANTIC_SKILLS.SNIPPETS.DEALS   PRIMARY KEY (DEAL_ID)
--     , rep_dim  AS SEMANTIC_SKILLS.SNIPPETS.DIM_REP PRIMARY KEY (REP_ID)
--   )
--   RELATIONSHIPS (
--     rep_to_deals AS rep_dim(REP_ID) REFERENCES deals(REP_ID)  -- ← reversed!
--     --                                                 ^^^^^^ not a PK of DEALS
--   )
--   FACTS   ( deals.amount AS AMOUNT )
--   DIMENSIONS ( rep_dim.rep_name AS REP_NAME )
--   METRICS   ( deals.total_amount AS SUM(AMOUNT) );


-- ══════════════════════════════════════════════════════════════════════════════
-- SCENARIO 5B: WRONG CARDINALITY (LYING ABOUT THE PRIMARY KEY)
-- ══════════════════════════════════════════════════════════════════════════════
--
-- PROBLEM: DEAL_ITEMS has ITEM_ID as its real primary key (many items per deal).
-- The modeler accidentally declares PRIMARY KEY (DEAL_ID) instead — asserting
-- that DEAL_ITEMS is at DEAL grain (one item per deal). Snowflake does NOT
-- enforce PK uniqueness, so the SV deploys without error.
--
-- The consequence is subtle and dangerous: the SV engine's fan trap detector
-- uses the declared PK to assess cardinality. By lying that DEAL_ITEMS is
-- at DEAL grain, the engine believes the DEAL_ITEMS → DEALS relationship is
-- 1:1. It therefore allows querying DEALS.AMOUNT (header-level) grouped by
-- DIM_PRODUCT dimensions — the exact query that correctly errors on a properly
-- declared model. The fan trap runs silently and inflates every number.
--
-- COMPARISON:
--   Correct SV (DEALS_FAN_TRAP_SV, PK=ITEM_ID):
--     deals.total_amount by products.category → ERROR (fan trap caught ✓)
--   Wrong cardinality SV (below, PK=DEAL_ID):
--     deals.total_amount by products.category → runs, returns inflated numbers ✗
--
--   Analytics: correct ≈ $240k → wrong = $430k  (multi-item deals counted 2-3×)
--   Data Pipelines: correct ≈ $73.5k → wrong = $146.5k
--
-- DETECTION: Run the same query on both a correctly-declared and wrong-cardinality
-- SV and compare totals. Wrong-cardinality results will be inflated by a factor
-- roughly equal to the average number of items per deal.
--
-- FIX: Declare PRIMARY KEY on the column that is actually unique in that table.
--      For bridge/line-item tables: PRIMARY KEY (ITEM_ID), not the FK column.

CREATE OR REPLACE SEMANTIC VIEW SEMANTIC_SKILLS.SNIPPETS.DEALS_BOTH_UNIQUE_SV
  TABLES (
    deals      AS SEMANTIC_SKILLS.SNIPPETS.DEALS      PRIMARY KEY (DEAL_ID)
    , deal_items AS SEMANTIC_SKILLS.SNIPPETS.DEAL_ITEMS PRIMARY KEY (DEAL_ID)
    --                                                              ^^^^^^^^
    --  Wrong: DEAL_ID is a FK in DEAL_ITEMS, not unique. Correct: PRIMARY KEY (ITEM_ID)
    , products   AS SEMANTIC_SKILLS.SNIPPETS.DIM_PRODUCT PRIMARY KEY (PRODUCT_ID)
    , rep_dim    AS SEMANTIC_SKILLS.SNIPPETS.DIM_REP     PRIMARY KEY (REP_ID)
  )
  RELATIONSHIPS (
    items_to_deals      AS deal_items(DEAL_ID)    REFERENCES deals(DEAL_ID)
    , items_to_products AS deal_items(PRODUCT_ID) REFERENCES products(PRODUCT_ID)
    , deals_to_rep      AS deals(REP_ID)          REFERENCES rep_dim(REP_ID)
  )
  FACTS (
    deals.amount             AS AMOUNT
    , deal_items.line_amount AS LINE_AMOUNT
  )
  DIMENSIONS (
    deals.stage               AS STAGE
    , rep_dim.rep_name        AS REP_NAME
    , products.product_name   AS PRODUCT_NAME
    , products.category       AS CATEGORY
  )
  METRICS (
    deals.total_amount       AS SUM(AMOUNT)
    , deal_items.total_revenue AS SUM(LINE_AMOUNT)
    , deal_items.item_count    AS COUNT(ITEM_ID)
  );


-- ══════════════════════════════════════════════════════════════════════════════
-- SCENARIO 6: FORGOTTEN SEMI-ADDITIVE BEHAVIOR
-- ══════════════════════════════════════════════════════════════════════════════
--
-- This scenario has no broken SV — the query always runs without error.
-- A SUM() on a balance, headcount, or inventory snapshot is syntactically valid
-- but semantically wrong: summing a point-in-time snapshot across time produces
-- a number that has no business meaning.
--
-- Example: a daily account balance table. Each row is the end-of-day balance
-- for one account. SUM(balance) across all days = nonsense. The correct
-- aggregation is LAST_VALUE(balance) per account, or AVG if smoothing is needed.
--
-- See the `semi_additive_metric` snippet for the full NON ADDITIVE BY pattern.
-- The checklist question here is: "Does this column represent a snapshot
-- (balance, headcount, inventory) rather than a flow (revenue, quantity sold)?
-- If yes, SUM across time is almost certainly wrong."
--
-- No DDL for this scenario — it is a model design heuristic, not a detectable error.
