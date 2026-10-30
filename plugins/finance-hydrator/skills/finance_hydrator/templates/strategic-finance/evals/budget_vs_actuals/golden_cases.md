# Golden Test Cases: budget_vs_actuals

7 capability test cases + 3 pitfall regression cases.

---

## Capability 1 — Monthly close variance

### Case BVA-01: Default monthly close

**Input:**
```
budget vs actuals for last month
```

**Expected behavior:**
- Defaults to most recent closed month
- Joins budget and actuals on line item ID
- Applies 5% + $50K materiality threshold
- Presents flagged lines with commentary
- Does NOT show full detail table unsolicited

**Guard checks:** G1–G5 all pass

**Expected output shape:**
- `## Summary` — total metric with absolute and % variance
- `## Flagged Variances` table with FAV/UNFAV column
- Commentary block under flagged table
- Footer with period and threshold

---

### Case BVA-02: Specific cost center scoped

**Input:**
```
show me variance for the engineering cost center vs budget
```

**Expected behavior:**
- Filters to engineering cost center only
- Same flagging and commentary flow
- Footer shows scope: "Scope: Engineering cost center"

---

## Capability 2 — Favorable/unfavorable direction

### Case BVA-03: Mixed expense and revenue lines

**Setup:** Budget table contains both revenue lines and expense lines.

**Input:**
```
full P&L variance vs budget
```

**Expected behavior:**
- Revenue lines: actual > budget = FAV
- Expense lines: actual < budget = FAV
- Direction derived from line_type column (not assumed)

**Expected output:**
- Revenue lines with actual > budget flagged as FAV
- Expense lines with actual < budget flagged as FAV
- Exact same amount could be FAV on an expense line and UNFAV on a revenue line

---

## Capability 3 — Commentary enrichment

### Case BVA-04: Commentary with context data available

**Setup:** Notes table present with GL notes per line item.

**Input:**
```
budget vs actuals March
```

**Expected behavior:**
- Commentary for each flagged line draws from GL notes
- Commentary is 2–3 sentences combining variance data with the note

**Expected output shape:**
- Commentary text references the specific reason from the notes table (e.g., "Travel expense was $XXK unfavorable primarily due to Q1 sales kickoff event in early March.")

---

### Case BVA-05: Commentary without context data

**Setup:** No notes table available.

**Input:**
```
show me variance vs plan
```

**Expected behavior:**
- Commentary block says "Context data unavailable — manual review required" for each flagged line
- Does NOT attempt to invent a reason for the variance

---

## Capability 4 — Full detail on request

### Case BVA-06: Full detail table requested

**Input (after initial flagged output):**
```
show me all lines, not just the flagged ones
```

**Expected behavior:**
- Produces the full detail table with ALL line items
- Table sorted by |variance $| descending
- Previously shown flagged lines still visible with flags

---

## Capability 5 — Critical binding missing

### Case BVA-07: Budget table missing

**Input:**
```
budget vs actuals Q2
```

**Setup:** No budget table resolved — only a versioned forecast table found.

**Expected behavior:**
- Does NOT use the forecast as a budget
- Outputs: "Budget vs Actuals requires a table with fixed budget values per line item. Add a binding under finance.budget.plan_actuals in hydration_hints.yaml and re-verify."
- Optionally: "If you're looking to compare two forecast versions, use forecast_vs_forecast."

---

## Pitfall Regression Cases

### Case P-01: Forecast not mistaken for budget

**Setup:** Snowflake account has both a versioned forecast table AND a budget table.

**Input:**
```
budget vs actuals
```

**Check:** Skill binds to the fixed budget table (no version_id column), NOT the forecast snapshot table (has version_id column). If both are found, skill asks for clarification.

---

### Case P-02: Zero-variance month handled

**Input:**
```
budget vs actuals
```

**Setup:** All line items are within 2% of budget this month.

**Check:** Output says "No lines exceeded the materiality threshold for <period>. All items are tracking within 2% of budget." — does NOT produce an empty flagged table or raise an error.

---

### Case P-03: FAV/UNFAV not hardcoded for expense

**Input:**
```
budget vs actuals
```

**Setup:** P&L table has both "Revenue" and "Cost of Sales" lines.

**Check:** "Revenue" lines use actual > budget = FAV logic. "Cost of Sales" uses actual < budget = FAV logic. The distinction comes from the line_type column, not from hardcoded line names.

---

## Running

```
/skill_eval — point it at this file for golden case assertions.
```
