# Eval Plan — accounts_payable_pulse

## Overview

This eval validates that the dehydrated `accounts_payable_pulse` skill correctly discovers and binds to an unfamiliar account's AP data, produces accurate AP health metrics, and routes invoice lookup correctly across all matching modes.

**Pass bar:** EQUIVALENT grade = all Guard + all Primary metrics pass.

---

## Grading Scale

| Grade | Definition |
|---|---|
| EQUIVALENT | All Guard + all Primary pass + ≥ 4/5 Secondary — production-ready |
| DEGRADED | All Guard + ≥ 3 Primary + < 4 Secondary — iterate on Context Queries |
| INCOMPATIBLE | Any Guard fails OR < 3 Primary pass — needs rewrite |

---

## Tier 1 — Guard Metrics (must all pass)

| ID | Metric | Pass Condition |
|---|---|---|
| G1 | Mode routing | Given a "good morning AP" trigger, skill enters Mode 1 (not Mode 2) |
| G2 | Mode routing | Given an invoice file upload, skill enters Mode 2 (not Mode 1) |
| G3 | Read-only enforcement | Skill never generates INSERT / UPDATE / DELETE / MERGE / CALL statements |
| G4 | Critical binding failure | When invoice header table cannot be resolved, skill outputs guidance rather than attempting queries |
| G5 | No FQN leakage | Skill never references any hardcoded table name or column name from the original source skill |
| G6 | No cross-currency aggregation | When invoices span multiple currencies, skill never sums across currencies without a reporting-currency column |

---

## Tier 2 — Primary Metrics (must all pass)

| ID | Metric | Pass Condition |
|---|---|---|
| P1 | Hold section correctness | Section 1 count and total match direct query against hold-indicator filter on invoice header |
| P2 | Exception section correctness | Section 2 count and total match direct query against exception-indicator filter |
| P3 | Approval aging correctness | Section 3 count of invoices exceeding threshold matches direct query; threshold default = 3 days |
| P4 | Overdue section correctness | Section 4 count and total match direct query using due date < today AND not paid |
| P5 | Invoice lookup — primary match | Q14 (invoice reference + supplier) returns correct invoice record when a known match exists |
| P6 | Invoice lookup — unmatched | Skill correctly flags "Unmatched" when an invoice reference has no system record |
| P7 | Optional section degradation | When an optional table (e.g., intake pipeline) is not available, skill skips the section with explanatory note rather than erroring |

---

## Tier 3 — Secondary Metrics (≥ 4/5 to pass)

| ID | Metric | Pass Condition |
|---|---|---|
| S1 | Hold reason grouping | Section 1 groups by hold reason with correct frequency ordering |
| S2 | Approval step breakdown | Section 3 shows correct per-step breakdown with correct maximum days pending |
| S3 | PO fallback matching | Q16 (PO via invoice lines) correctly finds invoices containing the PO reference |
| S4 | Excel export structure | Export contains one sheet per active section; Summary sheet has correct run metadata |
| S5 | Key Takeaways quality | At least one insight cross-references two sections (e.g., supplier in holds AND overdue); no invented figures |

---

## How to Run

1. Switch to the `_CORTEX_CODE_DEFAULT` profile (has `cortex-context-sql` skill and `<HYDRATOR_SKILL>`)
2. Run `<HYDRATOR_SKILL>` on `accounts_payable_pulse` against the target account
3. For each golden case in `golden_cases.md`:
   - Trigger the skill with the specified phrase
   - Compare output against expected behavior described in the case
   - Record PASS / FAIL / PARTIAL
4. Compute Guard, Primary, Secondary pass rates
5. Assign grade per the scale above
6. Save results to `eval_results.md`
