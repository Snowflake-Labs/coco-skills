# Eval Plan: budget_vs_actuals

## Purpose

Verify that `skills/budget_vs_actuals/` (refactored from `fva_analysis` BvA mode, dehydrated)
hydrated against a Snowflake account correctly flags material variances from a fixed annual
plan, generates written commentary per flagged line, and presents the full detail table on
request — without confusing fixed budgets with evolving forecast snapshots.

Run these cases with `/skill_eval` pointed at `evals/budget_vs_actuals/golden_cases.md`.

---

## Metrics

### Guard Metrics (must pass)

| ID | Metric | Pass Condition |
|---|---|---|
| G1 | Hydration executes without error | Plugin hydration completes without error |
| G2 | Budget binding resolved | A fixed-baseline budget table (NOT a versioned forecast) discovered at HIGH or MEDIUM confidence |
| G3 | Actuals binding resolved | An actuals table sharing a join key with the budget table discovered |
| G4 | Join succeeds without data loss | Join between budget and actuals produces rows for all in-scope lines |
| G5 | No forecast-as-budget confusion | Skill does NOT use a versioned forecast table as the budget — if only forecast exists, routes to `forecast_vs_forecast` |

### Primary Metrics (correctness)

| ID | Metric | Pass Condition |
|---|---|---|
| P1 | Materiality threshold applied | Only lines exceeding BOTH % and $ thresholds are flagged |
| P2 | FAV/UNFAV direction correct | Expense lines: actual < budget = FAV. Revenue lines: actual > budget = FAV. |
| P3 | Commentary present for each flagged line | Every flagged line has 2–3 sentences of commentary |
| P4 | Full detail table gated behind request | Full detail table NOT shown unsolicited — only after user asks |
| P5 | Variance computed correctly | Variance = actual − budget (not reversed) |

### Secondary Metrics (output quality)

| ID | Metric | Pass Condition |
|---|---|---|
| S1 | Summary stat present | "X of Y line items exceeded materiality threshold" shown at top |
| S2 | Materiality thresholds stated in footer | Footer shows the threshold used (e.g., ">5% and >$50K") |
| S3 | Context data unavailable handled | When no notes table, commentary says "Context data unavailable — manual review required" rather than being blank |
| S4 | Period labeled in output | Output clearly shows the period analyzed (month/quarter start and end) |
| S5 | Zero flagged case handled | If no lines exceed threshold, outputs "No lines exceeded materiality threshold" — does not produce empty table |

## Equivalence Threshold

- **EQUIVALENT** = all G + all P pass + ≥4/5 S pass
- **DEGRADED** = all G + ≥3 P pass + <4 S pass — iterate on Context Queries
- **INCOMPATIBLE** = any G fails OR <3 P pass — needs rewrite
