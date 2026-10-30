# Eval Plan: interview_debrief

## Purpose

Checks that `interview_debrief` is connected to the right data in your account. People Hydrator grades the connection at the end of Bind with the rules below. The golden cases in `golden_cases.md` then test the skill's answers at the end of Tune.

## Behavior Definition

The hydrated skill must:
- Route by request: debrief by application ID, debrief by candidate name + role (with disambiguation), pending debriefs for a requisition, cross-candidate comparison / stack rank, calibration-only, competency deep-dive
- Run an access preflight first and stop with a plain-language message if access fails
- Warn up front about interviewers who have not submitted scorecards
- Classify interviewers as Hawk / Dove / Neutral from their trailing 12-month recommendations, and report panel alignment (Aligned / Mixed / Split)
- Apply calibration-adjusted scoring (Hawk positive × 1.3, Dove positive × 0.8) in cross-candidate ranking only
- Produce the fixed 5-section debrief; Section 5 (Comp / Offer Context) only when offer data exists
- Never show table names, column names, SQL or row counts to the user

## Data the hydrator must find

Data needs from `catalog/people.json` (required ones in bold):

| Data need | What it holds | Used for |
|---|---|---|
| **`people.ats.scorecards`** | One row per scorecard submission: application ID, scorecard ID, interviewer ID, overall recommendation; optionally submitted time | Panel signal, calibration, missing scorecards |
| **`people.ats.scorecard_rubric_answers`** | One row per scorecard question: scorecard ID, question text, answer text | Strengths / concerns, quotes, competency breakdown |
| **`people.ats.applications`** | One row per application: application ID, candidate name, job ID; optionally status | Section 1 candidate context, name search |
| **`people.ats.jobs`** | One row per job: job ID, title; optionally grade, department | Role spec, signal gaps |
| **`people.ats.interviews`** | One row per interview event: application ID; optionally scorecard ID, interview date, stage name | Interview timeline, missing scorecards |
| **`people.ats.users_directory`** | One row per interviewer: user ID, full name | Interviewer names |
| `people.ats.offers` | One row per offer: application ID, status; optionally base salary | Section 5 Comp / Offer Context |
| `people.qoh.application_survey` | One row per post-hire survey response: application ID, rating | QoH pattern signal |
| `people.ats.semantic_view` | A semantic view over application, scorecard and interview data | Follow-up questions |

## Binding rules

G = must pass (otherwise key data isn't confirmed and the skill is hydrated as partial with a warning). P = needed for correct answers. S = optional; reported, not graded.

| ID | Rule | Passes when |
|---|---|---|
| G1 | Scorecards connected | Scorecards table with application ID, scorecard ID, interviewer ID and recommendation, each at MEDIUM confidence or better |
| G2 | Applications connected | Applications table with application ID, candidate name and job ID |
| G3 | Most required data connected | At least 4 of the 6 required data needs have a table |
| P1 | Recommendation reliable | Recommendation bound at HIGH confidence (panel signal and calibration depend on it) |
| P2 | Rubric answers | Rubric table with scorecard ID, question text and answer text |
| P3 | Role spec | Jobs table with job ID and title |
| P4 | Interview timeline | Interviews table with application ID, scorecard ID and interview date |
| P5 | Interviewer names | Directory with user ID and full name |
| S1 | Offer context | Offers table with application ID and status |
| S2 | Quality of hire | Survey table with application ID and rating |
| S3 | Calibration window | Submitted time bound on the scorecards table |
| S4 | Semantic view | Semantic view connected |

```binding-gate
{"rules": [
  {"id": "G1", "title": "Scorecards connected", "check": "need", "need": "people.ats.scorecards",
   "fields": ["application_id", "scorecard_id", "interviewer_id", "recommendation"], "min_confidence": "MEDIUM"},
  {"id": "G2", "title": "Applications connected", "check": "need", "need": "people.ats.applications",
   "fields": ["application_id", "candidate_name", "job_id"], "min_confidence": "MEDIUM"},
  {"id": "G3", "title": "Most required data connected", "check": "min_needs",
   "needs": ["people.ats.scorecards", "people.ats.scorecard_rubric_answers", "people.ats.applications", "people.ats.jobs", "people.ats.interviews", "people.ats.users_directory"], "min": 4},
  {"id": "P1", "title": "Recommendation reliable", "check": "need", "need": "people.ats.scorecards",
   "fields": ["recommendation"], "min_confidence": "HIGH"},
  {"id": "P2", "title": "Rubric answers", "check": "need", "need": "people.ats.scorecard_rubric_answers",
   "fields": ["scorecard_id", "question_text", "answer_text"], "min_confidence": "MEDIUM"},
  {"id": "P3", "title": "Role spec", "check": "need", "need": "people.ats.jobs",
   "fields": ["job_id", "title"], "min_confidence": "MEDIUM"},
  {"id": "P4", "title": "Interview timeline", "check": "need", "need": "people.ats.interviews",
   "fields": ["application_id", "scorecard_id", "interview_date"], "min_confidence": "MEDIUM"},
  {"id": "P5", "title": "Interviewer names", "check": "need", "need": "people.ats.users_directory",
   "fields": ["user_id", "full_name"], "min_confidence": "MEDIUM"},
  {"id": "S1", "title": "Offer context", "check": "need", "need": "people.ats.offers",
   "fields": ["application_id", "status"], "min_confidence": "MEDIUM"},
  {"id": "S2", "title": "Quality of hire", "check": "need", "need": "people.qoh.application_survey",
   "fields": ["application_id", "rating"], "min_confidence": "MEDIUM"},
  {"id": "S3", "title": "Calibration window", "check": "need", "need": "people.ats.scorecards",
   "fields": ["submitted_at"], "min_confidence": "MEDIUM"},
  {"id": "S4", "title": "Semantic view", "check": "any_need", "needs": ["people.ats.semantic_view"]}
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
| Calibration | Hawk: bottom-tier rate > 60%; Dove: top-tier rate > 80%; otherwise Neutral; trailing 12 months |
| Panel alignment | Max recommendation count ÷ total scorecards: Aligned ≥ 75%, Mixed 50–74%, Split < 50% |
| Calibration-adjusted score | Hawk positive × 1.3, Dove positive × 0.8, Neutral × 1.0; cross-candidate ranking only |
| Confidence labels | n ≥ 10 HIGH; 5–9 "(limited data)"; < 5 "(low confidence)" |
| Debrief format | 5 sections in fixed order; Sections 1–4 always present, "—" for empty fields; Section 5 only when offer data exists |
| Recommendation scale | 4-level scale, degrading gracefully to 3- or 2-level |
| Rubric parsing | Leaf text extracted from nested rich-text answers; "X out of Y" normalized to the question's max weight |
| QoH | Favorable = rating ≥ 3; first survey per application |
| Privacy | No table names, column names, SQL or error text shown to the user |

## Expected challenges

1. **Scorecards are the hardest binding.** They often live in an app-specific view layer that search doesn't surface; the hydrator may find upstream ATS tables instead, which is fine if equivalent.
2. **Rich-text rubric answers.** Answers stored as nested JSON need leaf-text extraction; without it, strengths and quotes come out empty.
3. **Submitter vs interviewer ID.** The scorecard may record a "submitted by" user while interviews use an interviewer ID; the two must be joined correctly.
4. **ATS migration boundary.** Data before a migration cutover may be incomplete and skew calibration; the skill focuses on data after the cutover if one is configured.
5. **Optional data is often empty.** Most candidates have no offer or survey row; that is normal, not an error.
