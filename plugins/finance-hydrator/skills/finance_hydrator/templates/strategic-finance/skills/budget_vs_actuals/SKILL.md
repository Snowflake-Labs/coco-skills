---
name: budget_vs_actuals
description: >
  Compare actuals to the plan/budget baseline for any P&L line, cost center, or
  business unit. The baseline is whichever plan version the organization has locked
  as its budget (a frozen budget table, or a designated version inside a versioned
  planning fact). Flags material variances automatically, generates written commentary
  for each flagged line, and supports monthly close and quarterly close workflows.
  Triggers: budget vs actuals, BvA, plan vs actuals, how did we do vs budget, variance
  to plan, are we on budget, monthly close, quarterly close, opex vs budget, expense
  variance, headcount vs budget, are we over budget, spending vs plan.
dehydrated: true
data_domain: "budget vs actuals, plan variance, P&L line item comparison, monthly close, quarterly close, opex variance, materiality flagging"
created_date: 2026-06-08
version: 1.2.0
license: Proprietary. See LICENSE.txt for complete terms
---

# Budget vs Actuals (Dehydrated)

## Intent

Compare actuals to **the plan baseline the organization treats as its budget**. This
skill flags every line where actuals deviate materially from that baseline, produces
written commentary for each flagged item, and presents the full detail table for
reference.

What counts as "the budget" depends on how the customer stores planning data — and the
two shapes are equally valid:
- **A frozen budget/plan table** (one fixed version per planning cycle; common when the
  budget is exported from a planning tool or a spreadsheet as a standalone table).
- **A designated version inside a versioned planning fact.** Most companies on a modern
  planning tool (Workday Adaptive, Anaplan, Pigment, Oracle EPM/Hyperion) store the
  locked annual plan *and* every rolling reforecast in the **same** fact table, separated
  by a version/scenario label (e.g. `FY26 Plan`, `AOP FY26 Locked`, `FY26 Q4 F2`). Here
  "the budget" is simply the version the company has blessed as the baseline.

This is the same underlying data contract `forecast_vs_forecast` uses (a versioned
planning fact + actuals). **The only difference is which versions you compare:**
- `budget_vs_actuals` → actuals vs **one designated baseline version** (the locked Plan).
- `forecast_vs_forecast` → **two evolving forecast snapshots** against each other.

Serves: FP&A analysts, controllers, and finance business partners running month-end or
quarter-end close processes.

Not for: comparing two forecast snapshots that evolve over time (use
`forecast_vs_forecast`), revenue performance narratives (use `revenue_analysis`),
forward-looking scenarios (use `scenario_planning`), or COGS/margin deep dives
(use `cloud_cogs_analysis`).

## Behavior

### Trigger Detection

Activate on: "budget vs actuals", "BvA", "plan vs actuals", "variance to plan",
"how did we do vs budget", "are we on budget", "monthly close", "quarterly close",
"opex vs budget", "expense variance", "headcount vs budget", "spending vs plan",
"are we over budget", "what's our variance", "close deck variance".

Do NOT activate for:
- Comparing two forecast versions (route to `forecast_vs_forecast`)
- Revenue performance narrative (route to `revenue_analysis`)
- Forward-looking scenarios or what-if analysis (route to `scenario_planning`)
- COGS or infrastructure cost deep dive (route to `cloud_cogs_analysis`)

If metric or period is unspecified, ask via `ask_user_question`:
1. Which metric? (revenue, total opex, a specific cost center/line, headcount cost, or all-in P&L)
2. Which period? (most recent closed month, current quarter-to-date, or a specific period)

### Workflow

**Step 0 — Establish the budget baseline**

Determine *what plays the role of budget* before fetching anything:
- If the plan source is a **frozen budget table** (no version column, or a single distinct
  version), use it directly as the baseline.
