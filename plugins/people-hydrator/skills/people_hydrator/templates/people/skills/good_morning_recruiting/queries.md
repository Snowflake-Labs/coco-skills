# Query Reference — Good Morning Recruiting (Dehydrated)

All physical table/column references are resolved at runtime by the People hydrator against the customer's ATS / HRIS data in Snowflake. This file describes **what** each query needs to compute, not **how** to compute it against a specific schema.

---

## Data Shape (resolved at runtime)

**Primary sources:**
- HM sentiment / survey scores table
- ATS applications / sourcing table
- Requisitions table
- Candidates-in-stage table
- Employee prior company / hire source table
- Job openings table
- Offers table

**Competitor Intel source:** Live web search (no Snowflake data).

---

## Query Type 1 — Hiring Manager Sentiment

**Purpose:** Average HM satisfaction score per recruiter for the period, and flag recruiters below a configurable threshold.

**Logic:**
1. Filter survey / feedback scores to the requested period.
2. Optionally filter by recruiter or department.
3. Group by recruiter.
4. Compute: average score, survey count, % of HMs surveyed.
5. Flag recruiters where average score is below a configurable threshold.

**Output grain:** One row per recruiter with average score and flag status.

**Confidence notes:** If HM survey data does not exist or has fewer than 3 responses per recruiter, mark as "(limited data)".

---

## Query Type 2 — Sourced % by Recruiter

**Purpose:** Share of closed candidates who were sourced (proactively identified) vs inbound (applied directly) per recruiter.

**Logic:**
1. Filter applications to the requested period and to filled/closed dispositions.
2. Optionally filter by recruiter or department.
3. Classify each application as sourced (source type = proactively identified / prospecting) vs inbound.
4. Group by recruiter.
5. Compute: total applications, sourced count, sourced %.
6. Flag recruiters below a configurable sourced % threshold.

**Output grain:** One row per recruiter.

---

## Query Type 3 — Req Load by Recruiter

**Purpose:** Number of open requisitions assigned to each recruiter, with breakdown by assignment type (sole vs shared) and status.

**Logic:**
1. Filter requisitions to open status as of the snapshot date.
2. Optionally filter by recruiter or department.
3. Group by recruiter.
4. Compute: total open reqs, sole-assigned reqs, shared reqs, unassigned reqs.
5. Return company totals in summary.

**Output grain:** One row per recruiter + one summary row.

---

## Query Type 4 — Stale Candidates

**Purpose:** Identify candidates who have been in the same interview stage longer than a configurable threshold.

**Logic:**
1. Filter candidates to active applications in an active interview stage.
2. Compute days in current stage = today − stage start date.
3. Apply two thresholds (configurable):
   - **Violation:** Days in stage > violation threshold (e.g., 14 days). Flag 🔴.
   - **Approaching:** Days in stage > warning threshold but ≤ violation threshold (e.g., 7–14 days). Flag 🟡.
4. Optionally filter by recruiter or department.

**Output grain:** One row per candidate per stage.

---

## Query Type 5 — Hires from Competitors

**Purpose:** Surface recent hires whose prior employer matches a configured list of competitor companies.

**Logic:**
1. Filter new hire events to the requested period.
2. Join to prior company data.
3. Match prior company name against the customer's configured competitor list (case-insensitive substring or exact match — customer-configurable).
4. Return: hire date, candidate name (or anonymized ID if privacy policy requires), prior company, job title, department, recruiter.

**Output grain:** One row per hire from a competitor company.

---

## Query Type 6 — Stale Job Openings

**Purpose:** Identify open requisitions that have been open longer than configurable thresholds, segmented by evergreen vs non-evergreen.

**Logic:**
1. Filter job openings to open status.
2. Compute days open = today − opening date.
3. Apply two thresholds (configurable):
   - **Critical / Red:** Days open > 180 days. Flag 🔴.
   - **Warning / Orange:** Days open > 120 but ≤ 180 days. Flag 🟡.
4. Segment results: non-evergreen roles (standard backfill / new headcount) vs evergreen roles (ongoing pipeline roles that remain open by design).
5. Compute summary counts by department.

**Output grain:** One row per opening, segmented by evergreen flag.

---

## Query Type 7 — Recent Offer Declines (Last 14 Days)

**Purpose:** List offer declines from the last 14 calendar days.

**Logic:**
1. Filter offers to declined status.
2. Apply time window: declined date ≥ today − 14 calendar days.
3. Return: candidate name, job title, location, recruiter, decline reason, declined date.
4. Count total declines in the window.

**Output grain:** One row per declined offer.

**Scope constraint:** This query surfaces decline detail ONLY. Do NOT compute offer acceptance rate or per-recruiter offer counts from this query — those are separate metric types.

---

## Pitfalls

1. **Sourced % denominator** — using total applications (including active pipeline) instead of filled/closed applications inflates the denominator and understates sourced %. Always filter to closed or filled dispositions.
2. **Stale candidate false positives** — only flag candidates where the current stage has already started (stage start date is not null and is in the past). Future-scheduled stages should not count.
3. **Evergreen vs non-evergreen segmentation** — including evergreen roles in the stale openings violation count inflates the red count. Always segment and present them separately.
4. **Competitor match quality** — fuzzy competitor name matching (substring) may return false positives (e.g., "Data" matching "Databricks" and "Data Corp"). Review the matching approach with the customer at setup.
5. **Offer declines scope** — this metric is a 14-day snapshot for leadership awareness only. Do NOT compute trailing acceptance rate or per-recruiter counts in this query — those belong in a separate offers analysis.
6. **HM sentiment confidence** — a recruiter with 2 survey responses can have a "perfect" 10/10 score that ranks above a recruiter with 50 responses averaging 9.5. Apply a minimum response count before flagging or surfacing in the leaderboard.
7. **Fiscal quarter alignment** — always resolve period boundaries from the fiscal calendar rather than computing manually from the current date. Hardcoding quarter dates breaks at fiscal year boundaries.
