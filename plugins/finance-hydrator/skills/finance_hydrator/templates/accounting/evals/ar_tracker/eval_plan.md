# Eval Plan: ar_tracker (Hydration Test)

## Purpose

Verify that `<customer-plugin>/skills/ar_tracker/` (dehydrated) can be hydrated against a Snowflake account containing accounts receivable data. The runtime context resolution must discover the AR aging table (or invoice + payment tables), due date and amount columns, and optional sources from natural-language Context Query descriptions alone — without any hardcoded FQNs or column names.

## Verification Harness

### Option A — Against live AR data (<SNOWFLAKE_CONNECTION>, <ROLE_WITH_FINANCE_READ>)

```bash
# Step 1 — Hydrate against <SNOWFLAKE_CONNECTION> with <ROLE_WITH_FINANCE_READ>
cortex -p "Use <HYDRATOR_SKILL> to hydrate <customer-plugin>/skills/ar_tracker/. \
  Use connection <SNOWFLAKE_CONNECTION> with role <ROLE_WITH_FINANCE_READ>."

# Step 2 — Run golden cases against hydrated skill
cortex -p "Run the golden cases from <customer-plugin>/evals/ar_tracker/golden_cases.md \
  against the hydrated ar_tracker skill."
```

### Option B — Against demo data (any account, no <ROLE_WITH_FINANCE_READ> required)

First, load the demo seed:

```sql
-- Run in Snowsight or via SnowSQL:
-- File: <customer-plugin>/templates/demo_data/ar_tracker_demo_seed.sql
-- Creates: AR_TRACKER_DEMO.DEMO_AR.{customers, invoices, payments, revenue_monthly}
```

Then hydrate and evaluate against the demo schema:

```bash
# Step 1 — Hydrate against demo data
cortex -p "Use <HYDRATOR_SKILL> to hydrate <customer-plugin>/skills/ar_tracker/. \
  Resolve against schema AR_TRACKER_DEMO.DEMO_AR. Tables: customers, invoices, payments, revenue_monthly."

# Step 2 — Run golden cases
cortex -p "Run the golden cases from <customer-plugin>/evals/ar_tracker/golden_cases.md \
  against the hydrated ar_tracker skill using schema AR_TRACKER_DEMO.DEMO_AR."
```

---

## Metrics

### Guard Metrics (must pass for valid eval)

| ID | Metric | Pass Condition |
|---|---|---|
| G1 | Hydration sources execute without error | At least 2/5 sources return results |
| G2 | Critical binding resolved: AR aging or invoice table | A table with per-invoice grain and open balance data discovered at HIGH or MEDIUM confidence |
| G3 | Critical binding resolved: due date column | A date column representing payment due date resolved (distinct from invoice date) |
| G4 | Critical binding resolved: amount column | Numeric column in reporting currency suitable for SUM aggregation found |
| G5 | No fabricated tables or columns | Hydrator reports what it found; doesn't invent |

### Primary Metrics (correctness)

| ID | Metric | Pass Condition |
|---|---|---|
| P1 | Aging bucket computable | Either pre-computed bucket column or due_date available for DATEDIFF; at least 3 non-empty buckets returned |
| P2 | Account / customer name column identified | String column for "Top 10 Overdue Accounts" section resolved |
| P3 | Payment / collections table found | A table with payment date and payment amount columns discovered at any confidence |
| P4 | Collection rate computable | Both cash collected MTD and amount invoiced MTD can be derived from available tables |
| P5 | DSO formula executable | Revenue table or invoice total column available; trailing 90-day denominator > 0 |

### Secondary Metrics (output quality)

| ID | Metric | Pass Condition |
|---|---|---|
| S1 | Dispute table found | Optional dispute table hydrates at any confidence level |
| S2 | P2P table found | Optional payment plan table hydrates at any confidence level |
| S3 | Collector assignment resolved | Column or table mapping accounts to collectors identified at any confidence |
| S4 | Column name stability | Same columns resolved on two consecutive hydration runs with no retries due to wrong names |
| S5 | Multi-currency detection | If multiple currency values are present in the amount column(s), skill groups by currency; no cross-currency sums |

---

## Equivalence Threshold

- **EQUIVALENT** = all G pass + all P pass + >= 4/5 S pass
- **DEGRADED** = all G pass + >= 3 P pass + < 4 S pass — iterate on Context Queries
- **INCOMPATIBLE** = any G fails OR < 3 P pass — needs rewrite of Context Queries or data domain description

---

## Expected Challenges

1. **Due date vs invoice date** — The AR table may contain both columns with similar names (e.g., `invoice_date`, `due_date`, `maturity_date`, `payment_due_date`). The hydrator must resolve the due date (when payment is owed) as the aging baseline, not the invoice issuance date.

2. **DSO denominator availability** — Many customers load AR data without a separate revenue table. The hydrator should gracefully fall back to billed invoice amounts as the denominator and label DSO as estimated. If the trailing 90-day window has no invoices, DSO should be omitted rather than returning infinity.

3. **Pre-computed vs derived aging** — Some ERP exports include a pre-computed aging bucket column; others provide only raw dates. The hydrator must detect which path applies and confirm the bucket definitions match the expected five-bucket structure (Current / 1-30 / 31-60 / 61-90 / 90+).

4. **Multi-currency portfolios** — Customers with international AR will have multiple currency codes. The hydrator must detect if a reporting-currency (translated) column exists. If it does, use it for all aggregations. If not, the skill must group by currency and refuse to produce a single consolidated total.

5. **Payment table grain** — The payments table may have one row per payment batch (not per invoice). The hydrator must confirm the grain and identify the correct date column (deposit date vs applied date).

---

## Remediation Guidance

| Scenario | Remediation |
|---|---|
| AR table not found | Customer must confirm AR data is loaded into Snowflake; check schema access |
| Due date column not found | Add a hydration hint describing the due date column; or check if payment terms + invoice date exist |
| Amount column ambiguous (multiple numeric columns) | Add a hydration hint specifying the reporting-currency amount column name |
| DSO denominator always zero | Check that revenue or invoice data covers the trailing 90-day window; confirm date range |
| Collection rate query returns null | Verify payment activity table covers the current month; check payment_date column format |
| Multi-currency sum detected | Add hydration hint specifying the reporting-currency column to use |

---

## Next Steps After Eval

1. [ ] Run hydration against <SNOWFLAKE_CONNECTION> with <ROLE_WITH_FINANCE_READ> — verify AR aging table and due date column discovery
2. [ ] Execute golden cases against hydrated skill
3. [ ] Verify DSO formula output matches manual calculation from known data
4. [ ] If DEGRADED: add hydration_hints.yaml entries for the specific bindings that failed
