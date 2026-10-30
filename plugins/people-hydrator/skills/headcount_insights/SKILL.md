---
name: headcount_insights
description: >
  Headcount analysis skill for People Analytics. Provides ending headcount,
  quarterly and monthly trends, QoQ/YoY change, headcount by organization /
  region / geography / management level / tenure / cost center, headcount
  movement drivers (hires, exits, net change), org shape (manager %, span of
  control), and workforce composition (% new, % retained). Supports
  company-wide and org-scoped (executive) views with parameterized filters.
  Triggers: current headcount, headcount trend, headcount by org, headcount by
  region, headcount change, movement drivers, span of control, workforce
  composition, full headcount summary, YoY headcount.
  Do NOT use for: attrition rates or regrettable exits (use attrition_insights),
  manager-level org health (use org_health_report), or a leader's weekly hiring
  report (use hiring_brief).
license: Proprietary. See LICENSE.txt for complete terms
---

# Headcount Insights (Dehydrated)

## About this skill

- **What it does:** Answers headcount questions consistently: current headcount, monthly and quarterly trends, change over time, headcount by org, region, management level, tenure or cost center, hires vs exits, span of control and workforce composition.
- **Who it's for:** HR / People Analytics leaders (company-wide) and executive leaders (scoped to their own management chain, with company-wide on request).
- **Works with:** Defines the headcount population that `attrition_insights`, `good_morning_chro` and `hiring_brief` also use. For attrition rates, use `attrition_insights`; for manager-level drivers, use `org_health_report`.
- **Before you start:** This skill runs on the data and definitions connected by **People Hydrator** (`plugins/people-hydrator/`). If it hasn't been set up for your account yet, run People Hydrator first.
- **How to use it:** Ask "what's our current headcount?", "headcount by org", "headcount trend" or "full headcount summary". The skill shows a menu of analyses if your question is broad.
- **If you don't get what you expect:** If a section is missing or says data isn't available, that data wasn't connected during setup: re-run People Hydrator to add it. If a number looks wrong, tell People Hydrator what you expected; it turns that into a checked correction. If the skill says a table or column has changed, re-run People Hydrator for this skill.
- **Good to know:** Who counts in headcount (for example, which employee types and statuses) is the definition you confirmed during setup. The skill reads your data and never changes it.

## Intent

Provide canonical answers to headcount questions — consistent, validated, and sliceable by organization, geography, cost center, management level, and time period. Serves two audiences: HR / People Analytics leaders who need company-wide data, and executive leaders who need org-scoped views of their own management chain. This skill covers **headcount-only** metrics. It does not cover attrition rates, performance / retention metrics, or recruiting pipeline — those are handled by dedicated skills.

## Behavior

### Audience & Scope

This skill serves two audiences with different default scoping:

| Audience | Default Scope | Company-Wide Available? |
|----------|--------------|------------------------|
| HR / People Analytics leadership | Company-wide (all employees) | Always |
| Executive leaders | Org-scoped to the exec's management chain | Yes — user can request company-wide |

When running for an executive, auto-resolve their identity and org scope before any analysis queries. If identity cannot be resolved, present a list of available executives for the user to select.

**Company-wide on demand:** If a scoped user asks for "company headcount", "all orgs", or "total", remove the org filter and run company-wide.

### Profile Detection (Step 0)

Before any analysis, determine which audience profile is active:
1. Detect available roles for the current session user.
2. If an executive role is detected, enter **org-first mode**: resolve the exec's identity and org scope, then default all queries to that org.
3. If an HR / People Analytics role is detected, skip personalization and run company-wide as default.
4. If ambiguous, default to company-wide.

### Category Menu (Entry Point)

When the user triggers this skill without specifying a category, present this menu with hook points:

| # | Category | Hook Point |
|---|----------|-----------|
| 1 | **Ending Headcount** | "Current headcount as of [Date]" |
| 2 | **Headcount Trends** | "QoQ change: [+/-X] ([Y]%). YoY: [+/-Z] ([W]%)" |
| 3 | **Headcount by Organization** | "[Top Org] has [X] employees — [Y]% of total" |
| 4 | **Headcount by Geography** | "[Top Location] has the highest concentration" |
| 5 | **Headcount Change** | "[Org A] grew by [+X] while [Org B] declined by [-Y]" |
| 6 | **Movement Drivers** | "Hires and exits driving net change" |
| 7 | **Org Shape & Span of Control** | "[X]% managers, avg [Y] direct reports" |
| 8 | **Workforce Composition** | "[X]% new hires, [Y]% retained workforce" |

If the user's question already maps to a category, skip the menu and route directly.

### Routing Quick Reference

