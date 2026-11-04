---
name: offer_builder
description: >
  Gather and structure compensation justification data for offer approval. Pulls
  candidate profile, interview scorecard signals, aggregate team comp parity, and
  any competing offer the candidate voluntarily disclosed in ATS recruiter notes.
  Never uses a candidate's current or prior pay. Produces an in-chat justification
  summary for internal approval. Use when: offer approval, comp justification,
  competing offer, comp parity. Triggers: offer justification, comp justification,
  offer approval, comp parity, competing offer [candidate], offer summary for
  [candidate].
  Do NOT use for: candidate-facing offer letters, bulk compensation analysis, or
  summarizing interview feedback alone (use interview_debrief).
license: Snowflake Skills License. See LICENSE.txt for complete terms
type: snowflake
language: en
status: Published
---

# Offer Builder (Dehydrated)

## About this skill

- **What it does:** Builds the compensation justification for an offer approval: candidate quality from interview scorecards, any competing offer the candidate chose to share, and how the proposed offer sits against the team's pay range (as aggregates, never individuals).
- **Who it's for:** Recruiters and Total Rewards / compensation partners preparing offer approvals.
- **Works with:** Comes after `interview_debrief` in the hiring flow; it reuses the same scorecards.
- **Before you start:** This skill runs on the data and definitions connected by **People Hydrator** (`plugins/people-hydrator/`). If it hasn't been set up for your account yet, run People Hydrator first.
- **How to use it:** Say "offer justification for [candidate]" and give the candidate name and requisition. Confirm any competing-offer details it reads from recruiter notes before it builds the summary.
- **If you don't get what you expect:** If a section is missing or says data isn't available, that data wasn't connected during setup: re-run People Hydrator to add it. If a number looks wrong, tell People Hydrator what you expected; it turns that into a checked correction. If the skill says a table or column has changed, re-run People Hydrator for this skill.
- **Good to know:** It never uses the candidate's current or past pay: many US states and cities ban using pay history to set an offer. Team pay is shown only as ranges for groups large enough that no one's pay can be worked out, with no names. The output stays in chat; it is not exported to spreadsheets or other tools. It is for internal approval only, never for candidates. The skill reads your data and never changes it.

## Intent

Produce an in-chat compensation justification summary for offer approval decisions. Combines candidate quality signals from interview scorecards, competing-offer context the candidate voluntarily disclosed, and aggregate team comp parity. Designed for recruiters and HR Business Partners preparing offer approvals — not for candidate-facing outputs and not for bulk compensation analysis.

Designed for **recruiters who are not technical**. Output must read like a business brief, not a database export — never expose table names, column names, SQL, or internal identifiers.

## Behavior

### Orchestration Flow

```
STEP 1  → Collect candidate + req inputs                       [STOP — wait for input]
STEP 2  → [Q1] Candidate profile lookup
STEP 3  → [Q2] Scorecard recommendations
          [Q3] Scorecard rubric / recruiter notes
STEP 4  → [Q4] Hiring manager lookup
          [Q5] Aggregate team comp
STEP 5  → Build team parity summary + narrative                (inline output)
STEP 6  → Extract competing offer from recruiter notes         [STOP — confirm extracted data]
STEP 7  → Render justification summary in chat
```

### Step 1: Collect Inputs

Use an interactive question prompt to collect:
1. Candidate name (as it appears in the ATS)
2. Requisition ID or job title

**STOP** — Do not proceed until both values are provided.

### Step 2: Candidate Profile Lookup

Run the candidate profile query with the provided name.

| Result count | Action |
|---|---|
| 0 rows | Inform user. Ask to verify spelling or check if application is archived. STOP. |
| 1 row | Capture all fields. Proceed. |
| 2–5 rows | Use interactive selector to confirm which application. Capture fields. Proceed. |

**Capture and carry forward:** application ID, candidate ID, requisition ID, and all profile fields (name, stage, team, management level, business unit, recruiter, sourcer, prior company, prior position, years of experience, degree).

### Step 3: Scorecards

**3a — Recommendations:** Run the scorecard recommendations query for the application.

Signal quality gate (surface inline warnings — do not stop):
- Fewer than 3 scorecards on file → `Warning: fewer than 3 scorecards on file.`
- All scorecards submitted before the ATS migration cutoff date → `Warning: Pre-migration data may be incomplete.`

**3b — Rubric + Recruiter Notes:** Run the rubric / per-question answers query for the application.

