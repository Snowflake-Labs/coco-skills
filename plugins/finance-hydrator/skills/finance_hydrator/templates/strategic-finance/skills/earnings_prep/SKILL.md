---
name: earnings_prep
description: >
  Earnings call preparation — financial summary packaging, guidance language drafting,
  and analyst Q&A preparation. Three modes: assemble key metrics with YoY/QoQ comparisons
  and vs-guidance variance (Financial Package), draft script language for CFO remarks
  (Script), and surface likely analyst questions with data-backed answers (Q&A Prep).
  Triggers: earnings prep, earnings call, earnings transcript, investor relations, guidance
  language, analyst questions, earnings pack, what do we say to analysts, quarterly earnings,
  prepare for earnings, Q&A prep, earnings script, earnings highlights, press release prep,
  verify earnings numbers, tie out, post-call summary, earnings call takeaways.
dehydrated: true
data_domain: "earnings preparation, financial summary packaging, guidance vs actuals, analyst Q&A, CFO script language, investor relations metrics"
mcp_optional: [web_search]
created_date: 2026-06-08
version: 1.2.0
license: Proprietary. See LICENSE.txt for complete terms
---

# Earnings Prep (Dehydrated)

## Intent

Prepare for an earnings call — from assembling the financial package through drafting
the CFO script to anticipating analyst questions with data-backed answers. Three modes
in one skill, typically run sequentially in the order: Financial Package → Script →
Q&A Prep.

Serves: CFO, IR lead, and FP&A teams preparing for quarterly earnings calls. Primarily
applicable to publicly traded companies, but Mode 1 (Financial Package) is useful for any
organization doing periodic external or board-level reporting.

Not for: internal budget close reviews (use `budget_vs_actuals`), forecast evolution
tracking (use `forecast_vs_forecast`), revenue narratives for internal QBRs (use
`revenue_analysis`), or real-time market monitoring.

## Behavior

### Trigger Detection

Activate on: "earnings prep", "earnings call", "prepare for earnings", "earnings pack",
"earnings highlights", "earnings script", "guidance language", "what do we say to analysts",
"press release prep", "quarterly earnings", "earnings release", "how did we do vs guidance",
"beat guidance", "miss guidance", "call prep", "financial highlights for the call",
"Q&A prep", "analyst questions", "what will analysts ask".

Do NOT activate for:
- Generic "investor relations" tasks without earnings call context
- Transcript retrieval ("find the Q3 earnings transcript") — this is a search/retrieval task
- Internal quarterly reviews or QBRs (route to `revenue_analysis`)
- Internal close variance reviews (route to `budget_vs_actuals`)
- Forecast snapshot comparison (route to `forecast_vs_forecast`)

**Mode detection:** Classify the user request into one of five modes:

| Mode | Trigger Signals |
|---|---|
| Financial Package | "earnings pack", "assemble the numbers", "key metrics", "pack the numbers", "earnings summary" |
| Script / Language | "earnings script", "draft the script", "guidance language", "what do we say", "CFO remarks", "script for the call" |
| Q&A Prep | "analyst questions", "Q&A prep", "what will analysts ask", "prep for the Q&A", "prepare answers" |
| Verify | "verify the numbers", "tie out", "check the script against the data", "do these numbers match" |
| Post-Call Takeaways | "summarize the call", "earnings call takeaways", "what did management say", "post-call summary" |

If unspecified, ask via `ask_user_question`:
1. Which mode?
2. Which quarter / fiscal period?

**Typical flow:** Run Mode 1 first. Modes 2 and 3 depend on Mode 1's output — they
require the financial package to have been assembled or provided. Run Mode 4 (Verify)
before any script or Q&A is shared for review. Mode 5 is independent and runs after the
call.

**Metric source routing.** Decide which source is authoritative for each class of metric
before assembling anything: aggregate company totals come from the summary source;
product- or feature-level breakdowns are subsets of that total and must never exceed it;
operating KPIs come from the KPI source. When two sources disagree, report the aggregate
source figure and flag the gap (see Mode 4).

### Mode 1 — Financial Package

**Purpose:** Assemble all key metrics for the quarter in a clean, comparison-ready table.

**Step 1 — Determine the reporting quarter.** Default: most recent completed fiscal quarter.
Confirm with user if ambiguous.

**Step 2 — Fetch current quarter actuals for standard metrics.**

