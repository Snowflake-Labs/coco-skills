---
name: cloud_cogs_analysis
description: >
  Cloud COGS and gross margin analysis. Breaks down cloud infrastructure cost of goods
  sold by cloud provider, product or feature, and cost category (compute, storage, data
  transfer, AI/GPU). Surfaces gross margin trends, unit economics (cost per consumption
  unit, cost per TB), committed-spend and discount impact, actuals vs forecast, and a
  period-over-period margin bridge. Triggers: COGS, cloud COGS, gross margin, cost of
  goods, cloud cost, compute cost, storage cost, cost per unit, cost per TB, unit
  economics, infrastructure cost, margin trend, what is driving COGS, margin compression,
  margin expansion, cloud billing, cost breakdown, net vs list cost, committed spend,
  enterprise discount, savings plan, reserved instance, commitment, GPU cost,
  AI cost, actual vs forecast COGS, month-to-date pace, provider mix.
dehydrated: true
data_domain: "cloud COGS analysis, gross margin trends, cloud infrastructure costs, committed spend and discounts, unit economics, cost-per-unit attribution, actuals vs forecast, period-over-period margin bridge"
created_date: 2026-06-08
version: 2.0.0
binding_contract: hydration_hints.yaml
license: Proprietary. See LICENSE.txt for complete terms
---

# Cloud COGS Analysis (Dehydrated)

## Intent

Break down cloud cost of goods sold and gross margin for any period or dimension.
Surfaces where costs are growing, which providers or product lines are driving changes,
how committed spend and discounts affect net cost, and what the trend looks like in unit
terms (cost per consumption unit, per TB, per compute hour).

Serves: product finance, FP&A, CFO office, and FinOps analysts who own the gross margin
line or the infrastructure cost narrative.

Not for: general operating expenses (T&E, headcount — use `budget_vs_actuals`), revenue
performance (use `revenue_analysis`), forward-looking scenario modeling (use
`scenario_planning`), cloud cost optimization and waste hunting (use a FinOps tool), or
contract negotiation modeling beyond a read-only summary of existing commitments.

> This skill was previously named `cogs_analysis`. Treat that phrase as a trigger alias.

## Behavior

### Trigger Detection

Activate on: "COGS", "cloud COGS", "gross margin", "cost of goods", "infrastructure
cost", "cloud cost", "compute cost", "storage cost", "cost per unit", "cost per TB",
"unit economics", "margin trend", "what is driving COGS", "margin compression", "margin
expansion", "cloud billing", "cost breakdown", "cost analysis", "net vs list cost",
"committed spend", "enterprise discount", "cloud commitment", "GPU cost", "AI cost",
"actual vs forecast COGS", "month-to-date pace", "provider mix", "WoW cost", "MoM cost".

Do NOT activate for:
- T&E, vendor, or opex analysis (route to `budget_vs_actuals`)
- Revenue or plan performance (route to `revenue_analysis`)
- Cloud cost optimization or waste analysis (route to a FinOps tool)
- Forward-looking cost scenarios (route to `scenario_planning`)

If the user doesn't specify a period or dimension, ask via `ask_user_question`:
1. Period: current quarter, trailing 12 months, or a specific quarter/month?
2. Dimension: total COGS, by cloud provider, by product/feature, or by cost category?

### Workflow

**Step 1 — Resolve scope**

Identify:
- **Mode**: *Actuals* (default), *Actual vs forecast*, or *Month-to-date pace* (an
  in-progress month paced to month-end).
- **Period**: specified by user or default to the current completed fiscal quarter vs the
  prior quarter. Confirm the fiscal calendar; fiscal quarters may not match calendar
  quarters.
- **Dimension**: primary breakdown — cloud provider, product/feature, cost category
  (compute, storage, data transfer, AI/GPU, support, marketplace), region, or deployment
  type. If unspecified, use whatever dimension the data naturally supports.
- **Cost basis**: *list* (before discounts), *net of discounts*, or *amortized with
  commitments*. Default to the basis the customer's reported COGS uses; state it in the
  output header.
- **Scope of cost layers**: direct cloud cost, AI/GPU cost (separate line), shared
  overhead, and manual adjustments (journal entries). Confirm which are in COGS.
- **Comparison baseline**: prior period (QoQ default) or prior year (YoY if specified).

**Step 1.25 — Resolve the data version and the actual/forecast boundary**

