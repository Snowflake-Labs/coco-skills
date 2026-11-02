# Query Playbook — Good Morning CFO

This file keeps durable query structure out of `SKILL.md`. It is optional but
recommended for this query-heavy skill. Cortex Code resolves the placeholders
from `hydration_hints.yaml` during plugin hydration/rehydration; customers should not
edit this file directly.

---

## Placeholders

| Placeholder | Resolved from `hydration_hints.yaml` |
|---|---|
| `{{forecast_daily_fqn}}` | `data_needs.finance.revenue.forecast_daily.sources[0].fqn` |
| `{{account_forecast_fqn}}` | `data_needs.finance.revenue.account_forecast.sources[0].fqn` |
| `{{daily_usage_fqn}}` | `data_needs.finance.consumption.daily_usage.sources[0].fqn` |
| `{{cols.X}}` | The active source's `columns.X` entry for the current query's data need |

---

## Query Type 1 — Revenue Performance (Daily)

**Purpose**: Yesterday's actual vs forecast, MTD pacing, QTD pacing.

### 1a. Headline beat/miss

```sql
WITH yday AS (
  SELECT
    SUM({{cols.actual_amount}})   AS actual_amount,
    SUM({{cols.forecast_amount}}) AS forecast_amount,
    {{cols.currency}}             AS currency
  FROM {{forecast_daily_fqn}}
  WHERE {{cols.date}} = (
    SELECT MAX({{cols.date}})
    FROM {{forecast_daily_fqn}}
    WHERE {{cols.date}} < CURRENT_DATE()           -- "yesterday" = most recent landed day
  )
  GROUP BY {{cols.currency}}
)
SELECT
  currency,
  actual_amount,
  forecast_amount,
  actual_amount - forecast_amount                                 AS variance_amount,
  DIV0(actual_amount - forecast_amount, NULLIF(forecast_amount, 0)) AS variance_pct
FROM yday;
```

### 1b. MTD pacing

```sql
SELECT
  {{cols.currency}}                                  AS currency,
  SUM({{cols.actual_amount}})                        AS mtd_actual,
  SUM({{cols.forecast_amount}})                      AS mtd_forecast,
  DIV0(SUM({{cols.actual_amount}}),
       NULLIF(SUM({{cols.forecast_amount}}), 0))     AS mtd_attainment
FROM {{forecast_daily_fqn}}
WHERE {{cols.date}} >= DATE_TRUNC('month', CURRENT_DATE())
  AND {{cols.date}} <  CURRENT_DATE()
GROUP BY {{cols.currency}};
```

### 1c. Segment-level breakdown (Day-over-Day computed in SQL)

> No pre-computed DoD column is required —
> we LAG the actual_amount over (segment, ordered by date).

```sql
WITH daily AS (
  SELECT
    {{cols.date}}                AS activity_date,
    {{cols.segment_id}}          AS segment,
    {{cols.currency}}            AS currency,
    SUM({{cols.actual_amount}})  AS actual_amount,
    SUM({{cols.forecast_amount}}) AS forecast_amount
  FROM {{forecast_daily_fqn}}
  WHERE {{cols.date}} >= DATEADD(day, -7, CURRENT_DATE())
  GROUP BY 1, 2, 3
),
with_dod AS (
  SELECT
    activity_date,
    segment,
    currency,
    actual_amount,
    forecast_amount,
    LAG(actual_amount) OVER (
      PARTITION BY segment, currency
      ORDER BY activity_date
    )                                                              AS prior_day_actual,
    actual_amount - LAG(actual_amount) OVER (
      PARTITION BY segment, currency
      ORDER BY activity_date
    )                                                              AS dod_change_amount,
    DIV0(
      actual_amount - LAG(actual_amount) OVER (
        PARTITION BY segment, currency ORDER BY activity_date
      ),
      NULLIF(LAG(actual_amount) OVER (
        PARTITION BY segment, currency ORDER BY activity_date
      ), 0)
    )                                                              AS dod_change_pct
  FROM daily
)
SELECT *
FROM with_dod
WHERE activity_date = (SELECT MAX(activity_date) FROM with_dod)
ORDER BY actual_amount DESC;
```

---

## Query Type 2 — Top Movers (Week-over-Week computed in SQL)

**Purpose**: Top 5 accounts driving positive forecast variance, top 5 driving negative.

> Again, no pre-computed WoW column required — LAG over
> (account_id, ordered by week_ending_date).

```sql
WITH weekly AS (
  SELECT
    {{cols.week_ending_date}}    AS week_end,
    {{cols.account_id}}          AS account_id,
    {{cols.account_name}}        AS account_name,
    {{cols.currency}}            AS currency,
    SUM({{cols.forecast_amount}}) AS forecast_amount
  FROM {{account_forecast_fqn}}
  WHERE {{cols.week_ending_date}} >= DATEADD(week, -3, CURRENT_DATE())
  GROUP BY 1, 2, 3, 4
),
with_wow AS (
  SELECT
    week_end,
    account_id,
    account_name,
    currency,
    forecast_amount,
    LAG(forecast_amount) OVER (
      PARTITION BY account_id, currency
      ORDER BY week_end
    )                                                              AS prior_week_forecast,
    forecast_amount - LAG(forecast_amount) OVER (
      PARTITION BY account_id, currency
      ORDER BY week_end
    )                                                              AS wow_change_amount,
    DIV0(
      forecast_amount - LAG(forecast_amount) OVER (
        PARTITION BY account_id, currency ORDER BY week_end
      ),
      NULLIF(LAG(forecast_amount) OVER (
        PARTITION BY account_id, currency ORDER BY week_end
      ), 0)
    )                                                              AS wow_change_pct
  FROM weekly
),
latest AS (
  SELECT *
  FROM with_wow
  WHERE week_end = (SELECT MAX(week_end) FROM with_wow)
    AND prior_week_forecast IS NOT NULL  -- exclude new accounts
)
-- Top 5 positive movers
SELECT 'POSITIVE' AS direction, account_id, account_name, currency,
       wow_change_amount, wow_change_pct
FROM latest
ORDER BY wow_change_amount DESC
LIMIT 5

UNION ALL

-- Top 5 negative movers
SELECT 'NEGATIVE' AS direction, account_id, account_name, currency,
       wow_change_amount, wow_change_pct
FROM latest
ORDER BY wow_change_amount ASC
LIMIT 5;
```

