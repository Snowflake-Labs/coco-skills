-- Accumulating Snapshot: Verification Queries
--
-- All queries use SEMANTIC_VIEW() against LOAN_PIPELINE_SV.
-- Expected outputs are from live runs against the seed data.

-- ── Q1: Applications by application month ─────────────────────────────────
-- Baseline: all 12 applications, dated by when they were submitted.
-- Expected: 4 rows (Jan=4, Feb=4, Mar=4) + NULL row for metrics with no date match

SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.LOAN_PIPELINE_SV
    DIMENSIONS date_dim.year, date_dim.month_num, date_dim.month_name
    METRICS    applications.application_count
)
ORDER BY year, month_num;

-- | YEAR | MONTH_NUM | MONTH_NAME | APPLICATION_COUNT |
-- |------|-----------|------------|-------------------|
-- | 2025 |         1 | January    |                 4 |
-- | 2025 |         2 | February   |                 4 |
-- | 2025 |         3 | March      |                 4 |
-- | NULL |      NULL |            |              NULL |

-- ── Q2: Fundings by FUNDING month ─────────────────────────────────────────
-- The USING clause switches the date path to FUNDING_DATE.
-- Fundings are distributed differently than applications — some lag 1-2 months.
-- Expected: Jan=2, Feb=2, Mar=1 (5 total funded; NULL row for unfunded NULLs → 0)

SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.LOAN_PIPELINE_SV
    DIMENSIONS date_dim.year, date_dim.month_num, date_dim.month_name
    METRICS    applications.funding_count
)
ORDER BY year, month_num;

-- | YEAR | MONTH_NUM | MONTH_NAME | FUNDING_COUNT |
-- |------|-----------|------------|---------------|
-- | 2025 |         1 | January    |             2 |
-- | 2025 |         2 | February   |             2 |
-- | 2025 |         3 | March      |             1 |
-- | NULL |      NULL |            |             0 |

-- ── Q3: Full funnel — all 4 stage counts in one query ─────────────────────
-- The critical test: can multiple USING-scoped metrics share one date dimension?
-- Each metric independently resolves its date path via USING.
-- When grouped by date_dim.month, each count is bucketed by ITS OWN milestone date.
-- (application_count by APPLICATION_DATE, review_count by REVIEW_DATE, etc.)
-- Expected funnel by application month: 12→10→7→5 spread across Jan/Feb/Mar

SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.LOAN_PIPELINE_SV
    DIMENSIONS date_dim.year, date_dim.month_num, date_dim.month_name
    METRICS    applications.application_count, applications.review_count,
               applications.decision_count, applications.funding_count
)
ORDER BY year, month_num;

-- | YEAR | MONTH_NUM | MONTH_NAME | APPLICATION_COUNT | REVIEW_COUNT | DECISION_COUNT | FUNDING_COUNT |
-- |------|-----------|------------|-------------------|--------------|----------------|---------------|
-- | 2025 |         1 | January    |                 4 |            4 |              3 |             2 |
-- | 2025 |         2 | February   |                 4 |            3 |              2 |             2 |
-- | 2025 |         3 | March      |                 4 |            3 |              2 |             1 |
-- | NULL |      NULL |            |              NULL |            0 |              0 |             0 |

-- ── Q4: Conversion rates by loan product ──────────────────────────────────
-- Derived metrics (funding_rate) reference USING-scoped constituent metrics.
-- No date dimension needed — product is a non-date attribute.
-- Note: Student Refi has 0% funding rate (no funded loans in seed data).

SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.LOAN_PIPELINE_SV
    DIMENSIONS applications.loan_product
    METRICS    applications.application_count, applications.funding_count,
               funding_rate
)
ORDER BY loan_product;

-- | LOAN_PRODUCT  | APPLICATION_COUNT | FUNDING_COUNT | FUNDING_RATE |
-- |---------------|-------------------|---------------|--------------|
-- | Home Equity   |                 2 |             1 |     0.500000 |
-- | Personal Loan |                 7 |             4 |     0.571429 |
-- | Student Refi  |                 3 |             0 |     0.000000 |

-- ── Q5: Full funnel rates by channel ──────────────────────────────────────
-- Referral channel: 2 applications, 1 review, 0 decisions → 0% decision and funding rate.
-- Paid Search: perfect 100% review and decision rate, 67% funding rate.

SELECT * FROM SEMANTIC_VIEW(
    SEMANTIC_SKILLS.SNIPPETS.LOAN_PIPELINE_SV
    DIMENSIONS applications.channel
    METRICS    applications.application_count, applications.review_count,
               applications.decision_count, applications.funding_count,
               review_rate, decision_rate, funding_rate
)
ORDER BY channel;

-- | CHANNEL     | APPLICATION_COUNT | REVIEW_COUNT | DECISION_COUNT | FUNDING_COUNT | REVIEW_RATE | DECISION_RATE | FUNDING_RATE |
-- |-------------|-------------------|--------------|----------------|---------------|-------------|---------------|--------------|
-- | Direct Mail |                 2 |            2 |              1 |             1 |    1.000000 |      0.500000 |     0.500000 |
-- | Organic     |                 5 |            4 |              3 |             2 |    0.800000 |      0.600000 |     0.400000 |
-- | Paid Search |                 3 |            3 |              3 |             2 |    1.000000 |      1.000000 |     0.666667 |
-- | Referral    |                 2 |            1 |              0 |             0 |    0.500000 |      0.000000 |     0.000000 |
