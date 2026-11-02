# Eval Plan: earnings_prep

## Purpose

Verify that `skills/earnings_prep/` (dehydrated) hydrated against a Snowflake account
correctly (1) assembles a quarterly financial package with vs-guidance / YoY / QoQ
comparisons, (2) drafts CFO earnings-call script language from that package, and
(3) surfaces likely analyst questions with data-backed answers — while treating
guidance/consensus as optional, never inventing numbers, and never presenting
forward-looking or non-GAAP figures as reported GAAP actuals.

Run these cases with `/skill_eval` pointed at `evals/earnings_prep/golden_cases.md`.

---

## Metrics

### Guard Metrics (must pass)

| ID | Metric | Pass Condition |
|---|---|---|
| G1 | Hydration executes without error | Plugin hydration completes without error |
| G2 | Financial actuals binding resolved | A quarterly financial-summary source (one wide table, one tall metric_name+value table, OR several composed on fiscal quarter) discovered at HIGH or MEDIUM confidence |
| G3 | Comparison basis available | QoQ/YoY basis satisfied — multi-year history in the actuals source, precomputed `QOQ_*`/`YOY_*` columns, or a distinct restated-prior-year source |
| G4 | Optional sources degrade gracefully | Missing/empty/RBAC-blocked guidance, consensus, or analyst-questions sources are skipped with a note — they never block the skill |
| G5 | No non-GAAP-as-GAAP confusion | Constant-currency / non-GAAP figures are labeled as such and flagged; never presented as reported GAAP actuals |

### Primary Metrics (correctness)

| ID | Metric | Pass Condition |
|---|---|---|
| P1 | Mode detection correct | Request routed to Financial Package / Script / Q&A Prep per trigger signals; if ambiguous, asks mode + period |
| P2 | Mode dependency enforced | Mode 2 (Script) and Mode 3 (Q&A) require Mode 1 output; if invoked first, skill prompts to run Mode 1 rather than inventing data |
| P3 | vs-guidance BEAT/MISS flagged | Metrics outside ±5% (or user threshold) of guidance midpoint flagged BEAT/MISS with absolute and % deviation; midpoint = (low+high)/2 when only a range exists |
| P4 | No invented numbers | Unavailable metrics left blank with "not available"; derived figures (e.g. gross profit $ = revenue × GM%) explicitly labeled "derived" |
| P5 | Forward guidance labeled draft | All script forward-guidance language marked draft and noted as requiring legal/IR review |

### Secondary Metrics (output quality)

| ID | Metric | Pass Condition |
|---|---|---|
| S1 | Period labeled in output | Output clearly states the fiscal quarter/year analyzed |
| S2 | Multi-table compose handled | When the summary spans several tables, the package is composed on fiscal quarter and this is treated as normal, not a degradation |
| S3 | Consensus source labeled | web_search consensus labeled "~consensus (web)" with source date; omitted with a note if unavailable from web and internal |
| S4 | Q&A ranked by sensitivity | Mode 3 questions grouped HIGH / MEDIUM / LOW, with each BEAT/MISS from Mode 1 surfaced as a likely question |
| S5 | Guidance-source RBAC handled | An authorization/RBAC error on the guidance source is treated as "absent" (skipped + noted), not a hard failure |

## Equivalence Threshold

- **EQUIVALENT** = all G + all P pass + >=4/5 S pass
- **DEGRADED** = all G + >=3 P pass + <4 S pass — iterate on Context Queries
- **INCOMPATIBLE** = any G fails OR <3 P pass — needs rewrite