The financial summary may live in **one table or several** — many companies keep P&L
metrics in one table and operating KPIs (NRR, RPO, customer count) in another. Compose the
package across whatever sources resolve, keyed by fiscal quarter; you do not need a single
canonical summary table (see Adaptation Patterns).

Metrics to include (include what's available; note any that are missing):
- Revenue (total and by product/segment if available)
- Gross margin % (and gross profit $ — if only the % is stored, derive $ = revenue × GM%
  and label it "derived")
- Operating income and operating margin %
- Non-GAAP variants if available (non-GAAP gross margin, non-GAAP operating income)
- Key operating metrics relevant to the business (e.g., NRR, ARR, RPO, customer count)
- Trend context: the last four quarters of each headline metric, and the historical high
  or low where relevant, so each figure can be placed in a trajectory

**Step 3 — Build comparison columns:**
- vs Prior Quarter (QoQ): actual vs prior quarter actual, $ and % change
- vs Prior Year (YoY): actual vs same quarter prior year, $ and % change
- vs Guidance: actual vs the most recently issued guidance range for this quarter.
  If guidance is a range, compute midpoint = (guidance_low + guidance_high) / 2.
  If guidance_midpoint column exists, use it directly.
- vs Consensus (if available from an internal consensus table or web_search). Use only a
  named, sourced consensus; never infer or construct one.
- Optional peer and macro context from public data, clearly labeled as external

**Step 4 — Flag material guidance misses/beats.**

Any metric where actual vs guidance is outside ±5% (or user-specified threshold):
flag as BEAT or MISS with the absolute and % deviation.

**Output:**

```
# Financial Package — Q<N> FY<YYYY>
*Prepared <date>*

## Key Metrics
| Metric | Actual | vs Guidance | vs PY (YoY) | vs PQ (QoQ) | vs Consensus |
|---|---|---|---|---|---|
| Revenue | $XXM | +/-$XXM (+/-X.X%) [BEAT/MISS] | +X.X% | +X.X% | +/-X.X% |
| Gross Margin | XX.X% | +/-X.Xpp | +/-X.Xpp | +/-X.Xpp | +/-X.Xpp |
| Operating Income | $XXM | +/-$XXM | +X.X% | +X.X% | +/-X.X% |
| Operating Margin | XX.X% | +/-X.Xpp | +/-X.Xpp | +/-X.Xpp | +/-X.Xpp |
| <Key operating metric> | X | — | +X.X% | +X.X% | — |

## Guidance Flags
- [BEAT] Revenue: +$XXM (+X.X%) above guidance midpoint
- [MISS] Operating Margin: -X.Xpp below guidance midpoint

---
Guidance source: <most recent issued guidance — date and source if available>
Consensus source: <internal table or web_search — note if approximate>
```

### Mode 2 — Script / Language

**Purpose:** Draft 4 paragraphs of CFO earnings call language using the financial package.

Requires Mode 1 to have been run or the user to provide the financial package data.

**Step 1 — Identify the 3–4 headline metrics from Mode 1** (the ones that are most
material, most notable, or most likely to be the lead in a press release).

**Step 2 — Draft the script.** Four paragraphs:

1. **Opening summary** — total revenue, revenue growth rate, operating margin, and
   one-sentence forward outlook
2. **Topline performance** — revenue vs guidance and vs prior year, key product/segment
   drivers, one-sentence retention/expansion highlight
3. **Operating metrics** — gross margin, operating leverage commentary, one key operating
   KPI (NRR, ARR, RPO, customers)
4. **Forward guidance** — next quarter and/or full-year guidance, if guidance data is available

**Language standards:**
- Declarative sentences — state facts directly: "Revenue was $XXM, up X% year-over-year."
- Neutral tone: facts only. No promotional adjectives (for example "strong", "robust",
  "momentum", "exceptional") in historical sections.
- No hedging in historical sections. Hedging language (may, expect, approximately) only
  in the forward guidance paragraph.
- Numbers: same formatting as Financial Package
- Avoid jargon that analysts may not share (internal product names, internal acronyms)

**Step 3 — Present and offer a revision loop.**

> **Stopping point:** Present the draft and ask: "Anything to adjust — tone, specific
> numbers to highlight, or guidance language to modify?"

**Output:**

```
# Earnings Script — Q<N> FY<YYYY>
*Draft — for internal review; forward guidance statements require legal/IR sign-off*

## Opening Summary
[3–4 sentences: total revenue, growth rate, operating margin, one-sentence outlook]

## Topline Performance
[2–3 sentences: revenue vs guidance, key drivers, retention/expansion highlight]

## Operating Metrics
[2–3 sentences: gross margin, operating leverage, one key operating KPI]

## Forward Guidance
[2 sentences: next quarter and/or full-year guidance]
```

### Mode 3 — Q&A Prep

**Purpose:** Surface the 10–15 most likely analyst questions and prepare concise,
data-backed answers.

Requires Mode 1 to have been run or the financial package to be provided.
**If Mode 3 is invoked before Mode 1:** prompt via `ask_user_question`:
"Mode 3 requires the financial package from Mode 1. Would you like me to run Mode 1 first?"
Do not generate Q&A answers from memory or invented data.

**Step 1 — Build the question bank.**

Maintain the question bank per quarter. Build it from the sources below, and tag every question
with a **category** (for example guidance, margins, retention, demand, competition,
capital allocation), a **priority** (high / medium / low), and the **analyst or firm
type** that tends to ask it when known:

| Source | How |
|---|---|
| Financial package flags | Any BEAT or MISS from Mode 1 becomes a likely question |
| Structural patterns | YoY growth deceleration, margin compression, RPO or backlog changes → analysts always ask |
| Prior-call and sell-side questions | Questions asked on past calls and raised in sell-side previews, if a table exists |
| Recurring question library | Standard quarterly questions regardless of results (seed list below) |
| Analyst coverage (optional) | web_search for recent analyst notes on the company's ticker — analyst focus areas |

**Recurring question seed list** (use these as a baseline for every earnings call, customized with current figures):
- "Can you walk us through the guidance for next quarter and the key assumptions?"
- "What are the biggest drivers of gross margin expansion/compression this quarter?"
- "How is net revenue retention trending, and what is driving the change?"
- "What is RPO/backlog and what does it say about the health of the pipeline?"
- "How are you thinking about operating expense growth relative to revenue growth?"
- "What are you seeing in terms of deal size, sales cycle, and pipeline activity?"
- "Can you discuss the competitive environment and any pricing dynamics?"

For competitive threat questions: note that the answer requires market context not available
from the financial package. Flag these as "[requires additional market context]" in the Q&A
prep output and leave a placeholder for the IR/CFO to fill in.

**Step 2 — For each likely question, generate a response in a fixed format:** a 1–2
sentence high-level takeaway that states the business reality with only 1–2 data points,
then 1–3 supporting-metric bullets, each with the current value, the comparison value, and
the change. Lead with the headline metric, add QoQ or YoY context, and end with a
forward-looking data point only if one is available. Use the neutral-tone rule from Mode 2.
Each response must cite specific numbers from the financial package; never invent figures.

**Step 3 — Rank questions by sensitivity:**
- HIGH sensitivity: guidance miss, margin deterioration, competitive threat, revenue deceleration
- MEDIUM sensitivity: gross margin trend, operating leverage, RPO/backlog
- LOW sensitivity: standard recurring questions (headcount, buybacks, guidance methodology)

**Step 4 — Present the ranked Q&A list.**

> **Stopping point:** Present Q&A list and ask: "Any specific questions you want to add,
> reprioritize, or expand the answer on?"

**Output:**

```
# Q&A Prep — Q<N> FY<YYYY>

## High-Sensitivity Questions
**Q: <Question>** [HIGH]
A: <3–5 sentences with specific numbers from the financial package>

## Medium-Sensitivity Questions
**Q: <Question>** [MEDIUM]
A: <3–5 sentences>

## Standard Recurring Questions
**Q: <Question>** [LOW]
A: <2–3 sentences>

---
Note: These are suggested answers for preparation purposes. Final responses are
subject to legal and IR review.
```

### Mode 4 — Verify

**Purpose:** Tie every figure in the script and Q&A back to the source of record before
anything goes to legal and IR review.

1. Extract every number and growth claim from the script and Q&A drafts.
2. Match each to the authoritative source (see Metric source routing). Where a published
   or disclosed figure exists, tie to it as well.
3. Cross-check sources against each other. Apply tiered escalation to the size of any
   discrepancy (tolerances are configurable defaults):
   - under 5%: proceed
   - 5–15%: note it in the output and proceed
   - over 15%: stop and investigate before anything is shared
   Where two sources use different attribution methods (for example an aggregate category
   vs the sum of its features), allow a wider tolerance (default ±10%) and say why.
4. Run reasonableness checks: breakdown components do not exceed their total; total
   customers exceed any single feature's customer count; growth direction agrees across
   sources; margin and dollar figures reconcile.
5. Output PASS / WARN / FAIL per check, listing the claim, the value used, the source,
   and the difference. Do not silently correct figures — report them.

### Mode 5 — Post-Call Takeaways

**Purpose:** Summarize a covered company's earnings call (a peer, customer, or
infrastructure partner) from its transcript, and state what it implies for the user's own
company.

