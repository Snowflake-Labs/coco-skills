---
name: attrition_insights
description: >
  Attrition intelligence brief for HR Business Partners, People leadership, and
  executive leaders. Generates daily (default) or weekly attrition analysis
  highlighting material changes, watch areas, and recommended actions. Supports
  HRBP Mode (operational detail), CPO/HR Leadership Mode (executive summary),
  and org-scoped mode for executive leaders (auto-detected). Triggers: attrition
  brief, attrition analysis, turnover report, regrettable exits, watch areas,
  voluntary attrition, involuntary attrition, attrition by org, attrition trend,
  upcoming exits.
  Do NOT use for: headcount counts or trends (use headcount_insights), a
  leader's full org health or performance outcomes (use org_health_report), or
  recruiting pipeline metrics (use good_morning_recruiting).
license: Proprietary. See LICENSE.txt for complete terms
---

# Attrition Insights (Dehydrated)

## About this skill

- **What it does:** A daily (default) or weekly attrition brief: what changed materially, which orgs or groups to watch, and recommended next steps. Covers total, voluntary, involuntary and regrettable exits.
- **Who it's for:** HR Business Partners (operational detail, the default), People / HR leadership (executive summary), and executive leaders (automatically scoped to their own org).
- **Works with:** Uses the same headcount population as `headcount_insights`, so the two always agree. `good_morning_chro` shows a short version of this brief; use this skill to dig deeper. For manager-level drivers, follow up with `org_health_report`.
- **Before you start:** This skill runs on the data and definitions connected by **People Hydrator** (`plugins/people-hydrator/`). If it hasn't been set up for your account yet, run People Hydrator first.
- **How to use it:** Ask for an "attrition brief", "turnover report" or "regrettable exits". Add a timeframe ("weekly"), an org, or a date. The attrition rate is exits in the period divided by the average of headcount at the start and end of the period (trailing 12 months by default) unless you chose a different definition during setup.
- **If you don't get what you expect:** If a section is missing or says data isn't available, that data wasn't connected during setup: re-run People Hydrator to add it. If a number looks wrong, tell People Hydrator what you expected; it turns that into a checked correction. If the skill says a table or column has changed, re-run People Hydrator for this skill.
- **Good to know:** Numbers follow the definitions you confirmed in People Hydrator (who counts in headcount, what counts as voluntary or regrettable). The skill reads your data and never changes it.

## Intent

Surface only what changed materially, score risk compositely, and recommend next actions — all quickly. Serves HR Business Partners who need operational detail for org/team-level watch areas, and People / HR leadership who need executive strategic analysis with board-ready talking points. Executive leaders automatically receive an org-scoped view of their own management chain. Not for: headcount sizing queries (use `headcount_insights`), performance analytics (use `org_health_report`), or recruiting pipeline reporting.

## Behavior

### Modes

| Mode | Audience | Default? | Focus |
|------|----------|----------|-------|
| **HRBP** | HR Business Partners | Yes | Operational detail — org/team-level watch areas, cohort drivers, specific follow-up actions |
| **CPO / HR Leadership** | People / HR Leadership | No | Executive strategic — company-wide trends, org comparisons, board-ready talking points |
| **Executive** | Executive leaders (auto-detected) | Auto | Org-scoped attrition for the logged-in exec's management chain. Uses CPO mode format with org filter applied |

### Input Parameters

| Parameter | Options | Default | Required? |
|-----------|---------|---------|-----------|
| `--mode` | hrbp, cpo | hrbp | No |
| `--timeframe` | daily, weekly | daily | No |
| `--as_of_date` | YYYY-MM-DD | Current date | No |
| `--org_scope` | Specific org name(s), or "all" | all | No |
| `--include_regrettable` | true, false | true | No |
| `--include_new_hires` | true, false (filter to <1yr tenure) | false | No |
| `--top_n_watch` | Number of watch cohorts to surface | 5 | No |

### Audience Detection & Org Scoping

| Audience | Default Scope | Org Filter |
|----------|--------------|-----------|
| HR / People Analytics leadership | Company-wide (all employees) | Not applied — uses `--org_scope` / `--manager_scope` params if provided |
| Executives one level below the top of the hierarchy | Org-scoped to the exec's management subtree (one level below the top) | Exec's direct-report subtree via identity resolution |
| Executives two levels below the top of the hierarchy | Org-scoped to the exec's management subtree (two levels below the top) | Exec's direct-report subtree via identity resolution |
| Top-level executive (e.g., CEO) | Company-wide | No filter |