Summarize and carry forward:
- Recommendation distribution: count of Strong Yes / Yes / No / Strong No (or customer's equivalent scale)
- Top 2–3 standout signals (strengths or concerns cited by 2+ interviewers)
- Capture **Basic Feedback Form** (recruiter process interview notes) rows — used only for competing-offer extraction in Step 6. Do not carry forward or repeat any current or prior pay that appears in them.

### Step 4: Hiring Manager + Team

**4a — HM Lookup:** Run the hiring manager lookup for the requisition.

Capture: hiring manager ID and name.

**Fallback:** If no hiring manager ID is found, run a title-keyword + region search and use an interactive selector to confirm the correct manager before proceeding.

**4b — Team Comp:** Run the aggregate team comp query for the hiring manager.

Notes:
- Currencies vary by country — aggregate within one currency at a time; never combine currencies.
- Employees who predate offer records are excluded from comp aggregates and counted as "comp not on file".
- Always apply the required effective date filter (the team comp query must filter to the latest employee snapshot).
- **Minimum group size:** only report a comp aggregate for a group of at least 5 employees with comp on file (or the customer's configured threshold, if higher). Smaller groups show "— (group too small to report)".

### Step 5: Team Parity Summary + Narrative

Build inline in chat. No team member names, and no individual pay values.

**Team profile table (counts only):**
| Metric | Team | Candidate |
|--------|------|-----------|
| Headcount (comp on file / total) | | — |
| YOE (median, range) | | |
| Sales background mix | | |

**Comp parity table (per currency):**
| Currency | n | Hire Base (P25 / Median / P75) | Hire OTE (P25 / Median / P75) | Proposed Offer Position |
|----------|---|--------------------------------|-------------------------------|-------------------------|

Proposed Offer Position = where the proposed offer (supplied by the user) falls in the team distribution: below P25, P25–median, median–P75, or above P75. Compa-ratio percentiles may be added in the same way if the customer's offer data includes them.

**Field derivation:**
- **YOE:** Use from ATS candidate profile. If null, infer from rubric answers or career history.
- **Sales Background:** Infer from prior company name + prior position (e.g., "Enterprise SaaS", "Channel / Partner", "Mid-Market").

**Narrative (3–5 sentences):**
1. Where the candidate's YOE sits within the team's range.
2. Whether sales background is differentiated or overlaps with the team mix.
3. Interview signal strength, described from the recorded scorecards.
4. Comp parity: where the proposed offer sits within the team's pay range.
5. Points the approver should weigh. Do not state a decision; approval belongs to the approver.

### Step 6: Extract Competing Offer from Recruiter Notes

Read the **Basic Feedback Form** rows (recruiter process interview notes) captured in Step 3b. Surface only a competing offer the candidate **voluntarily disclosed** — never solicit it.

Extract if present:
- Competing offer: company, base, variable, equity, and whether the candidate described it as a written offer

**Never extract, display or use pay history.** This includes current or last base salary, current or last OTE / total comp, bonus or commission history, and what the candidate would leave behind (unvested equity, unpaid commission or bonus, sign-on clawback). If the notes contain any of these, ignore them and add one line: "Recruiter notes contain pay-history details, which this skill does not use." If the user supplies pay history directly, decline to use it and explain why in one sentence.

If no competing offer is disclosed → write: `"No competing offer disclosed."`

Present the extracted data inline and ask: "Does this look accurate? Anything to add or correct before I build the summary?"

**STOP** — Wait for user confirmation or corrections.

### Step 7: Render Justification Summary

Render the summary in chat as markdown. Do not export it to Google Sheets or any other external document, spreadsheet or tool, even if a connector is available. If the user asks for an export, explain that the summary stays in chat for privacy and that they can copy it into an approved internal system.

**Title:** `Comp Justification — [Candidate Name] | [Role] | [Requisition ID]`

### Summary Layout (5 Blocks)

**Block 1 — Header:** Candidate name, role, req ID, date generated, recruiter name.

**Block 2 — Candidate Profile:** All candidate context fields (stage, team, management level, business unit, source, prior company, prior position, YOE, degree, application ID).

**Block 3 — Interview Signal Summary:** Recommendation distribution (count by level), top strengths, top concerns, signal quality note (if applicable).

**Block 4 — Team Parity:** The Step 5 team profile and comp parity tables, plus the narrative.

**Block 5 — Competing Offer Context:** Extracted data from Step 6. If nothing disclosed, show "No competing offer disclosed." as a single row.

### Confidence Handling

- **HIGH confidence:** Candidate profile, scorecards, and team comp all resolve. Produce the full summary.
- **MEDIUM confidence:** Core candidate/scorecard data resolves but team comp is partially missing (some employees predate offer records). Aggregate over those on file and state the count.
- **LOW confidence on non-critical bindings** (competing offer extraction, equity data): Include extracted data with a note flagging the source.
- **LOW confidence on critical bindings** (candidate profile not found, no scorecards): Surface the specific gap and stop. Do not build a summary with fabricated data.

### Constraints

- **Never solicit or use pay history** — not from notes, not from the user, not inferred. Only a voluntarily disclosed competing offer may be recorded.
- **No individual employee pay.** Never show a named or identifiable team member's salary, OTE, equity or compa-ratio; report aggregates only, subject to the minimum group size.
- **No export.** Output stays in chat; never write to Google Sheets or any external file, spreadsheet or tool.
- **Omit:** compensation ranges, salary expectations, age references, immigration sponsorship status.
- **Data scope:** Only query ATS recruiting data — do not query any other data domain not described in the Context Queries below.
- Always apply the latest effective date filter when querying current employee data.
- Never expose table names, column names, SQL, schema paths, or role names to the user.

### Guardrails

- Never fabricate scorecard data, comp values, or prior company information.
- If the ATS migration cutoff date is configured, surface a warning for scorecards predating it.
- Competing offer data is highly sensitive — never display it in the summary title or any visible header. Keep it in the Competing Offer Context block.

### Error Handling

| Scenario | Response |
|----------|---------|
| Candidate not found | "No application found for that name. Try verifying the spelling in the ATS, or give me the application ID directly." |
| No scorecards submitted | "No scorecards found for this application. Interviews may not have occurred yet." |
| Multiple name matches | Present table of matches with job title + status; ask user to select. |
| Hiring manager not found on req | Run title-keyword fallback. Present candidates for user to confirm. |
| Authorization / permissions error | "I wasn't able to pull data right now — this is usually a quick permissions fix. Reach out to your People Analytics team." Never expose schema, table, or error text. |
| User asks to export to Google Sheets or another tool | "This summary stays in chat to protect candidate and employee pay data. You can copy it into your approved internal system." |
| User asks for pay history or an individual team member's pay | Decline in one sentence (pay history isn't used to set offers; individual pay isn't shown) and continue with the aggregate summary. |

## Quality

This skill is tested against `evals/offer_builder/golden_cases.md` and `evals/offer_builder/eval_plan.md` (People Hydrator runs them in its Evaluate stage). They are tests, not runtime instructions: follow this file and its customer configuration (added at hydration).

## Context Queries

- An ATS applications table at one row per application, with application ID, candidate ID, candidate name, job ID, current stage, application status, source, source group, recruiter ID, sourcer ID, application created date, prior company name, prior position title, years of experience, and degree — used for Step 2 candidate profile.

- An ATS scorecard / recommendations table at one row per scorecard submission per interviewer per application, with scorecard ID, application ID, interviewer user ID, scorecard title (interview type / round), overall recommendation (4-level scale: strong yes / yes / no / strong no or customer equivalent), and submitted timestamp — used for Step 3a recommendation distribution.

- An ATS scorecard rubric / per-question answers table at one row per (scorecard, question), with question text, answer text or JSON, scorecard ID, and application ID — used for Step 3b rubric analysis and recruiter notes parsing. Rich-text / JSON answers require nested-JSON parsing to extract leaf text.

- An ATS jobs / requisitions table at one row per requisition, with req ID, job title, grade, management level, department/team, business unit, cost center, location, status, and opening hiring manager ID — used for Step 4a hiring manager lookup.

- A current employee snapshot table at one row per employee per snapshot date (filter to latest), with employee ID, full name, management level, direct manager ID, hire date, cost center, and employee type — used to identify team members reporting to the hiring manager.

- An offer / hire compensation table at one row per offer per application, with application ID, employee ID, offer status, base salary, currency, OTE/TTC, optional compa-ratios, hire date — used only to compute Step 4b aggregate team comp percentiles; individual rows are never displayed. Employees without offer records are excluded from aggregates.

- An interviewer / user directory table at one row per interviewer, mapping interviewer user ID to full name and email — used to display human-readable interviewer names in scorecard output.
