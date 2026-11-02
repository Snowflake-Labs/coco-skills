# Golden Test Cases: interview_debrief

6 test cases covering the debrief by ID, name search, cross-candidate comparison, calibration, missing scorecards, and Section 5 / access guardrails. People Hydrator runs them at the end of Tune.

Replace placeholders ([application], [candidate], [requisition], [interviewer]) with values from your own data that fit each case. Results are rules checked against your own data, not fixed values. A case whose data isn't connected is recorded as not tested, not failed.

---

## Case 1: Full Debrief by Application ID

Headline case: the preview asks you to confirm this output looks right.

**Input:**
```
Debrief for application [application]
```
(Pick an application with several submitted scorecards and no offer.)

**Expected behavior:**
- Resolves the application by ID, no disambiguation
- Access preflight passes
- Warns up front if any interviewer hasn't submitted a scorecard
- Gathers scorecards, rubric answers, role spec, interview timeline and offer (empty here)
- Computes calibration and panel alignment
- Produces the 5-section debrief in the fixed format

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns
- [ ] No individual candidate data beyond what the user asked about

**Key assertions:**
- Section 1 has all 9 rows (Role through Status); missing values show "—"
- Section 2 has 2–3 Strengths, 2–3 Concerns, the Panel Signal table and the Interview Feedback Highlights table
- Panel Signal counts add up to the number of submitted scorecards; the agreement % = max count ÷ total
- Section 3 has Recommendation (HIRE / NO HIRE / CONDITIONAL), Role Fit and Rationale
- Section 4 has blockquoted quotes, Competency Breakdown, Interview Timeline, Signal Gaps, Calibration table and Missing Scorecards line
- Every Strength and Concern traces to a quote or rubric answer in Section 4
- Section 5 is absent
- No table names, SQL or row counts in the output

---

## Case 2: Candidate Name Search and Disambiguation

**Input:**
```
Debrief for [candidate] — [job title]
```

**Expected behavior:**
- Takes the name + role search path, not an ID lookup
- Shows one friendly progress line, no query echoes
- If several applications match, shows a table with job title + status and asks the user to pick
- After confirmation, produces the same debrief as Case 1

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns
- [ ] No individual candidate data beyond what the user asked about

**Key assertions:**
- Never auto-selects one of several matches
- Debrief title is "## Candidate Debrief: [candidate]"
- Format identical to Case 1

---

## Case 3: Cross-Candidate Comparison

**Input:**
```
Compare all candidates for [requisition]
```
(Pick a requisition with several candidates who have scorecards.)

**Expected behavior:**
- Routes to cross-candidate comparison, not a single debrief
- Ranks candidates with calibration-adjusted scoring and panel-alignment classification
- Offers a competency deep-dive as the next step

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Header is "## Cross-Candidate Comparison: [job title] ([requisition])"
- Columns exactly: Rank | Candidate | Company | Scorecards | Strong Yes | Yes | No | Def Not | Avg Score | Positive% | Cal. Score | Alignment
- Cal. Score weights Hawk positive votes × 1.3 and Dove × 0.8; ranking may differ from raw
- Each candidate's recommendation counts add up to their Scorecards count
- All candidates with scorecards are listed, or any truncation is stated
- No full debrief and no comp data unless asked

---

## Case 4: Calibration-Only Request

**Input:**
```
Show me the calibration profile for [interviewer]
```
(Pick an interviewer with 10+ scorecards in the last 12 months.)

**Expected behavior:**
- Returns the interviewer pattern profile only, no full debrief, no application lookup
- Shows trailing 12-month recommendation distribution, pattern and confidence label

**Guard checks:**
- [ ] G1 passes
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Pattern = Hawk if bottom-tier rate > 60%, Dove if top-tier rate > 80%, else Neutral
- Rates are computed over the interviewer's scorecards in the trailing 12 months
- Confidence is HIGH for n ≥ 10, "(limited data)" for 5–9, "(low confidence)" for < 5
- Described neutrally, not pejoratively

---

## Case 5: Missing Scorecard Detection

Needs interview dates linked to scorecards. If that isn't connected, record as not tested.

**Input:**
```
Are there any missing scorecards for [candidate]?
```
(Pick a candidate with at least one past interview without a submitted scorecard.)

**Expected behavior:**
- Access preflight passes; resolves the candidate's application
- Lists interviewers who took part but haven't submitted a scorecard
- Output in plain language

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns
- [ ] No individual candidate data beyond what the user asked about

**Key assertions:**
- Only interviews that have already happened are flagged; no future interviews
- Each flagged interview has a matching interview record with no submitted scorecard
- Each row names the interviewer, interview and date

---

## Case 6: Section 5 Conditionality and Access Guardrail

Part A needs offer data. If that isn't connected, record Part A as not tested.

**Part A — with an offer:**

**Input:**
```
Debrief for application [application with an offer]
```

**Expected behavior:**
- Full debrief, including Section 5 Comp / Offer Context

**Part B — without an offer:**

**Input:**
```
Debrief for application [application without an offer]
```

**Expected behavior:**
- Full debrief with Section 5 entirely absent

**Part C — no access:**

**Input:**
```
Debrief for [candidate]
```
(Run with a role that can't read the recruiting data.)

**Expected behavior:**
- Preflight fails; the skill stops and attempts no further queries
- Friendly message saying a permissions update is needed and to contact the People Analytics team

**Guard checks:**
- [ ] G1, G2 pass (Parts A and B)
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Part A: Section 5 present; Offer Status matches the offer record; missing fields show "—"
- Part B: no Section 5 heading, no "N/A" placeholder
- Part C: message contains no schema, table, column, SQL or raw error text, and doesn't name the missing role

---

## Scoring Summary

For each case, check:
1. **Routing**: correct mode (by ID, by name, comparison, calibration-only, missing scorecards)
2. **Data**: scorecards, rubric answers and applications for the debrief
3. **Format**: 5 sections in fixed order; exact table headers; Section 5 only with an offer
4. **Calibration**: Hawk / Dove / Neutral thresholds and adjusted weights as defined
5. **Guardrails**: no table names, SQL or error text shown
