# Query Playbook — Financial Planning Scenarios

This file keeps durable query patterns out of `SKILL.md`. It describes the query
types and logic patterns the skill uses — no SQL, no FQNs. Cortex Code resolves
physical sources and columns from `hydration_hints.yaml` during plugin hydration/rehydration.

---

## Data Shape (resolved at runtime)

**Primary data needs Cortex Code must resolve:**
- `finance.scenario.pnl` — P&L summary (GAAP and/or non-GAAP metrics by fiscal period)
- `finance.scenario.assumptions` — forecast assumptions and defaults per planning domain
- `finance.scenario.notes` — optional scenario notes/metadata (historical scenario descriptions)
- `finance.scenario.revenue_detail` — optional revenue detail by cohort and region
- `finance.scenario.headcount_cph` — optional headcount and cost-per-head by cost center

---

## Mode 1 Queries: What-If Scenario Modeling

### Query 1.1 — Retrieve Historical Scenario Notes

**Purpose**: Get all historical scenario descriptions for semantic matching.

**Logic**:
1. Query the scenario notes table for all entries with non-empty description text
2. Return: scenario identifier, description/notes, creation date, forecast version
3. Order by most recent first

**Output grain**: one row per historical scenario with its metadata

**Pitfalls**:
- Notes may exist across multiple environments (dev/prod) — union both if available
- Empty or null notes should be excluded from matching

---

### Query 1.2 — Semantic Match (LLM-based, not SQL)

**Purpose**: Match user's what-if question to the most relevant historical scenario.

**Logic**:
1. Take the user's question as input
2. Use LLM to semantically compare against all retrieved scenario notes
3. Rank by relevance (how closely the historical scenario addresses the same planning question)
4. Return top 1-3 matches with relevance explanation

**Output grain**: ranked list of matched scenario identifiers with relevance scores

**Pitfalls**:
- Semantic matching may surface partially relevant scenarios — always show the match reasoning
- If no scenario exceeds a relevance threshold, report "no match" rather than forcing a bad one

---

### Query 1.3 — Find Adjustments in Matched Scenario

**Purpose**: Identify what assumptions were changed in the matched historical scenario.

**Logic**:
1. For the matched scenario's forecast version, retrieve all assumption values from the assumptions table
2. Compare each value against the known default for that parameter
3. Any value that differs from default = a deliberate adjustment
4. Catalog: planning domain, assumption parameter, actual value, default value, delta

**Output grain**: one row per adjusted parameter with domain, parameter name, value, default, delta

**Pitfalls**:
- Defaults vary by domain — numeric parameters typically default to 0, but string/categorical parameters have domain-specific defaults
- A scenario may have many adjustments; rank by magnitude or relevance to the user's question
- Some adjustments may be period-specific (only changed for certain fiscal years)

---

## Mode 2 Queries: Sensitivity Analysis

### Query 2.1 — Capture Baseline Snapshot

**Purpose**: Record the current state of key P&L metrics before any adjustment.

**Logic**:
1. Query the P&L summary table for the active forecast version
2. Filter to highlighted metrics (operating income, operating margin, net income — or configured set)
3. Return metric values across the full forecast horizon (all fiscal periods)

**Output grain**: one row per (fiscal_period, metric_name) with dollar or percentage value

---

### Query 2.2 — Capture Post-Adjustment Snapshot

**Purpose**: Record P&L metrics after the adjustment has been applied and downstream recomputation completes.

**Logic**:
1. Same query as 2.1, executed after the model recomputes
2. Return the same metrics for the same periods

**Output grain**: same as Query 2.1

---

### Query 2.3 — Compute Delta

**Purpose**: Compare baseline to post-adjustment and quantify impact.

**Logic**:
1. Join baseline and post-adjustment snapshots on (fiscal_period, metric_name)
2. Compute: absolute_delta = after - before, percentage_delta for dollar metrics, basis_point_delta for percentage metrics
3. Flag direction: favorable vs unfavorable (context-dependent — higher margin = favorable, higher expense = unfavorable)

**Output grain**: one row per (fiscal_period, metric_name) with before, after, delta, direction

**Pitfalls**:
- "Favorable" depends on the metric — revenue increase is favorable, expense increase is unfavorable
- Margin metrics use basis points (pp) not percentage change
- If recomputation is not available, delta is limited to the directly adjusted metric only

---

## Mode 3 Queries: Root Cause Analysis

### Query 3.0 — Verify User's Claim

**Purpose**: Before decomposing, confirm that the user's stated observation is accurate.

**Logic**:
1. Query the P&L table for the metric the user is asking about across relevant periods
2. Compute YoY change (absolute and percentage) or relevant comparison
3. Determine if the user's claim matches: is the metric actually declining, or is it growing but decelerating? Is it actually high, or is it high in absolute terms but declining as a percentage of revenue?

