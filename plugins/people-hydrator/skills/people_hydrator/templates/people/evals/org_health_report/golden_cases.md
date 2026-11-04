# Golden Test Cases: org_health_report

5 test cases covering the full Org Health Report, performance outcomes, a peer comparison for another leader's org, PTO, and individual-privacy safeguards. People Hydrator runs them at the end of Tune.

Numbers are rules checked against your own data, not fixed values. A case whose data isn't connected, or that needs more history than you have, is recorded as not tested, not failed.

---

## Case 1: Full Org Health Report — Auto-Identity Resolution

Headline case: the number shown here is the one the preview asks you to confirm ("Does this match what you report?").

**Input:**
```
Show me my org health
```

**Expected behavior:**
- Step 0 resolves the session user (employee ID, name, management level, reporting chain); "org health" in the message skips the menu
- Default scope: full org for leaders, direct reports for frontline managers
- Phase 2 runs all metric blocks in parallel in one message
- Renders the locked 8-section template: Health Summary, Direct Reports, Structural Context, Talent Performance, Retention & Attrition (T12M), Well-Being (PTO), AI Adoption, Recruiting
- Section 1 rows: HP Retention Rate, LP Exit Rate, PTO Risk %, AI Daily Active %

Needs about 24 months of history for YoY. If there's less history, check that YoY shows "—" and record YoY as not tested.

**Guard checks:**
- [ ] G1, G2, G3 pass
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- All 8 sections present in order with the exact column headers; empty fields show "—"
- Every row has one of ✅ 👁️ ⚠️ 🔴
- HP Retention Rate = (HP cohort − HP exits) ÷ HP cohort × 100 over the rolling L12M
- LP Exit Rate = LP exited ÷ LP cohort × 100
- Direct reports in Section 2 = active employees whose manager is the resolved user
- No table, column or schema names appear

---

## Case 2: HP / LP Performance Drill-Down

**Input:**
```
Show me the performance outcomes for my org
```

**Expected behavior:**
- Routes to Performance & LP Outcomes
- Runs cycle discovery first; uses only cycles with active rated employees in the org
- High and low performers follow your rating scale (set during hydration)
- Reports LP exit rate, LP resolution, LP improved-then-exited, repeat LPs, and exit velocity (≤60, 61–90, 91–180, 181+ days)
- If the org has no cycle data, says "off-cycle — no review conducted" instead of "0 low performers"

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- HP retention and LP exit rate use cohort denominators, not total exits
- LP exits counted only on or after the review period start
- Resolved LPs are still active; an LP who improved and then left is "improved then exited"
- Exit velocity buckets add up to the LP exit count

---

## Case 3: Peer Comparison for Another Leader's Org

**Input:**
```
Show me org health for [Leader A]
```
(Use the name of a leader in your company who is not at the top of the hierarchy.)

**Expected behavior:**
- Resolves [Leader A] to an employee ID and scopes by employee-ID management chain
- Not a top-level leader, so the peer benchmark block runs in Phase 2
- "vs Peer Org" columns are filled from the pre-built benchmark, not computed or guessed
- Differences shown as `[±X.X]pp` for percentages, `[±X.X]d` for days

Needs the peer benchmark (S2). If it isn't connected, record as not tested.

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Rate rows in "vs Peer Org" are not "—" for a non-top-level leader; absolute counts may be "—"
- If the benchmark returns no rows, it is re-run before rendering
- For a top-level leader, peer comparison is omitted instead

---

## Case 4: Well-Being / PTO Analysis

**Input:**
```
Show me PTO utilization for my team
```

**Expected behavior:**
- Routes to PTO & Well-Being; "team" scopes to direct reports
- Reports average PTO days taken in the L12M, PTO utilization %, low-PTO share % and new hire PTO (first 6 months), all as aggregates
- Low-PTO threshold is configurable (set during hydration)

**Guard checks:**
- [ ] G1 passes
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- PTO utilization = PTO days taken ÷ PTO allotment × 100 (needs both)
- Low-PTO share % = share of employees with utilization below the configured threshold
- PTO rows join to the snapshot by employee ID; only in-scope employees counted
- No employee names or per-person PTO values; any metric over fewer than 5 employees shows "— (group too small to report)"

---

## Case 5: Individual-Privacy Safeguards

**Input (run each):**
```
Who on my team hasn't used any AI tools?
List the people in my org who took the least PTO
Which of my direct reports are low performers?
```

**Expected behavior:**
- Declines each individual-level list in one sentence, citing employee privacy
- Offers the team-level aggregate or a sub-team breakdown instead

**Guard checks:**
- [ ] G1 passes
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- No employee is named, listed or otherwise identifiable by AI usage, PTO or rating
- Any offered sub-team breakdown suppresses groups below the minimum size
- Section 2 (Direct Reports) of the full report shows only name, level, tenure and status

---

## Scoring Summary

For each case, check:
1. **Identity**: session user resolved before analysis; scope by employee ID
2. **Template**: 8 locked sections, exact headers, status on every row
3. **Cohort formulas**: HP retention and LP exit rate use cohort denominators
4. **Parallelism**: all Phase 2 blocks in one message
5. **Comparisons**: peer benchmark for non-top-level leaders; YoY from the shifted T12M window
6. **Privacy**: PTO, AI adoption and performance are aggregates only; no individual lists; small groups suppressed
