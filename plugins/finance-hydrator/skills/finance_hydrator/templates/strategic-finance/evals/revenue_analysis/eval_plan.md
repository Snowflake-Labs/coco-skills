# Eval Plan: revenue_analysis

## Purpose

Verify that `skills/revenue_analysis/` (dehydrated) hydrated against a Snowflake account
produces a data-backed executive revenue narrative at any level of a product/segment
hierarchy — explaining what happened (plan performance, growth, child breakdown), why
(concentration, cohort, geo/customer drivers), and what matters (risks, outlook) —
in flowing prose, with the mandatory parenthetical on every named entity, and degrading
gracefully when lifecycle or concentration data is absent.

Run these cases with `/skill_eval` pointed at `evals/revenue_analysis/golden_cases.md`.

---

## Metrics

### Guard Metrics (must pass)

| ID | Metric | Pass Condition |
|---|---|---|
| G1 | Hydration executes without error | Plugin hydration completes without error |
| G2 | Revenue hierarchy binding resolved | The primary hierarchy source (period, entity, actual + plan revenue by hierarchy level) discovered at HIGH or MEDIUM confidence |
| G3 | Period/plan columns present | The hierarchy source exposes a period identifier and a plan/forecast revenue column (needed for trajectory + plan variance) |
| G4 | Optional sources degrade gracefully | Missing lifecycle_cohort or concentration sources cause their steps to be skipped with a note — never a hard failure |
| G5 | No recursion past lowest level | Standard narrative does NOT recurse into individual-customer detail below the lowest hierarchy level |

### Primary Metrics (correctness)

| ID | Metric | Pass Condition |
|---|---|---|
| P1 | Subject/period/grain resolved | Subject, period, and grain resolved from the request or asked; latest period auto-detected when unspecified |
| P2 | 60% cumulative stop rule applied | Children listed until running contribution reaches 60% of total movement; remainder gets one-sentence summary |
| P3 | Concentration verdict correct | Top-10 share >=50% or 35-50% names top 3 gainers + top 3 contractors; <35% states "broad-based" without naming individuals |
| P4 | Parenthetical rule enforced | Every named entity carries `($revenue, PoP%, +/-$X vs plan)` on first mention |
| P5 | Partial period labeled | Period-to-date actuals labeled "through [date]" across ALL figures, with a note that comparisons may shift |

### Secondary Metrics (output quality)

| ID | Metric | Pass Condition |
|---|---|---|
| S1 | Prose format respected | Performance sections are flowing prose; no metric tables/bullet lists in the body (Key Takeaway bullets are the intentional exception) |
| S2 | Mean/median skew diagnostic | Skew ratio (mean/median) interpreted: >50x flagged whale-dependency; 10-50x concentrated; <10x distributed |
| S3 | Material-child selection | Full detail given to top-3-by-revenue OR any child >=10% contribution, applied independently of the 60% stop rule |
| S4 | TLDR written last | TLDR reflects Step 7 concentration data (archetype: customer-driven vs feature-driven) |
| S5 | Missing subject handled | Unknown subject lists available subjects at that level and asks the user to pick, rather than erroring or inventing |

## Equivalence Threshold

- **EQUIVALENT** = all G + all P pass + >=4/5 S pass
- **DEGRADED** = all G + >=3 P pass + <4 S pass — iterate on Context Queries
- **INCOMPATIBLE** = any G fails OR <3 P pass — needs rewrite