**Output grain**: summary of actual metric behavior to validate against user's claim

**Pitfalls**:
- "Revenue is decreasing" may actually be "growth rate is decelerating" — these require different analyses
- "Expenses are too high" may mean absolute level vs percentage of revenue — clarify before proceeding

---

### Query 3.1 — Revenue Decomposition

**Purpose**: Decompose revenue change into component drivers (cohort, region, adjustments).

**Logic**:
1. Get total revenue by period from P&L table; compute YoY delta
2. Get revenue adjustments from assumptions table; compute adjustment contribution to delta
3. Get initial forecast revenue by customer cohort (acquisition year) and region
4. For each cohort: compute YoY growth rate and dollar contribution to total delta
5. For each region: compute share of total and growth contribution
6. Rank drivers by dollar impact

**Output grain**: 
- Summary: one row per period with total revenue, YoY delta, adjustment portion
- Detail (on request): one row per (period, cohort) or (period, region) with revenue and growth

**Pitfalls**:
- Adjustments may be zero in many periods — only highlight if material (>5% of delta)
- Cohort definitions depend on customer acquisition date — use whatever grouping exists in the data
- Growth deceleration is not the same as decline — frame correctly

---

### Query 3.2 — OPEX Decomposition

**Purpose**: Decompose expense change into headcount (volume) vs cost-per-head (rate) drivers.

**Logic**:
1. Get total OPEX by category (R&D, Sales, Marketing, G&A) from P&L table
2. For each category: get headcount and average cost-per-head from the HC/CPH table
3. Compute: OPEX ≈ Headcount × Cost-per-Head
4. YoY change attribution:
   - Volume effect = (new HC - old HC) × old CPH
   - Rate effect = old HC × (new CPH - old CPH)
   - Mix/interaction = residual
5. Rank by dollar impact

**Output grain**: one row per (period, expense_category) with HC, CPH, volume_effect, rate_effect

**Pitfalls**:
- Not all OPEX is headcount-driven (rent, depreciation, external services) — these should be called out separately
- CPH includes salary, benefits, overhead allocation — it's a fully-loaded rate
- Cost center granularity may vary by customer — use whatever level is available

---

### Query 3.3 — Operating Income / Margin Decomposition

**Purpose**: Explain margin expansion or compression by decomposing into revenue growth vs expense growth.

**Logic**:
1. Get operating income and operating margin from P&L across periods
2. Compute: operating leverage = revenue growth rate minus OPEX growth rate
3. If revenue growing faster than OPEX → operating leverage → margin expansion
4. If OPEX growing faster than revenue → investment/loss of leverage → margin compression
5. Break down which OPEX categories are growing fastest relative to revenue
6. Identify COGS contribution if gross margin is changing

**Output grain**: one row per period with revenue growth %, OPEX growth %, margin delta, leverage indicator

---

### Query 3.4 — SBC Decomposition

**Purpose**: Explain changes in stock-based compensation relative to revenue.

**Logic**:
1. Get SBC total and SBC as % of revenue from P&L across periods
2. Break down SBC by functional area (R&D, Sales, Marketing, G&A) if available
3. Compute YoY change in SBC-to-revenue ratio
4. Identify drivers: is SBC growing faster than revenue (dilution pressure), or is revenue catching up (ratio improving)?

**Output grain**: one row per (period, function) with SBC amount, SBC % of revenue, YoY change

**Pitfalls**:
- SBC is a non-cash expense — the GAAP vs Non-GAAP gap is often dominated by SBC
- SBC growth can be lumpy due to grant timing and vesting schedules — note if a single period is anomalous

---

## Cross-Mode Pitfalls

1. **Historical vs current baseline** — P&L impact from a historical scenario is meaningless for the user's current position. Always apply to current baseline before showing impact.
2. **Partial forecast horizons** — Some models only cover 3-5 years forward. Note coverage gaps; don't extrapolate.
3. **Cascading effects** — Changing headcount affects OPEX, which affects operating income, which affects margin. If the model supports cascading, let it run. If not, note that only first-order effects are shown.
4. **Fiscal calendar alignment** — Period boundaries vary by customer. Never assume Jan-Dec. Read from metadata.
5. **Multiple P&L versions** — Customer may have GAAP and Non-GAAP P&L. Always label which is being used.
6. **Adjustment attribution** — Adjustments may stack across scenarios. When decomposing, attribute to the specific adjustment being analyzed, not cumulative history.
7. **Causal depth** — Root cause analysis should go beyond "what" to "why." If the decomposition only shows which component changed, dig one level deeper into the business mechanism.
