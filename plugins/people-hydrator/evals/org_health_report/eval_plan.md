# Eval Plan: org_health_report

## Purpose

Checks that `org_health_report` is connected to the right data in your account.
People Hydrator grades the connection at the end of Bind with the rules below. The golden cases in `golden_cases.md` then test the skill's answers at the end of Tune.

## Behavior Definition

The hydrated skill must:
- Resolve the session user's identity first (Step 0) and use it as the default scope; abort if it can't
- Scope to direct reports, full org (by employee-ID management chain) or company; default full org for leaders, direct for frontline managers
- Render the Org Health Report as the locked 8-section template, every section always present
- Cover six analysis areas: Retention & Attrition, Performance & LP Outcomes, Team Structure & Span, PTO & Well-Being, AI Adoption, Recruiting
- Run identity resolution in Phase 1 and all metric blocks in parallel in one Phase 2 message
- Compare to the peer org (from a pre-built benchmark) and to the prior year, skipping peer comparison only for top-level leaders

## Data the hydrator must find

Data needs from `catalog/people.json` (required ones in bold):

| Data need | What it holds | Used for |
|---|---|---|
| **`people.org_health.employee_snapshot`** | One row per employee per snapshot date: employee ID, manager ID; optionally full name, management level, employment status, direct reports, cost center | Identity, scope, team structure, span |
| **`people.exits.event_records`** | One row per exit: employee ID, termination date; optionally termination category, regrettable flag | Attrition, HP retention, LP exit rate |
| **`people.org_health.performance_ratings`** | One row per employee per review cycle: employee ID, cycle ID, performance rating | HP / LP cohorts, cycle discovery |
| **`people.org_health.pto_usage`** | One row per employee per period: employee ID, PTO days taken, period; optionally PTO allotment | PTO utilization, burnout risk |
| **`people.org_health.ai_usage`** | One row per user per tool per day: employee ID, tool name, date; optionally active flag | AI adoption, daily active % |
| **`people.ta.requisitions`** | One row per requisition: req ID, status; optionally open date | Recruiting section |
| **`people.hires.event_records`** | One row per hire: employee ID, hire date | New hire attrition, pending starts |
| `people.org_health.peer_benchmarks` | One row per executive-level org: org; optionally attrition rate, PTO utilization, AI adoption | Peer org comparison columns |
| `people.qoh.new_hire_survey` | One row per survey response: employee ID, rating | New hire satisfaction |
| `people.org_health.semantic_view` | A semantic view over snapshot and exit data | Follow-up questions |
| `people.calendar.fiscal` | Date → fiscal year / quarter | Fiscal period boundaries |

## Binding rules

