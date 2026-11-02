# Eval Plan: good_morning_cfo

## Purpose

Verify that `skills/good_morning_cfo/` (dehydrated) hydrated against a Snowflake
account produces a correct daily executive briefing: revenue pulse, top account
movers, expense anomalies, and optional consumption and external signal sections.

Run these cases with `/skill_eval` pointed at `evals/good_morning_cfo/golden_cases.md`.

---

## Metrics

### Guard Metrics (must pass)

| ID | Metric | Pass condition |
|----|--------|---------------|
| G1 | Hydration succeeds | Plugin hydration completes and all critical bindings resolve |
| G2 | Critical binding resolved | finance.revenue.forecast_daily resolves to a HIGH or MEDIUM confidence FQN |
| G3 | Time filter applied | Generated SQL includes a date range constraint (yesterday / MTD / QTD) |
| G4 | Currency awareness | Output shows currency code, not raw numbers without unit |
| G5 | No leaked internal terms | Output contains zero of: internal schema names, internal role names, named internal people |

### Primary Metrics (correctness)

| ID | Metric | Pass condition |
|----|--------|---------------|
| P1 | Headline beat/miss | Briefing leads with whether yesterday beat or missed forecast and by how much |
| P2 | All 4 sections present | Revenue Performance, Top Movers, Consumption Pulse (or skip notice), External Signals |
| P3 | Top 5 movers in each direction | Top 5 positive + Top 5 negative variance accounts |
| P4 | Revenue numbers correct | Yesterday's actual/forecast values match what is in the bound table |
| P5 | Top-3 mover accuracy | Top 3 positive movers and top 3 negative movers match the resolved data |

### Secondary Metrics (output quality)

| ID | Metric | Pass condition |
|----|--------|---------------|
| S1 | Length under 600 words | Total briefing word count <= 600 |
| S2 | Hydration footer present | Last line shows binding source breakdown |
| S3 | No assumed fiscal calendar | No mention of "Q1 = Feb-Apr" or similar without a configured fiscal_calendar |
| S4 | Zero column retries | Agent trace shows zero SQL retries due to wrong column names |
| S5 | Latency acceptable | Hydrated briefing completes in a reasonable time (no hanging queries) |

---

## Equivalence Threshold

- **EQUIVALENT** = all G + all P pass + >=4/5 S pass
- **DEGRADED** = all G + >=3 P pass + <4 S pass — iterate on Context Queries
- **INCOMPATIBLE** = any G fails OR <3 P pass — needs rewrite
