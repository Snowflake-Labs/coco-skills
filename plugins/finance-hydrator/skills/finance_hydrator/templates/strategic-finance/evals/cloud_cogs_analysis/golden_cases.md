# Golden Test Cases: cloud_cogs_analysis

6 capability test cases + 3 pitfall regression cases.

---

## Capability 1 — Default quarterly COGS summary

### Case C-01: Quarterly COGS breakdown

**Input:**
```
show me our COGS for last quarter
```

**Expected behavior:**
- Detects most recent completed quarter as the period
- Defaults to QoQ comparison
- Fetches gross margin summary, COGS by whatever dimension the data supports
- Runs Q1–Q3 in parallel, then Q4 (and Q5/Q6 when their data is bound)

**Guard checks:** G1–G5 all pass

**Expected output shape:**
- `## Gross Margin Summary` table with Revenue, COGS, Gross Profit, Gross Margin %
- Prior period column present
- Change column in `+/-X.X%` for revenue/COGS, `+/-X.Xpp` for margin %
- `## COGS by <Dimension>` table present
- `## Unit Economics` section (or "unit cost data not found" note)
- `## Cost Attribution` list of top drivers

---

### Case C-02: YoY comparison

**Input:**
```
how is gross margin trending year over year?
```

**Expected behavior:**
- Detects comparison = YoY (not QoQ)
- Fetches same quarter prior year as the comparison baseline
- Labels comparison direction explicitly: "vs Q3 FY24 (YoY)"

**Expected output shape:**
- All tables show current period vs prior year same period
- Comparison labeled "YoY" in footer

---

## Capability 2 — Dimension breakdown

### Case C-03: By cloud provider

**Input:**
```
break down cloud costs by AWS, Azure, GCP
```

**Expected behavior:**
- Detects user wants breakdown by cloud provider
- Resolves COGS table's provider column
- Q2 returns provider-level breakdown

**Expected output:**
- `## COGS by Provider` table with AWS, Azure, GCP rows (or whatever providers exist)
- % of total COGS per provider
- Period-over-period change per provider

---

### Case C-04: By product line

**Input:**
```
what's our cost breakdown by product?
```

**Expected behavior:**
- Detects user wants product-level breakdown
- If product dimension not in COGS table, note limitation and present whatever dimension is available

---

## Capability 3 — Unit economics

### Case C-05: Cost per consumption unit present

**Setup:** Customer's account has a unit cost table with cost-per-consumption-unit data.

**Input:**
```
what's our cost per unit trend?
```

**Expected behavior:**
- Detects specific unit type request
- Fetches cost per consumption unit for current and prior period
- Presents trend commentary

**Expected output:**
- `## Unit Economics` table with a Cost per consumption unit row
- Trend direction called out in commentary

---

## Capability 3b — Commitments, forecast, and partial periods

### Case C-07: Net vs list cost and commitment

**Setup:** Customer has cloud cost detail with list and net cost, and a commitment table.

**Input:**
```
what is our effective cloud discount and how much of our cloud commitments have we consumed?
```

**Expected behavior:**
- Computes blended discount as discount $ / list cost $ (cost-weighted)
- Reports committed amount, consumed to date, and contract term by provider

**Expected output:**
- `## Commitment & Discount` table
- Cost basis stated in the header

---

### Case C-08: Actual vs forecast

**Setup:** Customer has a published COGS forecast with a version column.

**Input:**
```
how is COGS tracking against the latest forecast?
```

**Expected behavior:**
- Resolves the latest forecast version and labels it
- Compares actuals to forecast for the same period only

**Expected output:**
- `## Actual vs Forecast` table with the version label

---

### Case C-09: Partial period

**Input:**
```
how is COGS looking so far this month vs last month?
```

**Expected behavior:**
- Labels all figures "through [date]"
- States that comparing a partial month with a full month makes cost look favorable
- Paces to month-end if daily actuals exist; otherwise says flat pacing was used

---

## Capability 4 — Critical binding missing

### Case C-06: No COGS table found

**Input:**
```
show me COGS for Q3
```

**Setup:** No table matching the COGS summary Context Query can be resolved.

**Expected behavior:**
- Blocks (does NOT produce hallucinated COGS figures)
- Outputs: "COGS analysis requires a table with periodic cost amounts. Add a binding under finance.cogs.summary in hydration_hints.yaml and re-verify."

---

## Pitfall Regression Cases

### Case P-01: No opex items in COGS

**Input:**
```
show me our cost breakdown
```

**Check:** Output contains zero mentions of S&M, R&D, G&A, headcount cost, or compensation as COGS line items. COGS = cost of delivering the product only.

---

### Case P-02: Gross margin % defined correctly

**Input:**
```
COGS summary last quarter
```

**Check:** Gross Margin % = (Revenue - COGS) / Revenue — NOT Revenue / COGS or any other formula.

---

### Case P-03: Unit types from data, not hardcoded

**Input:**
```
show me unit economics
```

**Check:** Output lists only unit types that exist in the resolved data (e.g., consumption units, TB). Does NOT assume any unit if those columns don't exist.

---


### Case P-04: Bridge terms add up and signs are stated

**Input:**
```
what drove the change in COGS versus last quarter?
```

**Check:** Volume effect + cost-ratio effect = the COGS change exactly. The output states the sign convention (negative cost-ratio effect = margin improvement). It does not label a margin improvement with a positive "margin driver" next to a COGS increase.

---

### Case P-05: Margin excluding adjustments reported

**Setup:** Source has credits, reclasses or other manual adjustments that changed between periods.

**Input:**
```
how is gross margin trending?
```

**Check:** Output shows gross margin both net and excluding credits/adjustments. If they move in opposite directions, the commentary leads with that.

---

### Case P-06: Overlapping category families not double counted

**Setup:** Source has a parent category family and a drill-down family that does not nest, plus rows with a blank category.

**Input:**
```
break down COGS by category
```

**Check:** Only the family that adds up to the reported COGS total is summed; the tie-out to reported COGS and revenue totals is verified before presenting.

---

### Case P-07: Forecast version and actual cutoff resolved

**Setup:** Source holds several forecast versions with text-typed periods and amounts, and no actual/forecast flag.

**Input:**
```
show me COGS for last quarter
```

**Check:** Uses exactly one version, casts periods before choosing "latest", labels the inferred actual cutoff as inferred and confirms it, and states the latest period with data.

---

## Running

```
/skill_eval — point it at this file for golden case assertions.
```