Some COGS sources hold several forecast versions in one table, with periods and amounts
stored as text. Before fetching:
- Cast period and amount columns explicitly. Never sort or take a "latest" version on text
  values.
- Filter to exactly one version (the latest, unless the user names one), or amounts
  multiply across versions.
- Find which periods are actuals. If the source has no actual/forecast flag, infer it:
  periods whose values are identical across consecutive versions are locked actuals;
  periods that differ between versions are still forecast. Confirm the inferred cutoff
  with the user before relying on it, and label it as inferred.
- State the latest period with data in the output header. If it is well behind other
  finance data in the same account, say so.

**Step 1.5 — Check period completeness**

Before fetching data, determine whether the requested period has complete or partial
actuals. If partial (period-to-date), label ALL output "through [date]" and note that
comparing a partial period against a full prior period makes **cost look artificially
favorable (lower)** and margin look artificially higher. In Month-to-date pace mode, pace
actuals to month-end using daily actuals; if daily actuals are unavailable, fall back to
flat pacing and say so.

**Step 2 — Fetch in parallel (Q1, Q2, Q3), then Q4–Q6**

Run Q1, Q2, Q3 simultaneously. Q4 reuses Q2 results (and uses category data as a fallback
when no line-item table exists). Q5 and Q6 run only when their data is bound.

| Query | What it computes | Dependency |
|---|---|---|
| Q1 — Gross margin summary | Revenue, COGS, gross profit, and gross margin % for current + prior period | None |
| Q2 — COGS by dimension | Current period COGS split by the resolved dimension, ordered by amount descending. Show an `Unallocated / Shared` bucket and manual adjustments as separate rows | None |
| Q3 — Unit economics | Cost per unit (consumption unit / TB / compute hour) — current vs prior period | None |
| Q4 — Margin bridge | Which categories or items drove the dollar change in COGS and the change in margin | Q2 (category fallback if line-item table absent) |
| Q5 — Commitment & discount | Effective (cost-weighted) discount rate by discount category (for example savings plan, reserved instance, other), and commitment consumed vs commitment amount by provider and contract term | Commitment data |
| Q6 — Actual vs forecast | Actuals vs the latest published forecast for the same period, by dimension | Forecast data |

**Step 3 — Synthesize and present**

Deliver the sections in order. After the initial report, offer:
"Want me to drill into [highest-variance category]?" — do not run unsolicited.

### Calculation Rules

- **Unit cost** = SUM(cost) / SUM(volume) across the selected scope. Never average
  per-feature or per-provider rates. If cost is missing and volume is explicitly zero,
  the rollup still works; if cost is missing and volume is nonzero or unknown, the rate is
  unavailable. A non-positive total volume also makes the rate unavailable. Do not fill
  missing cost or volume with zero when computing a unit cost. Show rates to 4 decimals
  and dollar totals to 2. Unit costs are context only: they have no separate driver
  amount in the margin bridge.
- **Missing cells in the margin bridge**: if a monthly pivot cell is missing, state that it
  was treated as zero and identify the substitution. Do not silently fill values.
- **Margin bridge** (arithmetic, not causal — say so in the output). Explain the change in
  COGS between a baseline period (1) and a comparison period (2) with two terms that add up
  exactly, with clear signs:
  - **Volume effect** = (comparison revenue − baseline revenue) × baseline COGS-to-revenue
    ratio. This is the COGS growth you would expect from revenue growth alone, at the
    baseline cost ratio.
  - **Cost-ratio effect** = (comparison COGS-to-revenue ratio − baseline COGS-to-revenue
    ratio) × comparison revenue. A negative value means COGS grew less than revenue (a
    margin improvement); a positive value means margin compression.
  - Check: volume effect + cost-ratio effect = COGS change. State the sign convention in
    the output ("negative = margin improvement"), because a positive "margin" label next to
    a COGS increase is easy to misread.
- **Margin excluding adjustments.** Also compute gross margin on gross cost, i.e. before
  credits, reclasses, true-ups and other manual adjustments. Report it beside the net
  margin. If the two move in opposite directions, say so first: a margin improvement that
  comes only from larger credits or reclasses is not an efficiency gain.
- **Blended discount rate** = SUM(discount $) / SUM(list cost $) across the scope, i.e.
  cost-weighted, never a simple average of discount percentages.
