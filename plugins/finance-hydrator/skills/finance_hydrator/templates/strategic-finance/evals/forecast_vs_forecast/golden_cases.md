# Golden Test Cases: forecast_vs_forecast

7 capability test cases + 3 pitfall regression cases.

---

## Capability 1 — Default two-version comparison

### Case FVF-01: Default latest-vs-prior forecast

**Input:**
```
what changed in the forecast this month?
```

**Expected behavior:**
- Lists available forecast versions ordered by date (most recent first)
- Proposes: Version B = most recent, Version A = second most recent
- Waits for user confirmation (or auto-confirms if user provides explicit version names)
- Runs Q1–Q4 in parallel after confirmation

**Guard checks:** G1–G5 all pass

**Expected output shape:**
- `## High-Level Delta` table with Version A, Version B, Change $, Change %
- `## Movement by <Segment>` table
- `## Item-Level Movers (Top 30)` table with taxonomy column
- `## Forecast Bias` section (or "actuals not available for bias analysis" note)

---

### Case FVF-02: User-specified version names

**Input:**
```
compare Q3F1 to Q3F2
```

**Expected behavior:**
- Skips version selection prompt
- Uses exactly the two versions specified
- Derives analysis window from version metadata

---

## Capability 2 — Variance taxonomy

### Case FVF-03: Push-out classification

**Setup:** Item predicted for Period A in Version A, now predicted for Period B (later) in Version B.

**Input:**
```
what moved between last week's and this week's forecast?
```

**Expected classification:**
- That item classified as "Push-out (by N periods)" in the taxonomy column
- N computed as the number of periods it shifted

---

### Case FVF-04: New item classification

**Setup:** An item appears in Version B that was not present in Version A.

**Expected classification:**
- That item classified as "New in Version B"
- NOT classified as a push-in or amount change

---

## Capability 3 — Bias analysis

### Case FVF-05: Bias detected across rolling periods

**Setup:** Last 6 forecast versions available; actuals show consistent over-forecasting by 8%.

**Input:**
```
are we biased in our forecasting?
```

**Expected behavior:**
- Q4 bias analysis computes mean error across 6 periods
- Reports: "over-forecasting by ~8%" with High bias label (>5% threshold)
- Commentary notes this is actionable: "team may need to apply a systematic correction factor"

---

### Case FVF-06: Bias analysis skipped (no actuals)

**Setup:** Forecast snapshot table has no actuals column; no historical actuals table resolved.

**Expected behavior:**
- Q4 bias section says: "Actuals not available for bias analysis — Q4 skipped."
- Q1–Q3 still run and produce output

---

## Capability 4 — Single version case

### Case FVF-07: Only one forecast version found

**Input:**
```
forecast comparison
```

**Setup:** Forecast table has only one distinct version identifier.

**Expected behavior:**
- Blocks comparison (cannot compare one version to itself)
- Outputs: "Only one forecast version found. Need at least two versions for comparison."
- Suggests: "If you want to compare actuals to your forecast, try budget_vs_actuals."

---

## Pitfall Regression Cases

### Case P-01: Budget not mistaken for forecast snapshot

**Setup:** Snowflake account has a budget table (no version column) AND a forecast table (has version column).

**Input:**
```
compare forecasts
```

**Check:** Skill uses the versioned forecast table, NOT the fixed budget table. The budget table has no version/snapshot identifier.

---

### Case P-02: Favorable direction depends on metric type

**Setup:** Comparison shows:
- Revenue forecast increased by $5M in Version B
- Expense forecast increased by $5M in Version B

**Check:**
- Revenue increase = labeled FAVORABLE
- Expense increase = labeled UNFAVORABLE
- Direction is NOT the same for both — depends on metric type.

---

### Case P-03: Display names used for items

**Input:**
```
forecast comparison
```

**Setup:** Item dimension table available mapping item_id to account_name.

**Check:** Item-Level Movers table shows account names (e.g., "Acme Corp"), NOT raw item IDs (e.g., "00Q1234567890"). If dimension table absent, opaque IDs are acceptable.

---


## Running

```
/skill_eval — point it at this file for golden case assertions.
```
