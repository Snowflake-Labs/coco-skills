---
name: good_morning_cfo
description: >
  Daily executive briefing for the office of the CFO. Covers revenue performance vs
  forecast, top account movers, expense anomalies vs budget, consumption trends, and
  external market signals — delivered as a single consolidated brief under 600 words.
  Triggers: good morning, daily briefing, morning briefing, CFO update, start my day,
  what should I know today, daily revenue update, morning revenue update.
dehydrated: true
data_domain: "finance daily executive briefing, revenue forecast vs actuals, account-level revenue trends, expense vs budget anomalies, consumption usage"
mcp_optional: [web_search]
created_date: 2026-05-19
version: 1.1.0
license: Proprietary. See LICENSE.txt for complete terms
---

# Good Morning CFO (Dehydrated)

## Intent

A 5-minute daily briefing for the CFO and FP&A leadership. Surfaces revenue performance
against forecast, top accounts driving variance, expense anomalies vs budget, consumption
trends, and a brief market scan — in a single consolidated brief. Designed to replace the
morning routine of pulling three dashboards and scanning Slack channels.

Not for: deep root-cause analysis (use `revenue_analysis` or `budget_vs_actuals`), dashboard
creation, transactional data lookups, or scenario modeling (use `scenario_planning`).

## Behavior

### Trigger Detection

Activate on: "good morning", "daily briefing", "morning briefing", "CFO update",
"start my day", "what should I know today", "morning revenue update".

If the prompt is a generic "good morning" with no finance context, confirm via
`ask_user_question` before pulling data — this skill hits multiple tables per run.

Do NOT activate for:
- Revenue deep dives (route to `revenue_analysis`)
- Expense variance analysis (route to `budget_vs_actuals`)
- Forecast comparison across snapshots (route to `forecast_vs_forecast`)

### Workflow

**Run all four streams in parallel — do not stream partial results. Wait for all to
complete, then deliver the full brief in one response.**

| Stream | Data Source | What It Computes |
|---|---|---|
| A — Revenue pulse | `finance.revenue.forecast_daily` | Yesterday actual vs forecast; MTD and QTD pacing |
| B — Top movers | `finance.revenue.account_forecast` | Top 5 accounts driving positive variance; top 5 driving negative |
| C — Expense anomalies | `finance.budget.expense` | Lines where \|actual vs budget\| > threshold; flag favorable and unfavorable |
| D — External signals | web_search | Top 3 macro or industry signals from the last 24 hours |

**Stream E — Consumption pulse (optional):** If `finance.consumption.daily_usage` resolves,
include a fifth section. If LOW confidence, skip silently with no mention.

**Time window defaults:**
- Revenue pulse: yesterday vs prior business day; MTD vs MTD prior year
  - Use the latest day that has actuals. If yesterday has not landed, say "No data yet for
    <date> — last available: <date>" and run the pulse on the last available day.
  - Compare actuals to a forecast version created **before** the day being measured. In a
    table that holds many forecast versions, the latest version often equals actuals for
    past days, which makes every day look on forecast. Say which version is the baseline.
  - Show MTD and QTD against the same pre-dated forecast baseline and against prior year.
- Top movers: most recent week ending date vs prior week. If a mover's change is more
  than ~90% down (or the account goes to near zero), describe it as a possible contract
  end, migration or data gap to verify, not as an ordinary trend, and say what was checked.
- Expense anomalies: most recent closed month vs budget. Before ranking, remove subtotal
  and rollup rows and keep revenue lines separate from expense lines (see the cleanup step
  in `budget_vs_actuals`), otherwise totals and parent rows crowd out the real line items.
- External signals: last 24 hours

If the user specifies a different scope or time window, apply it to all streams.

**After delivery:** Ask "Anything here you want to drill into?" — do not proactively
run follow-up queries without user confirmation. Offer these follow-ups: a segment or
account drill-down, a flagged expense line, or a short email draft of the brief for the
user to review before anything is sent.

### Output Format

Output rules:
- Begin the response with the greeting line. Do not narrate the data pulls or print any
  text before it.
