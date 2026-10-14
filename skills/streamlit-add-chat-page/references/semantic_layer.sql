-- ============================================================================
-- Semantic layer for a Cortex Analyst "chat with your data" page.
-- Two objects: (1) a flattened base VIEW, (2) a single-table SEMANTIC VIEW,
-- plus Cortex Search services for fuzzy literal resolution on string dimensions.
-- Replace <DB>.<SCHEMA>, table/column names, dims and metrics for your data.
-- ============================================================================

USE ROLE <ADMIN_ROLE>;          -- separate admin role that OWNS these objects (NOT the SiS app owner;
                                -- grant the app's least-privilege role only what SKILL.md lists)
USE WAREHOUSE <WAREHOUSE>;

-- 1) FLATTEN TO ONE VIEW ------------------------------------------------------
-- Do all joins here, and pre-derive any parsed/computed columns (e.g. regex
-- splits, flags, per-row composite scores). This keeps the semantic view
-- single-table: no relationships to model, simpler + reliable — but you own the
-- grain, so mind fan-out (below).
-- ⚠️ GRAIN & FAN-OUT: decide the ONE row-per-<grain> the view produces and keep
--    the join to it. If a join is one-to-many, rows DUPLICATE and SUM/AVG/COUNT
--    silently double-count. Guards: keep the join at the intended grain (or
--    pre-aggregate the many side first), and count entities with COUNT(DISTINCT).
-- ⚠️ NULLs: NULL dimensions aren't groupable as a visible bucket, and AVG ignores
--    NULL rows. COALESCE dimension values you want to filter/group on.
CREATE OR REPLACE VIEW <DB>.<SCHEMA>.<BASE_VIEW>
  COMMENT = 'Flattened base for Cortex Analyst. One row per <grain>.'
AS
SELECT
    f.<key_col>,
    COALESCE(d.<dim_col_a>, 'Unknown') AS <DIM_A>,      -- COALESCE so NULLs are groupable
    d.<dim_col_b>              AS <DIM_B>,
    -- Example derived dimension (parse instead of doing regex per query):
    REGEXP_REPLACE(f.<raw_key>, '<pattern>', '')    AS <DERIVED_DIM>,
    f.<measure_1>,
    f.<measure_2>,
    -- Example per-row composite (also expose the canonical metric below):
    (f.<measure_1> + f.<measure_2> * 10) / 60.0 * 100 AS <COMPOSITE_PER_ROW>
FROM <DB>.<SCHEMA>.<FACT_TABLE> f
JOIN <DB>.<SCHEMA>.<DIM_TABLE>  d ON f.<key_col> = d.<key_col>;

-- 2) CORTEX SEARCH SERVICES (literal resolution) ------------------------------
-- Attach to HIGH-cardinality string dimensions (>10 distinct values) so Analyst
-- maps user phrasing ('cortex search') to the stored value ('Cortex Search').
-- (Snowflake string `=` is CASE-SENSITIVE; without this, mis-cased/worded
-- filters return zero rows.) For LOW cardinality (1-10) use sample values instead.
CREATE OR REPLACE CORTEX SEARCH SERVICE <DB>.<SCHEMA>.CSS_<DIM_A>
  ON <DIM_A> WAREHOUSE = <WAREHOUSE> TARGET_LAG = '24 hour'
  AS (SELECT DISTINCT <DIM_A> FROM <DB>.<SCHEMA>.<BASE_VIEW> WHERE <DIM_A> IS NOT NULL);

