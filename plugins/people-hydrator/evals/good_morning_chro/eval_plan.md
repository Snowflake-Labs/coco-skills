# Eval Plan: good_morning_chro

## Purpose

Checks that `good_morning_chro` is connected to the right data in your account.
People Hydrator grades the connection at the end of Bind with the rules below. The golden cases in `golden_cases.md` then test the skill's answers at the end of Tune.

## Behavior Definition

The hydrated skill must:
- Default to company-wide scope without a confirmation prompt; scope every section to an org when the user names one
- Cover 4 sections: Attrition Watch, Hiring Velocity, Engagement Signals, Hot Spots
- Compute T12M voluntary and total attrition as exits ÷ ((HC at period start + HC at period end) ÷ 2) × 100, no annualization, with a company-wide benchmark
- Flag a hot spot when an org's attrition rate is > 2× the company average, pulse score < 70 (or bottom quartile), or > 30% of its open reqs are stale (> 60 days); rank by number of flags
- Use the regrettable flag when present, otherwise the stated proxy, and say the classification is estimated
- Use rolling windows only (last 24h, last 7d, last 30d, T12M for attrition rate); no fiscal calendar
- Produce a 1-page briefing under 500 words, leading with "Workforce steady" or "X hot spots need attention"

## Data the hydrator must find

Data needs from `catalog/people.json` (required ones in bold):

| Data need | What it holds | Used for |
|---|---|---|
| **`people.exits.event_records`** | One row per exit: employee ID, exit date, org; optionally voluntary flag, regrettable flag, level, tenure months | Attrition Watch, hot spots |
| **`people.headcount.active_employees`** | One row per active employee: employee ID, org; optionally snapshot date | Attrition rate denominator |
| `people.offers.pipeline` | One row per offer: offer ID, status, extended date | Hiring Velocity (offers) |
| `people.reqs.open_positions` | One row per requisition: req ID, status; optionally created date | Hiring Velocity (open / stale reqs) |
| `people.engagement.pulse_survey` | One row per org per survey date; optionally pulse score, eNPS | Engagement Signals |
| `people.pto.leave_balances` | One row per employee; optionally used days, utilization % | PTO burnout-risk flag |
| `people.attrition.semantic_view` | A semantic view over exits / headcount data | Follow-up questions |

## Binding rules

G = must pass (otherwise key data isn't confirmed and the skill is hydrated as partial with a warning). P = needed for correct answers. S = optional; reported, not graded.

| ID | Rule | Passes when |
|---|---|---|
| G1 | Exit records connected | Exits table with employee ID, exit date and org, each at MEDIUM confidence or better |
| G2 | Headcount connected | Active employee table with employee ID and org |
| P1 | Voluntary split | Voluntary flag bound on the exits table (the attrition rate numerator) |
| P2 | Average headcount | Snapshot date bound on the headcount table (needed for start and end headcount of the T12M period) |
| P3 | Regrettable flag | Regrettable flag bound on the exits table (otherwise the proxy is used) |
| P4 | Offers pipeline | Offers table with offer ID, status and extended date |
| P5 | Open reqs | Requisitions table with req ID and status |
| S1 | Stale reqs | Created date bound on the requisitions table |
| S2 | Engagement | Pulse survey table with org and survey date |
| S3 | PTO risk | Leave balance table with employee ID and used days or utilization % |
| S4 | Exit detail | Tenure months or level bound on the exits table |
| S5 | Semantic view | Semantic view over exits / headcount connected |

```binding-gate
{"rules": [
  {"id": "G1", "title": "Exit records connected", "check": "need", "need": "people.exits.event_records",
   "fields": ["employee_id", "exit_date", "org"], "min_confidence": "MEDIUM"},
  {"id": "G2", "title": "Headcount connected", "check": "need", "need": "people.headcount.active_employees",
   "fields": ["employee_id", "org"], "min_confidence": "MEDIUM"},
  {"id": "P1", "title": "Voluntary split", "check": "need", "need": "people.exits.event_records",
   "fields": ["voluntary_flag"], "min_confidence": "MEDIUM"},
  {"id": "P2", "title": "Average headcount", "check": "need", "need": "people.headcount.active_employees",
   "fields": ["snapshot_date"], "min_confidence": "MEDIUM"},
  {"id": "P3", "title": "Regrettable flag", "check": "need", "need": "people.exits.event_records",
   "fields": ["regrettable_flag"], "min_confidence": "MEDIUM"},
  {"id": "P4", "title": "Offers pipeline", "check": "need", "need": "people.offers.pipeline",
   "fields": ["offer_id", "status", "extended_date"], "min_confidence": "MEDIUM"},
  {"id": "P5", "title": "Open reqs", "check": "need", "need": "people.reqs.open_positions",
   "fields": ["req_id", "status"], "min_confidence": "MEDIUM"},
  {"id": "S1", "title": "Stale reqs", "check": "need", "need": "people.reqs.open_positions",
   "fields": ["created_date"], "min_confidence": "MEDIUM"},
  {"id": "S2", "title": "Engagement", "check": "need", "need": "people.engagement.pulse_survey",
   "fields": ["org", "survey_date"], "min_confidence": "MEDIUM"},
  {"id": "S3", "title": "PTO risk", "check": "need", "need": "people.pto.leave_balances",
   "fields": ["employee_id", "used_days|utilization_pct"], "min_confidence": "MEDIUM"},
  {"id": "S4", "title": "Exit detail", "check": "need", "need": "people.exits.event_records",
   "fields": ["tenure_months|level"], "min_confidence": "MEDIUM"},
  {"id": "S5", "title": "Semantic view", "check": "any_need", "needs": ["people.attrition.semantic_view"]}
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
| Scope | Company-wide by default with no confirmation; org-scoped when an org is named |
| Attrition rate | T12M exits ÷ ((start HC + end HC) ÷ 2) × 100 (voluntary and total); company-wide rate as the benchmark |
| Hot spots | Org rate > 2× company average, pulse < 70 (or bottom quartile), or > 30% of open reqs stale > 60 days; ranked by flag count |
| Regrettable logic | Regrettable flag if present; otherwise the proxy, noted as estimated; never show the underlying performance rating |
| New exits | More than 5 exits in 24h are shown as counts by org, not names; zero exits reported positively |
| Missing sections | No pipeline → Hiring Velocity skipped with "connect your ATS"; no engagement data → section skipped silently |
| Survey freshness | Survey date shown; surveys older than 90 days flagged |
| Output | 1 page, under 500 words, 4 sections, headline first |

## Expected challenges

1. **Org column naming.** The "org" on exits may be an executive group or level-1 leader field; the hydrator must map it to the same org grouping as headcount.
2. **No req open date.** A requisition table may lack a created date, so stale-req detection isn't possible; the skill should say so rather than estimate.
3. **Survey type mismatch.** An available survey table may be recruiting-type (candidate experience, quality of hire) rather than workforce pulse; it should not be bound as engagement.
4. **Masked fields.** Voluntary or regrettable flags may be masked for some roles; the skill notes it and continues.
