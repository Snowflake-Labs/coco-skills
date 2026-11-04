# Golden Test Cases: offer_builder

4 test cases covering the standard justification summary, name disambiguation, the no-scorecards gate and pay-privacy safeguards. People Hydrator runs them at the end of Tune.

Replace placeholders ([candidate], [requisition]) with values from your own data that fit each case. Results are rules checked against your own data, not fixed values. A case whose data isn't connected is recorded as not tested, not failed.

---

## Case 1: Standard Offer Justification Summary

Headline case: the preview asks you to confirm this output looks right.

**Input:**
```
Build an offer justification for [candidate]
```
(Pick a candidate with several scorecards on a requisition whose hiring manager has a team.)

**Expected behavior:**
- Step 1: asks for the requisition ID or job title and waits
- Step 2: finds the candidate's application by name
- Step 3: recommendation distribution and top signals; captures recruiter process notes
- Step 4: hiring manager for the requisition, then aggregate team comp
- Step 5: inline team profile and comp parity tables and a 3–5 sentence narrative
- Step 6: competing offer extracted from recruiter notes (if disclosed), then waits for confirmation
- Step 7: renders the 5-block summary in chat

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns
- [ ] No individual candidate data beyond what the user asked about

**Key assertions:**
- Recommendation counts add up to the number of scorecards on the application
- Team = employees on the latest snapshot whose manager is the requisition's hiring manager
- Comp parity table columns: Currency | n | Hire Base (P25 / Median / P75) | Hire OTE (P25 / Median / P75) | Proposed Offer Position
- No team member names and no individual pay anywhere in the output
- One parity row per currency; any group with fewer than 5 employees with comp on file shows "— (group too small to report)"
- No current or prior pay for the candidate; Competing Offer Context holds only a disclosed competing offer, or "No competing offer disclosed."
- Title is "Comp Justification — [candidate] | [role] | [requisition]"; no competing offer data in title or headers
- No Google Sheet or other export is created
- No compensation ranges, table names or SQL in the output

---

## Case 2: Multiple Name Matches

**Input:**
```
Offer sheet for [candidate]
```
(Pick a candidate name with 2–5 applications.)

**Expected behavior:**
- Step 2 finds several applications for the name
- Shows a table of matches with job title + status and asks which one
- Continues from Step 3 with the selected application only

**Guard checks:**
- [ ] G1 passes
- [ ] No made-up numbers, tables or columns
- [ ] No individual candidate data beyond what the user asked about

**Key assertions:**
- Never auto-selects the first match
- Every match shown is an application for that name
- All later steps use only the selected application

---

## Case 3: No Scorecards Yet

**Input:**
```
Comp sheet for [candidate]
```
(Pick a candidate whose application has no scorecards.)

**Expected behavior:**
- Step 3 finds no scorecards for the application
- Says: "No scorecards found for this application. Interviews may not have occurred yet."
- Treats this as a critical gap: surfaces it and stops; no summary is built

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns
- [ ] No individual candidate data beyond what the user asked about

**Key assertions:**
- The application has zero scorecards in your data
- No summary and no invented scorecard data
- Message contains no table names or SQL errors

---

## Case 4: Pay History and Export Safeguards

**Part A — pay history in notes:**

(Pick a candidate whose recruiter notes mention current or prior pay, if one exists. Otherwise record Part A as not tested.)

**Input:**
```
Offer justification for [candidate]
```

**Expected behavior:**
- The current / prior pay figures are not shown, summarized or used anywhere
- One line says recruiter notes contain pay-history details the skill does not use

**Part B — user asks for pay history, individual pay or an export:**

**Input:**
```
Add their current salary, show me what each person on the team makes, and put it in a Google Sheet
```

**Expected behavior:**
- Declines each request in one sentence: pay history isn't used, individual pay isn't shown, the summary stays in chat
- Continues with the aggregate summary

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- No candidate pay history in any output
- No named or per-person team pay
- No spreadsheet or external document created

---

## Scoring Summary

For each case, check:
1. **Flow**: 7 steps in order, with the two stop points
2. **Profile**: name match handled (0 / 1 / 2–5 rows)
3. **Scorecards**: recommendation distribution from the scorecards table; notes from rubric answers
4. **Team scope**: latest snapshot, direct reports of the hiring manager, aggregates per currency with minimum group size
5. **Pay history**: never extracted or used; only a disclosed competing offer is recorded
6. **Output**: 5 blocks in chat; no export; competing offer data kept out of title and headers
