# Eval Plan: cloud_cogs_analysis

## Purpose

Verify that `skills/cloud_cogs_analysis/` (new, dehydrated) hydrated against a Snowflake account
produces correct COGS breakdowns, gross margin summaries, and unit economics — with proper
period-over-period comparisons and attribution commentary.

Run these cases with `/skill_eval` pointed at `evals/cloud_cogs_analysis/golden_cases.md`.

## Metrics

### Guard Metrics (must pass)

| ID | Metric | Pass Condition |
|---|---|---|
| G1 | Hydration executes without error | Plugin hydration completes without error |
| G2 | Critical binding resolved: COGS summary table | Table with (period, cost_category, amount) discovered at HIGH or MEDIUM confidence |
| G3 | Revenue binding resolved | Table with periodic revenue discovered for gross margin calculation |
| G4 | Comparison period detected | Prior period or prior year resolved — not hardcoded |
| G5 | No internal-FQN leakage | Output contains zero of: internal schema names, internal system names, or internal cost identifiers |

### Primary Metrics (correctness)

| ID | Metric | Pass Condition |
|---|---|---|
| P1 | Gross margin % computed correctly | Gross Margin % = (Revenue - COGS) / Revenue — verified against raw numbers in output |
| P2 | COGS breakdown by dimension present | At least one dimensional breakdown (provider, product, or category) in the output |
| P3 | Period-over-period direction labeled | Output explicitly states QoQ or YoY comparison, not just raw numbers |
| P4 | Attribution section lists top drivers | At least 2 cost drivers with direction and dollar impact |
| P5 | Unit economics derived (or section skipped with note) | Cost-per-unit metrics shown, OR "unit cost data not found" noted |
| P6 | Commitment section only when bound | Commitment & Discount section appears only if commitment data resolved; otherwise omitted with a note |
| P7 | Blended discount is cost-weighted | Effective discount = discount $ / list cost $, not a simple average of percentages |
| P8 | Margin bridge labeled non-causal | Bridge section states it is arithmetic, not causal |

### Secondary Metrics (output quality)

| ID | Metric | Pass Condition |
|---|---|---|
| S1 | Margin change in percentage points | Margin delta expressed as `+/-X.Xpp`, not as `+/-X.X%` |
| S2 | Favorable/unfavorable labeled | Commentary uses "favorable" or "unfavorable" directional label |
| S3 | Drill-down offer at end | Output ends with an offer to drill into the highest-variance category |
| S4 | No COGS/opex confusion | Output contains no mention of S&M, R&D, G&A, or headcount as COGS items |
| S5 | Revenue binding verified before margin | Gross margin % computed only after revenue binding confirmed — not hardcoded |

## Equivalence Threshold

- **EQUIVALENT** = all G + all P pass + ≥4/5 S pass
- **DEGRADED** = all G + ≥3 P pass + <4 S pass — iterate on Context Queries
- **INCOMPATIBLE** = any G fails OR <3 P pass — needs rewrite
