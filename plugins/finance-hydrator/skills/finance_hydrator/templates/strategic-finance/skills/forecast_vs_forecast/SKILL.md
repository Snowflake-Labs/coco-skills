---
name: forecast_vs_forecast
description: >
  Compare two forecast snapshots to understand what moved between cycles. Surfaces
  new items, removed items, timing changes (push-outs, pull-ins), and amount changes.
  Includes forecast drift analysis and bias detection across rolling periods. Triggers:
  forecast vs forecast, FvF, forecast comparison, forecast drift, what changed in the
  forecast, compare F1 to F2, what moved between forecasts, forecast accuracy, forecast
  bias, are we forecasting worse, forecast roll-forward, forecast version comparison.
dehydrated: true
data_domain: "forecast snapshot comparison, forecast version analysis, timing changes, push-outs pull-ins, forecast drift, forecast accuracy, forecast bias detection"
created_date: 2026-06-08
version: 1.1.0
binding_contract: hydration_hints.yaml
license: Proprietary. See LICENSE.txt for complete terms
---

# Forecast vs Forecast (Dehydrated)

## Intent

Compare two forecast snapshots — or track how a forecast has drifted over multiple
cycles — to explain what moved between versions. Answers: what items are new, what
disappeared, what changed in timing (push-outs, pull-ins), and what changed in amount.
Also surfaces systematic forecast bias across rolling periods.

Serves FP&A analysts tracking forecast accuracy, finance leadership understanding
forecast hygiene, and anyone who needs to explain "why did the forecast change?"

Not for: comparing actuals to a fixed plan (use `budget_vs_actuals`), revenue
performance narrative (use `revenue_analysis`), forward-looking scenarios (use
`scenario_planning`). The key distinction: this skill requires a table with a version
or snapshot identifier column — multiple forecast versions per period.

## Behavior

### Trigger Detection

Activate on: "forecast vs forecast", "FvF", "forecast comparison", "forecast drift",
"what changed in the forecast", "compare F1 to F2", "compare two forecasts",
"what moved between versions", "forecast accuracy", "forecast bias", "forecast hygiene",
"are we forecasting consistently", "forecast roll-forward", "MoM forecast change",
"how has the forecast evolved".

Do NOT activate for:
- Budget vs actuals (fixed plan — route to `budget_vs_actuals`)
- Revenue performance narrative (route to `revenue_analysis`)
- Forward-looking "what if" scenarios (route to `scenario_planning`)
- Single-metric lookup ("what was Q2 forecast?")

If a comparison uses a fixed annual budget (not an evolving forecast), route to
`budget_vs_actuals`. The distinguishing question: "Does the comparison baseline
change over time, or was it set once?"

### Workflow

**Mode selection.** Decide the data shape first:
- **Item-level mode** (default): the forecast table has an item identifier (deal, line
  item, account), so push-out / pull-in / new / removed can be classified. Follow Steps
  1–4 below.
- **Time-series mode**: the forecast is an aggregate series with no item identity (for
  example total revenue by period). The variance taxonomy does not apply. Skip Q3 and the
  taxonomy, run Q1, Q2 (if a segment column exists), the error metrics in Bias Analysis,
  and the Stability by Horizon step. State the mode in the output header.

**Step 1 — Select forecast versions**

Query the forecast table to list all distinct snapshot/version identifiers, ordered by
creation date (most recent first). Present as a numbered list and ask the user to confirm
which two versions to compare:

- **Version A** (baseline — "what we said before"): default = second most recent
- **Version B** (comparison — "what we say now"): default = most recent

If the user specifies versions explicitly ("compare F1 to F2"), skip this prompt.

Version labels are often inconsistent (for example Preliminary vs Final of the same cycle,
separator or suffix variants, or an extra pass tagged for the next planning year). Order by
run date, not by label. Show the label and run date side by side, default to the Final
version of each cycle, and ask before comparing a preliminary or special-pass version.
Do not compare versions whose forecast horizons differ without restricting both to the
periods they share (total forecast across different horizons is not comparable).

> **Stopping point:** Wait for version confirmation before proceeding to Step 2.

**Step 2 — Determine analysis window**

From the confirmed Version A, derive the time window it was predicting (typically the
calendar month or quarter immediately after the forecast creation date). Store
`analysis_start` and `analysis_end` for all subsequent queries.

Periods that closed before Version A was created are usually actuals in both versions and
show zero change. Report them as "locked actuals, no change" (or exclude them) so they do
not dilute percentages, and compute the headline change only over periods that are still
forecast in both versions. Put the current, partly elapsed period on its own line.

**Step 3 — Run four queries in parallel**

