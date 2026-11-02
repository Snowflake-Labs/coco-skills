# Eval Plan: scenario_planning

## Purpose

Verify that `skills/scenario_planning/` (dehydrated) hydrated against a Snowflake account
correctly (1) matches a what-if question to similar historical scenarios and recommends
assumption changes, (2) shows before/after P&L impact when assumptions are adjusted, and
(3) decomposes a P&L movement into causal root-cause drivers — while never presenting a
historical scenario's P&L impact as the user's own, verifying claims before decomposing,
and discovering planning domains/defaults from the data rather than hardcoding them.

Run these cases with `/skill_eval` pointed at `evals/scenario_planning/golden_cases.md`.

---

## Metrics

### Guard Metrics (must pass)

| ID | Metric | Pass Condition |
|---|---|---|
| G1 | Hydration executes without error | Plugin hydration completes without error |
| G2 | P&L binding resolved | A P&L summary source (long metric_name+value OR wide metrics-as-columns, any grain) discovered at HIGH or MEDIUM confidence; wide layouts UNPIVOTed |
| G3 | Assumptions binding resolved | At least one forecast-assumptions source (per version/parameter/period) resolved, including the fragmented-per-domain case |
| G4 | Mode 1 gracefully gated | If the scenario-notes source is absent OR empty, Mode 1 is reported unavailable and Modes 2/3 offered — never a fabricated history |
| G5 | Optional Mode-3 sources degrade | Missing revenue_detail or headcount_cph sources limit only the relevant decomposition, with a note; never a hard failure |

### Primary Metrics (correctness)

| ID | Metric | Pass Condition |
|---|---|---|
| P1 | Mode selection correct | Request routed to What-If (Mode 1) / Sensitivity (Mode 2) / Root Cause (Mode 3) per trigger signals; asks when ambiguous |
| P2 | No historical P&L impact shown (Mode 1) | Mode 1 shows only the recommended adjustments; it does NOT present the matched historical scenario's P&L impact (computed against a different baseline) |
| P3 | Claim verified before decomposition (Mode 3) | Skill checks the user's stated observation against data first; if it disagrees (e.g. revenue growing but decelerating), states what the data shows and asks to confirm |
| P4 | Drivers are causal, not observational (Mode 3) | Each ranked driver explains a business mechanism (WHY), not a restated data point (WHAT) |
| P5 | No hardcoded domains/defaults | Planning domains, assumption parameter names, and default values are discovered from the resolved data; real parameter names used, not invented friendly labels |

### Secondary Metrics (output quality)

| ID | Metric | Pass Condition |
|---|---|---|
| S1 | Before/after/delta table (Mode 2) | Sensitivity output shows a before / after / delta table per highlighted P&L metric across the horizon |
| S2 | FAV/UNFAV annotated (Mode 2) | Each delta annotated favorable or unfavorable, with secondary/cascading effects noted where applicable |
| S3 | No-match handled honestly (Mode 1) | When no historical scenario matches, the skill says so and suggests relevant planning domains rather than forcing a bad match |
| S4 | Annual-grain limitation noted | For annual-only P&L, sensitivity is run at annual granularity and the no-intra-year-shape limitation is stated |
| S5 | Static-delta fallback (Mode 2) | When the model has no cascading recomputation, Mode 2 shows the static before/after for the adjusted metric and notes downstream effects are estimated |

## Equivalence Threshold

- **EQUIVALENT** = all G + all P pass + >=4/5 S pass
- **DEGRADED** = all G + >=3 P pass + <4 S pass — iterate on Context Queries
- **INCOMPATIBLE** = any G fails OR <3 P pass — needs rewrite
