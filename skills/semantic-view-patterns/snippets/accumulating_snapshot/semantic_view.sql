-- Accumulating Snapshot: Semantic View DDL
--
-- Pattern: one DIM_DATE alias, four relationships (one per milestone).
-- Each stage metric uses USING to declare which date relationship it counts through.
-- This is the multi-path metrics pattern applied to a funnel.
--
-- Syntax: entity.logical_name USING (relationship) AS physical_expression
--         USING comes BEFORE AS

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.LOAN_PIPELINE_SV

  TABLES (
    applications AS SNIPPETS.PUBLIC.LOAN_APPLICATIONS
      PRIMARY KEY (APPLICATION_ID)

    -- One date dimension alias, four relationships pointing into it.
    -- Each USING clause on a metric selects which relationship to traverse.
    , date_dim AS SNIPPETS.PUBLIC.DIM_DATE
        PRIMARY KEY (DATE_KEY)
  )

  RELATIONSHIPS (
    -- Four milestone paths — all lead to the same DIM_DATE table
    app_to_application_date AS applications(APPLICATION_DATE)
      REFERENCES date_dim(DATE_KEY)

    , app_to_review_date    AS applications(REVIEW_DATE)
      REFERENCES date_dim(DATE_KEY)

    , app_to_decision_date  AS applications(DECISION_DATE)
      REFERENCES date_dim(DATE_KEY)

    , app_to_funding_date   AS applications(FUNDING_DATE)
      REFERENCES date_dim(DATE_KEY)
  )

  FACTS (
    -- logical: requested_amount → physical: REQUESTED_AMOUNT
    applications.requested_amount AS REQUESTED_AMOUNT

    -- logical: funded_amount → physical: FUNDED_AMOUNT (NULL until loan funds)
    , applications.funded_amount  AS FUNDED_AMOUNT
  )

  DIMENSIONS (
    -- Application attributes — no date path needed
    applications.loan_product AS LOAN_PRODUCT
      WITH SYNONYMS ('product', 'loan type', 'product type')
    , applications.state      AS STATE
      WITH SYNONYMS ('state', 'us state', 'geography')
    , applications.channel    AS CHANNEL
      WITH SYNONYMS ('channel', 'acquisition channel', 'marketing channel')

    -- Date dimension — the same columns serve all four milestone roles via USING
    , date_dim.month_name AS MONTH_NAME
      WITH SYNONYMS ('month', 'month name')
    , date_dim.month_num  AS MONTH_NUM
      WITH SYNONYMS ('month number')
    , date_dim.quarter    AS QUARTER
      WITH SYNONYMS ('quarter', 'qtr')
    , date_dim.year       AS YEAR
      WITH SYNONYMS ('year')
  )

  METRICS (
    -- ── Stage counts ─────────────────────────────────────────────────────────
    -- USING (relationship) comes before AS — declares the date path for this metric.
    -- "Count of X by the date that X happened."

    -- Applications submitted — dated by APPLICATION_DATE
    applications.application_count USING (app_to_application_date) AS COUNT(APPLICATION_ID)
      WITH SYNONYMS ('applications', 'apps submitted', 'application volume')
      COMMENT = 'Count of submitted applications, dated by application_date'

    -- Reviews started — COUNT(REVIEW_DATE) skips NULLs (not-yet-reviewed apps)
    , applications.review_count USING (app_to_review_date) AS COUNT(REVIEW_DATE)
      WITH SYNONYMS ('reviews', 'reviews started', 'underwriting count')
      COMMENT = 'Count of applications that entered review, dated by review_date'

    -- Decisions made (approved or denied)
    , applications.decision_count USING (app_to_decision_date) AS COUNT(DECISION_DATE)
      WITH SYNONYMS ('decisions', 'decisions made', 'approvals and denials')
      COMMENT = 'Count of applications with a final decision, dated by decision_date'

    -- Loans funded — COUNT(FUNDING_DATE) skips denied/in-progress applications
    , applications.funding_count USING (app_to_funding_date) AS COUNT(FUNDING_DATE)
      WITH SYNONYMS ('fundings', 'funded loans', 'loan count', 'originations')
      COMMENT = 'Count of funded loans, dated by funding_date'

    -- ── Dollar volumes ────────────────────────────────────────────────────────
    , applications.total_requested USING (app_to_application_date) AS SUM(REQUESTED_AMOUNT)
      WITH SYNONYMS ('requested amount', 'application volume dollars', 'pipeline value')

    , applications.total_funded USING (app_to_funding_date) AS SUM(FUNDED_AMOUNT)
      WITH SYNONYMS ('funded amount', 'origination volume', 'funded dollars')

    -- ── Funnel conversion rates ───────────────────────────────────────────────
    -- Derived metrics combine stage counts from different USING paths.
    -- When grouped by date_dim.month, each constituent is counted in its own
    -- date bucket — the ratio is same-period, NOT cohort-based (see GOTCHAS).
    , applications.review_rate   AS DIV0(review_count, application_count)
      WITH SYNONYMS ('review rate', 'application to review rate')
      COMMENT = 'Fraction of applications that entered review (same-period)'

    , applications.decision_rate AS DIV0(decision_count, application_count)
      WITH SYNONYMS ('decision rate', 'approval rate', 'application to decision rate')
      COMMENT = 'Fraction of applications that received a decision (same-period)'

    , applications.funding_rate  AS DIV0(funding_count, application_count)
      WITH SYNONYMS ('funding rate', 'close rate', 'conversion rate', 'pull-through rate')
      COMMENT = 'Fraction of applications that funded (same-period, not cohort-based)'
  )

  COMMENT = 'Loan origination pipeline modeled as an Accumulating Snapshot Fact Table (Kimball). One row per application; four milestone dates (application, review, decision, funding). Each stage metric uses USING to declare its date relationship — enabling stage-specific time analysis from a single DIM_DATE alias.'

  AI_SQL_GENERATION 'This SV models a loan origination funnel as an Accumulating Snapshot Fact Table. One DIM_DATE alias with four milestone relationships; each metric uses USING to declare which milestone date it is counted against.

Stage count metrics and their date paths:
  application_count USING (app_to_application_date) → APPLICATION_DATE
  review_count      USING (app_to_review_date)      → REVIEW_DATE
  decision_count    USING (app_to_decision_date)    → DECISION_DATE
  funding_count     USING (app_to_funding_date)     → FUNDING_DATE

To analyze a single stage over time: use that stage metric alone with date_dim.year / date_dim.month_name dimensions.
Funnel conversion metrics (review_rate, decision_rate, funding_rate) are same-period ratios.
To slice by loan type or geography, add applications.loan_product / applications.state — these do not require a date path.';