| User Says | Route To |
|-----------|----------|
| "Current headcount" / "how many employees" | Cat 1 — Ending Headcount |
| "Total headcount" / "all employees" | Cat 1 — total (all employee types, no type filter) |
| "Quarterly trend" / "monthly trend" | Cat 2 — Headcount Trends |
| "Headcount by org" / "by executive org" / "by cost center" | Cat 3 — By Organization |
| "Headcount by region" / "by country" / "by location" | Cat 4 — By Geography |
| "Which orgs are growing?" / "YoY headcount" | Cat 5 — Headcount Change |
| "What's driving the change?" / "net workforce change" | Cat 6 — Movement Drivers |
| "Manager %" / "span of control" / "over-spanned managers" | Cat 7 — Org Shape |
| "% new hires" / "% retained" / "workforce composition" | Cat 8 — Workforce Composition |
| "Full headcount summary" | All categories combined |
| Filtered query (e.g., "headcount in [Region]") | Parameterized filter pass-through |

### Workflow (Per Category)

1. **Profile detection** — Determine audience scope (Step 0 above). For execs, resolve identity and org filter.
2. **Execute queries** — Run the relevant category queries with the resolved scope.
3. **Output** — Present results following the Output Formatting Rules below.
4. **Drill-down prompts** — Offer 3–5 follow-up questions the user can ask to go deeper.
5. **Cross-skill handoffs** — When headcount signals imply deeper analysis, hand off to the appropriate skill (see Cross-Skill Handoffs below).

### Financial Year Convention

The financial year must be resolved from the customer's fiscal calendar. The skill must determine the current fiscal year, current quarter, and quarter-end snapshot dates dynamically from the customer's date reference table rather than assuming any fixed calendar.

### Employee Type Filter

**Active Regular Headcount (default):** Standard employee types only — excludes interns, contingent workers, and contractors. Apply this filter by default.

**Total Headcount (on request):** All employee types — no type filter. Use when the user explicitly asks for "total" or "all employees".

Always apply an **active status** filter to exclude terminated employees from point-in-time snapshots.

### Exit Date Logic

When computing headcount movement (exits), count an exit on the day **after** the termination date. Apply this rule consistently across all movement queries.

### Avg Headcount Formula

Avg HC = sum of month-end headcount values / number of months in the period.

Attrition rate (when referenced in movement analysis) does not use this average: it is exits in the period ÷ (HC_start + HC_end) / 2 × 100, with HC_start = snapshot at (or nearest before) the period start and HC_end = latest snapshot in the period (default T12M, no annualization), unless your configuration sets a different attrition method.

### Organization Mapping Convention

Map employees to organizational units using a cost center → organization lookup table. Apply a dual-join pattern with a fallback for decommissioned cost centers. Always use the finance-certified cost center mapping as the source of truth for org mapping.

### Status Indicators

Apply to every metric row in output:

| Symbol | Meaning | When to Use |
|--------|---------|------------|
| ✅ | Healthy / On target / Growth | HC growing or stable per plan |
| 🟡 | Monitor / Approaching threshold | Flat or small unexpected movement |
| 🔴 | Alert / Action needed | Significant unplanned decline |
| 📈 | Growth trend | Net positive HC change |
| 📉 | Decline trend | Net negative HC change |
| ⚠️ | Warning / Structural concern | Org concentration risk or data gap |

### Analysis Period Dating Rules

Every output **must** display the analysis period dates. Include:
- Analysis period start and end dates
- Data freshness (latest snapshot date)
- Fiscal year and quarter label
- For comparative outputs (QoQ/YoY), confirm both dates explicitly

### Confidence Handling

- **HIGH confidence:** All data sources resolve. Run full analysis; produce complete output.
- **MEDIUM confidence:** Core headcount resolves but some dimensional data (e.g., cost center mapping, org labels) is thin. Run with affected dimensions marked "(limited data)".
- **LOW confidence on non-critical bindings** (e.g., org labels, location groupings): Run with those dimensions noting "data unavailable"; continue with what resolves.
- **LOW confidence on critical bindings** (employee snapshot data): Skip skill execution; output the data domain description and recommend the customer connect their HRIS employee data.

### Output Formatting Rules

- **Tables over paragraphs.** Every category defaults to tabular output.
- **Status indicators on every row.** Never omit ✅ 🟡 🔴.
- **Date header first.** Every output opens with the analysis period and data freshness.
- **Start with wins.** Lead with growth / healthy metrics before surfacing declines.
- **One-liner insights only.** After each table, one concise sentence of key insight.
- **Drill-down questions at end.** Offer 3–5 follow-up questions.
- **Never expose** internal source names, table names, column names, SQL, or schema details to the user.

#### Standard Output Template (Mode A — Structured Tabular)

```
**Analysis Period:** [Start Date] — [End Date] | **Generated:** [Timestamp]
**Data Freshness:** Snapshot as of [Date] | **FY:** FY[XX] Q[X]

[Category heading]

[Table with status indicators]

[One-liner insight]

**Drill deeper:**
- [Question 1]
- [Question 2]
- [Question 3]
```

#### Mode B — Conversational (for follow-ups)

2–5 sentence response with inline data. Use when the user asks a focused follow-up question.

### Org Shape Metrics (Category 7)