Requires the company ticker and quarter from the user, and a transcript supplied by the
user or available in a bound table or transcript tool. Search for sourced consensus before
writing the table. Output: a lead paragraph (move after the report, direction of the key
metric, whether it beat or missed expectations); a table of five quarters of reported
results followed by a consensus column labeled with its named source and a beat/(miss) %
column; two to three themes, each with at most one short verbatim quote tagged to its
source (prepared remarks or Q&A); and one to two sentences on implications for the user's
company and peers. Never invent numbers, never infer a consensus from the actual result,
and never use an estimate from an anonymous or unverified source. Confirm prior-quarter
figures that are not in the transcript before using them.

### Confidence Handling

| Confidence | Behavior |
|---|---|
| HIGH (financial actuals + comparison basis + key operating metrics all resolved — across one or more tables) | Full 3-mode workflow; complete financial package with all comparisons |
| MEDIUM (actuals resolve but guidance history or consensus is unavailable **or RBAC-blocked**) | Mode 1: omit guidance/consensus columns; flag as "not available"; Modes 2–3 work from available data |
| MEDIUM (the summary is spread across multiple wide tables) | Compose the package across them, keyed by fiscal quarter (see Adaptation Patterns); run normally |
| LOW on critical (no financial actuals source at all) | Block. Output: "Earnings prep requires quarterly financial metrics. Add a binding under finance.earnings.financial_summary in hydration_hints.yaml and re-verify." |
| LOW on non-critical (operating metrics unavailable) | Mode 1: financial lines only; note "operating metrics not found"; Modes 2–3 continue without those metrics |

