# Query Reference — Budget vs Actuals

Every `{{token}}` below is a key in the `placeholders:` map of `hydration_hints.yaml`
(format: `engine/references/output_contract.md`). Substitute literally; never guess a
table or column. Runtime values (periods, version label, line filters) are bound
parameters (`:period_start`, `:period_end`, `:budget_version`, …) — never concatenated.

| Token | Source |
|---|---|
| `{{plan_fqn}}` | budget / plan (`finance.budget.plan`) |
| `{{actuals_fqn}}` | actuals (`finance.budget.actuals`) |
| `{{cols.line_item_id}}`, `{{cols.period}}`, `{{cols.budget_amount}}`, `{{cols.account_type}}`, `{{cols.cost_center}}`, `{{cols.currency}}` | plan columns |
| `{{cols.actual_line_item_id}}`, `{{cols.actual_period}}`, `{{cols.actual_amount}}`, `{{cols.actual_account_type}}`, `{{cols.actual_cost_center}}`, `{{cols.actual_currency}}` | actuals columns |
| `{{settings.version_column}}` | plan version column (versioned planning facts only) |
| `{{settings.natural_sign}}` | `credit_negative` or `as_stored` (confirm on first run) |

`cols.cost_center` / `cols.actual_cost_center` are optional. When both resolve, keep the
lines marked `-- cost center`; when either is unresolved, delete those lines (do not
substitute a guess).

---

## Query Type 0 — Currency Check (run first)

**Purpose**: Plan and actuals must be compared in the same currency. Budgets are often in
reporting currency while actuals are in local/functional currency; joining them as-is
leaves every line unmatched. Run this before Query Type 1.

```sql
SELECT 'plan' AS side, {{cols.currency}} AS currency, COUNT(*) AS rows_
FROM {{plan_fqn}}
WHERE {{cols.period}} BETWEEN :period_start AND :period_end
GROUP BY 1, 2
UNION ALL
SELECT 'actuals', {{cols.actual_currency}}, COUNT(*)
FROM {{actuals_fqn}}
WHERE {{cols.actual_period}} BETWEEN :period_start AND :period_end
GROUP BY 1, 2
ORDER BY 1, 2;
```

If the two sides do not share the same currency set, **stop**: convert actuals to the
plan currency (or both to the configured `reporting_currency`) with the customer's FX
source and confirm with the user. Never compare amounts across currencies. If neither
currency column resolves, ask the user which currency each source is in.

---

## Query Type 1 — Variance by Line Item

**Purpose**: Budget vs actual per line item for the analysis window, with absolute and
percent variance and favorability (SKILL.md Workflow Step 2).

**Logic**:
1. One window for both sides: `:period_start`–`:period_end`. In actuals-only mode set
   `:period_end` to the last closed month so open months drop from **both** sides.
2. Budget side: plan rows in the window, filtered to the chosen budget version (Step 0).
   Omit the version filter when the plan source is a frozen table with no version column.
3. FULL OUTER JOIN on line item (and cost center when bound) so budget-only and
   actual-only lines both show.
4. `account_type` comes from either side, so actual-only lines still get the right sign
   and favorability.
5. Apply `natural_sign` before comparing (credit-normal lines × −1 when `credit_negative`).
6. Favorable: revenue lines when actual > budget; expense lines when actual < budget.

**Output grain**: one row per line item (per cost center when bound) per currency, summed
over the window, ranked by |variance|.

```sql
WITH budget AS (
  SELECT
    {{cols.line_item_id}}            AS line_item_id,
    {{cols.cost_center}}             AS cost_center,      -- cost center
    {{cols.currency}}                AS currency,
    ANY_VALUE({{cols.account_type}}) AS account_type,
    SUM({{cols.budget_amount}})      AS budget_amount
  FROM {{plan_fqn}}
  WHERE {{cols.period}} BETWEEN :period_start AND :period_end
    AND {{settings.version_column}} = :budget_version   -- omit for a frozen budget table
  GROUP BY ALL
),
actual AS (
  SELECT
    {{cols.actual_line_item_id}}            AS line_item_id,
    {{cols.actual_cost_center}}             AS cost_center,      -- cost center
    {{cols.actual_currency}}                AS currency,
    ANY_VALUE({{cols.actual_account_type}}) AS account_type,     -- use NULL if unresolved
    SUM({{cols.actual_amount}})             AS actual_amount
  FROM {{actuals_fqn}}
  WHERE {{cols.actual_period}} BETWEEN :period_start AND :period_end
  GROUP BY ALL
),
joined AS (
  SELECT
    COALESCE(b.line_item_id, a.line_item_id)  AS line_item_id,
    COALESCE(b.cost_center, a.cost_center)    AS cost_center,    -- cost center
    COALESCE(b.currency, a.currency)          AS currency,
    COALESCE(b.account_type, a.account_type)  AS account_type,
    b.budget_amount                           AS raw_budget_amount,
    a.actual_amount                           AS raw_actual_amount
  FROM budget b
  FULL OUTER JOIN actual a
    ON  a.line_item_id = b.line_item_id
    AND a.cost_center  = b.cost_center                            -- cost center
    AND a.currency     = b.currency
),
signed AS (
  SELECT
    *,
    -- natural_sign normalization: credit-normal lines arrive negative in most ERPs
    IFF('{{settings.natural_sign}}' = 'credit_negative'
        AND UPPER(account_type) IN ('REVENUE', 'LIABILITY', 'EQUITY'), -1, 1) AS sign_mult,
    COALESCE(raw_budget_amount, 0) * sign_mult AS budget_amount,
    COALESCE(raw_actual_amount, 0) * sign_mult AS actual_amount
  FROM joined
)
SELECT
  line_item_id,
  cost_center,                                                    -- cost center
  currency,
  account_type,
  budget_amount,
  actual_amount,
  actual_amount - budget_amount                                  AS variance,
  DIV0(actual_amount - budget_amount, ABS(budget_amount)) * 100  AS variance_pct,
  CASE
    WHEN actual_amount = budget_amount THEN 'ON PLAN'
    WHEN account_type IS NULL THEN 'UNCLASSIFIED'
    WHEN UPPER(account_type) = 'REVENUE'
      THEN IFF(actual_amount > budget_amount, 'FAV', 'UNFAV')
    WHEN UPPER(account_type) IN ('ASSET', 'LIABILITY', 'EQUITY')
      THEN IFF(actual_amount > budget_amount, 'INCREASE', 'DECREASE')
    ELSE IFF(actual_amount < budget_amount, 'FAV', 'UNFAV')
  END                                                            AS direction
FROM signed
ORDER BY currency, ABS(variance) DESC;
```

---

## Pitfalls

1. **Window parity**: both sides use the same `:period_start`–`:period_end`. In
   actuals-only mode set `:period_end` to the last closed month; never let the budget run
   further than the actuals.
2. **Currency**: run Query Type 0 first. Never sum or compare across currencies; convert
   to one currency before Query Type 1.
3. **Period type**: `period` must be a date (e.g. period start date). Text period labels
   such as `'Jan-26'` sort alphabetically, so `BETWEEN` returns the wrong months; map them
   to dates first.
4. **Grain**: budgets are usually set by cost center × account. If the plan has a cost
   center / department column, bind it on both sides; otherwise departments are summed
   together.
5. **Version**: a versioned plan source without a version filter double-counts every
   version. Resolve the budget baseline (SKILL.md Step 0) before running.
6. **Sign**: apply `natural_sign` before computing variance or favorability. Lines with no
   `account_type` on either side are reported as `UNCLASSIFIED`, not guessed as expense.