- **Changes** are always To minus From; growth uses the From value as denominator.
- **Actual vs forecast**: compare actuals to the forecast for the same period from a
  specific published forecast version, and label the version. Cross-period comparisons are
  labeled as such and are not budget variances.

### Output Format

```
# Cloud COGS Analysis — <Period>
Cost basis: <list | net of discounts | amortized> | Mode: <actuals | actual vs forecast | month-to-date pace>

## Gross Margin Summary
| Metric | <Current Period> | <Prior Period> | Change |
|---|---|---|---|
| Revenue | $XXM | $XXM | +X.X% |
| COGS | $XXM | $XXM | +X.X% |
| Gross Profit | $XXM | $XXM | +X.X% |
| Gross Margin % | XX.X% | XX.X% | +/-X.Xpp |
| Gross Margin % excl. credits and adjustments | XX.X% | XX.X% | +/-X.Xpp |

<1–2 sentence commentary: favorable/unfavorable, what drove the margin change. If the net
margin and the margin excluding adjustments move in different directions, lead with that.>

## COGS by <Dimension>
| <Category> | Amount | % of gross cost | vs Prior Period |
|---|---|---|---|
| ... | $XXM | XX% | +/-X.X% |
| Unallocated / Shared | $XXM | XX% | +/-X.X% |
| **Gross cost** | $XXM | | +/-X.X% |
| Credits, reclasses and adjustments | -$XXM | | +/-$XXM |
| **Net COGS** | $XXM | | +/-X.X% |

<1–2 sentence commentary on the highest-growth category>

## Unit Economics
| Unit Type | <Current Period> | <Prior Period> | Change |
|---|---|---|---|
| Cost per <consumption unit> | $X.XXXX | $X.XXXX | +/-X.X% |
| Cost per TB | $X.XXXX | $X.XXXX | +/-X.X% |

<Note: only include unit types that resolve from the data>

## Margin Bridge (arithmetic, not causal; negative cost-ratio effect = margin improvement)
Top drivers of the $XXM change in COGS:
1. <Category / Provider> — +/-$XXM (+/-X.X%): <1-sentence reason if context available>
2. ...
Volume effect (revenue growth at the prior cost ratio): +/-$XXM | Cost-ratio effect: +/-$XXM

## Commitment & Discount  (only if commitment data is bound)
| Provider | Effective discount | Committed amount | Consumed to date | Contract term |
|---|---|---|---|---|
| ... | XX.X% | $XXM | $XXM | XX months |

## Actual vs Forecast  (only in that mode)
| <Category> | Actual | Forecast (<version>) | Variance | Variance % |
|---|---|---|---|---|

---
Period: <start> to <end> | Comparison: <QoQ or YoY> | Data coverage: <sources included / missing>
```

**Number formatting:** `$X.XB`, `$XXXM`, `$XX.XM` — consistent with revenue skills.
Margin percentages to one decimal place (`XX.X%`). Points of margin change as `+/-X.Xpp`.

### Confidence Handling

| Confidence | Behavior |
|---|---|
| HIGH (COGS summary, dimension breakdown, and unit cost all resolved) | Run the full workflow; complete report |
| MEDIUM (COGS total resolves but dimension breakdown or unit costs ambiguous) | Run Q1 and Q4; skip Q2/Q3; note "dimension breakdown unavailable — verify COGS table has a category column" |
| LOW on critical (no COGS table found) | Block. Output: "Cloud COGS analysis requires a table with periodic cost amounts. Add a binding under finance.cogs.summary in hydration_hints.yaml and re-verify." |
| LOW on non-critical (unit economics unavailable) | Skip Q3; note "unit cost data not found — unit economics section omitted"; run the rest |
| LOW on non-critical (commitment or forecast data missing) | Skip Q5 and/or Q6; note which section was omitted and why |
| Margin data restricted by access policy | Produce COGS-only output; note that margin is unavailable for the caller's role |

### Guardrails

- Never confuse COGS with operating expenses. COGS is cost of delivering the product;
  opex (S&M, R&D, G&A) is not in scope for this skill.
- Double-counting guard: if the COGS table contains both summary rows and detail rows for
  the same period (e.g., a "Total" row plus category rows that sum to it), aggregate only
  at one grain level. Prefer category-level rows; exclude any row labeled "Total" or with
  a null category identifier. Include manual journal adjustments **once**, never also as a
  "Total" row, and never plug a residual "Other" bar into the COGS total.
