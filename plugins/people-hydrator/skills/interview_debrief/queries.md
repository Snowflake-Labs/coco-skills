# Query Reference — Interview Debrief (Dehydrated)

All physical table/column references are resolved at runtime by the People hydrator against the customer's ATS / HRIS data in Snowflake. This file describes **what** each query needs to compute, not **how** to compute it against a specific schema.

---

## Data Shape (resolved at runtime)

**Primary sources:**
- Scorecards table (one row per scorecard submission per interviewer per application)
- Rubric / scorecard answers table (one row per question per scorecard)
- Applications table (one row per application)
- Jobs / requisitions table (one row per requisition)
- Interviews table (one row per interview event with scorecard link)
- Interviewer / user directory (one row per interviewer)

**Optional sources:**
- Offers table (one row per offer per application)
- Semantic view for Cortex Analyst follow-ups
- Cortex Search service for fuzzy candidate-name lookup

---

## Query Type 1 — Access Preflight

**Purpose**: Confirm the session role can read the scorecard / application data layer before doing any other work.

**Logic**:
1. Issue a minimal probe against the primary scorecards source (e.g., `SELECT 1 FROM <scorecards> LIMIT 1`).
2. On failure, do not proceed. Surface a friendly access-issue message; never expose the schema, table, or error text.

**Output grain**: success / fail signal only.

---

## Query Type 2 — Resolve Application

**Purpose**: Look up an application by ID, or search by candidate name (+ optional role) when the user does not have an ID.

**Logic**:
1. **By ID**: filter applications by exact application ID. Return candidate basics, job context, recruiter, current stage, status, source, application created date.
2. **By name**: case-insensitive substring match across active applications. Optionally filter by job title or grade.
3. If multiple matches, return all and let the user disambiguate.

**Output grain**: one row per matching application.

---

## Query Type 3 — Fetch Scorecards

**Purpose**: All scorecard submissions for an application, with interviewer details.

**Logic**:
1. Filter scorecards by application ID.
2. Join interviewer directory for full name + email.
3. Return: scorecard ID, scorecard title (interview type / round), interviewer name, overall recommendation (4-level), submitted timestamp.

**Output grain**: one row per scorecard.

---

## Query Type 4 — Fetch Rubric Answers

**Purpose**: Per-question answers across all scorecards for the application, including free-text narrative.

**Logic**:
1. Join rubric/answers table to scorecards on scorecard ID, filtered to the application.
2. Return: scorecard ID, scorecard title, question text, answer text (or parsed JSON leaf text).
3. Apply nested-JSON parsing if the answer field is a rich-text doc structure — extract the text leaf (typically a path like `content[].content[].text`).
4. Recognize answer shapes: pure numeric, "X out of Y", or free-text feedback.

**Output grain**: one row per (scorecard, question).

**Notes**:
- For numeric scoring, extract a max-weight hint from the question title where present (e.g., "Coding (max weight 25)") and normalize "X out of Y" to that scale.

---

## Query Type 5 — Fetch Role Spec

**Purpose**: Job profile, grade, level, department, competencies for gap analysis and Section 1 context.

**Logic**:
1. Filter jobs/requisitions by job ID.
2. Return: title, grade, level, department/team, profile, competencies (must-have / nice-to-have if available), comp ranges (base, OTE, equity) if available.

**Output grain**: one row per job.

---

## Query Type 6 — Fetch Interview Timeline

**Purpose**: Ordered interview events for the application with stage context.

**Logic**:
1. Filter interviews by application ID.
2. Join scorecards on scorecard ID for recommendation, and interviewer directory for names.
3. Order by stage order, then interview date.
4. Return: stage group, stage name, interview title, interviewer name, interview date, recommendation.

**Output grain**: one row per interview event.

---

## Query Type 7 — Detect Missing Scorecards

**Purpose**: Identify interviewers who participated but have not submitted feedback.

**Logic**:
1. From interviews for the application, get all participating interviewer IDs where feedback is required.
2. From scorecards for the application, get all submitted-by interviewer IDs.
3. Anti-join: required-but-not-submitted = missing.
4. Filter to interviews that have already occurred (interview date in the past).
5. Return: interviewer name, interview type, interview date, hours overdue.

**Output grain**: one row per missing scorecard.

---

## Query Type 8 — Calculate Interviewer Calibration

**Purpose**: Hawk / Dove / Neutral classification per interviewer, based on trailing 12-month recommendation history.

