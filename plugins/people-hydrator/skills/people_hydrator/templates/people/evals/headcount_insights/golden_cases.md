# Golden Test Cases: headcount_insights

4 test cases covering ending HC, movement drivers, org shape and an org-scoped view. People Hydrator runs them at the end of Tune.
Numbers are rules checked against your own data, not fixed values. A case whose data isn't connected, or that needs more history than you have, is recorded as not tested, not failed.

---

## Case 1: Ending Headcount — Company-Wide Summary

Headline case: the number shown here is the one the preview asks you to confirm ("Does this match what you report?").

**Input:**
```
What's our current headcount?
```

**Expected behavior:**
- Routes to Cat 1 — Ending Headcount
- Detects HR / People Analytics audience profile → company-wide scope
- Uses the latest available snapshot date
- Applies the active status filter and your included employee types
- Displays ending HC with QoQ change, and YoY change when 13+ monthly snapshots exist (otherwise says YoY isn't available yet)

**Guard checks:**
- [ ] G1: Snapshot used with active status + employee type filters
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Ending HC = count of active employees of the included types on the latest snapshot date
- Output is dated "as of [latest snapshot date]"
- QoQ uses the prior quarter-end snapshot per your fiscal calendar (or the nearest available snapshot, stated)
- Status indicator ✅ / 🟡 / 🔴 on the result row

---

## Case 2: Movement Drivers — Net Change Decomposition

**Input:**
```
What's driving the net headcount change this fiscal quarter?
```

**Expected behavior:**
- Routes to Cat 6 — Movement Drivers
- Resolves the current fiscal quarter from your fiscal calendar (or the fiscal year start set during hydration)
- Starting HC from the quarter-start snapshot; hires from the hires table; exits from the exits table
- Presents: Starting HC + Hires - Exits = Ending HC, and Net Change
- If a termination category is available, splits exits into voluntary vs involuntary; otherwise says the split isn't available

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Hires counted by hire date within the quarter
- Exits counted on the day after the termination date, within the quarter
- Ending HC - Starting HC equals Hires - Exits (within rounding; any gap explained, e.g. transfers or late-entered records)
- The included employee types are applied to hires and exits as well as the snapshot

---

## Case 3: Org Shape — Span of Control

Needs a direct-report count (or manager ID) on the snapshot. If that isn't connected, record as not tested.

**Input:**
```
Show me the org shape and span of control
```

**Expected behavior:**
- Routes to Cat 7 — Org Shape & Span
- Identifies managers as employees with at least one direct report (not by job title or level)
- Presents: total HC, manager count, manager %, average span of control
- Span buckets: 0, 1–2, 3–9, 10+ direct reports
- Flags over-spanned (>10 → 🔴) and under-spanned (<3 → 🟡) managers
- Uses the latest snapshot date

**Guard checks:**
- [ ] G1 passes
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Manager count = employees with direct reports > 0
- Average span = total direct reports ÷ manager count
- Buckets add up to the manager count

---

## Case 4: Org-Scoped View

**Input:**
```
Show me headcount for the Engineering org
```
(Use one of your own org names if Engineering doesn't exist.)

**Expected behavior:**
- Routes to Cat 3 — Headcount by Organization, scoped to that org
- Maps employees to orgs through the cost center → org mapping
- Shows ending HC for the org and its sub-orgs where the mapping provides them
- Offers to drill down: "Want to see by region, management level, or cost center?"
- If the org name doesn't exist, lists the closest org names instead of guessing

**Guard checks:**
- [ ] G1 passes; P4 and P5 pass (org mapping connected and joined)
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Org HC = active included-type employees on the latest snapshot whose cost center maps to that org
- Org totals across all orgs add up to company HC (any unmapped cost centers shown as their own line)

---

## Scoring Summary

For each case, check:
1. **Routing**: correct category
2. **Data**: snapshot for point-in-time counts; hires and exits tables for movement
3. **Filters**: active status, your included employee types, date windows
4. **Org mapping**: cost center → org lookup with the decommissioned-cost-center fallback
5. **Movement math**: starting HC + hires - exits = ending HC
6. **Org shape**: direct reports > 0 identifies managers
