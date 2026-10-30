---
name: scenario_planning
description: >
  Scenario-based financial planning and P&L impact analysis. Helps FP&A teams answer
  "what if?" questions by finding similar historical scenarios, adjusting assumptions,
  and showing downstream P&L impact. Also projects revenue from leading drivers and
  decomposes P&L movements into root cause drivers. Triggers: what if, scenario planning,
  sensitivity analysis, what would happen, P&L impact, why did margin change, what is
  driving revenue, root cause, decompose, adjust assumptions, model a scenario, driver-based
  forecast, lead-lag, low base high case.
dehydrated: true
data_domain: "financial planning scenarios, P&L sensitivity analysis, what-if modeling, forecast assumption adjustment, root cause decomposition of P&L movements"
mcp_optional: []
created_date: 2026-05-21
version: 1.1.0
license: Proprietary. See LICENSE.txt for complete terms
---

# Financial Planning Scenarios (Dehydrated)

## Intent

Enable FP&A teams to explore "what if?" questions against their financial model. Four capabilities: (1) find similar historical scenarios and recommend assumption changes, (2) show before/after P&L impact when assumptions are adjusted, (3) explain WHY a P&L metric changed by decomposing into drivers, and (4) project revenue from leading usage or volume drivers.

For: FP&A analysts, finance business partners, CFO office doing scenario planning, budget owners exploring trade-offs.
Not for: actual data entry into planning tools (this skill recommends and analyzes, not writes), daily revenue monitoring (use briefing skills), or cost optimization (use cost intelligence skills).

## Behavior

### Trigger Detection

Activate on: "what if", "what would happen if", "scenario", "model a scenario", "simulate", "sensitivity analysis", "P&L impact", "adjust assumptions", "what drives", "why did [metric] change", "root cause", "decompose", "explain this movement", "what is driving", "margin expansion", "margin compression", "why is [metric] up/down".

### Mode Selection

Classify the user request into one of four modes:

| Mode | Trigger Signals |
|---|---|
| **What-If Scenario Modeling** | "what if we...", "what would happen if...", "model a scenario where...", "simulate", "explore scenario" |
| **Sensitivity Analysis** | "show me the impact", "before and after", "P&L impact of changing...", "sensitivity", "what's the effect on margin" |
| **Root Cause Analysis** | "why did [metric] change", "what is driving", "explain this movement", "decompose", "root cause" |
| **Driver-Based Projection** | "if usage grows X%, what happens to revenue", "driver-based forecast", "lead-lag", "leading indicator", "low / base / high case" |

If ambiguous, ask the user which mode via `ask_user_question`.

### Baseline Run-Rate (required before any scenario)

Every scenario is a delta against a defined base case. Before running Mode 2 or Mode 4,
establish and label the baseline:
- **Current forecast** (default): the latest published forecast version.
- **No-growth run-rate**: the latest actual held flat, as a naive floor.
- **Trend baseline**: a simple trailing-growth extrapolation, when the customer wants one.

State which baseline is used in the output header. If the baseline cannot be resolved,
stop and ask rather than comparing against an unlabeled number.

### Mode 1: What-If Scenario Modeling

**Purpose:** User asks a hypothetical ("what if we hire 50 more people?" / "what if we expand into EMEA?"). The skill finds similar historical scenarios via semantic matching, identifies what assumptions were adjusted, and recommends changes.

**Workflow:**

1. **Capture user intent.** Parse the what-if question into a searchable concept (e.g., "open new office" → facilities expansion, capex increase).

2. **Semantic search against historical scenarios.**
   - Query the scenario notes/metadata repository for all historical planning scenarios with descriptions.
   - Use semantic matching (LLM-based) to find scenarios whose notes are similar to the user's question. This match is not a SQL lookup and is not perfectly reproducible; say so if the user asks why a result changed between runs.
   - Rank by relevance. If no match found, acknowledge and suggest which planning domains might be relevant.
   - If no scenario-notes table exists, skip this mode and ask the user to describe the adjustment manually (Mode 2).

3. **Find adjustments in the matched scenario.**
   - For the top-matched historical scenario, compare assumption values against known defaults.
   - Any value that differs from the default represents a deliberate adjustment.
   - Catalog: which domain was adjusted, which assumption, what was the delta.

4. **Present recommendation (adjustments only — NOT P&L impact).**
   - Show what was adjusted in the historical scenario as a recommendation.
   - Do NOT show the historical scenario's P&L impact — it was computed against a different baseline and would be misleading.
   - Offer: "Would you like me to apply these adjustments to your current forecast and show the P&L impact?"

5. **If user opts in → route to Mode 2 (Sensitivity Analysis).**

**Critical rule:** Never show P&L impact from a historical scenario. P&L impact is only meaningful after the adjustment is applied to the user's own current forecast baseline.

