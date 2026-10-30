# Eval Plan: attrition_insights

## Purpose

Checks that `attrition_insights` is connected to the right data in your account.
People Hydrator grades the connection at the end of Bind with the rules below. The golden cases in `golden_cases.md` then test the skill's answers at the end of Tune.

## Behavior Definition

The hydrated skill must:
- Support HRBP mode (default), CPO / HR Leadership mode, and an auto-detected org-scoped Executive mode
- Run the execution waves: Identity & Context, Snapshot, Watch Areas, Drivers, Regrettable Deep Dive, Future Exits Pipeline, Weekly Additions (weekly timeframe only), Recommendations
- Count an exit one day after the termination date
- Compute attrition rate as exits ÷ ((headcount at period start + headcount at period end) ÷ 2) × 100, default T12M, stating both dates
- Report total, voluntary, involuntary, regrettable and non-regrettable attrition
- Score watch cohorts by rate deviation from the company baseline, trend direction and regrettable share, using configurable thresholds
- Resolve fiscal periods from your fiscal calendar

## Data the hydrator must find

Data needs from `catalog/people.json` (required ones in bold):

| Data need | What it holds | Used for |
|---|---|---|
| **`people.attrition.snapshot_monthly`** | One row per employee per month: snapshot date, employee ID, active flag, exit flag; optionally voluntary / involuntary / regrettable flags, termination reason category, management level, tenure group, termination date, cost center, region | Rates, trend, watch areas, drivers |
| **`people.cost_center.org_mapping`** | One row per cost center: cost center ID → org name (optionally leader) | Org breakdowns, org-scoped views |
| **`people.exits.event_records`** | One row per exit: employee ID, termination date; optionally termination category, regrettable flag, termination reason | Regrettable deep dive, future exits pipeline, weekly trend |
| **`people.calendar.fiscal`** | Date → fiscal year (optionally fiscal quarter) | Period windows |
| **`people.users.org_scoping`** | User → position in the hierarchy | Executive org-scoped mode |
| `people.attrition.exec_summary` | Pre-computed attrition by org and date | Fast path for CPO / executive mode |
| `people.attrition.semantic_view` | A semantic view over attrition data | Follow-up questions |

## Binding rules

G = must pass (otherwise key data isn't confirmed and the skill is hydrated as partial with a warning). P = needed for correct answers. S = optional; reported, not graded.

| ID | Rule | Passes when |
|---|---|---|
| G1 | Attrition snapshot connected | Snapshot table with snapshot date, employee ID, active flag and exit flag, each at MEDIUM confidence or better |
| G2 | Exit records connected | Exits table with employee ID and termination date |
| G3 | Most required data connected | At least 4 of the 5 required data needs have a table |
| P1 | Rate inputs reliable | Active flag and exit flag bound at HIGH confidence (numerator and denominator depend on them) |
| P2 | Voluntary / involuntary split | Voluntary or involuntary flag bound on the snapshot table |
| P3 | Regrettable split | Regrettable flag bound on the snapshot table |
| P4 | Org mapping | Cost center → org mapping with cost center ID and org name |
| P5 | Snapshot joins to orgs | Cost center ID bound on the snapshot table |
| P6 | Fiscal calendar | Fiscal calendar with date and fiscal year |
| P7 | Executive scoping | User → hierarchy position mapping connected |
| S1 | Termination reasons | Termination reason bound on the exits table |
| S2 | Watch-area dimensions | Management level, tenure group or region bound on the snapshot table |
| S3 | Executive summary fast path | Pre-computed attrition summary connected |

```binding-gate
{"rules": [
  {"id": "G1", "title": "Attrition snapshot connected", "check": "need", "need": "people.attrition.snapshot_monthly",
   "fields": ["snapshot_date", "employee_id", "is_active", "exit_flag"], "min_confidence": "MEDIUM"},
  {"id": "G2", "title": "Exit records connected", "check": "need", "need": "people.exits.event_records",
   "fields": ["employee_id", "termination_date"], "min_confidence": "MEDIUM"},
  {"id": "G3", "title": "Most required data connected", "check": "min_needs",
   "needs": ["people.attrition.snapshot_monthly", "people.cost_center.org_mapping", "people.exits.event_records", "people.calendar.fiscal", "people.users.org_scoping"], "min": 4},
  {"id": "P1", "title": "Rate inputs reliable", "check": "need", "need": "people.attrition.snapshot_monthly",
   "fields": ["is_active", "exit_flag"], "min_confidence": "HIGH"},
  {"id": "P2", "title": "Voluntary / involuntary split", "check": "need", "need": "people.attrition.snapshot_monthly",
   "fields": ["voluntary_flag|involuntary_flag"], "min_confidence": "MEDIUM"},
  {"id": "P3", "title": "Regrettable split", "check": "need", "need": "people.attrition.snapshot_monthly",
   "fields": ["regrettable_flag"], "min_confidence": "MEDIUM"},
  {"id": "P4", "title": "Org mapping", "check": "need", "need": "people.cost_center.org_mapping",
   "fields": ["cost_center_id", "org_name"], "min_confidence": "MEDIUM"},
  {"id": "P5", "title": "Snapshot joins to orgs", "check": "need", "need": "people.attrition.snapshot_monthly",
   "fields": ["cost_center_id"], "min_confidence": "MEDIUM"},
  {"id": "P6", "title": "Fiscal calendar", "check": "need", "need": "people.calendar.fiscal",
   "fields": ["date", "fiscal_year"], "min_confidence": "MEDIUM"},
  {"id": "P7", "title": "Executive scoping", "check": "need", "need": "people.users.org_scoping",
   "fields": ["user_id", "position_in_hierarchy"], "min_confidence": "MEDIUM"},
  {"id": "S1", "title": "Termination reasons", "check": "need", "need": "people.exits.event_records",
   "fields": ["termination_reason"], "min_confidence": "MEDIUM"},
  {"id": "S2", "title": "Watch-area dimensions", "check": "need", "need": "people.attrition.snapshot_monthly",
   "fields": ["management_level|tenure_group|region"], "min_confidence": "MEDIUM"},
  {"id": "S3", "title": "Executive summary fast path", "check": "any_need", "needs": ["people.attrition.exec_summary"]}
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
| Exit date logic | An exit counts on the day after the termination date, in every exit query |
| Attrition rate | Exits ÷ ((HC at period start + HC at period end) ÷ 2) × 100; default T12M, no annualization |
| Attrition types | Total, voluntary, involuntary, regrettable, non-regrettable |
| Composite risk score | Rate deviation from company baseline + trend direction + regrettable share; thresholds configurable |
| Org mapping | Cost center → org lookup; cost centers not in the mapping are excluded from org breakdowns but kept in totals |
| Masked fields | If the regrettable flag is masked, omit it and say elevated access is needed (never report zero) |
| Benchmarks | Only customer-configured benchmark rates; no hardcoded industry averages |
| Dating | Every metric states the analysis period and data freshness date; stale data (>7 days) gets a banner |

## Expected challenges

1. **One table may serve several needs.** The monthly snapshot can also carry termination dates or regrettable flags; the skill still uses the exits table for the deep dive and future exits.
2. **Short history.** The month-over-month trend needs 13 monthly snapshots; with less, the skill shows what it has and says so.
3. **Masked fields.** Voluntary / regrettable flags may be masked for some roles; the skill notes it and continues.
4. **Snapshot lag.** The monthly snapshot may trail the current date; the skill states the latest snapshot date rather than presenting it as current.
