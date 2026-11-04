# Eval Plan: offer_builder

## Purpose

Checks that `offer_builder` is connected to the right data in your account. People Hydrator grades the connection at the end of Bind with the rules below. The golden cases in `golden_cases.md` then test the skill's answers at the end of Tune.

## Behavior Definition

The hydrated skill must:
- Follow the 7-step flow: inputs → candidate profile → scorecards + rubric → hiring manager + aggregate team comp → team parity summary → competing offer from recruiter notes → in-chat summary
- Stop and wait at Step 1 (inputs) and Step 6 (confirm extracted competing offer)
- Ask the user to pick when 2–5 applications match the name; stop when none match
- Surface signal-quality warnings inline (fewer than 3 scorecards; pre-migration data) without stopping
- Scope the team to people reporting to the requisition's hiring manager on the latest employee snapshot; report team comp only as per-currency aggregates with a minimum group size of 5, never individual or named pay
- Never extract or use pay history (current / prior pay, amounts left behind); record only a voluntarily disclosed competing offer; never solicit it
- Produce a 5-block summary in chat; never export to Google Sheets or other tools
- Never show table names, column names, SQL or internal identifiers

## Data the hydrator must find

Data needs from `catalog/people.json` (required ones in bold):

| Data need | What it holds | Used for |
|---|---|---|
| **`people.ats.applications`** | One row per application: application ID, candidate name, job ID; optionally recruiter, prior company, years of experience | Step 2 candidate profile |
| **`people.ats.scorecards`** | One row per scorecard submission: scorecard ID, application ID, interviewer ID, recommendation | Step 3a recommendation distribution |
| **`people.ats.scorecard_rubric_answers`** | One row per scorecard question: scorecard ID, question text, answer text | Step 3b signals and recruiter notes |
| **`people.ats.jobs`** | One row per requisition: req ID, title; optionally hiring manager | Step 4a hiring manager lookup |
| **`people.offer_builder.employee_snapshot`** | One row per employee per snapshot date: employee ID, manager ID; optionally name, employee type | Team members under the hiring manager |
| **`people.offer_builder.offer_comp`** | One row per offer: application ID, base salary; optionally employee ID, currency, OTE | Step 4b team hire-time comp |
| `people.compensation.ranges` | One row per job code and pay market: min and max salary | Optional; not used by the skill's steps (the summary omits compensation ranges) |
| **`people.ats.users_directory`** | One row per interviewer: user ID, full name | Interviewer names |

## Binding rules

G = must pass (otherwise key data isn't confirmed and the skill is hydrated as partial with a warning). P = needed for correct answers. S = optional; reported, not graded.

| ID | Rule | Passes when |
|---|---|---|
| G1 | Candidate profile connected | Applications table with application ID, candidate name and job ID, each at MEDIUM confidence or better |
| G2 | Scorecards connected | Scorecards table with scorecard ID, application ID and recommendation |
| G3 | Most required data connected | At least 5 of the 7 skill-used required data needs have a table |
| P1 | Recruiter notes | Rubric table with scorecard ID, question text and answer text |
| P2 | Hiring manager lookup | Jobs table with req ID and hiring manager |
| P3 | Team scoping | Employee snapshot with employee ID and manager ID |
| P4 | Team comp | Offer comp table with employee ID and base salary |
| P5 | Interviewer names | Directory with user ID and full name |
| S1 | Currency per row | Currency bound on the offer comp table |
| S2 | OTE | OTE bound on the offer comp table |
| S3 | Profile extras | Prior company and years of experience bound on the applications table |

```binding-gate
{"rules": [
  {"id": "G1", "title": "Candidate profile connected", "check": "need", "need": "people.ats.applications",
   "fields": ["application_id", "candidate_name", "job_id"], "min_confidence": "MEDIUM"},
  {"id": "G2", "title": "Scorecards connected", "check": "need", "need": "people.ats.scorecards",
   "fields": ["scorecard_id", "application_id", "recommendation"], "min_confidence": "MEDIUM"},
  {"id": "G3", "title": "Most required data connected", "check": "min_needs",
   "needs": ["people.ats.applications", "people.ats.scorecards", "people.ats.scorecard_rubric_answers", "people.ats.jobs", "people.offer_builder.employee_snapshot", "people.offer_builder.offer_comp", "people.ats.users_directory"], "min": 5},
  {"id": "P1", "title": "Recruiter notes", "check": "need", "need": "people.ats.scorecard_rubric_answers",
   "fields": ["scorecard_id", "question_text", "answer_text"], "min_confidence": "MEDIUM"},
  {"id": "P2", "title": "Hiring manager lookup", "check": "need", "need": "people.ats.jobs",
   "fields": ["req_id", "hiring_manager_id"], "min_confidence": "MEDIUM"},
  {"id": "P3", "title": "Team scoping", "check": "need", "need": "people.offer_builder.employee_snapshot",
   "fields": ["employee_id", "manager_id"], "min_confidence": "MEDIUM"},
  {"id": "P4", "title": "Team comp", "check": "need", "need": "people.offer_builder.offer_comp",
   "fields": ["employee_id", "base_salary"], "min_confidence": "MEDIUM"},
  {"id": "P5", "title": "Interviewer names", "check": "need", "need": "people.ats.users_directory",
   "fields": ["user_id", "full_name"], "min_confidence": "MEDIUM"},
  {"id": "S1", "title": "Currency per row", "check": "need", "need": "people.offer_builder.offer_comp",
   "fields": ["currency"], "min_confidence": "MEDIUM"},
  {"id": "S2", "title": "OTE", "check": "need", "need": "people.offer_builder.offer_comp",
   "fields": ["ote"], "min_confidence": "MEDIUM"},
  {"id": "S3", "title": "Profile extras", "check": "need", "need": "people.ats.applications",
   "fields": ["prior_company_name", "years_of_experience"], "min_confidence": "MEDIUM"}
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
| Stop points | Wait for inputs (Step 1) and confirmation of the extracted competing offer (Step 6) |
| Name matches | 0 → stop and ask to verify; 1 → proceed; 2–5 → interactive selector |
| Signal quality | Inline warning when fewer than 3 scorecards or all pre-migration; does not stop |
| Team scope | Latest employee snapshot; direct reports of the requisition's hiring manager |
| Team comp | Aggregates within each currency; groups under 5 suppressed; missing comp excluded and counted |
| Team parity | Team profile and per-currency comp parity tables; no team member names or individual pay |
| Pay history | Never extracted or used; "Recruiter notes contain pay-history details, which this skill does not use." when present |
| Competing offer | Voluntarily disclosed only; "No competing offer disclosed." when none |
| Output | 5 blocks in chat; no Google Sheets or other export; competing offer data never in the title or headers |
| Omitted content | No compensation ranges, salary expectations, age or immigration references |
| Privacy | No table names, column names, SQL or error text shown to the user |

## Expected challenges

1. **Scorecard and rubric views may not be searchable.** App-specific views can be missed by search; upstream ATS tables are fine if equivalent.
2. **Rich-text recruiter notes.** Rubric answers may be nested JSON; leaf text must be extracted before competing-offer extraction.
3. **Team comp gaps.** Employees who predate offer records have no comp row; the skill excludes them from aggregates and reports the count.
4. **Multiple currencies.** Teams spanning countries mix currencies; comparisons must not convert or sum across them silently.
