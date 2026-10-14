# Accumulating Snapshot Fact Table

## The Problem

You're modeling a multi-stage business pipeline — loan origination, hiring, claims processing, SaaS trial-to-paid — where each entity moves through a sequence of milestones. Each milestone has its own date, and you want to analyze each stage independently:

- "How many applications did we receive in January?"
- "How many loans funded in Q1?"
- "What was our review-to-decision rate last month?"

A standard fact table with one date column can't answer all these questions from a single table. A separate fact table per stage works but forces analysts to write multi-table joins and risks inconsistent metric definitions.

**How You Might Express This Need:**
- "I want to see the full loan funnel — applications, reviews, decisions, fundings — all in one table."
- "How do I model a pipeline where each stage has its own date?"
- "I want conversion rates between stages, but the stages happen on different dates."
- "My data team calls this an 'accumulating snapshot' — how do I build a SV for it?"

## The Solution: Accumulating Snapshot + USING per Stage

Kimball's **Accumulating Snapshot Fact Table** puts one row per business entity (one per loan application). The row accumulates updates as the entity moves through stages — milestone date columns start NULL and get filled in when each stage is reached.

```sql
-- One row per application; milestone dates NULL until stage reached
LOAN_APPLICATIONS
  application_id    -- PK
  application_date  -- always set
  review_date       -- NULL until underwriting starts
  decision_date     -- NULL until approved or denied
  funding_date      -- NULL until funded
  funded_amount     -- NULL until funded
```

In the Semantic View, a **single `date_dim` alias** serves all four milestone paths. Each stage metric declares its own date relationship with `USING`:

```sql
RELATIONSHIPS (
  app_to_application_date AS applications(APPLICATION_DATE) REFERENCES date_dim(DATE_KEY)
  app_to_review_date      AS applications(REVIEW_DATE)      REFERENCES date_dim(DATE_KEY)
  app_to_decision_date    AS applications(DECISION_DATE)    REFERENCES date_dim(DATE_KEY)
  app_to_funding_date     AS applications(FUNDING_DATE)     REFERENCES date_dim(DATE_KEY)
)

METRICS (
  -- USING (relationship) comes BEFORE AS — declares the date path for this metric
  applications.application_count USING (app_to_application_date) AS COUNT(APPLICATION_ID)
  applications.review_count      USING (app_to_review_date)      AS COUNT(REVIEW_DATE)
  applications.decision_count    USING (app_to_decision_date)    AS COUNT(DECISION_DATE)
  applications.funding_count     USING (app_to_funding_date)     AS COUNT(FUNDING_DATE)
)
```

When grouped by `date_dim.month`, each metric independently uses its own date path. `application_count` buckets by `APPLICATION_DATE`; `funding_count` buckets by `FUNDING_DATE` — in a single query.

## What the Demo Shows

12 loan applications across January–March 2025 (4 per month). The funnel narrows naturally:

| Stage | Count | Notes |
|-------|-------|-------|
| Applications | 12 | 4 per month |
| Reviews | 10 | 2 not yet reviewed |
| Decisions | 7 | 3 in review, not yet decided |
| Fundings | 5 | 2 denied/withdrawn |

Milestone dates may differ from application date — a January application may not fund until February or March. This cross-stage date shift is what makes USING essential.

**Full funnel by application month** (Q3 — all 4 metrics in one query):

```
YEAR  MONTH     APPLICATION_COUNT  REVIEW_COUNT  DECISION_COUNT  FUNDING_COUNT
2025  January               4            4               3               2
2025  February              4            3               2               2
2025  March                 4            3               2               1
```

Each column uses a different date path under the hood.

**Conversion rates by channel** (Q5):

```
CHANNEL      APPLICATION_COUNT  REVIEW_RATE  DECISION_RATE  FUNDING_RATE
Direct Mail          2             1.00          0.50           0.50
Organic              5             0.80          0.60           0.40
Paid Search          3             1.00          1.00           0.67
Referral             2             0.50          0.00           0.00
```

## USING Clause Syntax

```sql
-- CORRECT: USING comes BEFORE AS
applications.funding_count USING (app_to_funding_date) AS COUNT(FUNDING_DATE)

-- WRONG: USING after AS — will error at deploy time
applications.funding_count AS COUNT(FUNDING_DATE) USING (app_to_funding_date)
```

## Derived Metrics Referencing USING-Scoped Metrics

