# Golden Test Cases: good_morning_recruiting

4 test cases covering the full briefing, HM sentiment, stale candidates and offer declines. People Hydrator runs them at the end of Tune.

Numbers are rules checked against your own data, not fixed values. A case whose data isn't connected, or that needs more history than you have, is recorded as not tested, not failed.

---

## Case 1: Full TA Briefing (All 8 Metrics)

Headline case: the number shown here is the one the preview asks you to confirm ("Does this match what you report?").

**Input:**
```
Run the TA metrics briefing
```

**Expected behavior:**
- No `--metric` given, so all 8 metrics run in order
- Timeframe defaults to the current fiscal quarter from your fiscal calendar
- Data gathering and validation are silent; only formatted results are shown
- Each metric heading has a 🟢 / 🟡 / 🔴 health indicator, with 3 blank lines after each section
- Competitor intel uses live web search; every item has a source link
- Ends by asking "Would you like me to generate a PDF report?"

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Sourced % per recruiter = sourced applications ÷ filled / closed applications × 100
- Req load totals across recruiters plus unassigned reqs equal the company open-req total
- Offer declines cover the last 14 calendar days only
- No SQL, table names, column names or JSON appear in chat
- A metric whose data isn't connected says the data is unavailable instead of showing numbers

---

## Case 2: Single Metric — Hiring Manager Sentiment

**Input:**
```
Show me hiring manager sentiment scores
```

**Expected behavior:**
- Runs only the HM sentiment metric
- Uses the current fiscal quarter unless another timeframe is given
- Per recruiter: average score, survey count, % of hiring managers surveyed
- Flags recruiters whose average is below your configured threshold, on your survey's own score scale
- Recruiters with fewer than 3 responses are marked "(limited data)"
- If no recruiter is below threshold, says all clear

**Guard checks:**
- [ ] G1 passes; P1 passes
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Recruiter average = mean sentiment score of that recruiter's responses in the period
- Flagged recruiters are exactly those with average below the configured threshold
- When a response credits several recruiters, each is counted separately (not as one combined name)

---

## Case 3: Stale Candidates — Threshold Check

**Input:**
```
Show me stale candidates
```

**Expected behavior:**
- Runs only the stale candidates metric
- Two tiers, both configurable: Violation (🔴, days in stage above the violation threshold) and Approaching (🟡, above the warning threshold up to the violation threshold)
- Only candidates in an active stage that has actually started
- Shows candidate, stage, days in stage and recruiter

**Guard checks:**
- [ ] G1 passes; P4 passes
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Days in stage = today − stage start date; never negative
- No candidate with a missing or future stage start date is counted
- Violation and approaching lists don't overlap; counts are ≥ 0

---

## Case 4: Offer Declines (Last 14 Days)

**Input:**
```
What offer declines happened in the last 2 weeks?
```

**Expected behavior:**
- Runs only the offer declines metric
- Window: declined date ≥ today − 14 calendar days (not fiscal)
- Shows the count of declines plus detail: candidate, job title, location, recruiter, decline reason, date
- Does not show offer acceptance rate or per-recruiter offer counts

Candidate, job title, location and recruiter on offers are not declared in the catalog. If they aren't connected, check the count and date assertions only.

**Guard checks:**
- [ ] G1 passes; P6 passes
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Decline count = offers with declined status and declined date within the last 14 calendar days
- Count equals the number of detail rows
- No acceptance rate appears in this metric's output

---

## Scoring Summary

For each case, check:
1. **Routing**: all 8 metrics by default; one metric when asked
2. **Data**: the right TA table per metric; web search only for competitor intel
3. **Filters**: fiscal-quarter default, 14 calendar days for declines, 30 calendar days for competitor intel
4. **Thresholds**: configurable values, applied on your own scales
5. **Silence**: no internal names, SQL, JSON or agent labels in chat
