# Eval Plan: forecast_vs_forecast

## Purpose

Verify that `skills/forecast_vs_forecast/` (refactored from `fva_analysis` FvF mode, dehydrated)
hydrated against a Snowflake account correctly compares two forecast snapshots, classifies item
movements into the variance taxonomy, detects forecast bias, and never confuses a versioned
forecast table with a fixed budget.

Run these cases with `/skill_eval` pointed at `evals/forecast_vs_forecast/golden_cases.md`.

## Metrics

### Guard Metrics (must pass)

| ID | Metric | Pass Condition |
|---|---|---|
| G1 | Hydration executes without error | Plugin hydration completes without error |
| G2 | Forecast snapshot binding resolved | Table with version_id + item_id + period + amount discovered at HIGH or MEDIUM confidence |
| G3 | At least 2 versions found | Forecast table contains ≥2 distinct version identifiers for comparison |
| G4 | Analysis window derived correctly | Period derived from version metadata — not hardcoded |
| G5 | No internal system IDs in output | Version identifiers shown as business-friendly labels (date/name), not opaque system IDs |

### Primary Metrics (correctness)

| ID | Metric | Pass Condition |
|---|---|---|
| P1 | Version selection step executed | Two versions presented for user confirmation before proceeding to Q1–Q4 |
| P2 | High-level delta correct | Total change in metric computed correctly: Version B − Version A |
| P3 | Item-level taxonomy applied | Each item classified into one of 5 taxonomy categories (timing match/amount change, push-out, pull-in, new, removed) |
| P4 | Push-out vs pull-in correctly identified | Push-out = item moved to later period; pull-in = item moved to earlier period |
| P5 | Bias analysis requires actuals (or skipped) | Q4 bias analysis runs only if actuals available in ≥3 periods — otherwise skipped with note |

### Secondary Metrics (output quality)

| ID | Metric | Pass Condition |
|---|---|---|
| S1 | No partial streaming | All four queries complete before any output is shown |
| S2 | Segment breakdown present | Q2 segment-level table produced (or noted unavailable with reason) |
| S3 | Favorable direction correct | Revenue/bookings: increase in Version B = favorable. Expense: decrease = favorable. |
| S4 | Bias direction clearly labeled | Bias output says "over-forecasting" or "under-forecasting" — not just a number |
| S5 | Item display names used | Opaque item IDs replaced with display names from dimension table where available |

## Equivalence Threshold

- **EQUIVALENT** = all G + all P pass + ≥4/5 S pass
- **DEGRADED** = all G + ≥3 P pass + <4 S pass — iterate on Context Queries
- **INCOMPATIBLE** = any G fails OR <3 P pass — needs rewrite