- Overlapping category families: a source may carry a parent category and a drill-down
  family that does not nest cleanly (for example a `COGS` family and a `COGS Subcategory`
  family, or rows with a blank category). Before summing, check which family adds up to
  the reported COGS total and use only that one. Treat the other as a drill-down, never
  add it. Likewise, build revenue from the lines that add up to the reported total revenue
  row, and verify the tie-out for both COGS and revenue before presenting anything.
- Shared overhead with no revenue of its own (e.g., a shared container or platform layer)
  is shown cost-only. Do not invent revenue or margin for it.
- Separate charge types before summing: usage, discount, credit, tax, support, and
  marketplace lines can distort list-vs-net comparisons. State how each was treated.
- Partial period: if the current period is incomplete (PTD), label all figures
  "through [date]" and apply the Step 1.5 rule — a partial period against a full period
  makes cost look favorable, not unfavorable.
- Never assume unit types — derive from whatever cost/quantity columns exist in the
  binding. Report AI/GPU and warehouse/compute rates separately when both exist.
- All amounts in USD by default. If multiple currencies are present, group by currency
  and note.
- Gross margin % = (Revenue - COGS) / Revenue. Verify this matches the customer's
  definition before using — some use a different COGS scope.
- Do not expose raw cloud billing IDs, SKU codes, account numbers, or internal cost center
  IDs in output.
- Period over period: always state the comparison direction explicitly (QoQ, YoY, or
  WoW). If margin is improving, say "favorable"; if worsening, say "unfavorable" — be
  direct.
- Selected comparison endpoints must exist and differ. Never silently fall back to a
  different period. A period being available does not mean every provider or metric in it
  is complete — show source coverage warnings rather than hiding them.
- Preserve source NULLs in unit-cost inputs; do not coerce them to zero.

### Error Handling

| Scenario | Action |
|---|---|
| COGS binding missing | Block; output setup instructions |
| Revenue binding missing (needed for margin %) | Compute COGS breakdown only; note gross margin unavailable |
| All unit cost columns null | Skip unit economics section; note omission |
| User asks for a provider not in the data | List available providers; ask user to pick |
| Requested period has no data | Output: "No COGS data for <period>. Latest available: <period>." |
| No forecast version found for the requested period | Skip Q6; list available forecast versions |
| Commitment term dates do not cover the period | Skip Q5 for that provider; note the gap |

## Context Queries

- A COGS summary table where each row is one (period, cost_category) combination, with
  columns for: period identifier (STRING or DATE), cost category or label (STRING), actual
  cost amount (NUMBER), and optionally cloud provider (STRING), region, and product/feature
  for breakdowns. Used for Q1 (gross margin summary) and Q2 (COGS by dimension).
  *(critical)*

- A revenue summary table where each row is one period with total revenue (NUMBER). Used
  to compute gross profit and gross margin % alongside COGS. May be the same table as the
  revenue hierarchy binding if already resolved. *(non-critical — gross margin % is
  unavailable if absent; COGS breakdown and unit economics still run)*

- A unit economics table where each row is one (period, unit_type) with quantity consumed
  (NUMBER) and cost (NUMBER), enabling cost-per-unit derivation. Unit types might include
  consumption units, terabytes, compute hours, or API calls depending on the customer's
  product. Used for Q3. *(non-critical — section skipped if absent)*

- (Optional) A line-item or SKU-level cloud cost detail table for Q4 attribution — one row
  per (period, provider, service/SKU, cost category) with list, net, and/or amortized cost,
  usage quantity and unit, and a charge-type column (usage, discount, credit, tax, support,
  marketplace). If absent, Q4 attribution is computed at the category level from Q2 data.

- (Optional) A cloud commitment table — one row per (provider, contract, period) with
  commitment amount, consumed amount, term start/end, and discount percentage.
  Used for Q5.

- (Optional) A discount schedule — one row per (provider, SKU or category, discount type)
  with discount percentage and a cost weight. Used for blended discount rates.

- (Optional) A separate AI/GPU cost table and a manual-adjustments (journal entry) table,
  when these are not already inside the COGS summary.

- (Optional) A published forecast table — one row per (forecast version, period, category,
  provider, feature) with forecast amount. Actual months precede the version's run date
  and forecast months follow it. Used for Q6 and Month-to-date pace.

- (Optional) A fiscal calendar mapping periods to fiscal year and quarter.
