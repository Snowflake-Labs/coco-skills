# Golden Test Cases: job_description_builder

4 test cases covering the ATS-first path, JD library exact match, location validation and fresh generation. People Hydrator runs them at the end of Tune.

Replace placeholders ([requisition], [job title], [team], [location]) with values from your own data that fit each case. Results are rules checked against your own data, not fixed values. A case whose data isn't connected is recorded as not tested, not failed.

---

## Case 1: ATS-First Path

Headline case: the preview asks you to confirm this output looks right.

**Input:**
```
Create a JD for req [requisition]
```
(Pick a requisition with a valid published JD in your ATS.)

**Expected behavior:**
- Access preflight passes
- Finds the published ATS JD and uses it as the base
- Presents each section (overview, responsibilities, requirements, bonus, why join) to keep, edit or omit
- Adds grade-aligned YOE and any missing sections; doesn't replace rich ATS content
- Runs the bias check and Inclusivity Score; offers export options

**Guard checks:**
- [ ] G1 passes
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Tells the user the content source is the ATS published JD
- The JD library is not used when a valid ATS JD exists
- The ATS posting used has valid content status (or non-empty, well-formed content if no status exists)
- Default view is external: no Recruiter Intelligence, HM / recruiter names, Inclusivity Score or internal grade
- No em-dashes; no compensation, age or visa content

---

## Case 2: JD Library Exact Match

Needs a JD library with saved JDs. If that isn't connected, record as not tested.

**Input:**
```
JD for [requisition], English
```
(Pick a requisition with no valid ATS JD and a saved English JD in the library.)

**Expected behavior:**
- Finds no valid ATS JD, then checks the JD library by req ID + language
- Presents the saved JD with options: use as-is, edit or regenerate

**Guard checks:**
- [ ] G1 passes
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Tells the user the content source is the JD library (exact match)
- The JD shown matches the library entry for that req ID and language
- ATS was checked before the library

---

## Case 3: Invalid Location

**Input:**
```
Create a JD for [job title], [location]
```
(Pick a location that isn't on your approved posting list or is internal-only.)

**Expected behavior:**
- Validates the location against the approved posting locations
- Says the location isn't available for job postings
- Lists up to 5 valid alternatives from the same country / region
- Waits for the user to choose before generating

**Guard checks:**
- [ ] G1 passes
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- The flagged location is not on your approved list (or matches the internal-only prefix)
- Every alternative shown is on the approved list and in the same country / region
- No silent substitution

---

## Case 4: Fresh Generation

**Input:**
```
Write a JD for a new Senior [job title] role on the [team] team
```

**Expected behavior:**
- No req ID, no ATS JD and no library match, so generates fresh from the template
- "AS A [JOB TITLE], YOU WILL:" and "OUR IDEAL [JOB TITLE] WILL HAVE:" with 5–8 bullets each
- Asks how the team differs, rather than writing the "Why join" section itself
- Never invents a req ID

**Guard checks:**
- [ ] G1 passes
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Tells the user the content is freshly generated
- Senior → "5+ years", a floor only, never a range
- Degree requirements include "or equivalent experience"
- Bullets lead with what the role decides or influences, not tool names
- No em-dashes; no compensation, age or visa content

---

## Scoring Summary

For each case, check:
1. **Priority order**: ATS → library exact → library similar → fresh, no skipping
2. **Transparency**: content source always stated
3. **Location validation**: approved list / internal-only check; no silent substitution
4. **Grade-to-YOE**: floor only, per the mapping
5. **Bias check**: no compensation, age, visa or protected-characteristic content
6. **Views**: external by default
