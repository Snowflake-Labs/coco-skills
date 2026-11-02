# Eval Plan: good_morning_collector (Hydration Test)

## Purpose

Verify that `<customer-plugin>/skills/good_morning_collector/` can be hydrated against a Snowflake account containing AR data. The runtime context resolution must discover the AR invoice table, due date and balance columns, payment activity table, and optional tables from natural-language Context Query descriptions alone — without hardcoded FQNs.

## Verification Harness

### Option A — Against live AR data (<SNOWFLAKE_CONNECTION>, <ROLE_WITH_FINANCE_READ>)

```bash
# Step 1 — Hydrate against <SNOWFLAKE_CONNECTION> with <ROLE_WITH_FINANCE_READ>
cortex -p "Use <HYDRATOR_SKILL> to hydrate <customer-plugin>/skills/good_morning_collector/. \
  Use connection <SNOWFLAKE_CONNECTION> with role <ROLE_WITH_FINANCE_READ>."

# Step 2 — Run golden cases
cortex -p "Run the golden cases from <customer-plugin>/evals/good_morning_collector/golden_cases.md \
  against the hydrated good_morning_collector skill."
```

### Option B — Against demo data (FINANCE_DEV.AR_TRACKER_DEMO)

```bash
cortex -p "Use <HYDRATOR_SKILL> to hydrate <customer-plugin>/skills/good_morning_collector/. \
  Resolve against schema FINANCE_DEV.AR_TRACKER_DEMO. Tables: customers, invoices, payments, revenue_monthly."

cortex -p "Run the golden cases from <customer-plugin>/evals/good_morning_collector/golden_cases.md \
  against the hydrated good_morning_collector skill using schema FINANCE_DEV.AR_TRACKER_DEMO."
```

---

## Metrics

### Guard Metrics (must pass for valid eval)

| ID | Metric | Pass Condition |
|---|---|---|
| G1 | Queries execute without error | All hydrated SQL templates compile and run |
| G2 | AR invoice or aging table resolved | Table with per-invoice grain, balance column, and due date discovered at HIGH or MEDIUM confidence |
| G3 | Due date column identified (distinct from invoice date) | DATE column representing payment due date resolved separately from invoice issue date |
| G4 | Amount column in reporting currency found | Numeric outstanding balance column suitable for SUM found |
| G5 | No fabricated tables or columns | Hydrator reports only what was confirmed via INFORMATION_SCHEMA |

### Primary Metrics (correctness)

| ID | Metric | Pass Condition |
|---|---|---|
| P1 | Aging buckets computable | At least 4 non-empty aging buckets returned from INVOICES (due-date-based) |
| P2 | Priority score computable | Top 5 accounts returned with composite score; last_contact fallback (90 days) applied when column absent |
| P3 | Collections query runs | PAYMENTS table found with PAYMENT_DATE and PAYMENT_AMOUNT; yesterday and MTD filters work |
| P4 | Due This Week query runs | DUE_DATE filter for current calendar week applied; urgency labels computed; overdue also included |
| P5 | Monthly Progress computable | Cash collected MTD and invoiced MTD both derivable; collection rate computed with formula label |

### Secondary Metrics (output quality)

| ID | Metric | Pass Condition |
|---|---|---|
| S1 | Collector assignment resolved | Column or table mapping accounts to a collector name identified at any confidence |
| S2 | Dispute section available | Dispute flag column or separate dispute table hydrates at any confidence |
| S3 | P2P section available | Payment plan table or P2P date column hydrates at any confidence |
| S4 | Aging delta available | Historical snapshot (snapshot_date column or separate prior-day snapshot) found for Δ Yesterday column |
| S5 | Monday morning edge case handled | "Yesterday" on Monday correctly resolves to Friday (business-day skip documented in skill behavior) |

---

## Equivalence Threshold

- **EQUIVALENT** = all G pass + all P pass + ≥ 4/5 S pass
- **DEGRADED** = all G pass + ≥ 3/5 P pass + < 4 S pass — iterate on Context Queries or add hydration hints
- **INCOMPATIBLE** = any G fails OR < 3 P pass — needs rewrite of Context Queries or data domain

---

## Expected Challenges

1. **No collector assignment in demo data** — `CUSTOMERS` table has no `<COLLECTOR_COL>` (e.g. `collector_name`) column. The skill must gracefully fall back to full-portfolio mode rather than failing hard.

2. **No `last_contact_date` column** — The priority score uses `LOG(days_since_last_contact + 1)`. With no contact date, all accounts default to 90 days (maximum priority weight). Priority list will sort purely on balance × √DPD.

3. **Monthly Progress with sparse PAYMENTS table** — Demo has only 12 payment rows. If no payments fall within the current month, cash_collected_mtd = $0 and collection rate = 0%. Skill should show 0%, not crash or omit the section.

4. **"Yesterday" is a weekend** — On Mondays, `DATEADD(day, -1, CURRENT_DATE())` returns Sunday. Demo PAYMENTS table likely has no Sunday payments. The skill behavior specifies Friday fallback — the SQL template should implement this.

5. **Due This Week may overlap with overdue** — Invoices that were due last week are both "overdue" and appear in the aging table. They should appear in the Due This Week section with urgency='Overdue', not be silently excluded.

---

## Remediation Guidance

| Scenario | Remediation |
|---|---|
| No collector assignment found | Expected for demo; note in output; full-portfolio fallback is correct behavior |
| Due This Week returns 0 rows | Verify DUE_DATE window covers current week; check if all invoices are already past due (appear as Overdue) |
| Monthly Progress rate = 0% | Check if PAYMENTS has any rows for current month; if demo data is static, rate = 0% is correct |
| Priority list fewer than 5 rows | Correct if portfolio has fewer than 5 overdue accounts — do not pad with current accounts |
| Dispute section unavailable | Expected for demo — show message, do not omit silently |

---

## Next Steps After Eval

1. [ ] Run golden cases against demo schema (FINANCE_DEV.AR_TRACKER_DEMO) — verify full-portfolio fallback
2. [ ] Run hydration against <SNOWFLAKE_CONNECTION> with <ROLE_WITH_FINANCE_READ> — verify `<COLLECTOR_COL>` discovery
3. [ ] Verify Due This Week urgency label output matches expected format
4. [ ] Verify Monthly Progress formula label is present in output
5. [ ] If DEGRADED on P metrics: review Context Queries wording in SKILL.md; add hydration_hints.yaml