---

## Query Type 3 — Consumption Pulse (WoW)

**Purpose**: Total usage trend WoW, top growing/declining accounts.

> If `{{daily_usage_fqn}}` resolves at LOW confidence (customer doesn't have
> a usage table), Cortex Code skips this query entirely.

### 3a. Total usage WoW

```sql
WITH bucketed AS (
  SELECT
    CASE
      WHEN {{cols.usage_date}} >= DATEADD(day, -7, CURRENT_DATE()) THEN 'current_week'
      WHEN {{cols.usage_date}} >= DATEADD(day, -14, CURRENT_DATE()) THEN 'prior_week'
    END                              AS bucket,
    SUM({{cols.credits}})            AS credits_used
  FROM {{daily_usage_fqn}}
  WHERE {{cols.usage_date}} >= DATEADD(day, -14, CURRENT_DATE())
  GROUP BY 1
)
SELECT
  MAX(CASE WHEN bucket = 'current_week' THEN credits_used END) AS current_week_credits,
  MAX(CASE WHEN bucket = 'prior_week'   THEN credits_used END) AS prior_week_credits,
  DIV0(
    MAX(CASE WHEN bucket = 'current_week' THEN credits_used END)
    - MAX(CASE WHEN bucket = 'prior_week' THEN credits_used END),
    NULLIF(MAX(CASE WHEN bucket = 'prior_week' THEN credits_used END), 0)
  )                                                            AS wow_growth_pct
FROM bucketed;
```

### 3b. Top growing / declining accounts

```sql
WITH per_account AS (
  SELECT
    {{cols.account_id}}              AS account_id,
    SUM(CASE WHEN {{cols.usage_date}} >= DATEADD(day, -7, CURRENT_DATE())
             THEN {{cols.credits}} END) AS current_week_credits,
    SUM(CASE WHEN {{cols.usage_date}} >= DATEADD(day, -14, CURRENT_DATE())
             AND  {{cols.usage_date}} <  DATEADD(day, -7,  CURRENT_DATE())
             THEN {{cols.credits}} END) AS prior_week_credits
  FROM {{daily_usage_fqn}}
  WHERE {{cols.usage_date}} >= DATEADD(day, -14, CURRENT_DATE())
  GROUP BY 1
),
deltas AS (
  SELECT
    account_id,
    current_week_credits,
    prior_week_credits,
    (current_week_credits - prior_week_credits) AS credit_change,
    DIV0(current_week_credits - prior_week_credits,
         NULLIF(prior_week_credits, 0))         AS credit_change_pct
  FROM per_account
  WHERE prior_week_credits IS NOT NULL AND prior_week_credits > 0
)
SELECT 'GROWING' AS direction, account_id, current_week_credits, prior_week_credits,
       credit_change, credit_change_pct
FROM deltas
ORDER BY credit_change DESC
LIMIT 5

UNION ALL

SELECT 'DECLINING', account_id, current_week_credits, prior_week_credits,
       credit_change, credit_change_pct
FROM deltas
ORDER BY credit_change ASC
LIMIT 5;
```

---

## Query Type 4 — External Market Signals

Uses the `web_search` MCP tool, not SQL.
See SKILL.md → Behavior → Workflow step 2.

---

## Pitfalls

1. **Fiscal calendar assumption** — never hardcode Q1 = Jan-Mar. Always read
   the configured `fiscal_calendar` setting.
2. **Missing current-day data** — "yesterday" is `MAX({{cols.date}}) WHERE {{cols.date}} < CURRENT_DATE()`,
   NOT `DATEADD(day, -1, CURRENT_DATE())`. Handles weekend gaps and late-arriving rows.
3. **Currency mixing** — every PARTITION BY includes `currency`. Never sum across currencies.
4. **NULL forecast / divide-by-zero** — every `/` is wrapped in `DIV0( …, NULLIF(…, 0) )`.
5. **New accounts in week 1** — `WHERE prior_week_forecast IS NOT NULL` excludes them from movers.
6. **Segment explosion** — if `{{cols.segment_id}}` has >20 distinct values, the skill should
   roll up to top-N + "Other" before rendering. Logic lives in SKILL.md → Behavior, not here.

---

## Cortex Analyst fallback path

If `finance.revenue.forecast_daily` resolves to a **semantic view** (preferred),
the skill SHOULD prefer routing complex follow-ups ("why did segment X miss?")
through Cortex Analyst against that SV, rather than constructing additional
raw SQL. The queries above are still used for the briefing-generation path
(deterministic, cheap), but ad-hoc Q&A goes through Analyst.

Detection:
```sql
SHOW SEMANTIC VIEWS LIKE '%' IN <db>.<schema>;
-- if the resolved FQN appears here, prefer Analyst for follow-ups.
```
