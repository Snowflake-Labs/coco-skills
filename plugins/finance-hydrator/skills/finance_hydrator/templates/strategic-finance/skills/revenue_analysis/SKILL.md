---
name: revenue_analysis
description: >
  Generate a data-backed executive revenue narrative for any level of a product or
  segment hierarchy. Explains what happened (plan performance, growth rates, child
  breakdown), why (customer concentration, cohort analysis, geographic/industry mix),
  and what matters (risks, opportunities, structural outlook). Triggers: revenue
  summary, revenue narrative, revenue analysis, what drove revenue, revenue trends,
  QBR prep, executive summary, quarterly review, monthly revenue, explain the quarter,
  revenue deep dive, product category performance, segment performance, how is revenue
  doing, what is driving growth, revenue breakdown.
dehydrated: true
data_domain: "revenue performance narrative, product hierarchy analysis, customer concentration, lifecycle cohort trends, plan vs actual revenue"
created_date: 2026-05-22
version: 2.1.0
license: Proprietary. See LICENSE.txt for complete terms
---

# Revenue Analysis (Dehydrated)

## Intent

Generate a 300–600 word executive narrative answering "what drove this period's revenue
performance and why?" at any level of a product/segment hierarchy. Serves CFOs, FP&A
leads, and product finance teams preparing for QBRs, board meetings, or recurring
category reviews.

This skill produces revenue narratives. It is NOT for: forecast variance drill-downs
(use `forecast_vs_forecast` or `budget_vs_actuals`), cost analysis (use `cloud_cogs_analysis`),
scenario modeling (use `scenario_planning`), or ad-hoc metric lookups.

## Behavior

### Trigger Detection

Activate when the user asks for any of: "revenue summary", "revenue narrative",
"revenue analysis", "what drove revenue", "explain the quarter/month", "QBR prep",
"executive summary" for a revenue subject, "quarterly/monthly review", "revenue deep
dive", "product category performance", "revenue trends", "how is revenue doing",
"what is driving growth".

Do NOT activate for:
- Forecast vs actual variance analysis (route to `budget_vs_actuals` or `forecast_vs_forecast`)
- Cost or expense analysis (route to `cloud_cogs_analysis`)
- Forward-looking scenarios (route to `scenario_planning`)
- Non-revenue metrics (headcount, pipeline, bookings unless revenue-related)

If ambiguous, ask via `ask_user_question`:
1. Which subject? (total business, a specific product line/category, BU, or segment)
2. Which period? (current quarter, specific quarter, month)
3. Which grain? (quarterly or monthly)

### Workflow

**Step 1 — Resolve subject, period, and hierarchy level**

Identify from the user's request:
- **Subject**: total business, a named product category, business unit, use case, or feature
- **Period**: specific quarter/month, or "latest" (auto-detect most recent with data)
- **Grain**: quarterly (default) or monthly

If not specified, detect the latest available period from the data.

**Step 2 — Check period completeness**

Determine whether the requested period has complete or partial actuals. If partial
(period-to-date), label ALL output with the "through [date]" qualifier. Confirm PTD
status with the user if unexpected. If the source carries an actual/forecast flag, count the
forecast days inside the requested period: a "closed" quarter can still include a few
forecast days at the end. Report the actual-through date and label figures "actuals
through [date] plus forecast to period end" rather than presenting them as final.

**Step 3 — Fetch parent-level data + historical trajectory (parallel)**

Run simultaneously:
- Current period snapshot (revenue, growth rates, plan variance, customer count)
- Last 5 completed periods for trajectory analysis

From the trajectory, classify:
- **Accelerating**: sequential dollar adds growing for 2+ consecutive periods
- **Decelerating**: sequential dollar adds shrinking
- **Base effect**: deceleration in % but stable in dollar adds (size of base, not demand)

*Sequential growth check.* Also compute the actual quarter-over-quarter (or
month-over-month) growth rate for each trajectory period and compare it with the growth
rate **implied by the plan** for the same period. Report the gap in percentage points. A
subject can beat plan in dollars while its sequential growth rate is below what the plan
assumed — that gap is the earlier signal.

**Step 4 — Fetch immediate children**

Retrieve all children one level below the subject, ordered by contribution to
period-over-period movement. Select **material children** with a single rule: a child is
material if it is in the top 3 by revenue **or** its contribution to movement is at least
the contribution threshold (default 10%; configurable). Material children get full
detail in Step 6. All other children are listed in one summary sentence, ordered by
contribution, until the running contribution reaches 60% of total movement.