G = must pass (otherwise key data isn't confirmed and the skill is hydrated as partial with a warning). P = needed for correct answers. S = optional; reported, not graded.

| ID | Rule | Passes when |
|---|---|---|
| G1 | Employee snapshot connected | Snapshot table with employee ID and manager ID, each at MEDIUM confidence or better |
| G2 | Exits connected | Exits table with employee ID and termination date |
| G3 | Most required data connected | At least 5 of the 7 required data needs have a table |
| P1 | Performance cohorts | Ratings table with employee ID, cycle ID and performance rating |
| P2 | PTO utilization | PTO table with employee ID, days taken, period and allotment |
| P3 | AI adoption | AI usage table with employee ID, tool name and date |
| P4 | Recruiting | Requisitions table with req ID and status |
| P5 | New hire attrition | Hires table with employee ID and hire date |
| P6 | Attrition splits | Termination category and regrettable flag bound on the exits table |
| S1 | Team structure detail | Management level and direct reports bound on the snapshot table |
| S2 | Peer benchmark | Pre-built peer benchmark connected |
| S3 | New hire survey | New hire survey with employee ID and rating |
| S4 | Semantic view path | A semantic view over snapshot / exit data connected |
| S5 | User identity | Email or login bound on the snapshot (finds the leader running the report) |
| S6 | Management chain | Management chain bound on the snapshot (org scoping, top-level leader detection) |
| S7 | Fiscal calendar | Fiscal calendar with date and fiscal year |

```binding-gate
{"rules": [
  {"id": "G1", "title": "Employee snapshot connected", "check": "need", "need": "people.org_health.employee_snapshot", "fields": ["employee_id", "manager_id"], "min_confidence": "MEDIUM"},
  {"id": "G2", "title": "Exits connected", "check": "need", "need": "people.exits.event_records", "fields": ["employee_id", "termination_date"], "min_confidence": "MEDIUM"},
  {"id": "G3", "title": "Most required data connected", "check": "min_needs", "needs": ["people.org_health.employee_snapshot", "people.exits.event_records", "people.org_health.performance_ratings", "people.org_health.pto_usage", "people.org_health.ai_usage", "people.ta.requisitions", "people.hires.event_records"], "min": 5},
  {"id": "P1", "title": "Performance cohorts", "check": "need", "need": "people.org_health.performance_ratings", "fields": ["employee_id", "cycle_id", "performance_rating"], "min_confidence": "MEDIUM"},
  {"id": "P2", "title": "PTO utilization", "check": "need", "need": "people.org_health.pto_usage", "fields": ["employee_id", "pto_days_taken", "period", "pto_allotment"], "min_confidence": "MEDIUM"},
  {"id": "P3", "title": "AI adoption", "check": "need", "need": "people.org_health.ai_usage", "fields": ["employee_id", "tool_name", "date"], "min_confidence": "MEDIUM"},
  {"id": "P4", "title": "Recruiting", "check": "need", "need": "people.ta.requisitions", "fields": ["req_id", "status"], "min_confidence": "MEDIUM"},
  {"id": "P5", "title": "New hire attrition", "check": "need", "need": "people.hires.event_records", "fields": ["employee_id", "hire_date"], "min_confidence": "MEDIUM"},
  {"id": "P6", "title": "Attrition splits", "check": "need", "need": "people.exits.event_records", "fields": ["termination_category", "regrettable_flag"], "min_confidence": "MEDIUM"},
  {"id": "S1", "title": "Team structure detail", "check": "need", "need": "people.org_health.employee_snapshot", "fields": ["management_level", "direct_reports"], "min_confidence": "MEDIUM"},
  {"id": "S2", "title": "Peer benchmark", "check": "any_need", "needs": ["people.org_health.peer_benchmarks"]},
  {"id": "S3", "title": "New hire survey", "check": "need", "need": "people.qoh.new_hire_survey", "fields": ["employee_id", "rating"], "min_confidence": "MEDIUM"},
  {"id": "S4", "title": "Semantic view path", "check": "any_need", "needs": ["people.org_health.semantic_view"]},
  {"id": "S5", "title": "User identity", "check": "need", "need": "people.org_health.employee_snapshot", "fields": ["email"], "min_confidence": "MEDIUM"},
  {"id": "S6", "title": "Management chain", "check": "need", "need": "people.org_health.employee_snapshot", "fields": ["management_chain"], "min_confidence": "MEDIUM"},
  {"id": "S7", "title": "Fiscal calendar", "check": "need", "need": "people.calendar.fiscal", "fields": ["date", "fiscal_year"], "min_confidence": "MEDIUM"}
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
| Identity first | Session user resolved before any analysis; zero rows → "I couldn't find you in the employee directory, so I can't scope this to your org. Ask the person who set up these skills to check the directory connection in People Hydrator." |
| Locked template | 8 sections in fixed order, exact column headers, "—" for empty fields, ✅ 👁️ ⚠️ 🔴 on every row |
| HP retention | (HP cohort − HP exits) ÷ HP cohort × 100, rolling L12M |
| LP exit rate | LP exited ÷ LP cohort × 100, exits on or after the review period start |
| Cycle discovery | Run before performance metrics; no cycle → "off-cycle — no review conducted" |
| RIF check | RIF exits detected → show both raw and organic attrition |
| Fiscal year | Period math uses your fiscal calendar |
| Scope | Management chain filtered by employee ID, not by name |
| YoY | T12M window shifted 12 months back; constant-cohort for reorged leaders |
| Peer comparison | Skipped only for top-level leaders; never "—" for a non-top-level leader |
| Parallelism | All Phase 2 blocks in one message |
| No internals | No table, column or schema names in any section |

## Expected challenges

1. **Restricted HR data.** Snapshot, ratings and benchmark data often sit in restricted schemas; check access under the analyst's role before grading.
2. **AI usage keyed by a different ID.** AI usage may use a different employee ID that must be mapped to the snapshot employee ID.
3. **Short history.** YoY needs about 24 months of data and HP retention 12 months; with less, the skill shows "—" for prior year instead of estimating.
4. **Benchmark freshness.** The peer benchmark is refreshed on a schedule; stale benchmarks should be called out.
