# Eval Plan: duplicate_invoice_check (Hydration Test)

## Purpose

Verify that `<customer-plugin>/skills/duplicate_invoice_check/` (dehydrated) can be hydrated against a Snowflake account containing accounts payable invoice data. The runtime context resolution must discover the invoice header table, invoice line table, and all critical columns from natural-language Context Query descriptions alone — without any hardcoded FQNs or column names.

## Verification Harness

```bash
# Step 1 — Hydrate against <SNOWFLAKE_CONNECTION> with <ROLE_WITH_FINANCE_READ>
cortex -p "Use <HYDRATOR_SKILL> to hydrate <customer-plugin>/skills/duplicate_invoice_check/. \
  Use connection <SNOWFLAKE_CONNECTION> with role <ROLE_WITH_FINANCE_READ>."

# Step 2 — Run golden cases against hydrated skill
cortex -p "Run the golden cases from <customer-plugin>/evals/duplicate_invoice_check/golden_cases.md \
  against the hydrated duplicate_invoice_check skill."
```

---

## Metrics

### Guard Metrics (must pass for valid eval)

| ID | Metric | Pass Condition |
|---|---|---|
| G1 | Hydration sources execute without error | At least 2/5 sources return results |
| G2 | Critical binding resolved: invoice header table | A table with per-invoice grain discovered at HIGH or MEDIUM confidence |
| G3 | Critical binding resolved: supplier identifier column | A column suitable for grouping invoices by vendor/supplier identified |
| G4 | Cross-join query compiles | Framework can generate a self-join or cross-join of the invoice table scoped to same supplier |
| G5 | No fabricated tables or columns | Framework reports what it found; doesn't invent |

### Primary Metrics (correctness)

| ID | Metric | Pass Condition |
|---|---|---|
| P1 | Invoice header table discovered with correct grain | Table has one row per invoice with received date, invoice date, amount, currency, invoice reference |
| P2 | Invoice received date column identified | A date column representing when the invoice arrived for processing is resolved (distinct from invoice date) |
| P3 | Invoice reference column identified | The vendor-assigned invoice number column is resolved and used for fuzzy matching |
| P4 | Invoice line table discovered | A table with per-line PO references joinable to the header table is found |
| P5 | Supplier identifier column resolved | A numeric or string vendor ID column suitable for equality join is identified |

### Secondary Metrics (output quality)

| ID | Metric | Pass Condition |
|---|---|---|
| S1 | Reporting-currency amount column found | A USD or reporting-currency amount column is discovered on the header table |
| S2 | Intercompany flag column identified | A boolean or indicator column for intercompany exclusion is found |
| S3 | Payment type column identified | A column for payment method (to exclude direct debit) is found |
| S4 | Zero column retries | Agent trace shows zero SQL retries due to wrong column names |
| S5 | AI function available | The account's Cortex LLM function is accessible and returns a verdict for a test pair |

---

## Equivalence Threshold

- **EQUIVALENT** = all G pass + all P pass + >= 4/5 S pass
- **DEGRADED** = all G pass + >= 3 P pass + < 4 S pass — iterate on Context Queries
- **INCOMPATIBLE** = any G fails OR < 3 P pass — needs rewrite of Context Queries or data domain description

---

## Expected Challenges

1. **Received date vs invoice date** — The invoice header table may have both columns with similar names. The hydrator must correctly identify the received date (when the invoice arrived for processing) as the partitioning column, not the invoice date. These are semantically distinct even if column names look similar.

2. **Jaro-Winkler function availability** — Fuzzy string matching requires `JAROWINKLER_SIMILARITY` or equivalent. Verify the function is available in the customer's account. If not, `EDITDISTANCE` can be used as a fallback with a calibrated threshold.

3. **Cross-join scale** — The new-vs-historical cross-join can be large for customers with high invoice volume. The hydrator should verify the account has sufficient warehouse resources or suggest the query partition approach.

4. **Currency handling** — Customers with multi-currency AP may not have a reporting-currency amount column. The hydrator should detect this and downgrade to same-currency Match C only, noting the limitation.

5. **PO reference grain** — The invoice line table may have multiple rows per invoice (multi-line invoices). The hydrator must recognize that PO references need to be aggregated at the header level before the cross-join.

---

## Remediation Guidance

| Scenario | Remediation |
|---|---|
| Invoice header table not found | Customer must confirm AP invoice data is loaded into Snowflake; check schema access |
| Received date column not found | Check if the column has a non-standard name; add a hydration hint with the column description |
| Invoice line table inaccessible | Grant SELECT to the executing role; or run without PO enrichment (disable Match D) |
| Jaro-Winkler function unavailable | Fall back to EDITDISTANCE with threshold ~4 characters |
| AI function unavailable | Run detection only; set all verdicts to UNCERTAIN; note limitation in output |
| Currency column missing | Run with same-currency Match C only; document limitation in output |

---

## Next Steps After Eval

1. [ ] Run hydration against <SNOWFLAKE_CONNECTION> with <ROLE_WITH_FINANCE_READ> — verify header and line table discovery
2. [ ] Execute golden cases against hydrated skill
3. [ ] Compare output structure to original duplicate_invoice_check runs
4. [ ] Document any behavioral gaps (vendor exclusions, currency handling, PO enrichment)
5. [ ] If DEGRADED: add hydration_hints.yaml entries for the specific bindings that failed