### Mode 2: Sensitivity Analysis

**Purpose:** Show before/after impact on key P&L metrics when assumptions change. Captures baseline, applies adjustment, shows delta.

**Workflow:**

1. **Identify what is being adjusted.**
   - Could come from Mode 1 recommendation, or user directly specifies ("show me what happens if I increase headcount growth by 10%").
   - Resolve: which planning domain, which assumption parameter, what magnitude of change.

2. **Capture baseline.**
   - Snapshot the current state of key P&L metrics before the change.
   - Key metrics: operating income, operating margin, net income (or whichever P&L metrics are configured as highlights).

3. **Apply the adjustment.**
   - Modify the assumption in the planning model.
   - Trigger downstream recomputation (if the model supports cascading recalculation).

4. **Capture post-adjustment state.**
   - Snapshot the same P&L metrics after recomputation.

5. **Present impact summary.**
   - Show a before/after/delta table for each highlighted metric across the forecast horizon.
   - Where the customer supplies or agrees to a range, show **low / base / high** cases rather than a single point, and state what differs between them. Do not invent ranges; if none is given, ask for the low and high assumption values.
   - Annotate whether the change is favorable or unfavorable.
   - Note any secondary effects (e.g., headcount change affects both OPEX and revenue capacity).

**Output Format:**
```
# Sensitivity Analysis — <Adjustment Description>

**Adjustment:** <domain> / <assumption> changed by <delta>

## P&L Impact

| Metric | Period 1 Before | Period 1 After | Period 1 Delta | Period 2 Before | ...
|--------|----------------|---------------|----------------|----------------|
| Operating Income | <amount> | <amount> | <+/-amount> | ... |
| Operating Margin | <pct> | <pct> | <+/-pp> | ... |
| Net Income | <amount> | <amount> | <+/-amount> | ... |

## Secondary Effects
- <bullet describing cascading impact if any>

## Recommendation
- <brief assessment: is this change favorable? What trade-offs exist?>
```

### Mode 3: Root Cause Analysis

**Purpose:** Explain WHY a P&L metric changed by decomposing into component drivers. Goes beyond "what changed" to explain the business mechanism.

**Workflow:**

1. **Verify the user's claim.**
   - Before decomposing, check whether the stated observation is accurate.
   - Example: user says "revenue is decreasing" but data shows revenue is growing while growth rate is decelerating. Clarify before proceeding.
   - If the claim doesn't match data, state what the data shows and ask for confirmation.

2. **Identify the metric and period.**
   - Which P&L line is the user asking about? (revenue, OPEX, operating income, margin, specific expense category)
   - What time period or comparison (YoY, QoQ, vs forecast, vs prior plan)?

3. **Route to the appropriate decomposition:**

   | Metric | Decomposition Approach |
   |---|---|
   | Revenue | Split into base forecast + adjustments. Decompose base by customer cohort (acquisition year) and region. Identify whether growth change is driven by mature cohort deceleration, new cohort ramp, geographic mix, or one-time adjustments. |
   | OPEX (R&D, Sales, Marketing, G&A) | Decompose into headcount × cost-per-head. Identify whether expense growth is driven by hiring (volume) or compensation/overhead inflation (rate). Break down by cost center if available. |
   | Operating Income / Margin | Margin decomposition: revenue growth rate vs OPEX growth rate. Is margin expanding due to operating leverage (revenue outpacing costs) or compressing due to investment? |
   | Stock-Based Compensation | Ratio to revenue trend. Driven by grant timing, vesting schedules, or competitive compensation pressure? |

4. **Present Key Drivers (causal, not observational).**
   - Lead with a one-sentence causal answer (WHY, not WHAT).
   - Top 5 drivers ranked by dollar impact.
   - Each driver must explain the root cause, not just restate the data point.
   - Include direction indicators and dollar contribution.

5. **Offer to expand.**
   - Ask if user wants detailed breakout tables (by cohort, region, cost center, etc.).
   - Run queries upfront so expansion is instant, but don't show tables in initial response.

**Key Principle — Causal vs Observational:**
- Observational (bad): "Mature cohorts contribute $X.XB of revenue but growth is decelerating"
- Causal (good): "Mature cohorts are decelerating because their deployed workloads are approaching steady-state consumption — there's a natural ceiling on expansion from the same installed base"

Every driver must trace to a business mechanism: cohort maturity, net retention compression, sales capacity constraints, pricing dynamics, geographic expansion timing, headcount ramp lag, etc.

### Mode 4: Driver-Based Projection

**Purpose:** Project revenue from leading usage or volume drivers instead of editing a P&L line directly. Useful when revenue is consumption-based or volume-driven and a change in a driver propagates to revenue with a delay.

**Workflow:**

