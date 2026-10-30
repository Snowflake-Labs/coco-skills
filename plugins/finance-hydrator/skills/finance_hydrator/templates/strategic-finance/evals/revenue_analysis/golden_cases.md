# Golden Test Cases: revenue_analysis

7 capability test cases + 3 pitfall regression cases.

---

## Capability 1 — Default revenue narrative

### Case REV-01: Total business, latest period

**Input:**
```
revenue analysis for the latest quarter
```

**Expected behavior:**
- Auto-detects the most recent period with actuals
- Runs the full workflow: topline, child breakdown (60% stop rule), cohort, concentration
- Produces a 300-600 word prose narrative

**Guard checks:** G1-G5 all pass

**Expected output shape:**
- `### TLDR` (2-3 sentences, written last)
- `### Topline Paragraph`
- `### Per-Child Paragraphs` ordered by revenue descending
- `### Key Takeaway` (3 bullets - the intentional exception to the prose rule)

---

### Case REV-02: Scoped to a product category

**Input:**
```
explain revenue for the Platform product line this quarter
```

**Expected behavior:**
- Scopes the narrative to the named category and its immediate children
- Every named child carries `($revenue, PoP%, +/-$X vs plan)` on first mention
- Footer/labels reflect the scoped subject

---

## Capability 2 — Child breakdown (60% stop rule)

### Case REV-03: Many children with long tail

**Setup:** Subject has 12 children contributing to period-over-period movement.

**Input:**
```
what drove revenue this quarter
```

**Expected behavior:**
- Lists children until cumulative contribution reaches 60% of total movement
- Remaining children summarized in one sentence
- A child excluded by the 60% rule but in the top 3 by revenue still receives detail (rules applied independently)

---

## Capability 3 — Concentration analysis

### Case REV-04: Highly concentrated subject

**Setup:** Concentration table shows top-10 share >= 50%.

**Input:**
```
revenue deep dive with concentration
```

**Expected behavior:**
- Verdict "highly concentrated"; names top 3 gainers and top 3 contractors inline
- Computes N-for-80% and classifies customer-driven (N<=3) / moderate (4-15) / broad-based (>15)

**Expected output:**
- Customer names embedded within their relevant paragraph, not a separate section

---

### Case REV-05: Broad-based subject

**Setup:** Concentration table shows top-10 share < 35%.

**Input:**
```
revenue narrative for the quarter
```

**Expected behavior:**
- States "broad-based" verdict only
- Does NOT name individual customers

---

## Capability 4 — Optional source unavailable

### Case REV-06: No lifecycle cohort table

**Setup:** No lifecycle_cohort source resolved.

**Input:**
```
revenue analysis latest quarter
```

**Expected behavior:**
- Runs topline + children + concentration from available data
- Skips the New/Existing/Churned cohort section and notes the omission
- Does NOT block the skill

---

## Capability 5 — Critical binding missing

### Case REV-07: No revenue hierarchy source

**Input:**
```
revenue analysis
```

**Setup:** No revenue table with period + plan columns resolves.

**Expected behavior:**
- Blocks
- Outputs: "Revenue analysis requires a table with periodic revenue by hierarchy and plan amounts. Add a binding to hydration_hints.yaml under finance.revenue.hierarchy and re-verify."
- Does NOT fabricate a narrative

---

## Pitfall Regression Cases

### Case P-01: Partial period labeled throughout

**Setup:** Requested quarter has period-to-date (partial) actuals only.

**Input:**
```
how is revenue doing this quarter
```

**Check:** ALL figures are labeled "through [date]" and the narrative notes comparisons may shift once the period closes. The skill does not present partial actuals as a completed period.

---

### Case P-02: Prose format, not tables

**Input:**
```
revenue summary for the quarter
```

**Check:** Performance sections are written as flowing prose paragraphs. There are NO metric tables or bullet lists inside the body. Only the Key Takeaway uses bullets.

---

### Case P-03: Whale-driven growth diagnosed, not mislabeled

**Setup:** Mean customer revenue change is positive while median is flat/negative (skew ratio > 50x).

**Input:**
```
what is driving growth
```

**Check:** The narrative diagnoses whale-driven growth (flags extreme whale-dependency) rather than describing it as broad expansion. The mean-vs-median distinction drives the verdict.

---

## Running

```
/skill_eval - point it at this file for golden case assertions.
```
