---
name: offer_builder
description: >
  Gather and structure compensation justification data for offer approval. Pulls
  candidate profile, interview scorecard signals, team comparison with comp
  parity, and parses competing offer and current comp from ATS recruiter notes.
  Produces a Google Sheet combining candidate quality signals, competitive comp
  data, and team parity. Use when: offer approval, comp justification, competing
  offer, comp details, indexing on cash or equity, leaving commission behind,
  unvested equity. Triggers: offer justification, comp justification, offer
  approval, comp parity, competing offer [candidate], offer sheet for [candidate].
  Do NOT use for: candidate-facing offer letters, bulk compensation analysis, or
  summarizing interview feedback alone (use interview_debrief).
license: Proprietary. See LICENSE.txt for complete terms
---

# Offer Builder (Dehydrated)

## About this skill

- **What it does:** Builds the compensation justification for an offer approval: candidate quality from interview scorecards, competing offer and current pay from recruiter notes, and how the offer compares with the team.
- **Who it's for:** Recruiters and Total Rewards / compensation partners preparing offer approvals.
- **Works with:** Comes after `interview_debrief` in the hiring flow; it reuses the same scorecards.
- **Before you start:** This skill runs on the data and definitions connected by **People Hydrator** (`people/people-hydrator/`). If it hasn't been set up for your account yet, run People Hydrator first.
- **How to use it:** Say "offer justification for [candidate]" and give the candidate name and requisition. Confirm the pay details it reads from recruiter notes before it builds the sheet.
- **If you don't get what you expect:** If a section is missing or says data isn't available, that data wasn't connected during setup: re-run People Hydrator to add it. If a number looks wrong, tell People Hydrator what you expected; it turns that into a checked correction. If the skill says a table or column has changed, re-run People Hydrator for this skill.
- **Good to know:** The output is a Google Sheet if Google Sheets is connected to your Cortex Code setup; otherwise you get the same content as a table in chat. It is for internal approval only, never for candidates. The skill reads your data and never changes it.

## Intent

Produce a compensation justification output (structured sheet or inline summary) for offer approval decisions. Combines candidate quality signals from interview scorecards, competitive compensation context parsed from recruiter notes, and team comp parity data. Designed for recruiters and HR Business Partners preparing offer approvals — not for candidate-facing outputs and not for bulk compensation analysis.

Designed for **recruiters who are not technical**. Output must read like a business brief, not a database export — never expose table names, column names, SQL, or internal identifiers.

## Behavior

### Orchestration Flow