### Guardrails

- Sanity-check every figure against its neighbors before presenting it. If a value in the
  summary source is far out of scale with adjacent periods (for example a future-quarter
  forecast at roughly double its neighbors, or a quarter that appears twice), treat it as a
  likely data error, exclude it, and say so rather than building commentary on it.
- When two sources report the same metric, cross-check them (Mode 4 tiers) before the
  package is presented; do not silently pick one.
- Never present forward guidance language as final — always label as draft and note
  that forward-looking statements require legal and IR review. Nothing is shared outside
  the finance team until legal / IR sign-off.
- Consensus data from web_search is approximate — label as "~consensus (web)" and note
  the source date. Do not present it as authoritative if it conflicts with internal data.
- Do not invent numbers or fill in blanks with estimates. If a metric is unavailable,
  leave the cell blank and note "not available" — never approximate. Derived figures
  (e.g. gross profit $ = revenue × GM%) are allowed but must be labeled "derived".
- **Constant-currency / FX-adjusted figures are NOT the GAAP figures used in press
  releases.** If the only available revenue/margin source is constant-currency or
  non-GAAP, label it explicitly and flag the materiality risk — do not present it as
  reported GAAP actuals.
- Do not expose internal planning model details, internal forecast versions, or compensation
  data in earnings language.
- Use the customer's defined non-GAAP definitions if available. Do not construct non-GAAP
  adjustments without knowing the reconciliation methodology.
- All guidance language must cite the guidance source (most recent issued date and channel).
- A guidance source that returns an authorization/RBAC error is treated the same as
  "absent" — skip the guidance column gracefully and note it; never block the whole skill
  on a missing optional source.

### Error Handling

| Scenario | Action |
|---|---|
| Financial actuals missing entirely | Block; output setup instructions |
| Guidance history missing OR RBAC-blocked (auth error) | Run Mode 1 without guidance column; note "no guidance history available"; try `web_search` for issued guidance |
| Guidance range has no midpoint | Compute midpoint = (guidance_low + guidance_high) / 2 |
| Gross profit $ not stored (only GM%) | Derive $ = revenue × GM%; label "derived" |
| Summary spread across multiple tables | Compose across them keyed by fiscal quarter |
| Consensus unavailable (web + internal) | Omit consensus column; note "vs Consensus: not available" |
| Multiple fiscal periods available | Ask user to confirm which quarter |
| User runs Mode 2 before Mode 1 | Prompt: "Let me assemble the financial package first — do you want Mode 1?" |
| User runs Mode 3 before Mode 1 | Prompt: "Mode 3 requires the financial package. Would you like to run Mode 1 first?" |
| Zero guidance flags (all within ±5%) | Output: "No material guidance variances — all metrics within ±X% of guidance midpoint." |
| Metric labels in financial table don't match standard names | Prompt user to confirm which column maps to Revenue, Gross Margin, etc. |

