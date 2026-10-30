---
name: hiring_brief
description: >
  Generate a Weekly Hiring Update PDF report for an executive leader. Includes a
  KPI banner (Current HC, Open Positions, Future HC, and optionally Forecast and
  % to Plan), a headcount-by-organization table grouped by the next level down,
  and a Progress / Problems / Plans narrative drafted by AI and reviewed by the
  user. Targeted for the recruiting team to produce for leadership. Supports the
  top-level executive (the leader at the very top of the company's management
  hierarchy — full company), leaders one level below the top of the hierarchy,
  and leaders two levels below the top. Triggers: weekly hiring update,
  hiring update PDF, hiring report, recruiting report, leader hiring update,
  weekly recruiting summary, hiring update for [leader name].
  Do NOT use for: live recruiting metrics (use good_morning_recruiting) or
  headcount trend analysis (use headcount_insights).
license: Proprietary. See LICENSE.txt for complete terms
---

# Hiring Brief (Weekly Hiring Update) (Dehydrated)

## About this skill

- **What it does:** A one-page Weekly Hiring Update (PDF) for a leader: headcount, open positions and projected headcount, a table by org, and a Progress / Problems / Plans narrative drafted for you to review.
- **Who it's for:** Recruiting teams preparing updates for executive leaders (the top executive, or leaders one or two levels below).
- **Works with:** Uses the same headcount as `headcount_insights`. For follow-up questions (trends, regions, span of control), the skill hands off to `headcount_insights`.
- **Before you start:** This skill runs on the data and definitions connected by **People Hydrator** (`plugins/people-hydrator/`). If it hasn't been set up for your account yet, run People Hydrator first.
- **How to use it:** Say "weekly hiring update for [leader name]". Review and edit the drafted narrative before the PDF is finalized.
- **If you don't get what you expect:** If a section is missing or says data isn't available, that data wasn't connected during setup: re-run People Hydrator to add it. If a number looks wrong, tell People Hydrator what you expected; it turns that into a checked correction. If the skill says a table or column has changed, re-run People Hydrator for this skill.
- **Good to know:** Forecast and plan figures only appear for leaders whose org maps to a forecast; others get the 3-number banner. The skill reads your data and never changes it.

## Intent

