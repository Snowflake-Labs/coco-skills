# Eval Plan: hiring_brief

## Purpose

Checks that `hiring_brief` is connected to the right data in your account.
People Hydrator grades the connection at the end of Bind with the rules below. The golden cases in `golden_cases.md` then test the skill's answers at the end of Tune.

## Behavior Definition

The hydrated skill must:
- Generate a one-page Weekly Hiring Update PDF per leader: header, KPI banner, headcount-by-organization table, executive drill-down tables (top-level executive only), hiring health metrics link, Progress / Problems / Plans narrative
- Support three leader depths: the top-level executive (6 KPIs, company-wide), leaders one level below the top (6 KPIs if their org maps to a cost center leader in the forecast, otherwise 3 KPIs), and leaders two levels below the top (3 KPIs)
- Group the org table by the next level down
- Compute Projected HC = Current HC + Future Starts − Future Exits; % to Forecast = Projected HC ÷ Forecast × 100; % to Adjusted Plan = Projected HC ÷ Adjusted Plan × 100
- Use a YTD timeframe whose start is the first day of the fiscal year containing the end date, from your fiscal calendar
- Use the most recent forecast version from your forecast data
- Follow the workflow: leader selection and end date → data + draft narrative for approval → PDF → offer another report

## Data the hydrator must find

Data needs from `catalog/people.json` (required ones in bold):

| Data need | What it holds | Used for |
|---|---|---|
| **`people.hiring.exec_weekly_summary`** | One row per leader per snapshot date: leader name, snapshot date, current HC; optionally open positions, future starts, future exits, projected HC | KPI banner, org table |
| **`people.hierarchy.management_chain`** | One row per employee: employee ID, full name; optionally leader indicator | Leader list, next-level-down grouping |
| **`people.calendar.fiscal`** | Date → fiscal year; optionally fiscal year start date | YTD start date |
| `people.hiring.forecast` | One row per org group per forecast version per period: org label, period; optionally forecast version, approved target | Forecast and % to Forecast KPIs |
| `people.hires.event_records` | One row per hire: employee ID, hire date | YTD starts when not pre-aggregated |
| `people.exits.event_records` | One row per exit: employee ID, termination date | YTD exits when not pre-aggregated |

## Binding rules

G = must pass (otherwise key data isn't confirmed and the skill is hydrated as partial with a warning). P = needed for correct answers. S = optional; reported, not graded.

| ID | Rule | Passes when |
|---|---|---|
| G1 | Weekly headcount summary connected | Summary table with leader name, snapshot date and current HC, each at MEDIUM confidence or better |
| G2 | Management hierarchy connected | Hierarchy table with employee ID and full name |
| G3 | Fiscal calendar connected | Fiscal calendar with date and fiscal year |
| P1 | Current HC reliable | Leader name, snapshot date and current HC bound at HIGH confidence |
| P2 | Open positions | Open positions bound on the summary table |
| P3 | Projected HC inputs | Future starts and future exits bound on the summary table |
| P4 | YTD start date | Fiscal year start date bound on the fiscal calendar |
| P5 | Leader list | Leader indicator bound on the hierarchy table |
| S1 | Forecast | Forecast table with org label, period and approved target |
| S2 | Forecast version | Forecast version bound on the forecast table |
| S3 | YTD starts | Hires table with employee ID and hire date |
| S4 | YTD exits | Exits table with employee ID and termination date |

```binding-gate
{"rules": [
  {"id": "G1", "title": "Weekly headcount summary connected", "check": "need", "need": "people.hiring.exec_weekly_summary",
   "fields": ["leader_name", "snapshot_date", "current_hc"], "min_confidence": "MEDIUM"},
  {"id": "G2", "title": "Management hierarchy connected", "check": "need", "need": "people.hierarchy.management_chain",
   "fields": ["employee_id", "full_name"], "min_confidence": "MEDIUM"},
  {"id": "G3", "title": "Fiscal calendar connected", "check": "need", "need": "people.calendar.fiscal",
   "fields": ["date", "fiscal_year"], "min_confidence": "MEDIUM"},
  {"id": "P1", "title": "Current HC reliable", "check": "need", "need": "people.hiring.exec_weekly_summary",
   "fields": ["leader_name", "snapshot_date", "current_hc"], "min_confidence": "HIGH"},
  {"id": "P2", "title": "Open positions", "check": "need", "need": "people.hiring.exec_weekly_summary",
   "fields": ["open_positions"], "min_confidence": "MEDIUM"},
  {"id": "P3", "title": "Projected HC inputs", "check": "need", "need": "people.hiring.exec_weekly_summary",
   "fields": ["future_starts", "future_exits"], "min_confidence": "MEDIUM"},
  {"id": "P4", "title": "YTD start date", "check": "need", "need": "people.calendar.fiscal",
   "fields": ["fiscal_year_start_date"], "min_confidence": "MEDIUM"},
  {"id": "P5", "title": "Leader list", "check": "need", "need": "people.hierarchy.management_chain",
   "fields": ["is_leader"], "min_confidence": "MEDIUM"},
  {"id": "S1", "title": "Forecast", "check": "need", "need": "people.hiring.forecast",
   "fields": ["org_label", "period", "approved_target"], "min_confidence": "MEDIUM"},
  {"id": "S2", "title": "Forecast version", "check": "need", "need": "people.hiring.forecast",
   "fields": ["forecast_version"], "min_confidence": "MEDIUM"},
  {"id": "S3", "title": "YTD starts", "check": "need", "need": "people.hires.event_records",
   "fields": ["employee_id", "hire_date"], "min_confidence": "MEDIUM"},
  {"id": "S4", "title": "YTD exits", "check": "need", "need": "people.exits.event_records",
   "fields": ["employee_id", "termination_date"], "min_confidence": "MEDIUM"}
]}
```

## Grading

- **INCOMPATIBLE**: any G rule fails. The skill can still be tuned and is hydrated as partial with a warning.
- **EQUIVALENT**: all G and all P rules pass.
- **DEGRADED**: all G pass, some P fail. The skill works; the failed P rules are named as gaps.

Every failed G or P rule becomes one "which table holds …?" question in Tune.

## Behavior the hydrated skill must preserve

| Component | Required behavior |
|---|---|
| Projected HC | Current HC (as of end date) + Future Starts − Future Exits |
| % to Forecast / Adjusted Plan | Projected HC ÷ Forecast (or Adjusted Plan) × 100 |
| YTD | Start = first day of the fiscal year containing the end date, from your fiscal calendar; never hardcoded |
| Forecast version | Most recent version from your forecast data; never hardcoded |
| Leader levels | 6 KPIs for the top-level executive and forecast-mapped leaders; 3 KPIs otherwise, with a note |
| Narrative | AI draft shown for approval; never final without user confirmation |
| PDF | One page per leader; `[LeaderName]_Weekly_Hiring_Update_[M.D.YY].pdf` saved to the user's desktop |
| Unknown leader | Error with the available list; no data fabricated |

## Expected challenges

1. **Forecast mapping coverage.** Forecast data may only map to orgs with a direct cost center leader; other leaders must get the 3-KPI report.
2. **Snapshot cadence.** Weekly or weekday snapshots mean a requested end date may have no snapshot; the skill offers the most recent available date instead.
3. **Hierarchy depth.** Grouping by the next level down needs management chain levels; if the hierarchy table doesn't provide them, grouping is limited.
4. **Masked fields.** Future exits or some org fields may be masked for some roles; the skill notes it and continues.
