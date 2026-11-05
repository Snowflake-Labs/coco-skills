# Query Reference — Budget vs Actuals

Every `{{token}}` below is a key in the `placeholders:` map of `hydration_hints.yaml`
(format: `engine/references/output_contract.md`). Substitute literally; never guess a
table or column. Runtime values (periods, version label, line filters) are bound
parameters (`:period_start`, `:period_end`, `:budget_version`, …) — never concatenated.

| Token | Source |
|---|---|
| `{{plan_fqn}}` | budget / plan (`finance.budget.plan`) |
| `{{actuals_fqn}}` | actuals (`finance.budget.actuals`) |
| `{{cols.line_item_id}}`, `{{cols.period}}`, `{{cols.budget_amount}}`, `{{cols.account_type}}`, `{{cols.currency}}` | plan columns |
| `{{cols.actual_line_item_id}}`, `{{cols.actual_period}}`, `{{cols.actual_amount}}`, `{{cols.actual_currency}}` | actuals columns |
| `{{settings.version_column}}` | plan version column (versioned planning facts only) |
| `{{settings.natural_sign}}` | `credit_negative` or `as_stored` (confirm on first run) |

---

## Query Type 1 — Variance by Line Item

**Purpose**: Budget vs actual per line item for the analysis window, with absolute and
percent variance and favorability (SKILL.md Workflow Step 2).

**Logic**:
1. Budget side: plan rows in the window, filtered to the chosen budget version (Step 0).
   Omit the version filter when the plan source is a frozen table with no version column.
2. Actuals side: actuals rows in the same window, closed months only (actuals-only mode).
3. FULL OUTER JOIN on line item and period so budget-only and actual-only lines both show.
4. Apply `natural_sign` before comparing (credit-normal lines × −1 when `credit_negative`).
5. Favorable: revenue lines when actual > budget; expense lines when actual < budget.

**Output grain**: one row per line item (summed over the window), ranked by |variance|.

```sql
WITH budget AS (
  SELECT
    {{cols.line_item_id}}            AS line_item_id,
    ANY_VALUE({{cols.account_type}}) AS account_type,
    {{cols.currency}}                AS currency,
    SUM({{cols.budget_amount}})      AS budget_amount
  FROM {{plan_fqn}}
  WHERE {{cols.period}} BETWEEN :period_start AND :period_end
    AND {{settings.version_column}} = :budget_version   -- omit for a frozen budget table
  GROUP BY 1, 3
),
actual AS (
  SELECT
    {{cols.actual_line_item_id}}     AS line_item_id,
    {{cols.actual_currency}}         AS currency,
    SUM({{cols.actual_amount}})      AS actual_amount
  FROM {{actuals_fqn}}
  WHERE {{cols.actual_period}} BETWEEN :period_start AND :closed_through
  GROUP BY 1, 2
),
joined AS (
  SELECT
    COALESCE(b.line_item_id, a.line_item_id)  AS line_item_id,
    COALESCE(b.currency, a.currency)          AS currency,
    b.account_type,
    -- natural_sign normalization: credit-normal lines arrive negative in most ERPs
    IFF('{{settings.natural_sign}}' = 'credit_negative'
        AND UPPER(b.account_type) IN ('REVENUE', 'LIABILITY', 'EQUITY'), -1, 1) AS sign_mult,
    COALESCE(b.budget_amount, 0) * sign_mult     AS budget_amount,
    COALESCE(a.actual_amount, 0) * sign_mult     AS actual_amount
  FROM budget b
  FULL OUTER JOIN actual a
    ON a.line_item_id = b.line_item_id
   AND a.currency     = b.currency
)
SELECT
  line_item_id,
  currency,
  account_type,
  budget_amount,
  actual_amount,
  actual_amount - budget_amount                          AS variance,
  DIV0(actual_amount - budget_amount, ABS(budget_amount)) * 100 AS variance_pct,
  CASE
    WHEN actual_amount = budget_amount THEN 'ON PLAN'
    WHEN UPPER(account_type) = 'REVENUE'
      THEN IFF(actual_amount > budget_amount, 'FAV', 'UNFAV')
    WHEN UPPER(account_type) IN ('ASSET', 'LIABILITY', 'EQUITY')
      THEN IFF(actual_amount > budget_amount, 'INCREASE', 'DECREASE')
    ELSE IFF(actual_amount < budget_amount, 'FAV', 'UNFAV')
  END                                                    AS direction
FROM joined
ORDER BY currency, ABS(variance) DESC;
```

---

## Pitfalls

1. **Window parity** — budget uses `:period_end` but actuals stop at `:closed_through`. In
   actuals-only mode set `:period_end = :closed_through` so open months drop from both sides.
2. **Currency** — never sum across currencies; the query groups by currency. Convert to the
   configured `reporting_currency` first if a single total is needed.
3. **Version** — a versioned plan source without a version filter double-counts every
   version. Resolve the budget baseline (SKILL.md Step 0) before running.
4. **Sign** — apply `natural_sign` before computing variance or favorability.