-- 3) SEMANTIC VIEW ------------------------------------------------------------
-- Dimensions = categorical/filter columns. Metrics = AGGREGATES.
-- ENCODE derived/composite metrics as NAMED metrics so the LLM cannot mis-derive
-- them. Add WITH SYNONYMS + COMMENT to everything to improve NL mapping.
--
-- GOTCHAS:
--   * Per-item COMMENT REQUIRES the `=` sign:  COMMENT = '...'   (COMMENT '...' fails)
--   * Dimension clause ORDER is fixed:
--       <alias>.<name> AS <expr>
--         [ WITH SYNONYMS = (...) ]
--         [ COMMENT = '...' ]
--         [ WITH CORTEX SEARCH SERVICE <fqn> [ USING <col> ] ]   <-- MUST be LAST
--   * Fully-qualify the search-service name (DB.SCHEMA.NAME).
CREATE OR REPLACE SEMANTIC VIEW <DB>.<SCHEMA>.<SEMANTIC_VIEW>
  TABLES (
    base AS <DB>.<SCHEMA>.<BASE_VIEW>
      PRIMARY KEY (<key_col>)
      WITH SYNONYMS = ('<synonym>', '...')
      COMMENT = 'One row per <grain>. <describe the grain and any config encoding>.'
  )
  DIMENSIONS (
    base.<DIM_A> AS <DIM_A>
      WITH SYNONYMS = ('<syn1>', '<syn2>')
      COMMENT = '<what this dimension is>'
      WITH CORTEX SEARCH SERVICE <DB>.<SCHEMA>.CSS_<DIM_A> USING <DIM_A>,
    base.<DERIVED_DIM> AS <DERIVED_DIM> COMMENT = '<...>',
    base.<DIM_B> AS <DIM_B> WITH SYNONYMS = ('<syn>') COMMENT = '<...>'
  )
  METRICS (
    -- Canonical composite as a NAMED metric (ratio of aggregates is fine).
    -- Use NULLIF on any denominator that can be zero to avoid div-by-zero errors:
    base.composite AS (AVG(<measure_1>) + AVG(<measure_2>) * 10) / NULLIF(60.0, 0) * 100
      WITH SYNONYMS = ('overall score','composite','score')
      COMMENT = '<exact formula, so it is never re-derived incorrectly>',
    base.pass_rate AS SUM(<passes>) / NULLIF(COUNT(*), 0) * 100
      COMMENT = 'Example ratio metric — NULLIF guards divide-by-zero',
    base.avg_measure_1 AS AVG(<measure_1>) COMMENT = '<...>',
    -- Count ENTITIES with COUNT(DISTINCT) so fan-out/duplication cannot inflate it:
    base.entity_count AS COUNT(DISTINCT <key_col>) WITH SYNONYMS = ('number of <entities>'),
    base.row_count AS COUNT(*) WITH SYNONYMS = ('number of rows')
  )
  COMMENT = '<one-line description of what this model answers>'
  -- Domain rules the model must always follow (biggest cheap accuracy lever after synonyms):
  AI_SQL_GENERATION 'Always use the named metrics for scores; never re-derive formulas.
    Filter string dimensions case-insensitively. Default to <sensible default> unless the user specifies otherwise.'
  -- Verified queries: anchor generation on known-good question->SQL pairs.
  -- Reuse your existing dashboard queries here — this is the #1 accuracy lever.
  AI_VERIFIED_QUERIES (
    vq_top_by_score AS (
      QUESTION 'top 5 <entities> by <metric>'
      ONBOARDING_QUESTION TRUE
      SQL 'SELECT * FROM SEMANTIC_VIEW(<DB>.<SCHEMA>.<SEMANTIC_VIEW> DIMENSIONS <DIM_A> METRICS composite) ORDER BY composite DESC LIMIT 5'
    )
  );

-- 3b) FEEDBACK LOG (continuous improvement) -----------------------------------
-- The chat page writes 👍/👎 + the question/SQL here. Mine it later for new
-- verified queries and to monitor accuracy over time.
CREATE TABLE IF NOT EXISTS <DB>.<SCHEMA>.CHAT_FEEDBACK (
  TS TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
  USERNAME     STRING,
  QUESTION     STRING,
  GENERATED_SQL STRING,
  REQUEST_ID   STRING,
  STATUS       STRING,   -- answered | empty | error
  RATING       STRING    -- up | down | null
);

-- 4) VERIFY (do this BEFORE wiring the app) -----------------------------------
--   Direct query:   SELECT * FROM SEMANTIC_VIEW(<DB>.<SCHEMA>.<SEMANTIC_VIEW>
--                        DIMENSIONS <DIM_A> METRICS composite) LIMIT 5;
--   NL round-trip:  cortex analyst query "<a real question>" --view <DB>.<SCHEMA>.<SEMANTIC_VIEW>
--   Confirm the generated SQL uses the canonical literal (e.g. 'Cortex Search', not 'cortex search').