**Logic**:
1. For the set of interviewers on this candidate's panel, pull all their scorecards in the trailing 12 months.
2. Compute distribution: `pct_strong_yes`, `pct_yes`, `pct_no`, `pct_definitely_not`, `total_count`.
3. Classify:
   - Hawk: `pct_no + pct_definitely_not > 60%`
   - Dove: `pct_strong_yes + pct_yes > 80%`
   - Neutral: otherwise
4. Apply confidence labels by sample size: HIGH (n ≥ 10), MEDIUM (n = 5–9, suffix "limited data"), LOW (n < 5, suffix "low confidence").

**Output grain**: one row per interviewer.

**Notes**: Calibration is descriptive context only. Never use it to reweight votes or order candidates.

---

## Query Type 9 — Calculate Panel Alignment

**Purpose**: Single agreement score across the interview panel.

**Logic**:
1. From scorecards for the application, count by overall recommendation.
2. `agreement_pct = max(count_per_recommendation) / sum(count) × 100`.
3. Classify: Aligned (≥75%), Mixed (50–74%), Split (<50%).

**Output grain**: one summary row per application.

---

## Query Type 10 — Fetch Offer Details

**Purpose**: Comp / offer breakdown for the application's current offer (if any).

**Logic**:
1. Filter offers by application ID, most recent / current offer.
2. Return: status (open / accepted / declined / withdrawn / rescinded), offer extended date, accept-by date, base salary + currency, target bonus %, equity value (USD) + type + vesting, equity compa-ratio, sign-on bonus + currency, TTC/OTE + currency, TTC compa-ratio, relocation amount + currency, commission (if applicable), work location, region, start date, days to decision.
3. Empty result is expected and Section 5 of the debrief is omitted entirely.

**Output grain**: zero or one row per application.

---

## Query Type 11 — Side-by-Side Candidates for a Requisition

**Purpose**: Show the recorded panel evidence for every candidate on a single requisition, unranked.

**Logic**:
1. For each candidate on the requisition with ≥ 2 scorecards:
   - Count scorecards by recommendation level.
   - Classify panel alignment (Aligned / Mixed / Split) per Query Type 9.
   - List role competencies not assessed (signal gaps).
2. Return: candidate name, scorecard count, count by recommendation, alignment, signal gaps.
3. Order alphabetically by candidate name. Do not compute a rank, composite score, average score or calibration-adjusted score.

**Output grain**: one row per candidate per requisition.

---

## Query Type 12 — Cross-Candidate Competency View

**Purpose**: Show candidates' recorded rubric scores competency by competency for a requisition, unranked (alphabetical order, no totals).

**Logic**:
1. From rubric answers across all candidates on the requisition:
   - Parse JSON / extract leaf text.
   - Identify numeric scores (pure number, or "X out of Y").
   - Extract max-weight from question title where present.
   - Normalize "X out of Y" to max weight: `score = raw × max_weight / Y`.
   - Compute percentage of max: `pct = score / max_weight × 100`.
2. Pivot by interview type, competency, and candidate.
3. Apply score bands: Strong (≥80%), Good (60–79%), Weak (<60%).

**Output grain**: one row per (interview_type, competency, candidate) cell in the heatmap.

---

## Pitfalls

1. **Recommendation scale variance** — not every ATS uses 4 levels. If the upstream uses 3 (yes / maybe / no) or 5 (with a separate "lean yes"), the calibration thresholds (60% / 80%) and the Panel Signal table need to flex. Detect the actual scale from data before classifying.
2. **JSON answer fields** — many ATSes store rubric answers as nested rich-text doc objects, not flat strings. Always TRY_PARSE before treating the field as text. The leaf path varies by ATS.
3. **"X out of Y" vs raw score** — if you extract the leading number naively, you'll record "8" when the answer is "8 out of 25". Always check for the "out of Y" pattern and normalize.
4. **Calibration sample size** — an interviewer with 3 lifetime scorecards is not a Hawk no matter what their distribution looks like. Apply the n ≥ 10 / 5–9 / <5 confidence bands and degrade the calibration label, not just the disclaimer.
5. **Panel of 2** — "75% agreement" with 2 scorecards is mathematically meaningless. Suppress the alignment classification when total scorecards < 3 and instead say "limited panel — 2 interviewers".
6. **Missing-scorecard false positives** — only flag interviewers where feedback is required and the interview has already occurred. Otherwise you'll page someone for tomorrow's interview.
7. **Offer absence is normal** — most candidates do not have an offer record. Empty offer result is the silent-skip path for Section 5, not an error.
8. **Pre-migration data** — if the customer migrated ATSes, the older data may have different field semantics or missing rubric answers. Respect any cutover date the customer has supplied and weight accordingly.
9. **No ranking by accident** — sorting the side-by-side or competency view by any score is a ranking. Always sort by candidate name.
