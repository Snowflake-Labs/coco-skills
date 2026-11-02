# Golden Test Cases: good_morning_chro

5 test cases covering the full briefing, an org-scoped briefing, Attrition Watch, Hiring Velocity and Hot Spots. People Hydrator runs them at the end of Tune.

Numbers are rules checked against your own data, not fixed values. A case whose data isn't connected, or that needs more history than you have, is recorded as not tested, not failed.

---

## Case 1: Full Company-Wide Briefing

Headline case: the number shown here is the one the preview asks you to confirm ("Does this match what you report?").

The attrition rate needs 12 months of history for T12M; with less, the briefing uses the available period and says so (record that part as not tested).

**Input:**
```
Good morning CHRO
```

**Expected behavior:**
- Company-wide scope with no confirmation prompt
- Runs all 4 sections: Attrition Watch, Hiring Velocity, Engagement Signals, Hot Spots
- Attrition Watch: new exits in the last 24 hours, T12M voluntary and total attrition vs the company benchmark, hot orgs
- Hiring Velocity: offers extended / accepted / declined this week, open reqs, filled reqs (30d), stale reqs (> 60d)
- Engagement Signals: skipped silently if no pulse / engagement data is connected
- Hot Spots: priority-ranked watch list
- Headline is "Workforce steady" or "X hot spots need attention"; briefing under 500 words

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- T12M voluntary attrition = voluntary exits in the last 12 months ÷ ((HC at period start + HC at period end) ÷ 2) × 100, both dates stated, no annualization
- The company benchmark is the same formula computed company-wide
- New exits (24h) count matches exits with an exit date in the last 24 hours; the window end is stated
- If more than 5 exits in 24h, they are shown as counts by org, not names
- Headline count equals the number of hot spots listed

---

## Case 2: Org-Scoped Briefing

**Input:**
```
Good morning — just Engineering
```
(Use one of your own org names if Engineering doesn't exist.)

**Expected behavior:**
- Detects the org from the request and scopes all sections to it, without a confirmation prompt
- Shows the org's attrition rate next to the company-wide benchmark
- Only that org's data appears in the scoped sections

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Org exits (24h and 30d) are a subset of the company-wide exits for the same windows
- The benchmark equals the company-wide rate from Case 1 for the same as-of date
- Org rate uses the org's own (start HC + end HC) ÷ 2 as the denominator

---

## Case 3: Attrition Watch

Needs 12 months of headcount and exit history for the T12M rate; with less, record as not tested.

**Input:**
```
How is our attrition looking?
```

**Expected behavior:**
- Answers with the Attrition Watch content: new exits (24h), regrettable count, T12M voluntary and total attrition vs benchmark, hot orgs
- Uses the regrettable flag if present; otherwise uses the proxy and notes "regrettable classification is estimated"
- If voluntary or regrettable flags are masked for the role, says so instead of reporting zero

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Rate = T12M exits ÷ ((start HC + end HC) ÷ 2) × 100, consistent with Case 1
- Regrettable exits ≤ total new exits
- Hot orgs listed have a rate > 2× the company rate
- No performance rating is shown to justify a regrettable classification

---

## Case 4: Hiring Velocity — Offers This Week

Needs an offers table (and a requisitions table for the req lines). If that isn't connected, record as not tested.

**Input:**
```
How many offers went out this week?
```

**Expected behavior:**
- Answers with the Hiring Velocity content: offers extended, accepted and declined this week; open reqs; filled reqs (30d); stale reqs (> 60d)
- A zero count is reported plainly, not as an error
- If no offers / pipeline data is connected, says "connect your ATS to enable this section"

**Guard checks:**
- [ ] G1 passes
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Offers extended = offers with an extended date in the current week; the week start is stated
- Accepted + declined ≤ extended for offers extended in the same window (or the difference is explained)
- Stale reqs = open reqs with a created date more than 60 days ago; omitted with a note if no created date is connected

---

## Case 5: Hot Spot Detection

Needs 12 months of headcount and exit history for the T12M rate; with less, record as not tested.

**Input:**
```
Which orgs need attention?
```

**Expected behavior:**
- Computes per-org T12M attrition and compares to 2× the company rate
- Adds stale-req and pulse flags where that data is connected
- Ranks hot spots by number of flags triggered, most first
- Shows org name, rate, company benchmark and flags triggered
- Says "No hot spots today — all orgs within healthy thresholds." when none qualify
- Does not compare orgs' pulse scores by name in a way that shames a leader

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Per-org rate = org voluntary exits in the last 12 months ÷ ((org start HC + org end HC) ÷ 2) × 100
- Every listed org meets at least one hot-spot rule: rate > 2× company rate, pulse < 70 (or bottom quartile), or > 30% of open reqs stale > 60 days
- Ranking order matches the flag counts shown
- If voluntary flags are masked, the skill says the voluntary rate isn't available rather than showing 0%

---

## Scoring Summary

For each case, check:
1. **Scope**: company-wide by default; org-scoped when an org is named; no confirmation prompt
2. **Data**: exits table for exits; active employees for the denominator; offers / reqs for Hiring Velocity
3. **Windows**: rolling 24h, 7d, 30d; the window end is stated
4. **Rate**: T12M exits ÷ ((start HC + end HC) ÷ 2) × 100, no annualization
5. **Hot spots**: > 2× company rate, pulse < 70, > 30% stale reqs; ranked by flag count
6. **Guardrails**: no wall of names, no performance ratings, missing sections skipped as the skill specifies
