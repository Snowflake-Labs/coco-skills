# Golden Test Cases: attrition_insights

4 test cases covering HRBP mode, CPO mode, an org-scoped brief and the weekly timeframe. People Hydrator runs them at the end of Tune.

Numbers are rules checked against your own data, not fixed values. A case whose data isn't connected, or that needs more history than you have, is recorded as not tested, not failed.

---

## Case 1: Daily HRBP Brief — Company-Wide

Headline case: the number shown here is the one the preview asks you to confirm ("Does this match what you report?").

**Input:**
```
Show me today's attrition brief
```

**Expected behavior:**
- Uses HRBP mode (default) and daily timeframe (default)
- Greets: "Good Morning, [First Name] — here's the Attrition Brief for [Date]. Scope: Company-Wide" (for an HR / People profile)
- Snapshot: total, voluntary, involuntary and regrettable attrition rates, plus the month-over-month exit trend
- Watch Areas: scans org group, management level, tenure cohort and region; ranks the top 5 cohorts by composite risk with a one-line narrative each
- Drivers: top termination reasons and their shift vs the prior period
- Regrettable Deep Dive: by org group and management level
- Future Exits Pipeline: confirmed future exits by org, management level and regrettable flag
- Recommendations: top 3 actions and a stopping-point prompt

Needs 13 monthly snapshots for the full trend. If there's less history, check only the months available.

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Attrition rate = exits in the period ÷ ((HC at period start + HC at period end) ÷ 2) × 100, both snapshot dates stated
- Exits are counted on the day after the termination date
- Voluntary + involuntary exits do not exceed total exits; regrettable + non-regrettable equals total when both are shown
- Every metric states the analysis period and data freshness date
- Status indicator 🔴 / 🟡 / ✅ on every row

---

## Case 2: CPO Mode — Executive Attrition Summary

**Input:**
```
Give me a CPO-mode attrition brief for this week
```

**Expected behavior:**
- Uses CPO / HR Leadership mode, weekly timeframe, company-wide scope
- Key KPIs upfront: total, voluntary, involuntary and regrettable attrition rates
- Comparison across org groups vs the company average
- Trend chart in text form
- Board-ready talking points and strategic recommendations
- Includes the weekly additions (4-week trailing exit trend)

**Guard checks:**
- [ ] G1, G2 pass; P4 and P5 pass (org mapping connected and joined)
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Company KPIs use the same formula as Case 1 (exits ÷ average month-end HC × 100) and match Case 1 for the same period
- A KPI deviating from the prior period by more than the configured threshold is flagged as a material change
- Org group rates are computed per org with the same formula; cost centers not in the org mapping are excluded from org rows but kept in the company total
- If the regrettable flag is masked for the role, the regrettable KPI is omitted with a note (not shown as zero)

---

## Case 3: Org-Scoped Brief

**Input:**
```
Give me the attrition brief for the Engineering org only
```
(Use one of your own org names if Engineering doesn't exist.)

**Expected behavior:**
- HRBP mode with the org scope set to that org
- Maps employees to the org through the cost center → org mapping
- All waves scoped to that org's employees
- Watch cohorts within the org ranked by composite risk
- If the org name doesn't exist, asks which org to use instead of guessing

**Guard checks:**
- [ ] G1, G2 pass; P4 and P5 pass
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Org exits are a subset of company exits for the same period
- Denominator is the org's (start HC + end HC) ÷ 2, not ending headcount alone
- Exit date offset (+1 day) applied the same way as company-wide
- Regrettable attrition shown as both a count and a rate

---

## Case 4: Weekly Mode — Trailing 4-Week Trend

**Input:**
```
Show me the weekly attrition brief
```

**Expected behavior:**
- HRBP mode with the weekly timeframe
- Runs the standard waves plus Weekly Additions
- 4-week trailing exit trend table per watch-area cohort
- Flags sustained hotspots: orgs with elevated attrition for 3+ consecutive weeks
- Benchmark context uses only customer-configured benchmark rates; omitted if none are configured

Needs exit records dated at day level for at least 4 weeks. If that isn't connected, record as not tested.

**Guard checks:**
- [ ] G1, G2 pass
- [ ] No made-up numbers, tables or columns

**Key assertions:**
- Weekly exit counts use termination date + 1 day to assign each exit to a week
- The 4 weekly counts add up to total exits over the same 4-week window
- A sustained hotspot has at least 3 consecutive weekly data points above the threshold
- Future exits pipeline counts only exits whose termination date is after the as-of date

---

## Scoring Summary

For each case, check:
1. **Mode**: HRBP vs CPO vs executive scope chosen correctly
2. **Data**: monthly snapshot for rates; exits table for deep dive, future exits and weekly trend
3. **Exit logic**: termination date + 1 day everywhere
4. **Rate**: exits ÷ ((start HC + end HC) ÷ 2) × 100
5. **Org mapping**: cost center → org lookup; unmapped cost centers kept in totals
6. **Masking**: masked flags omitted with a note, never reported as zero
