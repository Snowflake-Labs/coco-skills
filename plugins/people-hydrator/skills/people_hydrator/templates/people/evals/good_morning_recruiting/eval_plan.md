# Eval Plan: good_morning_recruiting

## Purpose

Checks that `good_morning_recruiting` is connected to the right data in your account.
People Hydrator grades the connection at the end of Bind with the rules below. The golden cases in `golden_cases.md` then test the skill's answers at the end of Tune.

## Behavior Definition

The hydrated skill must:
- Run 8 metrics in order by default: HM sentiment, sourced %, req load, stale candidates, competitor hires, competitor intel, stale job openings, offer declines; one metric only when `--metric` is given
- Use your structured TA data for 7 metrics; competitor intel uses live web search with mandatory source links
- Default the timeframe to the current fiscal quarter from your fiscal calendar; support `--recruiter` and `--department` filters
- Keep data gathering, validation and PDF payload building silent; show only the formatted results
- Mark each metric heading 🟢 / 🟡 / 🔴 and flag rows with 🔴 / 🟡 / 🟢
- Ask "Would you like me to generate a PDF report?" after the results, and only then generate it

## Data the hydrator must find

Data needs from `catalog/people.json` (required ones in bold):

| Data need | What it holds | Used for |
|---|---|---|
| **`people.ta.hm_sentiment`** | One row per survey response per recruiter per period: recruiter ID, sentiment score, survey date; optionally feedback text | HM sentiment |
| **`people.ta.applications`** | One row per application: application ID, recruiter ID, source type; optionally application date, disposition | Sourced % by recruiter |
| **`people.ta.requisitions`** | One row per requisition per recruiter: req ID, recruiter ID, status; optionally days open | Req load by recruiter |
| **`people.ta.candidates_in_stage`** | One row per candidate per active stage: candidate ID, recruiter ID, stage name, stage start date; optionally application ID | Stale candidates |
| **`people.ta.job_openings`** | One row per opening: opening ID, status, open date; optionally evergreen flag | Stale job openings |
| **`people.ta.offers`** | One row per offer: offer ID, status; optionally application ID, declined date, decline reason | Offer declines |
| `people.employees.prior_company` | One row per employee: employee ID, prior company name, hire date | Competitor hires |
| `people.ta.semantic_view` | A semantic view over the ATS tables | Follow-up questions |
| `people.calendar.fiscal` | Date → fiscal year / quarter | Fiscal period boundaries |

## Binding rules