Derived metrics that combine USING-scoped constituents must be defined **without an entity prefix on the left side**:

```sql
-- CORRECT: no entity prefix on the left
, funding_rate AS DIV0(applications.funding_count, applications.application_count)

-- WRONG: entity prefix on left causes compilation error
, applications.funding_rate AS DIV0(applications.funding_count, applications.application_count)
```

The constituent references on the right side (`applications.funding_count`) still need the entity prefix.

## What Doesn't Work

- **Cohort analysis**: The conversion rates here are *same-period* ratios, not cohort-based. `funding_rate` for January = fundings-in-January ÷ applications-in-January, not "of all January applications, how many eventually funded." January applications that fund in February are NOT counted in January's `funding_count` — they appear in February's. True cohort analysis requires a different model structure (e.g., a pre-aggregated cohort summary table).

- **NULL milestone dates and COUNT**: `COUNT(REVIEW_DATE)` naturally skips NULLs, so non-reviewed applications are automatically excluded. This is intentional — it's what makes the pattern work. `COUNT(APPLICATION_ID)` counts all rows regardless.

- **NULL row in output**: When grouping by a date dimension, applications with NULL milestone dates (e.g., unfunded loans when querying `funding_count`) produce a NULL dimension row. This is expected LEFT JOIN behavior.

- **Mixing USING and non-USING metrics in one query**: Works correctly — each metric independently resolves its own date path. The NULL row appears when any metric in the query has a NULL date for some rows.

## Accumulating Snapshot vs. Role-Playing Dimensions

Both patterns handle multiple relationships to the same physical date table. The key distinction:

| | Accumulating Snapshot (this snippet) | Role-Playing Dimensions (`role_playing_dimensions`) |
|--|--|--|
| Aliases in TABLES | One `date_dim` alias | Two aliases: `order_date_dim`, `ship_date_dim` |
| Disambiguation | `USING` on each metric | None needed — each alias has unique dim names |
| Date dimensions | Shared: `year`, `month_name` (resolve differently per metric) | Independent: `order_year`, `ship_year` |
| Use both dates together? | No — USING locks each metric to one path | Yes — produces cross-tab |
| Best for | Stage-based pipeline funnels | Entity with two independent date attributes |

Use **accumulating snapshot + USING** when you have one entity moving through sequential stages.
Use **role-playing dimensions** when you have multiple independent date attributes (order date *and* ship date) that analysts need to group by simultaneously.

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **Power BI** | Separate inactive relationships to DIM_DATE; DAX measures use `USERELATIONSHIP()` to activate the correct path per metric. Verbose — each measure must repeat the relationship reference. |
| **Tableau** | Typically requires either a UNION of stage-level fact tables or a pre-pivoted "funnel summary" table. No clean accumulating snapshot pattern in the native semantic layer. |
| **LookML** | `dimension: review_date` with `fanout_on: applications`; derived measures using `sql_table_name` overrides. Requires careful handling to avoid double-counting. |
| **dbt** | Model the accumulating snapshot in SQL; metrics layer (dbt Semantic Layer / MetricFlow) can define multiple `time_grains` but doesn't natively handle per-metric date disambiguation at query time. |
| **Raw SQL** | Four LEFT JOINs to DIM_DATE with aliases (`app_date`, `review_date_dim`, etc.); each COUNT wrapped in a CASE or separate CTE. The SV encodes this join structure once and exposes it cleanly. |

## Docs

- [Semantic View — USING clause on metrics](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view#metrics)
- [Semantic View — RELATIONSHIPS clause](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view#relationships)
- [SEMANTIC_VIEW() table function](https://docs.snowflake.com/en/sql-reference/functions/semantic_view)
- [Kimball Group — Accumulating Snapshot Fact Tables](https://www.kimballgroup.com/data-warehouse-business-intelligence-resources/kimball-techniques/dimensional-modeling-techniques/accumulating-snapshot-fact-table/)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `DIM_DATE` calendar table + `LOAN_APPLICATIONS` accumulating snapshot fact table |
| `seed_data.sql` | 12 loan applications (Jan–Mar 2025) + 26 DIM_DATE rows; funnel: 12→10→7→5 |
| `semantic_view.sql` | `LOAN_PIPELINE_SV` — one `date_dim` alias, four milestone relationships, USING per stage metric |
| `queries.sql` | Five verification queries with expected outputs: single-stage, multi-stage, rates by product and channel |
