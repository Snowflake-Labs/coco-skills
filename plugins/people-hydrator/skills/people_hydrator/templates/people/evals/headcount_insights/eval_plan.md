# Eval Plan: headcount_insights

## Purpose

Checks that `headcount_insights` is connected to the right data in your account. People Hydrator grades the connection at the end of Bind with the rules below. The golden cases in `golden_cases.md` then test the skill's answers at the end of Tune.

## Behavior Definition

The hydrated skill must:
- Cover 8 categories: Ending HC, Trends, By Org, By Geography, HC Change, Movement Drivers, Org Shape & Span, Workforce Composition
- Use the dual-audience model (HR / People Analytics company-wide vs executive leadership team org-scoped)
- Apply your included employee types by default (e.g. regular full-time); all types only when asked for "total"
- Decompose movement as starting HC + hires - exits = ending HC
- Map employees to orgs through the cost center → org mapping, with a fallback for decommissioned cost centers
- Produce structured tabular output with status indicators

## Data the hydrator must find

Data needs from `catalog/people.json` (required ones in bold):

| Data need | What it holds | Used for |
|---|---|---|
| **`people.headcount.employee_snapshot`** | One row per employee per snapshot date: employee ID, employment status, employee type; optionally management level, cost center, location, hire date | Ending HC, trends, geography, composition |
| **`people.cost_center.org_mapping`** | One row per cost center: cost center ID → org name (optionally leader) | By org, org-scoped views |
| **`people.hires.event_records`** | One row per hire: employee ID, hire date | Movement drivers (hires leg) |
| **`people.exits.event_records`** | One row per exit: employee ID, termination date | Movement drivers (exits leg) |
| `people.calendar.fiscal` | Date → fiscal year / quarter | Fiscal quarter and year boundaries |
| `people.users.org_scoping` | User → position in the hierarchy | Scoping answers to an executive's org |
| `people.headcount.exec_summary` | Pre-computed headcount by org group and date | Fast cross-check of totals |
| `people.headcount.semantic_view` | A semantic view over headcount data | Preferred query path when present |

## Binding rules

G = must pass (otherwise key data isn't confirmed and the skill is hydrated as partial with a warning). P = needed for correct answers. S = optional; reported, not graded.

| ID | Rule | Passes when |
|---|---|---|
| G1 | Employee snapshot connected | Snapshot table with snapshot date, employee ID, employment status and employee type, each at MEDIUM confidence or better |
| G2 | Most required data connected | At least 3 of the 4 required data needs have a table |
| P1 | Ending HC filters reliable | Employment status and employee type bound at HIGH confidence (the active and included-type filters depend on them) |
| P2 | Hires leg | Hires table with employee ID and hire date |
| P3 | Exits leg | Exits table with employee ID and termination date |
| P4 | Org mapping | Cost center → org mapping with cost center ID and org name |
| P5 | Snapshot joins to orgs | Cost center ID bound on the snapshot table |
| S1 | Fiscal calendar | Fiscal calendar with date and fiscal year |
| S2 | Management level | Management level bound on the snapshot table |
| S3 | Geography | Location bound on the snapshot table |
| S4 | Workforce composition | Hire date bound on the snapshot table |
| S5 | Executive summary fast path | Pre-computed headcount summary connected |
| S6 | Span of control | Direct-report count or manager ID bound on the snapshot table |

```binding-gate
{"rules": [
  {"id": "G1", "title": "Employee snapshot connected", "check": "need", "need": "people.headcount.employee_snapshot", "fields": ["snapshot_date", "employee_id", "employment_status", "employee_type"], "min_confidence": "MEDIUM"},
  {"id": "G2", "title": "Most required data connected", "check": "min_needs", "needs": ["people.headcount.employee_snapshot", "people.cost_center.org_mapping", "people.hires.event_records", "people.exits.event_records"], "min": 3},
  {"id": "P1", "title": "Ending HC filters reliable", "check": "need", "need": "people.headcount.employee_snapshot", "fields": ["employment_status", "employee_type"], "min_confidence": "HIGH"},
  {"id": "P2", "title": "Hires leg", "check": "need", "need": "people.hires.event_records", "fields": ["employee_id", "hire_date"], "min_confidence": "MEDIUM"},
  {"id": "P3", "title": "Exits leg", "check": "need", "need": "people.exits.event_records", "fields": ["employee_id", "termination_date"], "min_confidence": "MEDIUM"},
  {"id": "P4", "title": "Org mapping", "check": "need", "need": "people.cost_center.org_mapping", "fields": ["cost_center_id", "org_name"], "min_confidence": "MEDIUM"},
  {"id": "P5", "title": "Snapshot joins to orgs", "check": "need", "need": "people.headcount.employee_snapshot", "fields": ["cost_center_id"], "min_confidence": "MEDIUM"},
  {"id": "S1", "title": "Fiscal calendar", "check": "need", "need": "people.calendar.fiscal", "fields": ["date", "fiscal_year"], "min_confidence": "MEDIUM"},
  {"id": "S2", "title": "Management level", "check": "need", "need": "people.headcount.employee_snapshot", "fields": ["management_level"], "min_confidence": "MEDIUM"},
  {"id": "S3", "title": "Geography", "check": "need", "need": "people.headcount.employee_snapshot", "fields": ["location"], "min_confidence": "MEDIUM"},
  {"id": "S4", "title": "Workforce composition", "check": "need", "need": "people.headcount.employee_snapshot", "fields": ["hire_date"], "min_confidence": "MEDIUM"},
  {"id": "S5", "title": "Executive summary fast path", "check": "any_need", "needs": ["people.headcount.exec_summary"]},
  {"id": "S6", "title": "Span of control", "check": "need", "need": "people.headcount.employee_snapshot", "fields": ["direct_reports|manager_id"], "min_confidence": "MEDIUM"}
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
| Employee type filter | Your included employee types (set during hydration); all types only when asked for "total" |
| Active status filter | Active employees only in point-in-time counts |
| Exit date logic | An exit counts on the day after the termination date |
| Avg HC | Sum of month-end headcount ÷ number of months (not the attrition denominator; attrition uses (start HC + end HC) ÷ 2) |
| Org mapping | Cost center → org lookup, with a fallback for decommissioned cost centers |
| Dating | Every output states the analysis period and latest snapshot date |

## Expected challenges

1. **One table may serve several needs.** The snapshot table can also hold hire dates or exit flags; the skill still uses the hires and exits tables for the movement legs.
2. **Short history.** Year-over-year comparisons need at least 13 monthly snapshots; with less, the skill says so instead of estimating.
3. **Masked fields.** Termination category or similar fields may be masked for some roles; the skill notes it and continues.
