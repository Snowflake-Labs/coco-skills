# Golden Test Cases: hiring_brief

3 test cases covering the top-level executive report, a leader one level below the top, and a missing-snapshot end date. People Hydrator runs them at the end of Tune.

Numbers are rules checked against your own data, not fixed values. A case whose data isn't connected, or that needs more history than you have, is recorded as not tested, not failed.

---

## Case 1: Top-Level Executive Weekly Hiring Update

Headline case: the number shown here is the one the preview asks you to confirm ("Does this match what you report?").

**Input:**
```
Generate the weekly hiring update for [top-level executive]
```
(Use the leader at the top of your management hierarchy, e.g. the CEO.)

**Expected behavior:**
- Step 0: greets, explains the report, shows a leader checklist from your management hierarchy, and suggests the most recent snapshot date as the end date
- Step 2: shows the KPI banner, org table and draft Progress / Problems / Plans narrative in one message, then asks "Does this look good, or would you like to edit the narrative before I generate the PDF?"
- KPI banner: 6 KPIs — Current HC, Open Positions, Future HC, Forecast, % to Forecast, % to Adjusted Plan
- Org table grouped by leaders one level below the top, with Current HC, Open Positions, YTD Starts, YTD Exits, Projected HC
- Executive drill-down tables for the configured orgs
- Step 3: one-page PDF named `[LeaderName]_Weekly_Hiring_Update_[M.D.YY].pdf` saved to the desktop
- Step 4: offers another report, carrying over the end date

Needs forecast data for the Forecast KPIs. If that isn't connected, the 3-KPI report with a note is the expected result.

**Guard checks:**
- [ ] G1, G2, G3 pass
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Projected HC = Current HC + Future Starts − Future Exits, for the banner and every org row
- Org table rows add up to the banner Current HC and Open Positions (any unmapped remainder shown)
- % to Forecast = Projected HC ÷ Forecast × 100, using the most recent forecast version
- YTD start = first day of the fiscal year containing the end date, per your fiscal calendar
- Report header states the leader and "Week of [date]"

---

## Case 2: Leader One Level Below the Top

**Input:**
```
Create the weekly hiring update for [leader one level below the top], as of [end date]
```

**Expected behavior:**
- Finds the leader in the management hierarchy
- If the leader's org maps to a cost center leader in the forecast data → 6-KPI report; otherwise 3-KPI report with "Forecast data is not mapped for this leader — showing Current HC, Open Positions, and Projected HC only."
- Org table grouped by leaders two levels below the top within that leader's org
- No executive drill-down tables
- Narrative bullets follow "**Short Bold Title:** detail" and reference data points from the report

**Guard checks:**
- [ ] G1, G2, G3 pass
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- The leader's Current HC is a subset of the company Current HC from Case 1 for the same end date
- Org rows add up to the leader's banner totals
- Projected HC formula identical to Case 1
- An unknown leader name gets "I couldn't find [leader name] in the org…" and the available list, not a report

---

## Case 3: End Date With No Snapshot

**Input:**
```
Generate the hiring update for [leader] as of [a date with no snapshot]
```

**Expected behavior:**
- Detects there is no headcount snapshot for the requested date
- Responds: "No headcount snapshot found for [date]. Here's the most recent available: [date]. Want to use that instead?"
- Proceeds only with the date the user confirms
- Does not interpolate or estimate values for the missing date

**Guard checks:**
- [ ] G1 passes
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- The suggested date is an actual snapshot date in the summary data, on or before the requested date
- KPIs come from the confirmed snapshot date only
- The report header shows the snapshot date actually used

---

## Scoring Summary

For each case, check:
1. **Leader level**: correct KPI count (6 vs 3) and next-level-down grouping
2. **KPI formula**: Projected HC = Current HC + Future Starts − Future Exits
3. **YTD**: start = first day of the fiscal year containing the end date
4. **Forecast**: most recent version, resolved from data
5. **Workflow**: narrative shown for approval before the PDF
6. **PDF**: one page, `[LeaderName]_Weekly_Hiring_Update_[M.D.YY].pdf`, saved to the desktop