1. **Identify the driver and the revenue line it feeds.** Where the data supports it, express revenue as an identity: revenue = volume × unit economics (for example price per unit, units per volume, units per job). Drivers such as ingested, processed or stored volume each feed this identity, and each lever can be overridden independently while the identity still reconciles. Confirm the driver with the user.
2. **Establish the baseline run-rate** (see above). Unit-economics ratios that move slowly can be held flat at a trailing, outlier-clipped moving average; volumes can use a time-series forecast or an existing production forecast.
3. **Estimate cross-product lead-lag effects where history allows:** for each dollar of incremental revenue in one product or use case this period, how many incremental dollars in another product follow next period. Normalize changes by prior-period account revenue, control for account growth trend, size, age and quarter, and shrink noisy estimates toward their parent-category estimates. Report the estimate with its uncertainty. If a lag or elasticity cannot be estimated, ask the user to supply the assumption instead.
4. **Apply the driver change** and propagate it through the identity and any estimated lag to revenue. Show low / base / high cases when the estimate is uncertain. When unit economics are trending (for example unit revenue falling while volume grows), the baseline is as sensitive to the unit assumption as to the volume change: build the three cases from the unit assumption (continue the recent trend, hold the last 4-period average, hold the longer average) and report the baseline range as well as the driver delta, because the delta can look stable across cases while the baseline level differs materially.
5. **Present** the projected revenue delta by period against the baseline, the assumptions used (driver, lag, elasticity), and a clear label that the result is a model projection, not a forecast of record.

If history is too short to estimate a lag (fewer than roughly eight periods), say so and fall back to Mode 2 with a user-supplied assumption.

### Confidence Handling

| Confidence | Behavior |
|---|---|
| HIGH (forecast model, scenario history, and P&L tables all resolved) | Full what-if, sensitivity, and root cause capabilities |
| MEDIUM (P&L data available but no scenario history, OR the scenario-notes table exists but is **empty**) | Modes 2 and 3 only; Mode 1 unavailable — tell the user: "No populated historical-scenario repository found, so I can't match against past what-ifs. I can still model a scenario directly (Mode 2) or decompose a movement (Mode 3) — which would you like?" |
| MEDIUM (P&L resolves only in **wide** format, or only at **annual** grain) | Run normally after adapting (UNPIVOT wide→long; see Adaptation Patterns). For annual-only P&L, note that sensitivity is at annual granularity (no intra-year/monthly precision). |
| MEDIUM (P&L data available but no cascading recomputation) | Mode 2 shows static delta only (no downstream effects); Mode 3 works fully |
| MEDIUM (assumptions fragmented across per-domain tables, defaults missing for some domains) | Mode 1 step-3 adjustment detection works per domain where defaults exist; for domains without a defaults table, note "no default baseline found for <domain> — showing the assumption value without a delta-vs-default." |
| LOW on critical (no P&L or forecast data found) | Do not run. Output guidance on what planning data to land. |

### Output Format

**Mode 1 output:**
```
# Scenario Match — "<User's Question>"

**Matched historical scenario:** <scenario name/ID>
**Relevance:** <brief explanation of why this matched>

## Recommended Adjustments
| Domain | Assumption | Change |
|--------|-----------|--------|
| <domain> | <assumption name> | <delta description> |

Would you like me to apply these to your current forecast and show the P&L impact?
```

**Mode 3 output:**
```
# Root Cause Analysis — <Metric>, <Period>

**Summary:** <One-sentence causal answer>

## Key Drivers (ranked by impact)

1. **<Driver name>** — <dollar amount> <direction>
   <Root cause explanation — WHY this is happening>

2. **<Driver name>** — <dollar amount> <direction>
   <Root cause explanation>
...

## How Root Cause is Identified
<One-sentence decomposition approach>
`Formula = Component A + Component B`
- **Component A** = definition
- **Component B** = definition

---
Want to see the detailed breakout by <cohort / region / cost center>?
```

### Guardrails

- Never show P&L impact from a historical scenario — only show impact after adjustments are applied to the user's own forecast.
- Always verify the user's claim before running root cause analysis. Do not assume their observation is correct.
- Root cause drivers must be causal, not observational. Every driver must explain WHY, not just WHAT.
- Do not hardcode planning domains, assumption names, or default values. Discover them from the resolved data.
- Fiscal calendar: never assume period boundaries. Read from configured calendar or metadata.
- Number formatting: use abbreviated format ($1.14B, $143.8M). Use B for billions, M for millions.
- When presenting adjustments, use the actual assumption parameter names from the model — do not invent friendly names that don't map to real inputs.
- If no historical scenario matches the user's question, acknowledge it honestly and suggest relevant domains rather than forcing a bad match.

### Error Handling