## Adaptation Patterns

IR/earnings data is rarely in one tidy "tall" table. Inspect resolved objects with
`INFORMATION_SCHEMA.COLUMNS` and adapt — use Cortex Sense / `cortex_context_sql` to confirm
columns before composing the package.

| If the data looks like… | Do this |
|---|---|
| **No single tall summary** (P&L metrics in one wide table, operating KPIs in another) | Compose the package by joining the sources on fiscal quarter. Each metric row can come from a different table — this is normal and not a degradation. |
| **Wide / metrics-as-columns** (one row per quarter, metrics like `PRODUCT_REVENUE`, `GROSS_MARGIN`, `OPERATING_MARGIN` as columns) | Read the columns directly per quarter; no UNPIVOT needed for a fixed metric list. Pre-computed `QOQ_*` / `YOY_*` columns, if present, satisfy the comparison bindings. |
| **Gross profit only as a %** | Derive gross profit $ = revenue × GM%; label "derived". |
| **YoY/QoQ already pre-computed** (`QOQ_REVENUE`, `YOY_OPERATING_MARGIN_PCT_CHANGE`, etc.) | Use them directly instead of self-joining prior periods. |
| **Guidance source RBAC-blocked or empty** | Treat as absent: skip the guidance column, note it, fall back to `web_search` for publicly issued guidance. |
| **Only constant-currency / non-GAAP revenue available** | Use it but label clearly; flag the GAAP-vs-adjusted materiality risk (see Guardrails). |
| **Historical analyst-questions / sell-side preview tables exist** | Seed Mode 3 from them (real questions analysts asked) instead of only the generic recurring list — a meaningful upgrade. |

## Context Queries

- A source (or sources) of quarterly financial **actuals**: revenue, gross margin,
  operating income/margin, optionally non-GAAP variants, per fiscal quarter. **This need
  not be a single tall table** — it may be one wide table, or several tables composed on
  fiscal quarter (e.g. P&L metrics in one, operating KPIs in another). Columns: fiscal
  quarter/period identifier (STRING or DATE), the metric values (NUMBER) either as a
  `metric_name`+`value` pair (tall) or as named columns (wide). Gross profit $ may be
  derived from revenue × GM% if not stored. *(critical)*

- A basis for **QoQ and YoY comparison**: either prior-period rows in the same source
  (multi-year history), or pre-computed `QOQ_*` / `YOY_*` columns, or a constant-currency
  restated-prior-year table. If the source has multi-year data this is already satisfied.
  *(critical — but satisfiable from the actuals source itself)*

- A guidance history giving the issued guidance range (or midpoint) per (quarter, metric).
  Columns: fiscal quarter (STRING or DATE), guidance issued date (DATE), metric name
  (STRING), guidance low/high (NUMBER), optionally midpoint (NUMBER). *(non-critical —
  guidance column skipped if absent, empty, or RBAC-blocked; `web_search` used as fallback)*

- (Optional) An analyst consensus table with one row per (quarter, metric_name) containing
  consensus estimate (NUMBER) and source date. If absent, the skill attempts web_search
  for consensus estimates. If web_search also returns nothing, the consensus column is
  omitted with a note.

- (Optional) A historical analyst-questions or sell-side-preview table (e.g. questions
  asked at past earnings calls, with fiscal period, category, priority, and analyst/firm;
  or sell-side preview reports with key questions/concerns). Used to seed Mode 3 Q&A prep
  with real questions instead of only the generic recurring list. If absent, Mode 3 uses
  the recurring seed list plus the current quarter's flags.

- (Optional) A multi-quarter KPI history (4+ quarters of metric name and value) used for
  trend context, and a product/feature breakdown table used for cross-source verification.

- (Optional) A table of published or disclosed figures per fiscal quarter and metric, used
  by Mode 4 to tie figures out.

- (Optional) A transcripts table (ticker, fiscal quarter, section, text) used for Mode 5,
  and a peer-set table (peer, fiscal quarter, growth metric, theme summary) used for peer
  context.