Skip children below a materiality threshold (default $50K; configurable).

**Step 5 — Fetch lifecycle cohort (parent level)**

Retrieve New / Existing / Churned revenue breakdown for the subject, and split
**Existing** into **expansion** (customers whose revenue grew) and **contraction**
(customers whose revenue shrank but did not churn) when the data supports it. Gross
retention = (prior-period revenue − contraction − churn) / prior-period revenue; net
retention adds expansion.
- Rising churn revenue → retention signal
- Flat/declining Existing revenue → usage contraction (check whether contraction or
  weak expansion is the cause)
- Declining New revenue → slowing adoption

**Step 6 — Per-child detail (parallel, for material children)**

For each material child (selected in Step 4), fetch in parallel:
- Sub-children breakdown (apply the same selection rule within each child)
- Geographic breakdown (flag when plan variance is concentrated in one region)
- Customer health metrics (mean vs median revenue, growth direction diagnostic)

**Step 7 — Customer concentration analysis**

Retrieve concentration tier data:

| Top-10 share | Verdict | Action |
|---|---|---|
| ≥ 50% | Highly concentrated | Name top 3 gainers + top 3 contractors |
| 35–50% | Moderately concentrated | Name top 3 gainers + top 3 contractors |
| < 35% | Broad-based | State verdict only — do not name individuals |

Compute N-for-80%: how many customers drove 80% of period-over-period movement. Also
express N as a share of customers with non-zero movement; the absolute cutoffs below are
defaults for small customer bases and overstate "broad-based" when the base is in the tens
of thousands (a few hundred customers can still be a small slice of the base).
- N ≤ 3 = customer-driven
- N 4–15 = moderately concentrated
- N > 15 = broad-based (feature-driven); if N is under ~5% of active customers, say
  "driven by a small slice of larger accounts" rather than "broad"

Single-account guard: if a child resolves to one or very few customers, its concentration
and skew statistics are meaningless (often an aggregate or internal placeholder account).
Say so and skip the concentration verdict for that child instead of calling it
"highly concentrated".

**Step 8 — Write and present narrative**

> **Stopping point:** Present narrative and ask: "Does this capture the full story? Any
> threads to close or areas to drill deeper?"

### Output Format

The narrative is written in **flowing prose paragraphs** for all sections except Key Takeaway.
No markdown tables or bullet lists within performance paragraphs. Structure:

```
### TLDR (2–3 sentences)
Total revenue, top growth driver, concentration verdict, category archetype
(customer-driven vs feature-driven). Note: TLDR is written last, after Step 7
concentration data is available.

### Topline Paragraph
Revenue performance, trajectory, plan variance, cohort summary, anchor child,
mix shift assessment.

### Per-Child Paragraphs (ordered by revenue descending)
**Child Name** ($revenue, +X% PoP, +/-$X vs plan) — opening phrase.
- What happened (revenue, growth, plan delta)
- Why (feature/customer/geo driver, N-for-80%, concentration)
- Trajectory and structural verdict

### Key Takeaway (3 bullets — bullet format is intentional exception to prose rule)
- Plan driver: which child carries the highest plan contribution
- Structural outlook: accelerating/decelerating trends
- Open thread: one unresolved risk or watch item
```

**Parenthetical rule (mandatory):** Every named entity must include
`($revenue, PoP%, +/-$X vs plan)` on first mention. No exceptions.

**Number formatting:**
- ≥$1B: `$X.XB` | ≥$100M: `$XXXM` | ≥$10M: `$XX.XM` | ≥$1M: `$X.XXM`
- ≥$100K: `$XXXK` | <$100K: `$XX.XK`
- Deltas: `+$X.XM vs plan` or `-$X.XM vs plan`
- Percentages: `+XX.X%`

### Metric Interpretation

The cutoffs below are tunable defaults, not universal rules. Confirm or adjust them for
the customer's business.

**Mean vs Median (skew ratio = mean / median):**
- > 50x: extreme whale-dependency — flag explicitly
- 10–50x: concentrated — mention in context
- < 10x: distributed — no flag needed

**Spending trajectory diagnostic:**
- Mean positive + Median positive = broad expansion, **only if the median change is a
  meaningful fraction of the mean change and of the subject's growth rate**. If the median
  customer grew far less than the total (for example about 2% against 15% overall), say
  growth is concentrated in larger accounts even though both are positive
- Mean positive + Median flat/negative = whale-driven growth
- Mean negative + Median positive = single-name contraction, investigate
- Mean negative + Median negative = broad contraction

### Confidence Handling