```
STEP 1  → Collect candidate + req inputs                       [STOP — wait for input]
STEP 2  → [Q1] Candidate profile lookup
STEP 3  → [Q2] Scorecard recommendations
          [Q3] Scorecard rubric / recruiter notes
STEP 4  → [Q4] Hiring manager lookup
          [Q5] Team members + hire-time comp
STEP 5  → Build team comparison table + narrative              (inline output)
STEP 6  → Parse comp from recruiter notes                      [STOP — confirm parsed data]
STEP 7  → Build + open Google Sheet                           [STOP — confirm sheet URL]
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
- Capture all **Basic Feedback Form** (recruiter process interview notes) rows verbatim — used for comp parsing in Step 6

### Step 4: Hiring Manager + Team

**4a — HM Lookup:** Run the hiring manager lookup for the requisition.

Capture: hiring manager ID and name.

**Fallback:** If no hiring manager ID is found, run a title-keyword + region search and use an interactive selector to confirm the correct manager before proceeding.

**4b — Team Comp:** Run the team members + hire-time comp query for the hiring manager.

Notes:
- Currencies vary by country — preserve the currency per row.
- Null comp = employee predates offer records; display as "—" in the sheet.
- Always apply the required effective date filter (the team comp query must filter to the latest employee snapshot).

### Step 5: Team Comparison Table + Narrative

Build inline in chat. Candidate row first, then team members ordered by hire date ascending.

**Table columns:**
| Name | Type | YOE | Prior Company | Sales Background | Hire OTE | Currency | OTE Compa-Ratio | Equity Compa-Ratio | Notable Signals |
|------|------|-----|--------------|-----------------|----------|----------|----------------|-------------------|----------------|

**Field derivation:**
- **YOE:** Use from ATS candidate profile. If null, infer from rubric answers or career history.
- **Sales Background:** Infer from prior company name + prior position (e.g., "Enterprise SaaS", "Channel / Partner", "Mid-Market").
- **Type:** "External" for the candidate; "Internal" for team members.

**Narrative (3–5 sentences):**
1. Where the candidate ranks on YOE vs team.
2. Whether sales background is differentiated or overlaps with team.
3. Interview signal strength vs hiring bar.
4. Comp parity: how proposed OTE and compa-ratio compare to team range.
5. One-line overall recommendation on fit.

### Step 6: Parse Comp from Recruiter Notes

Parse **Basic Feedback Form** rows (recruiter process interview notes) captured in Step 3b. Surface only what was **voluntarily disclosed** by the candidate — never solicit salary.

Extract if present:
- Current / last base salary (and currency)
- Current / last OTE or total comp
- Competing offer: company, base, variable, equity
- What the candidate is leaving behind: unvested equity, unpaid commission, sign-on clawback, unpaid bonus

If nothing is disclosed → write: `"Current comp not disclosed in scorecard."`

Present parsed data inline and ask: "Does this look accurate? Anything to add or correct before I build the sheet?"

**STOP** — Wait for user confirmation or corrections.

### Step 7: Build Google Sheet

**Sheet title:** `Comp Justification — [Candidate Name] | [Role] | [Requisition ID]`
**Tab name:** `Comp Justification`

Build the sheet in this order (no skipping):
1. Create the spreadsheet with the title above.
2. Write all data blocks in a single batch values update.
3. Apply all formatting in a single batch formatting update.
4. Open the sheet in the browser and confirm the URL to the user.

**STOP** — Confirm the sheet URL and that the browser opened successfully.

### Sheet Layout (5 Blocks)

**Block 1 — Header:** Candidate name, role, req ID, date generated, recruiter name.

**Block 2 — Candidate Profile:** All candidate context fields (stage, team, management level, business unit, source, prior company, prior position, YOE, degree, application ID).

**Block 3 — Interview Signal Summary:** Recommendation distribution (count by level), top strengths, top concerns, signal quality note (if applicable).

**Block 4 — Team Comparison Table:** Same structure as Step 5 inline table — candidate row highlighted, team sorted by hire date. Include narrative below the table.

**Block 5 — Competitive Comp Context:** Parsed comp data from Step 6. If nothing disclosed, show "Current comp not disclosed in scorecard." as a single row.

### Confidence Handling

- **HIGH confidence:** Candidate profile, scorecards, and team comp all resolve. Produce full sheet.
- **MEDIUM confidence:** Core candidate/scorecard data resolves but team comp is partially null (some employees predate offer records). Run with null rows shown as "—".
- **LOW confidence on non-critical bindings** (competitive comp parsing, equity data): Include parsed data with a note flagging the source.
- **LOW confidence on critical bindings** (candidate profile not found, no scorecards): Surface the specific gap and stop. Do not build a sheet with fabricated data.

### Constraints

- **Never solicit salary** — only record what appears in scorecards or what the user supplies.
- **Omit:** compensation ranges, salary expectations, age references, immigration sponsorship status.
- **Data scope:** Only query ATS recruiting data — do not query any other data domain not described in the Context Queries below.
- Always apply the latest effective date filter when querying current employee data.
- Never expose table names, column names, SQL, schema paths, or role names to the user.

### Guardrails

- Never fabricate scorecard data, comp values, or prior company information.
- If the ATS migration cutoff date is configured, surface a warning for scorecards predating it.
- Competing offer data is highly sensitive — never display it in the sheet title, tab name, or any visible header. Keep it in the Competitive Comp Context block.

### Error Handling

| Scenario | Response |
|----------|---------|
| Candidate not found | "No application found for that name. Try verifying the spelling in the ATS, or give me the application ID directly." |
| No scorecards submitted | "No scorecards found for this application. Interviews may not have occurred yet." |
| Multiple name matches | Present table of matches with job title + status; ask user to select. |
| Hiring manager not found on req | Run title-keyword fallback. Present candidates for user to confirm. |
| Authorization / permissions error | "I wasn't able to pull data right now — this is usually a quick permissions fix. Reach out to your People Analytics team." Never expose schema, table, or error text. |
| Query timeout | "This is taking longer than expected — the system might be busy. Want me to try again?" |
| Google Sheets creation fails | Offer to provide the output as an inline table in chat instead. |

## Quality

This skill is tested against `evals/offer_builder/golden_cases.md` and `evals/offer_builder/eval_plan.md` (People Hydrator runs them in its Evaluate stage). They are tests, not runtime instructions: follow this file and its customer configuration (added at hydration).

## Context Queries

- An ATS applications table at one row per application, with application ID, candidate ID, candidate name, job ID, current stage, application status, source, source group, recruiter ID, sourcer ID, application created date, prior company name, prior position title, years of experience, and degree — used for Step 2 candidate profile.

- An ATS scorecard / recommendations table at one row per scorecard submission per interviewer per application, with scorecard ID, application ID, interviewer user ID, scorecard title (interview type / round), overall recommendation (4-level scale: strong yes / yes / no / strong no or customer equivalent), and submitted timestamp — used for Step 3a recommendation distribution.

- An ATS scorecard rubric / per-question answers table at one row per (scorecard, question), with question text, answer text or JSON, scorecard ID, and application ID — used for Step 3b rubric analysis and recruiter notes parsing. Rich-text / JSON answers require nested-JSON parsing to extract leaf text.

- An ATS jobs / requisitions table at one row per requisition, with req ID, job title, grade, management level, department/team, business unit, cost center, location, status, and opening hiring manager ID — used for Step 4a hiring manager lookup.

- A current employee snapshot table at one row per employee per snapshot date (filter to latest), with employee ID, full name, management level, direct manager ID, hire date, cost center, and employee type — used to identify team members reporting to the hiring manager.

- An offer / hire compensation table at one row per offer per application, with application ID, employee ID, offer status, base salary, currency, target bonus %, OTE/TTC, equity grant value, equity compa-ratio, OTE compa-ratio, hire date — used for Step 4b team comp. Employees without offer records show null comp.

- An interviewer / user directory table at one row per interviewer, mapping interviewer user ID to full name and email — used to display human-readable interviewer names in scorecard output.