**Detection logic:** Check the invoking profile or session role. If an executive profile is detected, use org-scoped queries with the exec's management chain predicate from identity resolution. If an HR profile is detected, use company-wide queries.

**Company-wide on demand (executive mode):** If an exec asks for "company attrition", "all orgs", or "compare to company", remove the org filter and run company-wide.

### Attrition Definitions

**Exit filtering rule (MANDATORY):** Count an exit one day after the termination date. Using the raw snapshot date for exit filtering produces incorrect counts. Apply: exit date = termination date + 1 day, and filter using this derived date against the period window.

**Attrition rate formula:** Attrition rate = exits in the period ÷ average of headcount at period start and period end × 100, i.e. denominator = (HC_start + HC_end) / 2. Period start HC = snapshot at (or nearest before) the period start; period end HC = latest snapshot in the period; state both dates. Default period is trailing 12 months (T12M); the same formula applies to any requested period (YTD, month, each month, quarter, custom range), with no annualization. Voluntary / involuntary / regrettable rates use the same denominator with the matching exit subset. This is the default; your hydrated configuration may set a different attrition method (attrition_rate_method).

**Standard attrition types:**
- Total attrition (all exits)
- Voluntary attrition (resignations / voluntary separations)
- Involuntary attrition (performance, RIF, other company-initiated)
- Regrettable attrition (exits the company would have preferred to prevent)
- Non-regrettable attrition

### Execution Waves

**Wave 0: Identity & Context (all modes)**

1. Detect session profile / available roles.
2. Resolve user identity (for executive mode): get the exec's name, org level, and management chain filter.
3. Route by org level: top-level executive → auto-scope; other executive → present list to select.
4. Select appropriate role for data access.
5. Greet user.

**Greeting format:**
- Executive (C-Level): `Good Morning, [First Name] — here's your Attrition Brief for [Date]. Scope: [Full Name]'s Org`
- Executive (Other): `Good Morning, [First Name] — here's the Attrition Brief for [Date]. Scope: [Selected Exec]'s Org`
- HR / People: `Good Morning, [First Name] — here's the Attrition Brief for [Date]. Scope: Company-Wide`
- Fallback: Use username without domain suffix

**Wave 1: Snapshot — Core Metrics**

Pull current-period attrition rates and month-over-month exit trend in a single efficient query pass:
1. Run snapshot query for total, voluntary, involuntary, and regrettable attrition rates.
2. Run month-over-month exit trend (last 13 months of monthly exit counts).
3. Apply spike detection: flag if the current period rate exceeds the prior-period rate by a configurable threshold.
4. Render snapshot table.

**Wave 2: Watch Areas — Watchlist Cohorts**

Identify cohorts with elevated attrition risk across four dimensions simultaneously:
1. Run a unified scan across: org group, management level, tenure cohort, and region.
2. Compute composite risk score per cohort (rate deviation from company average + trend direction + regrettable share).
3. Rank top N cohorts by risk score.
4. Generate a one-line narrative per watch cohort.

**Wave 3: Drivers — Termination Reasons**

1. Run top termination reasons by count for the current period.
2. Run QoQ shift: which reasons increased or decreased vs prior period.
3. Render Drivers table.

**Wave 4: Regrettable Deep Dive (when `--include_regrettable = true`)**

1. Run regrettable exit breakdown by org group and management level.
2. Surface top 20 regrettable cohorts ranked by regrettable rate.

**Wave 5: Future Exits Pipeline**

1. Run upcoming confirmed exits (employees with a future termination date already entered).
2. Summarize by org, management level, and regrettable flag.

**Wave 6: Weekly Additions (when `--timeframe = weekly`)**

1. Run 4-week trailing exit trend.
2. Detect sustained hotspots: orgs with elevated attrition for 3+ consecutive weeks.
3. Include narrative on external benchmark context (use customer-configurable benchmark rates; do not hardcode industry averages).

**Wave 7: Recommendations & Follow-Up**

1. Generate top 3 suggested actions from highest-risk watch areas.
2. Generate personalized follow-up prompts based on mode (HRBP detail vs executive summary).
3. Offer stopping point: "Drill into any watch area, or compare specific orgs?"

### Financial Year Convention

Resolve fiscal year, quarter, and period start/end dates from the customer's fiscal calendar data rather than assuming a fixed calendar.

### Change Detection

Flag metrics as material changes if they deviate by a configurable threshold from the prior period. Configurable thresholds replace hardcoded cutoffs that may not fit every customer's workforce scale.