Do not stream partial results. Wait for all four to complete.

| Query | What it computes |
|---|---|
| Q1 — High-level delta | Total metric: Version A vs Version B; absolute and % change |
| Q2 — Movement by segment | Which dimension (geography, product, business unit) drove the most change. Show net change plus counts of items up, down, new and removed per segment: a small net can hide large offsetting moves, so say so when up and down counts are both large |
| Q3 — Item-level movers | Top 30 items by absolute change; classified by variance type |
| Q4 — Bias analysis | Rolling N-period view: are we systematically over- or under-forecasting? |

**Step 4 — Consolidate and present**

Present all four sections as a single report in drill-down order (Q1 → Q2 → Q3 → Q4),
regardless of execution completion order.

**Step 5 (on request, or in Time-series mode) — Forecast stability by horizon**

For each forecast run date, compute the total forecast over fixed forward windows (for
example the next 91 days and the next 364 days). Only use run dates that have the full
window of forecast data. Then show how each window total changes from one run date to the
next, and as a percentage change since the first run in the selected range. A stable
forecast moves little between runs; a large drift means the forecast is being revised
heavily as new data arrives. Because the window moves forward with each run date, a
rising total partly reflects ordinary growth rather than revision. To isolate revision,
also compare successive runs on the same fixed calendar period (as in the main version
comparison) and report both. If the data has a model-type column (for example ensemble vs
an alternative model), compare by model type. Aggregate to fiscal quarters and years using
the customer's fiscal calendar — never assume one.

When a naive baseline is available (for example the latest actual held flat, a trailing
growth extrapolation, or a time-series baseline), show it alongside, so a forecast change
is judged against a naive alternative as well as against the prior version.

### Variance Taxonomy

Every item-level change in Q3 is classified into one of five types:

| Category | Logic | Interpretation |
|---|---|---|
| Timing match, amount change | Same item, same period, different amount | Forecast refinement — right timing, updated magnitude |
| Push-out | Item predicted for an earlier period in Version A, later in Version B | Delay: procurement cycle, approval holdup, rescheduling |
| Pull-in | Item predicted for a later period in Version A, earlier in Version B | Acceleration: early close, expedited renewal |
| New in Version B | Present in Version B only | Added between cycles: new deal, new line item, data correction |
| Removed from Version B | Present in Version A only | Removed: cancellation, reclassification, data cleanup |

When the data supports finer granularity (push-out by 1 month vs 2 vs 3+), use the
finer label.

### Bias Analysis

Across the last N available forecast versions (default: last 6), compute:
- Mean error (signed bias): average of (actual − forecast) per period
- MAE: average of |actual − forecast| — signed errors can cancel out, so always report
  MAE alongside bias
- RMSE: square root of the average squared error — penalizes large misses
- When forecasts are expressed as growth rates, compute the error in percentage points
  (actual growth rate − forecast growth rate) and require a complete set of forecast
  quarters per run date before computing RMSE and MAE
- Summarize errors by forecast horizon (for example the first, second, third and fourth
  quarter ahead) with count, mean, standard deviation, and quartiles, when version run
  dates are available, since short-horizon and long-horizon accuracy differ
- Bias direction: see the convention below (actual − forecast); state it in the output
- Trend: is bias improving or worsening over recent cycles?

**Bias direction convention (standard statistical):**
- Mean error = average of (actual − forecast) per period
- Positive mean error = actual > forecast = **under-forecasting** (forecasts were too pessimistic)
- Negative mean error = actual < forecast = **over-forecasting** (forecasts were too optimistic)

| Bias Level | Threshold (tunable default) |
|---|---|
| Low | |Mean error / forecast| < 2% |
| Moderate | 2–5% |
| High | > 5% |

The 2% and 5% cutoffs are starting defaults; confirm or adjust them with the customer.
Present bias result only if actuals are available for at least 3 periods in the dataset.

**Version order guardrail:** Version A must be the earlier snapshot; Version B must be the later one.
If the user provides version identifiers out of order (A is newer than B), warn and ask for confirmation
before proceeding — swapping A and B inverts all push-out/pull-in classifications.

### Output Format