- Label fiscal periods as `FY<YYYY>-Q<N>` using the customer's configured fiscal calendar.
- For revenue, show actual against forecast **and** against the prior-year period when a
  prior-year comparison exists, so a beat against forecast is not read in isolation.
- Expense anomalies use the same tiered materiality as `budget_vs_actuals`. If the expense
  data includes a policy-exception flag (for example out-of-policy travel and expense
  lines), list those separately beneath the anomalies table.

```
# Good Morning, <Persona or "Team">
**<Date> — <One-sentence headline: beat/miss vs forecast>**

## Revenue Performance
- Yesterday: $XXM actual vs $XXM forecast (+/-$XXM, +/-X.X%)
- MTD: $XXM actual, pacing <on track / X.X% behind / X.X% ahead>
- QTD: $XXM actual vs $XXM target (<attainment>%)

## Top Movers (WoW)
**Driving positive variance:**
1. <Account> — +$XXM (<brief reason if available>)
2. ...
**Driving negative variance:**
1. <Account> — -$XXM
2. ...

## Expense Anomalies
| Line Item | Budget | Actual | Variance | Flag |
| <item> | $XXM | $XXM | +/-$XXM (+/-X.X%) | FAV / UNFAV |
<2-sentence commentary on the largest flagged line>

## Consumption Pulse  [include only if data available]
- WoW usage: +X.X%
- Top growing accounts: <list>
- Watch list (declining): <list>

## External Signals
- <signal 1> [<source>]
- <signal 2> [<source>]

---
Hydration: <N bindings via SV>, <N via INFORMATION_SCHEMA>, <N via hints>
```

### Confidence Handling

| Confidence | Behavior |
|---|---|
| HIGH (Streams A, B, C all resolved) | Run all streams; full brief |
| MEDIUM (any stream has column ambiguity) | Run all streams; prefix brief with: "Some bindings are MEDIUM confidence — verify hydration_hints.yaml" |
| LOW on non-critical (Stream C or E missing) | Skip that section; note "data unavailable"; produce brief from remaining streams |
| LOW on critical (Stream A — forecast_daily — missing) | Block. Output: "Daily briefing requires a revenue forecast binding. Add an entry under finance.revenue.forecast_daily in hydration_hints.yaml and re-run." |

### Guardrails

- Never expose internal system identifiers or raw IDs in output — use account names.
- Never assume a fiscal calendar. Read from plugin config (`feb_start`, `jan_start`, or `custom`).
- If no data has landed for the requested window, surface this explicitly: "No data yet for <window> — last available: <timestamp>."
- Currency: respect the `currency` column. Display in dominant currency; group by currency if multiple present.
- Web search: only include sources from the last 24 hours. If no relevant results, skip the section silently.
- Expense anomalies: apply a materiality threshold (suggest >5% OR >$50K absolute). Do not flag immaterial lines. Confirm the thresholds with the user rather than hardcoding them.
- Keep the full brief under 600 words.

### Error Handling

| Scenario | Action |
|---|---|
| Critical binding (Stream A) fails | Block briefing; output setup instructions |
| Query timeout | Retry once with a 30-day window; if still fails, omit that section |
| Web search returns nothing | Skip External Signals silently |
| User asks for a different scope mid-briefing | Re-run with new scope; do not merge scopes |
| Expense binding missing | Skip anomalies section; note "expense data not available" |

## Context Queries

- A table or semantic view at the (date, segment) grain with actual revenue, forecast
  revenue, and a currency code — used for the Revenue Pulse section (headline beat/miss,
  MTD pacing, QTD attainment). If a semantic view is available, complex follow-up
  questions route through Cortex Analyst. *(critical)*

- A table at the (week_ending_date, account_id) grain with the current week's forecast
  amount per account. The skill computes WoW change via LAG() — no pre-computed delta
  column required. Used for the Top Movers section. *(critical)*

- A table at the (period, line_item_id) grain with budget amount and actual amount per
  line item. Used for the Expense Anomalies section. Materiality threshold applied at
  runtime. *(non-critical — section skipped if absent)*

- (Optional) A table at the (usage_date, account_id) grain with a usage or units column
  for the Consumption Pulse section. Skill computes WoW change in SQL. Skipped if absent.
