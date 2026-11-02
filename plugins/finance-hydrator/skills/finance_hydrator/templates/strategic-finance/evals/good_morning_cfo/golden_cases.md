# Golden Test Cases: good_morning_cfo

5 capability test cases + 3 pitfall regression cases.

---

## Capability 1 — Default daily briefing

### Case D-01: Bare "good morning"

**Input:**
```
good morning
```

**Expected behavior:**
- Confirms scope before running (since the prompt is bare)
- After confirmation, runs all 4 sections in parallel
- Produces 1-page briefing under 600 words

**Guard checks:**
- [ ] G1: Hydration succeeded
- [ ] G2: forecast_daily resolved
- [ ] G3: Time filter applied
- [ ] G4: Currency shown
- [ ] G5: No leaked internal terms

**Expected output shape:**
- Headline starting with "**<Yesterday | This Week | This Month> — <Beat | Missed> forecast by <X.X%>**"
- 4 section headers: `## Revenue Performance`, `## Top Movers`, `## Consumption Pulse` (or skip notice), `## External Signals`
- Hydration footer line at end

---

### Case D-02: Scoped daily briefing

**Input:**
```
good morning - just enterprise segment
```

**Expected behavior:**
- Skips the confirmation step (scope is given)
- Filters all sections by segment = enterprise
- Headline reflects enterprise-only numbers

**Guard checks:** all G1-G5 pass

**Expected output shape:**
- Same 4 sections, but every metric scoped to enterprise
- Top movers list shows only enterprise accounts

---

## Capability 2 — Top movers analysis

### Case M-01: Top 5 positive and negative movers

**Input:**
```
who's driving variance this week?
```

**Expected SQL pattern:**
```sql
SELECT account_id, account_name,
       (forecast_amount - prior_forecast) AS forecast_change,
       delta_pct
FROM <RESOLVED account_forecast FQN>
WHERE week = (SELECT MAX(week) FROM <FQN>)
ORDER BY forecast_change DESC
LIMIT 5;
-- and the same with ORDER BY forecast_change ASC
```

**Guard checks:**
- [ ] G1: Hydration succeeded
- [ ] G2: account_forecast resolved
- [ ] Two separate queries (one for top, one for bottom)

**Expected output:**
- Two ranked lists of 5 accounts each
- Each entry includes account name and amount
- Top 3 in each list match the original skill output

---

## Capability 3 — Consumption pulse

### Case C-01: With consumption data

**Input:**
```
good morning
```

**Setup:** Customer account has finance.consumption.daily_usage resolved.

**Expected:**
- Consumption Pulse section appears with WoW growth, top growing, top declining
- No "data unavailable" message

---

### Case C-02: Without consumption data (degraded mode)

**Input:**
```
good morning
```

**Setup:** Customer account does NOT have a consumption table (no Source B match, no INFORMATION_SCHEMA match for `finance.consumption.daily_usage`).

**Expected behavior:**
- Skill detects LOW confidence on the (non-critical) consumption binding
- Skips Consumption Pulse section
- Output still includes the 3 other sections
- Briefing notes "Consumption Pulse: data unavailable for your account" near the section header

---

## Capability 4 — External signals

### Case E-01: Market signals

**Input:**
```
good morning
```

**Expected:**
- External Signals section contains 2-5 bullets
- Each bullet has a source URL (web_search result)
- All sources have publish dates within the last 24 hours
- No bullet is older than 24h

---

## Capability 5 — Critical binding missing

### Case F-01: forecast_daily not resolvable

**Input:**
```
good morning
```

**Setup:** Customer account has no forecast_daily table or semantic view (LOW on critical binding).

**Expected behavior:**
- Skill does NOT produce a briefing
- Outputs the LOW-on-critical message:
  ```
  We can't run the daily briefing without a revenue forecast binding.
  Add an entry to hydration_hints.yaml under finance.revenue.forecast_daily
  and re-run the hydrator's verification step.
  ```
- Suggests the exact YAML stanza to add

---

## Pitfall Regression Cases

### Case P-01: No assumed fiscal calendar

**Input:**
```
how are we pacing this quarter?
```

**Setup:** Plugin config has `fiscal_calendar: calendar` (calendar year, not a Feb-start fiscal year).

**Check:** SQL must use calendar-year quarter boundaries (Q1 = Jan-Mar) NOT Feb-start. Output must NOT contain "Q1FY27 (Feb-Apr)" or any Snowflake-specific fiscal label.

---

### Case P-02: No leaked internal FQNs

**Input:**
```
good morning
```

**Check:** Output contains ZERO mentions of internal schema names (e.g., any schema from the plugin template), internal system names, or named internal individuals. Competitor names surfaced by web_search are acceptable when legitimate market signals.

---

### Case P-03: Currency not assumed USD

**Input:**
```
good morning
```

**Setup:** Customer's forecast_daily table has currency='EUR' on all rows.

**Check:** Output displays values with EUR (or `EUR ` prefix or "EUR" label), not bare USD-style "$" sign. No "$" character in the briefing body.

---

## Running

```
/skill_eval — point it at this file for golden case assertions.
```
