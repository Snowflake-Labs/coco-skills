# Query Reference — Offer Builder (Dehydrated)

All physical table/column references are resolved at runtime by the People hydrator against the customer's ATS / HRIS data in Snowflake. This file describes **what** each query needs to compute, not **how** to compute it against a specific schema.

---

## Data Shape (resolved at runtime)

**Primary sources:**
- ATS applications table (one row per application)
- ATS scorecard / recommendations table (one row per scorecard per interviewer per application)
- ATS scorecard rubric / per-question answers table (one row per question per scorecard)
- ATS jobs / requisitions table (one row per req)
- Current employee snapshot table (one row per employee per snapshot date)
- Offer / hire compensation table (one row per offer per application)
- Interviewer / user directory (one row per interviewer)

---

## Query Type 1 — Candidate Profile Lookup (Q1)

**Purpose:** Retrieve the candidate's application profile for a given name (and optionally req ID).

**Logic:**
1. Case-insensitive substring match on candidate name across active (and recently archived) applications.
2. If req ID is provided, filter to that req.
3. Return: application ID, candidate ID, candidate name, job ID, req ID, current stage, application status, source, source group, recruiter, sourcer, application created date, prior company, prior position, years of experience, degree.
4. If 1 row: return directly. If 2–5 rows: return all for user disambiguation. If 0 rows: report not found.

**Output grain:** One row per matching application.

---

## Query Type 2 — Scorecard Recommendations (Q2)

**Purpose:** All scorecard submissions for the application with recommendation distribution.

**Logic:**
1. Filter scorecards by application ID.
2. Join interviewer directory for full name.
3. Return: scorecard ID, interview type / round (scorecard title), interviewer name, overall recommendation (4-level scale), submitted timestamp.
4. Compute distribution: count of each recommendation level.

**Output grain:** One row per scorecard.

**Signal quality gates:**
- If total scorecards < 3: surface warning inline.
- If all submitted timestamps predate the ATS migration cutoff date (customer-configured): surface warning inline.

---

## Query Type 3 — Scorecard Rubric + Recruiter Notes (Q3)

**Purpose:** Per-question answers across all scorecards for the application, including free-text recruiter process notes.

**Logic:**
1. Join rubric/answers table to scorecards on scorecard ID, filtered to the application.
2. Return: scorecard ID, scorecard title (interview type), question text, answer text (or JSON requiring parsing).
3. Flag rows where the scorecard title matches a "Basic Feedback Form" pattern (recruiter process notes) — these are the rows used for comp parsing in Step 6.
4. For non-recruiter-notes rubric rows: summarize top strengths and concerns cited by 2+ interviewers.

**Output grain:** One row per (scorecard, question).

**Notes:** Rich-text / JSON answer fields require nested-JSON parsing to extract leaf text. Recognize three answer shapes: pure numeric, "X out of Y", free-text.

---

## Query Type 4 — Hiring Manager Lookup (Q4)

**Purpose:** Identify the hiring manager for the requisition.

**Logic:**
1. Filter requisitions by req ID.
2. Return: opening hiring manager ID and name.

**Fallback (if no hiring manager found on req):**
- Search employees by job title keyword + region match.
- Return candidate list for user disambiguation.

**Output grain:** One row per req (primary) or multiple rows for fallback disambiguation.

---

## Query Type 4 Fallback — Title + Region Search (Q4_FALLBACK)

**Purpose:** Find likely hiring managers when none is recorded on the req.

**Logic:**
1. Search employee snapshot (latest) by job title keyword (case-insensitive substring) and optionally filter by region or location.
2. Filter to managers only (employees with direct reports).
3. Return: employee ID, full name, job title, location — for user to confirm.

**Output grain:** One row per candidate manager.

---

## Query Type 5 — Team Members + Hire-Time Comp (Q5)

**Purpose:** List all current team members reporting to the hiring manager and their compensation at hire.

**Logic:**
1. From the employee snapshot (latest date), find all employees where direct manager ID = hiring manager ID.
2. Join to the offer / hire comp table on employee ID (or application ID) to get hire-time compensation.
3. Apply latest effective date filter on the employee snapshot table.
4. Return per team member: employee ID (anonymized for display as name only), management level, hire date, prior company (if available), years of experience (if available), sales background inference (from prior company + prior position), hire base salary, hire OTE/TTC, OTE compa-ratio, equity compa-ratio, currency.
5. For employees without an offer record: show null comp (display as "—").

**Output grain:** One row per team member.

**Notes:** Currencies may vary by country — preserve the currency code per row. Never aggregate across currencies without converting first.

---

## Pitfalls

1. **No scorecard edge case** — an application with no scorecards returns zero rows on Q2/Q3. Do not attempt to parse comp from empty results. Surface the missing-scorecards warning and let the user decide whether to proceed.
2. **Multiple hiring manager records** — some ATS platforms allow multiple hiring managers on a req. Use the primary/opening hiring manager for team scoping. If the array is empty, fall back to Q4_FALLBACK.
3. **Null comp is normal** — employees hired before offer data was tracked will have null comp. Show "—" — do not suppress the row or treat null as zero.
4. **Currency heterogeneity** — teams with members in multiple countries will have comp values in different currencies. Never sum OTEs across currencies without a conversion step. Display each row's currency explicitly.
5. **Recruiter notes vs candidate feedback** — the rubric table contains both interviewer-written feedback (candidate evaluation) and recruiter process notes (logistics, comp discussions). Distinguish these by scorecard/interview type. Comp parsing is only from recruiter process notes rows, never from candidate evaluation rows.
6. **Comp data sensitivity** — hire-time compensation is sensitive employee data. Only surface aggregate context (compa-ratio, range position) to the user, not raw salary figures unless the session role has explicit compensation access. Follow the customer's configured data access policy.
7. **ATS migration cutoff** — scorecards submitted before the ATS migration cutoff date may have different field semantics or missing rubric answers. Always surface the warning and let the user decide how much weight to give pre-migration data.
8. **Latest effective date filter** — the employee snapshot must be filtered to the latest available effective date. Omitting this filter returns multiple rows per employee (one per historical snapshot date) and inflates team size.