G = must pass (otherwise key data isn't confirmed and the skill is hydrated as partial with a warning). P = needed for correct answers. S = optional; reported, not graded.

| ID | Rule | Passes when |
|---|---|---|
| G1 | ATS applications connected | Applications table with application ID, recruiter ID and source type, each at MEDIUM confidence or better |
| G2 | Most required data connected | At least 4 of the 6 required data needs have a table |
| P1 | HM sentiment | Sentiment table with recruiter ID, sentiment score and survey date |
| P2 | Sourced % reliable | Source type bound at HIGH confidence (the sourced vs inbound split depends on it) |
| P3 | Req load | Requisitions table with req ID, recruiter ID and status |
| P4 | Stale candidates | Candidates-in-stage table with candidate ID, recruiter ID, stage name and stage start date |
| P5 | Stale openings | Job openings table with opening ID, status and open date |
| P6 | Offer declines | Offers table with offer ID, status and declined date |
| S1 | Sourced % closed filter | Disposition bound on the applications table |
| S2 | Evergreen split | Evergreen flag bound on the job openings table |
| S3 | Decline reason | Decline reason bound on the offers table |
| S4 | Competitor hires | Prior company table with employee ID, prior company name and hire date |
| S5 | Semantic view path | A semantic view over ATS data connected |
| S6 | Fiscal calendar | Fiscal calendar with date and fiscal year |

```binding-gate
{"rules": [
  {"id": "G1", "title": "ATS applications connected", "check": "need", "need": "people.ta.applications", "fields": ["application_id", "recruiter_id", "source_type"], "min_confidence": "MEDIUM"},
  {"id": "G2", "title": "Most required data connected", "check": "min_needs", "needs": ["people.ta.hm_sentiment", "people.ta.applications", "people.ta.requisitions", "people.ta.candidates_in_stage", "people.ta.job_openings", "people.ta.offers"], "min": 4},
  {"id": "P1", "title": "HM sentiment", "check": "need", "need": "people.ta.hm_sentiment", "fields": ["recruiter_id", "sentiment_score", "survey_date"], "min_confidence": "MEDIUM"},
  {"id": "P2", "title": "Sourced % reliable", "check": "need", "need": "people.ta.applications", "fields": ["source_type"], "min_confidence": "HIGH"},
  {"id": "P3", "title": "Req load", "check": "need", "need": "people.ta.requisitions", "fields": ["req_id", "recruiter_id", "status"], "min_confidence": "MEDIUM"},
  {"id": "P4", "title": "Stale candidates", "check": "need", "need": "people.ta.candidates_in_stage", "fields": ["candidate_id", "recruiter_id", "stage_name", "stage_start_date"], "min_confidence": "MEDIUM"},
  {"id": "P5", "title": "Stale openings", "check": "need", "need": "people.ta.job_openings", "fields": ["opening_id", "status", "open_date"], "min_confidence": "MEDIUM"},
  {"id": "P6", "title": "Offer declines", "check": "need", "need": "people.ta.offers", "fields": ["offer_id", "status", "offer_declined_date"], "min_confidence": "MEDIUM"},
  {"id": "S1", "title": "Sourced % closed filter", "check": "need", "need": "people.ta.applications", "fields": ["disposition"], "min_confidence": "MEDIUM"},
  {"id": "S2", "title": "Evergreen split", "check": "need", "need": "people.ta.job_openings", "fields": ["evergreen"], "min_confidence": "MEDIUM"},
  {"id": "S3", "title": "Decline reason", "check": "need", "need": "people.ta.offers", "fields": ["decline_reason"], "min_confidence": "MEDIUM"},
  {"id": "S4", "title": "Competitor hires", "check": "need", "need": "people.employees.prior_company", "fields": ["employee_id", "prior_company_name", "hire_date"], "min_confidence": "MEDIUM"},
  {"id": "S5", "title": "Semantic view path", "check": "any_need", "needs": ["people.ta.semantic_view"]},
  {"id": "S6", "title": "Fiscal calendar", "check": "need", "need": "people.calendar.fiscal", "fields": ["date", "fiscal_year"], "min_confidence": "MEDIUM"}
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
| Metric order | All 8 metrics in order unless `--metric` is given |
| Timeframe | Current fiscal quarter by default, from your fiscal calendar |
| Thresholds | HM sentiment, sourced %, stale candidate and stale opening thresholds are configurable (set during hydration) |
| HM sentiment confidence | Fewer than 3 responses for a recruiter → marked "(limited data)" |
| Sourced % denominator | Filled / closed applications only |
| Stale openings | Evergreen and non-evergreen openings shown separately |
| Offer declines | Last 14 calendar days only; no acceptance rate or per-recruiter offer counts |
| Competitor intel | Live web search, last 30 calendar days, topic gate, mandatory source links; competitors set during hydration |
| Silence | No SQL, table or column names, JSON, code or agent labels in chat |
| PDF | Only after the user confirms; saved to the user's desktop as `TA_Metrics_Report_[period_label].pdf` |

## Expected challenges

1. **One table may serve several needs.** The applications table can also hold stage data; the skill still treats sourced % and stale candidates as separate metrics.
2. **Multi-recruiter fields.** A recruiter field may hold several recruiters in one value; per-recruiter metrics must split it rather than count the combined value as one recruiter.
3. **ATS migration.** Data from before an ATS migration cutoff may be incomplete; when a cutoff is set, analysis focuses on data after it.
4. **Restricted ATS data.** Permission errors return the skill's permissions message instead of partial numbers.