Generate a polished one-page PDF hiring update report for a specific leader. Designed for the recruiting team to produce for leadership — not for real-time recruiting analytics or deep headcount trend analysis (use `headcount_insights` for those). Supports three leader depths: the top-level executive (the leader at the very top of the company's management hierarchy, e.g., a CEO — full company view), leaders one level below the top of the hierarchy (grouped by two levels below the top), and leaders two levels below the top (grouped by three levels below the top).

## Behavior

### Report Structure

Every report includes:

1. **Header:** "Weekly Hiring Update" + leader name + "Week of [date]"
2. **KPI Banner:** See leader-level rules below
3. **Headcount by Organization Table:** Grouped by next level down (see leader-level rules). Columns: Leader/Organization, Current HC, Open Positions, YTD Starts, YTD Exits, Projected HC
4. **Executive Drill-Down Tables** *(top-level executive only):* Additional tables for the top-level executive's two largest or most strategically significant orgs (configurable at customer setup), showing their direct reports
5. **Hiring Health Metrics Banner:** Link to the live recruiting health dashboard (configurable URL at customer setup), placed before the narrative sections
6. **Progress / Problems / Plans:** AI-generated draft narrative, reviewed and edited by the user before finalizing

### Leader-Level Behavior

| Leader Level | KPI Banner | Org Table Groups By | Forecast Included | Drill-Down Tables |
|---|---|---|---|---|
| Top-level executive (e.g., CEO) | 6 KPIs (HC, Open, Future HC + Forecast, % to Forecast, % to Adj. Plan) | Leaders one level below the top of the hierarchy | Yes — company-wide | Configurable (typically top 2 orgs) |
| Cost center leader with forecast mapping (one level below the top) | 6 KPIs | Leaders two levels below the top of the hierarchy | Yes — by cost center leader | None |
| Other leaders one or two levels below the top | 3 KPIs (HC, Open, Future HC) | Next level down | No — forecast only maps via cost center leader | None |

**Forecast availability:** Forecast and Adjusted Plan metrics are only available for the top-level executive and leaders whose org maps directly to a cost center leader in the headcount forecast data. For other leaders, surface only the 3-KPI banner.

### Derived Columns

- **Projected HC** = Current HC (as of end date) + Future Starts − Future Exits
- **% to Forecast** = Projected HC / Forecast × 100
- **% to Adjusted Plan** = Projected HC / Adjusted Plan × 100

### Fiscal Year Convention

Report timeframe is YTD (year-to-date). Start date = first day of the customer's fiscal year that contains the end date. Compute dynamically from the fiscal calendar — do not hardcode dates.

### Workflow

**Step 0: Introduction & Leader Selection**

When first invoked, greet the user with an intro explaining what the report includes, then present a multi-select checklist of available leaders. The leader list is populated from the customer's management hierarchy data (not hardcoded). After leader selection, confirm the end date (data as-of date). Suggest the most recent available snapshot date as the default.

**Step 1: Determine Report Parameters**

- Resolve the end date (confirmed in Step 0).
- Compute start date dynamically (fiscal year start that contains the end date).
- Resolve forecast parameters: determine the most recent standard forecast version from the customer's forecast data (sorted by recency). Both forecast version and calendar month for forecast lookup are dynamic — do not hardcode.

**Step 2: Generate Data + AI Narrative, Then Present for Approval**

1. Run the headcount data query to fetch KPI and org table data for the leader.
2. Generate AI draft narrative for each narrative section (Progress, Problems, Plans).
3. Present all of the following to the user for quick approval in a single message:
   - KPI summary as a formatted table
   - Org table as a markdown table
   - AI-generated narrative for each section
4. Ask: "Does this look good, or would you like to edit the narrative before I generate the PDF?"

**Step 3: Generate the Final PDF**

Once the user approves or provides edits, generate the PDF using the confirmed narrative. Output file name: `[LeaderName]_Weekly_Hiring_Update_[M.D.YY].pdf`. Save to the user's desktop. Confirm file name and location to the user.

**Step 4: Offer to Build Another Report**

After each report (or batch) is complete, offer to generate another. Carry over the end date from the previous run unless the user changes it.

### Narrative Format

Each bullet in the narrative follows this pattern:
```
**Short Bold Title:** Detailed description paragraph referencing specific data points.
```

Three sections: **Progress** (wins, velocity, completions), **Problems** (blockers, gaps, risk areas), **Plans** (actions planned, upcoming milestones, next steps).

### PDF Formatting Rules

- One page per leader report.
- Header: bold leader name, "Weekly Hiring Update", "Week of [date]".
- KPI Banner: values in large format, clearly labeled.
- Org table: all columns aligned; truncate long org names with ellipsis if needed.
- Color coding: healthy rows in standard styling; flagged rows (large gaps to forecast or plan) highlighted.
- Hiring health metrics banner: placed as a clearly labeled link section before the narrative.
- Narrative sections: Progress / Problems / Plans in three clearly labeled blocks.

### Cross-Skill Handoff: Headcount

At any point during a session — before, during, or after report generation — the user may ask headcount questions that go beyond the weekly report's scope. When this happens, hand off to the **headcount** skill:

| User Question Pattern | Handoff |
|---|---|
| "How does this compare to last quarter?" | `headcount_insights` — Headcount Change |
| "Break it down by region / country" | `headcount_insights` — Headcount by Geography |
| "What's driving the exits?" | `headcount_insights` — Movement Drivers |
| "Show me the quarterly trend" | `headcount_insights` — Headcount Trends |
| "What's the org shape / span of control?" | `headcount_insights` — Org Shape |
| "What's our current headcount?" | `headcount_insights` — Ending Headcount |

Anything about the weekly report PDF → stay in this skill.

### Confidence Handling

- **HIGH confidence:** All data layers resolve (snapshot, starts, exits, forecast). Generate full 6-KPI report.
- **MEDIUM confidence:** Core headcount resolves but forecast data is unavailable for this leader. Generate 3-KPI report and note that forecast is not mapped for this leader.
- **LOW confidence on non-critical sections** (e.g., drill-down tables, specific org detail): Generate report with available data; note gaps.
- **LOW confidence on critical bindings** (headcount snapshot is inaccessible): Skip report generation; output the data domain description and recommend the customer connect their HRIS headcount data.

### Guardrails

- Never expose table names, column names, SQL, or schema details to the user.
- Never save the PDF to the skill directory — only to the user's desktop.
- The AI narrative is a draft for the user to review and edit — never present it as final without user confirmation.
- Do not fabricate data for leaders who are not in the management hierarchy — surface an error if a leader is not found.

### Error Handling

| Scenario | Response |
|----------|---------|
| Leader not found in management hierarchy | "I couldn't find [leader name] in the org. Please verify the name or select from the available list." |
| No snapshot data for the requested end date | "No headcount snapshot found for [date]. Here's the most recent available: [date]. Want to use that instead?" |
| Forecast data unavailable for leader | Generate 3-KPI report; note: "Forecast data is not mapped for this leader — showing Current HC, Open Positions, and Projected HC only." |
| Authorization / permissions error | "I wasn't able to pull headcount data right now — this usually means your account needs a permissions update. Reach out to your People Analytics team." |

## Quality

This skill is tested against `evals/hiring_brief/golden_cases.md` and `evals/hiring_brief/eval_plan.md` (People Hydrator runs them in its Evaluate stage). They are tests, not runtime instructions: follow this file and its customer configuration (added at hydration).

## Context Queries

- A weekly headcount reporting / executive summary table at one row per leader per snapshot date, with columns for leader name, org group, current HC, open positions (approved unfilled reqs), future starts (accepted offers with future start dates), future exits (confirmed future terminations), projected HC (derived), and optionally forecast and adjusted plan targets — used as the primary data source for the KPI banner and org table.

- A management hierarchy table at one row per employee, with employee ID, full name, management chain levels (by name, as deep as the customer's hierarchy provides), and leader indicator — used to determine the leader list for selection and to group the org table by "next level down".

- A fiscal calendar / date reference table mapping calendar dates to fiscal year and fiscal year start date — used to compute the YTD start date dynamically.

- A headcount forecast table at one row per org group per forecast version per period, with organization label, calendar month, forecast version identifier (e.g., "Q2 Forecast"), approved headcount target, and adjusted plan — used for the % to Forecast and % to Adjusted Plan KPIs. Only available for orgs with a direct cost center leader mapping to the forecast data.

- (Optional) A starts / new hires event table at one row per hire — used if the executive summary table does not pre-aggregate YTD starts and a direct computation is needed.

- (Optional) A exits / terminations event table at one row per exit — used if the executive summary table does not pre-aggregate YTD exits and a direct computation is needed.