### Composite Risk Score

Risk score per cohort = weighted combination of:
- Deviation of cohort attrition rate from company baseline
- Trend direction (worsening vs improving over trailing 3 periods)
- Regrettable share of exits in the cohort

Thresholds are customer-configurable at setup; do not hardcode specific percentages.

### Confidence Handling

- **HIGH confidence:** All data layers resolve (snapshot + exits + org mapping). Run full 7-wave analysis.
- **MEDIUM confidence:** Core attrition snapshot resolves but org mapping or regrettable flag is thin. Run with affected sections marked "(limited data)".
- **LOW confidence on non-critical bindings** (e.g., termination reason, org labels): Run with those sections noting "data unavailable".
- **LOW confidence on critical bindings** (exit data): Skip skill execution; output the data domain description and recommend the customer connect their HRIS termination data.

### Output Formatting Rules

**HRBP Mode:** Operational tables — watch area list with cohort, rate, trend, risk score; termination reasons breakdown; regrettable heatmap; recommended actions.

**CPO / HR Leadership Mode:** Executive summary — key KPIs upfront, comparison across org groups, board-ready talking points, trend chart (text representation), and strategic recommendations.

**Weekly additions:** 4-week trend table per watch area cohort.

Rules:
- Every metric shows the analysis period and data freshness date.
- Status indicators on every row (🔴 above threshold, 🟡 approaching, ✅ below).
- Never expose schema names, table names, column names, SQL, or role names.
- Regrettable / non-regrettable split is only surfaced if data is accessible; if masked, note that elevated access is needed.

### Edge Case Handling

| Scenario | Handling |
|----------|---------|
| Snapshot date not available | Return nearest available with warning |
| Cost center not in org mapping | Exclude from org breakdowns; include in totals |
| Role cannot access exit detail / masking active | Report: "Cannot access exit detail data. Elevated access is required." |
| Data stale (>7 days old) | Banner: "⚠️ Data may be stale — last snapshot: [date]. Contact your People Analytics team." |

### Guardrails

- Never expose attrition data in a way that could identify individual employees (aggregate only; surface individuals only to credentialed HRBP users with appropriate access).
- Never hardcode external benchmark percentages — use customer-configurable thresholds.
- Always apply the exit date offset (+1 day) consistently across all exit queries.
- If the regrettable flag is masked for the current session role, omit the regrettable column and surface a note rather than reporting zero.

### Error Handling

| Scenario | Response |
|----------|---------|
| Authorization / permissions error on exit data | "I wasn't able to pull attrition data right now — this usually means your account needs a permissions update. Reach out to your People Analytics team." Never expose schema, table, or error text. |
| Query timeout | "This is taking longer than expected — the system might be busy. Want me to try again?" |
| Exec identity not resolvable | "I couldn't resolve your identity automatically. Which leader's org would you like to see?" Present list of available executives. |

## Quality

This skill is tested against `evals/attrition_insights/golden_cases.md` and `evals/attrition_insights/eval_plan.md` (People Hydrator runs them in its Evaluate stage). They are tests, not runtime instructions: follow this file and its customer configuration (added at hydration).

## Context Queries

- An employee attrition snapshot table at one row per employee per month, with columns for snapshot date, active headcount flag, exit flag, voluntary exit flag, involuntary exit flag, regrettable flag, termination reason category, management level, tenure group, termination date, cost center ID, region, and management chain levels (by name, several levels deep) — used for all attrition rate, watch area, and driver computations. Regrettable and voluntary/involuntary flags may be masked for some session roles.

- A cost center → finance org mapping table at one row per cost center, with cost center ID, finance organization name, and cost center leader — used to translate cost centers into org labels. Dual-join fallback required for decommissioned cost centers.

- An individual exit records table at one row per exit event, with employee ID, termination date, regrettable flag, termination category (voluntary / involuntary), termination reason, and org hierarchy IDs — used for regrettable deep dive and future exits pipeline.

- A fiscal calendar / date reference table mapping calendar dates to fiscal year, fiscal quarter, and period boundaries — used for period window computation.

- A user identity / org scoping table that maps the current session user to their position in the management hierarchy — used for executive org-first scoping.

- (Optional) A pre-computed executive reporting summary table that aggregates attrition metrics by org for rapid KPI delivery — used as a fast-path option for CPO / executive mode.

- (Optional) A semantic view over the attrition snapshot and org mapping tables for Cortex Analyst-driven follow-up questions like "show me regrettable attrition trend for [org] this year".
