# Golden Test Cases: offer_builder

3 test cases covering the standard justification sheet, name disambiguation and the no-scorecards gate. People Hydrator runs them at the end of Tune.

Replace placeholders ([candidate], [requisition]) with values from your own data that fit each case. Results are rules checked against your own data, not fixed values. A case whose data isn't connected is recorded as not tested, not failed.

---

## Case 1: Standard Offer Justification Sheet

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
- Step 4: hiring manager for the requisition, then team members and their hire-time comp
- Step 5: inline team comparison table and 3–5 sentence narrative
- Step 6: parsed comp from recruiter notes, then waits for confirmation
- Step 7: builds the 5-block sheet (or an inline table if sheet creation fails) and confirms the URL

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns
- [ ] No individual candidate data beyond what the user asked about

**Key assertions:**
- Recommendation counts add up to the number of scorecards on the application
- Team = employees on the latest snapshot whose manager is the requisition's hiring manager
- Table columns: Name | Type | YOE | Prior Company | Sales Background | Hire OTE | Currency | OTE Compa-Ratio | Equity Compa-Ratio | Notable Signals
- Candidate row first ("External"), team rows ("Internal") by hire date ascending
- Each team row keeps its own currency; missing comp shows "—"
- Competitive comp holds only what the notes disclose, or "Current comp not disclosed in scorecard."
- Sheet title is "Comp Justification — [candidate] | [role] | [requisition]"; no competing offer data in title, tab or headers
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
- Treats this as a critical gap: surfaces it and stops; no sheet is built

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns
- [ ] No individual candidate data beyond what the user asked about

**Key assertions:**
- The application has zero scorecards in your data
- No sheet and no invented scorecard data
- Message contains no table names or SQL errors

---

## Scoring Summary

For each case, check:
1. **Flow**: 7 steps in order, with the three stop points
2. **Profile**: name match handled (0 / 1 / 2–5 rows)
3. **Scorecards**: recommendation distribution from the scorecards table; notes from rubric answers
4. **Team scope**: latest snapshot, direct reports of the hiring manager, currency per row
5. **Comp parsing**: voluntarily disclosed only
6. **Sheet**: 5 blocks in order; competing offer data kept out of title and headers