Manager % is computed based on employees with at least one direct report (not solely by job title or grade). Apply this definition consistently to match the source-of-truth dashboard.

Span of control buckets: 0 DRs, 1–2 DRs, 3–9 DRs, 10+ DRs.

**Outlier thresholds:**
- Over-spanned: >10 direct reports (flag 🔴)
- Under-spanned: <3 direct reports for a manager (flag 🟡)
- Healthy span: 5–8 direct reports (✅)

Flag ICs with direct reports (unusual) and managers with zero reports (structural gap).

### Workforce Composition (Category 8)

**% New:** Employees hired within a configurable tenure window (typically ≤ 12 months) as a share of total active HC.

**% Retained:** Employees beyond that tenure window as a share of total active HC.

Present by org group on request.

### Edge Case Handling

| Scenario | Handling |
|----------|---------|
| Snapshot date not available for requested quarter | Return nearest available date with warning |
| Cost center not in org mapping (unknown org) | Exclude from org breakdowns; include in company totals |
| Employee appears in both exits and starts (rehire) | Count both events separately |
| Zero headcount for an org in a quarter | Show 0 in change calculations, not NULL |
| Negative net change at company level | Flag 🔴; recommend movement driver drill-down |
| Single org dominates HC (>30% share) | Add ⚠️ concentration warning in org breakdown |
| Exit data unavailable due to permissions | Skip Category 6; note "Movement data requires elevated access" |
| Quarter-end date has no snapshot data | Use nearest available date within ±5 days; add data gap note |

### Cross-Skill Handoffs

| Trigger Signal | Handoff |
|---------------|--------|
| High exits or declining net change | "For detailed attrition rate analysis and regrettable exit breakdown, ask for **attrition analysis**." |
| Org declining consistently | "To investigate manager-level drivers, ask for an **org health report** (`org_health_report`)." |
| Net negative change | "To understand retention signals driving negative net change, ask for the **retention & attrition** analysis in `org_health_report`." |
| Large hiring spike | "To check hiring quality and offer acceptance rates, ask for **recruiting metrics**." |

### Guardrails

- Never expose table names, column names, schema paths, SQL, or role names to the user.
- Never present undated numbers — every metric must show "as of [Date]".
- If data quality is degraded (stale snapshot, partial org mapping), surface a banner to the user before presenting results.
- Always apply the active status filter for snapshot queries; never include terminated employees in point-in-time counts.

### Error Handling

| Scenario | Response |
|----------|---------|
| No snapshot data available | "I wasn't able to pull headcount data right now. This usually means a permissions update is needed — reach out to your People Analytics team." |
| Org filter resolves to zero employees | "No employees found for that org scope. Verify the org name or try a broader filter." |
| Data freshness stale (>7 days) | Banner: "⚠️ Data may be stale — last snapshot: [date]. Contact your People Analytics team." |
| Authorization / permissions error | "I wasn't able to pull data right now — this is usually a quick permissions fix. Reach out to your People Analytics team." Never expose schema, table, or error text. |

## Quality

This skill is tested against `evals/headcount_insights/golden_cases.md` and `evals/headcount_insights/eval_plan.md` (People Hydrator runs them in its Evaluate stage). They are tests, not runtime instructions: follow this file and its customer configuration (added at hydration).

## Context Queries

- A daily or weekly employee snapshot table at one row per employee per snapshot date, with columns for employee ID, employment status (active/inactive), employee type (regular, fixed-term, contingent, intern), management level (individual contributor / manager tiers), tenure, org hierarchy (management chain levels by name and ID, as deep as the customer's hierarchy provides), cost center ID, location / region / country, and hire date — used for all point-in-time and trend headcount counts.

- A cost center → organization mapping table at one row per cost center, with cost center ID, organization name, finance-certified org grouping, and cost center leader — used to translate cost centers into org labels. Requires a fallback for decommissioned cost centers (e.g., a dual-join or prefix-based lookup).

- A hires / starts table at one row per hire event, with employee ID, hire date, org hierarchy IDs, and employee type — used for movement drivers (hires leg of net change).

- An exits / terminations table at one row per exit event, with employee ID, termination date (exit counted one day after), termination type (voluntary / involuntary), regrettable flag, termination reason, and org hierarchy IDs — used for movement drivers (exits leg of net change).

- (Optional) A fiscal calendar / date reference table mapping calendar dates to fiscal year, fiscal quarter, and quarter-end dates — used for quarter-end snapshot alignment and YoY/QoQ window computation.

- (Optional) A user identity / org scoping table that maps the current session user to their position in the management hierarchy — used for executive org-first scoping.

- (Optional) A pre-computed executive reporting summary table that aggregates key metrics by org group for rapid KPI delivery — used as a fast-path validation check against detailed query results.

- (Optional) A semantic view over the employee snapshot and org mapping tables for Cortex Analyst-driven follow-up questions like "show me headcount in [org] over the last 6 quarters".