| Confidence | Behavior |
|---|---|
| HIGH (revenue binding resolved with hierarchy, period, plan, and customer columns) | Run full 8-step workflow; complete narrative |
| MEDIUM (revenue resolved but customer metrics or hierarchy ambiguous) | Run Steps 1–5; topline + children; skip concentration; note degraded sections |
| LOW on critical (no revenue table or no period/plan columns) | Block. Output: "Revenue analysis requires a table with periodic revenue by hierarchy and plan amounts. Add a binding to hydration_hints.yaml under finance.revenue.hierarchy_summary and re-verify." |
| LOW on non-critical (lifecycle or geo data unavailable) | Skip those sections; produce narrative from available data; note omissions |

### Guardrails

- Never assume a fiscal calendar. Read from plugin config or ask the user.
- Write in flowing prose — no tables of metrics in the narrative body.
- Every named entity gets the parenthetical on first mention. No exceptions.
- Customer names embedded inline within their relevant paragraph — not in a separate section.
- Do not recurse past the lowest hierarchy level into individual customer detail for the standard narrative.
- If a metric doesn't sharpen the story, cut it. Every sentence must earn its place.
- Respect currency from the data. Display in dominant currency; group by currency if multiple present.
- For partial periods: label ALL figures with "through [date]" and note comparisons may shift.
- Unmapped and test buckets: before ranking children or summing to a total, look for rows
  with a blank hierarchy value or a test/placeholder name (for example a bucket that is
  marked for deletion). Show unassigned revenue as its own line, exclude test buckets from
  growth and concentration statements, and say what was excluded. Plan assigned to an
  unmapped bucket makes the rest look slightly under or over plan, so name that effect.
- Plan that mirrors actuals: if plan equals actuals exactly for most children and the
  entire gap sits in one or two rows, plan was probably allocated top-down rather than
  built per child. Report variance at the level where plan is real (usually the total or
  the parent level), and do not call children "on plan" as if that were an independent
  finding.
- Distinguish related revenue measures (for example total revenue vs a sales-led subset)
  and state which one the narrative uses.

### Error Handling

| Scenario | Action |
|---|---|
| Requested subject not found in hierarchy | List available subjects at that level; ask user to pick |
| Revenue values masked/null | Note role may lack unmasked access; suggest required privilege |
| Period has no actuals (future period) | Output: "No actual data for <period>. Latest available: <period>." |
| Zero children at next level (terminal node) | Skip child breakdown; focus on customer and geo analysis |
| Query timeout | Retry with reduced scope (fewer children, skip optional enrichments) |

## Context Queries

- A table where each row is one entity per period at a specific hierarchy level, with
  columns for: hierarchy level indicator (STRING), period identifier (STRING or DATE),
  time grain (STRING — quarterly or monthly), entity name (STRING), actual revenue
  (NUMBER), plan/forecast revenue (NUMBER), period-over-period change in dollars (NUMBER),
  period-over-period change percentage (NUMBER), year-over-year percentage (NUMBER),
  variance to plan in dollars (NUMBER), customer count (NUMBER), mean customer revenue
  (NUMBER), median customer revenue (NUMBER), revenue mix percentage (NUMBER), and mix
  shift (NUMBER). Grain: one row per entity per period per hierarchy level. Primary
  binding for hierarchy navigation, trajectory, and child breakdown. *(critical)*

- A table with lifecycle cohort data where each row is one cohort status (New, Existing,
  Churned) per entity per period, with columns for: lifecycle status (STRING), entity
  name (STRING), period (STRING or DATE), actual revenue (NUMBER), period-over-period
  change (NUMBER), and customer count (NUMBER). Optionally expansion revenue, contraction
  revenue, churned revenue, and prior-period revenue (NUMBER) for the retention split.
  Used for cohort decomposition (Step 5).
  *(non-critical — Step 5 skipped and noted in output if absent)*

- A table with customer-level concentration data where each row is either a concentration
  tier summary or a named top-N customer, with columns for: tier label or customer name
  (STRING), entity/subject context (STRING), period (STRING or DATE), actual revenue
  (NUMBER), period-over-period change (NUMBER), and contribution percentage (NUMBER).
  Used for concentration analysis and N-for-80% calculation (Step 7).
  *(non-critical — Step 7 skipped with MEDIUM confidence note if absent)*

- (Optional) A semantic view over the revenue hierarchy table for Cortex Analyst routing —
  complex follow-up questions ("why did segment X miss?") route through Analyst for
  natural-language drill-down instead of raw queries.