- If the plan source is a **versioned planning fact** (a `PLANNING_VERSION` / `SCENARIO` /
  `VERSION` column with >1 distinct value):
  - If `hydration_hints.yaml` specifies `budget_version_label` (or `budget_version_pattern`),
    filter to that version and treat it as the budget.
  - Otherwise, list the distinct versions and **ask the user which one is the locked plan**
    via `ask_user_question` (e.g. "Multiple plan versions exist — which is your budget
    baseline: `FY26 Plan`, `FY26 Q4 F2`, …?"). Label all output with the chosen version.
  - Only route the user to `forecast_vs_forecast` if they actually want to compare **two
    evolving snapshots** — not merely because a version column is present.

**Step 1 — Identify scope and period**

Default: most recent closed month, all-in opex and revenue lines.
Apply user-specified scope or defaults. Determine the analysis period (start/end dates).

**Step 2 — Fetch budget and actuals for all in-scope lines**

Join the budget baseline (Step 0) and the actuals table on line item identifier and period.
Compute for each line:
- Absolute variance: actual − budget
- Percentage variance: (actual − budget) / |budget|
- Direction: FAV (actual < budget for expense lines; actual > budget for revenue lines)
  or UNFAV

*Period handling for a partially closed period.* When the analysis window includes months
that are not yet closed, do not compare a full-period actual to a full-period budget.
Offer two modes and label the output with the one used:
- **Actuals-only (default):** closed months only; open months are excluded from both sides.
- **Blended:** closed months use actuals and open months use the selected plan or forecast
  version, so open-month variance nets to zero and the reported variance is purely
  closed-month actual vs plan. Label the output "N months actual + M months forecast".

*Stock vs flow measures.* Flow measures (spend, revenue) sum across months. Stock measures
(ending headcount, balances, any point-in-time snapshot) must **not** be summed across
months — use the target month (the last month of the window) and apply the same
actual/forecast blend rule. For trailing-history columns, take one month per quarter.

**Step 2.5 — Clean the line-item set before summing, ranking, or flagging**

Real planning views mix row types in the same line-item and amount columns. Apply this to
the Step 2 data, and inspect the distinct line items first. Separate:
- **Subtotals and rollups** (for example "Total …", "… (Excl. …)", or two names that
  carry identical values). Do not sum a subtotal together with its leaf lines — that
  double-counts. Choose one level: present leaf lines and show subtotals only as
  reference totals, or present one rollup level. If it is not obvious which rows are
  subtotals, list the candidates and ask the user.
- **Non-dollar measures** (headcount, counts, rates, ratios) stored in the same amount
  column. Never show these in dollar columns or add them to dollar totals. Report them in
  a separate section in their native unit.
- **Duplicate grain rows** such as annual or full-year rollup rows stored beside monthly
  rows. Filter to one period grain so the same dollars are not counted twice.
- **Mixed revenue and expense rows** in one view with no line-type column. Ask the user
  which lines are revenue and which are expense before assigning FAV/UNFAV.

State in the output which rows were treated as subtotals, excluded, or moved to a
separate section.

**Step 3 — Apply materiality filter**

There is no universally correct flat dollar threshold: a threshold that is sensible for a
mid-size company will flag most lines at a company with a very large budget. Do not
assume a flat default. Instead:
1. Compute the total absolute budget for the scope and propose thresholds relative to it
   (as a tunable starting point, for example a detail threshold of roughly 0.1% of the
   scope's monthly budget and a roll-up threshold roughly 10x that). If the customer
   configured thresholds (`materiality_detail_usd`, `materiality_rollup_usd`), use those.
2. Present the proposed thresholds and confirm with the user.
3. After applying them, report how many lines were flagged. If more than roughly a third
   of the lines are flagged, say so and offer to tighten the threshold — a flag list that
   covers most of the P&L is not useful.

Thresholds apply by level of the hierarchy (all configurable):
- **Detail level** (line item, cost center, or org): flag when |variance $| meets the
  detail threshold.
- **Roll-up / total level** (department, function, consolidated): use the higher
  roll-up threshold.
- Compare variances after rounding to the display unit (e.g. $K) so the flag matches what
  the reader sees.
- Optionally add a percentage test (default 5%) if the customer wants one. When it is
  used, require both the dollar and the percentage test, so a tiny line with a large
  percentage is not flagged.

**Step 4 — Generate commentary for flagged lines**

For each flagged line, build commentary from **decomposed drivers first**, then context
notes:
1. If driver breakdown data is available (sub-category, cost center, vendor/supplier,
   purchase order, project, spend category), rank the movers within the flagged line.
   Tag each driver as **same direction** or **offsetting** relative to the sign of the
   line's total variance. Name the top ~10 movers individually and group the rest as
   "other".
2. For personnel-cost lines, explain the variance with headcount movement: ending
   headcount is a stock; hires, exits, and transfers are flows signed by their effect on
   headcount. Headcount flows are people, not dollars — they explain the ending-headcount
   variance but do not tie to the dollar line. Source headcount actuals from the HR/people
   system, not the planning version: closed months in a planning fact can hold locked
   actuals, so reading "actuals" from the plan version compares a forecast to itself.
3. If a driver source is off-ledger (manual travel sheets, rate/mix schedules), state that it
   "does not tie to the BvA line" when citing it.
4. **When the top movers oppose the net variance** (for example the largest movers all move
   against the line's total, or each is far larger than the net), do not stop at a warning.
   Explain it:
   - Split the variance into **gross positive** and **gross negative** and report both, with
     the count of cost centers on each side, alongside the net.
   - Test for a **reallocation or plan-placement effect**: compare where the plan sits with
     where actuals post. Signs include the plan concentrated in a few cost centers while
     actuals spread across many; many cost centers with zero plan and nonzero actuals; or
     cost centers with a plan and near-zero actuals. If the largest negative movers equal
     their own plan (actual near zero), the plan was likely held centrally and released.
   - State the **true net variance** at the level where plan and actuals are comparable
     (usually the total), and say that variances at the individual org level are not
     meaningful for this line until the plan is allocated the same way as actuals.
   - Label this as an inference from the pattern, and name the cost centers on each side so
     FP&A can confirm it. Never present the net at org level as an overspend or underspend
     of that org.
5. Add any analyst notes from the context table.

Produce 2–3 sentences per flagged line. If no driver or context data is available, note
"Context data unavailable — manual review required." Mark all generated text as a draft
for human review; if a human-edited comment exists for the line and period, it supersedes
the draft. For roll-up commentary, consolidate member-level commentary and include only
members whose own variance meets the roll-up inclusion threshold, each annotated with its
dollar variance and FAV/UNFAV.

> **Stopping point after Step 4:** Present the flagged section + commentary. Then ask:
> "Want to see the full detail table for all lines, drill into the journal-entry detail
> behind a category, check the reconciliation to the total, or drill into any specific
> item?" Do not run these unsolicited.

**Step 5 (on request) — Reconciliation and drill-down**

- *Reconciliation:* list ledger accounts whose cost center has no organization/leader
  mapping. They roll into the grand total but into no individual org, which explains any
  gap between the total and the sum of the orgs. Show them by account and ignore amounts
  below a small noise floor.
- *Drill-down:* show the journal-entry lines behind a category, filtered by category, cost
  center, or org, with a row limit. Map ledger accounts to expense categories through a
  ledger-account mapping, and cost centers to leader and department through a cost-center
  mapping.

### Output Format

```
# Budget vs Actuals — <Metric Scope>, <Period>

## Summary
<Metric scope>: $XXM actual vs $XXM budget (+/-$XXM, +/-X.X%) — [favorable / unfavorable]
Flagged variances: X of Y line items exceeded materiality threshold (>X% and >$XXK).

## Flagged Variances
| Line Item | Budget | Actual | Variance ($) | Variance (%) | Flag |
|---|---|---|---|---|---|
| <item> | $XXM | $XXM | +/-$XXM | +/-X.X% | FAV / UNFAV |

### Commentary
**<Line item 1>** (+/-$XXM, FAV/UNFAV)
<2–3 sentences explaining the driver. Context: [source if available, or "Context data unavailable"]>

**<Line item 2>** (+/-$XXM, FAV/UNFAV)
<2–3 sentences>

---
Period: <start> to <end> | Materiality threshold: >X% and >$XXK
```

### Full Detail Table (on request only)

```
## Full Detail — All Line Items
| Line Item | Budget | Actual | Variance ($) | Variance (%) | Flag |
|---|---|---|---|---|---|
| ... (all lines, sorted by |variance $| descending) |
```

### Confidence Handling

| Confidence | Behavior |
|---|---|
| HIGH (a budget baseline resolves — frozen table or a designated/single plan version — and actuals resolve with matching line-item identifiers) | Full workflow; flagged commentary with context |
| MEDIUM (tables resolve but line-item join is fuzzy, OR the plan source is versioned and no baseline version is designated yet) | If a version must be chosen, run Step 0's `ask_user_question`. If the join is fuzzy, run with best-match join and prefix: "Line-item join is MEDIUM confidence — verify budget and actuals share a common identifier." |
| LOW on critical (no plan/budget source at all) | Block. Output: "Budget vs Actuals requires a plan source — a budget table or a versioned planning fact with a plan version. Add the binding under `finance.budget.plan` in hydration_hints.yaml and re-verify." |
| LOW on critical (actuals table missing) | Block. Output: "Actuals table required. Verify the actuals binding resolves to a table with line-item amounts for the analysis period." |
| LOW on non-critical (context/notes table missing) | Run without commentary enrichment; note "Context data unavailable" on all flagged lines |

### Guardrails

- **The budget is a *role*, not a specific table shape.** It is whichever version the
  organization has locked as its plan. A versioned planning fact is a perfectly valid
  budget source — pin the baseline version via `budget_version_label` in
  hydration_hints, or ask the user (Step 0). **Do not refuse or auto-route to
  `forecast_vs_forecast` just because a version column exists.** Only route away when the
  user genuinely wants to compare two evolving snapshots.
- Once a baseline version is chosen, treat it as fixed for the analysis — do not silently
  swap to a newer reforecast mid-analysis. Always label output with the version used.
- Favorable vs unfavorable direction depends on the line type: for expense lines,
  actual < budget = FAV. For revenue lines, actual > budget = FAV. Always derive
  this from the line type column in the dimension table.
  **If the dimension table is absent and no line_type column exists on the budget/actuals table,
  ask the user via `ask_user_question`: "Which lines are expense lines and which are revenue lines?"
  before computing FAV/UNFAV — do not guess or default all lines to a single type.**
- Do not hardcode materiality thresholds. Present the defaults and confirm if the user
  wants different thresholds.
- Division by zero: if any line has `budget = 0`, compute absolute variance only — omit the
  percentage variance column for that line and note "% variance not applicable (zero budget)".
- Period grain mismatch: if the budget baseline is at monthly grain and actuals are at daily grain,
  aggregate actuals to monthly before joining. Always match to the budget baseline's grain.
- Currency: display in USD by default. If multiple currencies present, group by currency.
- Do not expose raw cost center IDs or GL account numbers — use display names if available.
- Period-to-date data: label all figures "through [date]" to indicate partial period.
- Percentage variance is secondary to dollar variance. Never rely on % alone to rank
  lines. Show a percentage only when the line's budget is above a floor (default: about 1%
  of the scope's total budget, tunable via `pct_budget_floor_usd`); below the floor,
  show the dollar variance and mark the percentage "n/m" (not meaningful). A tiny budget
  produces absurd percentages such as thousands of percent.
- Never place subtotal or rollup rows in the same sum as their leaf lines, and never show
  non-dollar measures in dollar columns (see Step 2.5).
- If FAV/UNFAV cannot be derived from a line type, fall back to the category-level
  direction (variance sign relative to the category's total) only after telling the user,
  and never for revenue-versus-expense mixes.

### Error Handling

| Scenario | Action |
|---|---|
| No plan/budget source found | Block; output setup instructions |
| Plan source is versioned, no baseline designated | Run Step 0 — list versions and ask which is the budget |
| Actuals table missing | Block; note actuals binding required |
| No common join key between budget and actuals | Ask user to clarify the join column name |
| Zero flagged variances | Output: "No lines exceeded the materiality threshold for <period>. All items are tracking within <X>% of budget." |
| User wants a different materiality threshold | Re-run with the new threshold |
| Period has no budget data | Output: "No budget found for <period> in version <V>. The plan may cover a different period range." |

## Adaptation Patterns

Real customers store plan/actuals data in different shapes. Before binding, inspect the
resolved objects with `INFORMATION_SCHEMA.COLUMNS` and adapt — do not assume one layout.
Use Cortex Sense / `cortex_context_sql` to confirm the actual columns.

| If the data looks like… | Do this |
|---|---|
| **Versioned planning fact** (a `PLANNING_VERSION`/`SCENARIO`/`VERSION` column with many values, table named `*_FORECAST` or `*_PLAN`) | This is the common case. Designate the baseline version (Step 0). Budget and actuals may be the **same table** split by version (e.g. a `value_type`/version where one value is the plan and another is actuals) — filter accordingly. |
| **Composite period key** (`FISCAL_YEAR` TEXT + `MONTH` NUMBER instead of a DATE) | Build a canonical period key on both sides before joining (e.g. `FISCAL_YEAR || '-' || LPAD(MONTH,2,'0')`). Never join a DATE column to a STRING/NUMBER period — it silently returns empty. |
| **Wide / metrics-as-columns budget** (one row per period, line items as columns like `ARR_TARGET`, `OPEX_BUDGET`, `SAAS_COGS`) | `UNPIVOT` to (period, line_item_id, amount) so it can join to a line-item actuals table. If actuals are also wide, unpivot both to the same shape. |
| **No `line_type` (expense vs revenue) anywhere** | Ask the user to classify before computing FAV/UNFAV (see Guardrails). Do not guess. |
| **Separate actuals table per domain** (opex / revenue / headcount, each with its own version column) | An all-in P&L BvA requires UNION/JOIN across the domain pairs at a common (line_item, period) grain. State which domains were included. |
| **One view holds plan, every reforecast, and actuals, plus subtotal rows and annual rollup rows** (a version/source column with dozens of values; line-item names such as "Total …"; a period column that includes a full-year value) | Filter to the baseline and actuals versions, restrict to a single period grain, and clean the line-item set per Step 2.5 before comparing. |
| **Cost-center IDs with placeholder prefixes or multiple snapshots** (e.g. "do not use" markers; a mapping table with many rows per cost center) | Normalize the prefix before joining, keep the original ID for display only, and dedupe the mapping to the latest snapshot per cost center. A point-in-time mapping has about one row per cost center; more than ~2 rows per cost center indicates a history or hierarchy table that needs filtering first. |
| **Multiple candidate tables for the same data** | Prefer curated finance-layer tables over raw or staging ones, and deprioritize dev, test, and personal schemas. Confirm with the user when two candidates are close. |

## Context Queries

- A plan/budget source giving the budgeted amount per (line_item_id, period). This is the
  baseline actuals are compared against, and it can take **either** of two valid shapes:
  (a) a **frozen budget/plan table** with a single fixed version, OR (b) a **versioned
  planning fact** with a version/scenario column, where one version is the locked plan
  (designate it via `budget_version_label` or ask the user — see Step 0). Columns: line
  item identifier (STRING or NUMBER), period (DATE, or a composite like FISCAL_YEAR+MONTH),
  budget/plan amount (NUMBER); optionally a version/scenario column (STRING), line item
  display name (STRING), line item type (STRING — "expense"/"revenue"), and cost
  center/department (STRING). *(critical)*

- An actuals table where each row is one (line_item_id, period) with the actual amount
  (NUMBER). Must share the same line item identifier and period grain as the budget
  baseline for a join (this may be the **same table** as the plan source, separated by a
  version/value-type column). Columns: line item identifier (STRING or NUMBER), period
  (DATE or composite), actual amount (NUMBER). *(critical)*

- (Optional) A context or notes table with one row per line item containing: line item
  identifier, note text or description (STRING), and optionally a date and author. Used
  to enrich commentary for flagged lines. If absent, commentary falls back to: "Context
  data unavailable — manual review required."

- (Optional) A dimension table mapping line item identifiers to display names, line type
  (expense/revenue), cost center name, and owning department. Used for readable output
  and correct favorable/unfavorable direction logic. **Note: FAV/UNFAV direction computation
  depends on the line_type column, which is typically sourced from this table. If this table
  is absent and neither the budget nor actuals table carries a line_type column, the skill
  must prompt the user to classify lines before proceeding.**

- Note on period column: the budget and actuals period columns must share the same type
  and grain. If `period` is a STRING (e.g. `"Q1 FY26"`, `"Jan-26"`) or a composite
  (FISCAL_YEAR + MONTH) rather than a DATE, build a matching key on both sides before
  joining (see Adaptation Patterns) — a type mismatch silently produces an empty join.

- Note on currency: if a currency column is present in either table, filter or group by
  currency. If no currency column exists, note that multi-currency support is not available
  and display in the account's default currency.

- (Optional) A cost-center mapping with cost center ID, name, department, leader, and a
  snapshot timestamp. Used for rollups by org and leader, and for the reconciliation check.

- (Optional) A ledger-account mapping from ledger account to expense category and parent
  category. Used for category rollups and journal-entry drill-down.

- (Optional) General-ledger journal lines (accounting date, ledger account, cost center,
  amount, vendor, spend category, description). Used for the drill-down and for vendor-level
  driver commentary.

- (Optional) A headcount table with period, org, ending headcount, hires, exits, and
  transfers in/out. Used for personnel-cost commentary.

- (Optional) One or more driver tables (vendor, purchase order, project, spend category),
  each with period, driver value, actual, and forecast or plan. Used to rank movers in
  commentary.