```
# Forecast Comparison: Version A vs Version B
**Analysis period: <start> to <end>**

## High-Level Delta
| Metric | Version A | Version B | Change ($) | Change (%) |
|---|---|---|---|---|
| <metric> | $XXM | $XXM | +/-$XXM | +/-X.X% |

<1–2 sentence commentary: favorable/unfavorable; what the overall change signals>

## Movement by <Segment Dimension>
| <Dimension> | Version A | Version B | Change ($) | Change (%) | % of Total Move |
|---|---|---|---|---|---|
| ... |

<1–2 sentence commentary on the dominant segment driver>

## Item-Level Movers (Top 30)
| # | Item | Segment | Ver A | Ver B | Change | Type |
|---|---|---|---|---|---|---|
| 1 | ... | ... | $XXM | $XXM | +/-$XXM | Push-out / Pull-in / etc. |

<Commentary on the top 5 items: patterns, root causes where context is available>

## Forecast Bias (last N periods)
Mean error: +/-X.X% [bias direction: over- or under-forecasting]
MAE: X.X | RMSE: X.X (same units as the error)
Trend: [improving / worsening / stable]

<1–2 sentences: actionable observation — should the team adjust methodology?>

---
Versions compared: <Version A name/date> vs <Version B name/date>
Hydration: <binding resolution summary>
```

### Confidence Handling

| Confidence | Behavior |
|---|---|
| HIGH (forecast table with version column, amount, segment, date, and item ID all resolved) | Full 4-query workflow; complete report including bias analysis |
| MEDIUM (forecast binding resolved but segment column absent) | Run Q1, Q3, Q4; skip Q2 (segment); omit Segment column from Q3 item table as well; note degraded sections |
| MEDIUM (version metadata absent) | Run Q1–Q4 but prompt user for analysis window in Step 2 before proceeding |
| LOW on critical (no forecast table found, or no version/snapshot column) | Block. Output: "Forecast comparison requires a table with a version or snapshot identifier column. Verify the forecast binding resolves correctly and re-verify." |
| LOW on actuals (Q4 — bias analysis — unavailable) | Skip bias section; note "actuals not available for bias analysis"; run Q1–Q3 |

### Guardrails

- Never confuse forecast snapshots with a fixed annual budget. If only a budget table
  is found (no version column), redirect to `budget_vs_actuals`.
- Never compare a version to itself. If the user specifies the same version for both
  A and B, output: "Cannot compare a forecast version to itself. Select two distinct versions." and re-prompt.
- Version list truncation: if more than 10 versions exist, present only the 10 most
  recent and note the total count available.
- Favorable vs unfavorable direction: for revenue/bookings forecasts, an increase in
  Version B = favorable. For expense forecasts, a decrease = favorable. Derive from
  metric type — do not hardcode.
- Present variance labels exactly as classified in the taxonomy — do not rename or abbreviate.
- Do not expose internal forecast system IDs — use business-friendly version labels (date, name).
- Never assume a fiscal calendar — read from plugin config or ask the user.
- Bias analysis requires historical actuals. Do not present bias if fewer than 3 actual
  periods are available.

### Error Handling

| Scenario | Action |
|---|---|
| Only one forecast version in the table | Cannot compare; output: "Only one forecast version found. Need at least two versions for comparison." |
| Version identifiers not understood | List all available version identifiers; let user pick |
| Analysis window has no data | Output: "No data for the derived analysis window <start>–<end>. Verify the forecast covers this period." |
| Q3 timeout on 30 items | Retry with top 10; note truncation |
| User asks about a metric not in the resolved binding | Ask which metric; suggest available options |

## Context Queries

- A forecast snapshot table where each row is one (version_id, item_id, period_date,
  forecasted_amount). This is the primary binding — it must contain at least two distinct
  version identifiers to enable comparison. Columns: forecast version identifier
  (STRING), item identifier (STRING or NUMBER), prediction period (DATE), forecasted
  amount (NUMBER), and optionally actual amount (NUMBER — if actuals are stored alongside
  forecasts) and segment/dimension (STRING). *(critical)*

- A version metadata table with one row per version_id: forecast version identifier
  (STRING), creation or run date (DATE), prediction period start (DATE), prediction
  period end (DATE). Used to auto-derive the analysis window and provide defaults for
  version selection. **This table is functionally required for Step 2 to derive the
  analysis window automatically.** If absent, Step 2 must ask the user for the analysis
  period manually — note this explicitly in the output before proceeding. *(non-critical
  for skill activation, but Step 2 blocks on user input if absent)*

- (Optional) A dimension/lookup table mapping item identifiers to display names. Columns:
  item identifier (STRING or NUMBER), display name (STRING). Used to replace opaque IDs
  with readable account names, deal names, or product labels in item-level output.

- (Optional) A historical actuals table or an actuals column in the forecast table, used
  for Q4 bias analysis. Requires at least 3 completed periods with both forecast and
  actuals. If absent, the bias section is skipped.

- (Optional) A model-type column on the forecast table (for example ensemble vs an
  alternative model), used to compare revisions and errors by model type.

- (Optional) A fiscal calendar mapping periods to fiscal year and quarter, used for
  period aggregation and horizon labeling.