| Scenario | Action |
|---|---|
| No matching historical scenario found (Mode 1) | Acknowledge; suggest which planning domains might be relevant based on keywords |
| User's stated observation doesn't match data (Mode 3) | State what data shows; ask for confirmation before proceeding |
| Forecast model doesn't support cascading recomputation (Mode 2) | Show static before/after for the directly adjusted metric only; note downstream effects are estimated |
| Multiple scenarios match equally well (Mode 1) | Present top 2-3 matches with brief descriptions; let user choose |
| Planning data only covers partial forecast horizon | Note the coverage gap; analyze only periods with data |
| Adjustment domain is ambiguous | Ask user to clarify which planning domain and parameter to adjust |

## Adaptation Patterns

Planning data varies enormously across customers. Inspect the resolved objects with
`INFORMATION_SCHEMA.COLUMNS` and adapt — do not assume a single canonical layout. Use
Cortex Sense / `cortex_context_sql` to confirm the actual columns before building queries.

| If the data looks like… | Do this |
|---|---|
| **Wide P&L** (one row per period/version with metrics as columns: `TOTAL_REVENUE`, `TOTAL_COGS`, `GROSS_PROFIT`, `OPERATING_INCOME`, `OPERATING_MARGIN`, …) — the common case for planning-tool exports | `UNPIVOT` the metric columns into (period, metric_name, value) so before/after and decomposition logic can operate row-wise. The skill does not require a physically tall table. |
| **Annual-grain P&L only** (`FISCAL_YEAR`, no month/quarter) | Run sensitivity at annual granularity. State the limitation: no intra-year shape. If monthly P&L exists elsewhere (e.g. a GL-account-level monthly table), offer to aggregate it up for finer analysis. |
| **Per-entity P&L rows** (one row per legal entity / BU) | Aggregate to a consolidated P&L (SUM across entities) unless the user scopes to one entity. |
| **Assumptions fragmented by domain** (separate tables for headcount growth, OPEX growth, revenue, CPH — no single unified registry) | Treat each as its own assumptions source keyed by (version, parameter, period). There is usually NO master assumptions table — do not block on finding one. |
| **Defaults missing for some domains** | Mode 1 step-3 (value-vs-default delta) only works where a defaults table exists. For domains without one, show the assumption value and note the delta-vs-default is unavailable. |
| **Scenario identifier = the version string** (e.g. `FORECAST_VERSION = "<cycle> <scenario name>"`) | Use the version string as the natural join key across P&L, assumptions, and notes — there may be no separate `scenario_id`. |
| **Scenario-notes table empty or absent** | Mode 1 (semantic matching against history) is unavailable. Say so and offer Modes 2/3 (see Confidence Handling). Never fabricate historical scenarios. |

## Context Queries

- A P&L summary giving the dollar amount (or percentage) for each P&L line item
  (revenue, COGS, gross profit, OPEX by category, operating income, net income, margins)
  per fiscal period — used as the sensitivity before/after baseline and the root-cause
  decomposition baseline. **Either shape is valid:** a **long** table of (period,
  metric_name, value), OR a **wide** table with metrics as columns (UNPIVOT it — see
  Adaptation Patterns). Grain may be monthly, quarterly, or annual; per-entity rows are
  aggregated to a consolidated P&L. *(critical)*

- One or more forecast-assumptions sources giving assumption values per (forecast_version,
  assumption_parameter, period), and — where available — a default/baseline value for each.
  Used for Mode 1 adjustment detection (current vs default) and Mode 2 sensitivity input.
  **Assumptions are commonly fragmented across per-domain tables** (headcount, OPEX,
  revenue, CPH) with no unified registry, and defaults may live in a separate table or be
  absent for some domains — both are handled (see Adaptation Patterns). *(critical)*

- (Optional) A scenario notes/metadata table with one row per historical forecast scenario
  containing a scenario identifier (often the version string itself), description or notes
  text, and creation date — used for Mode 1 semantic matching against historical what-if
  scenarios. **If absent OR empty, Mode 1 is unavailable** and the skill offers Modes 2/3.

- (Optional) A revenue detail table with one row per (period, customer_cohort, region)
  containing revenue amount — used for Mode 3 revenue root cause decomposition by cohort
  and geography.

- (Optional) A headcount and cost-per-head table with one row per (period, cost_center)
  containing headcount count and average cost per head — used for Mode 3 OPEX root cause
  decomposition into volume (headcount) vs rate (cost-per-head) drivers.

- (Optional) A driver table with one row per (period, driver name) containing the driver value, and optionally a lead/lag in periods and an elasticity estimate — used for Mode 4. If absent, Mode 4 estimates lag and elasticity from the revenue and usage history, or falls back to Mode 2.

- (Optional) A semantic view over the P&L data for Cortex Analyst Priority 0 routing —
  if present, follow-up questions like "break down OPEX by cost center" route through
  Analyst for natural-language drill-down. If no P&L semantic view exists, the skill
  queries the base tables directly (Analyst routing is a convenience, not a requirement).
