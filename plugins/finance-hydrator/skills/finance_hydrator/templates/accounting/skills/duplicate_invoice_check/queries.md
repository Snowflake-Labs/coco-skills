# Query Reference — Duplicate Invoice Check

All physical table and column references are resolved at runtime against the customer's Snowflake account. This file describes query types and logic patterns — no SQL, no FQNs.

---

## Data Shape (resolved at runtime)

**Primary data sources:**
- Invoice header table (one row per supplier invoice, with received date, amount, supplier, invoice reference, status)
- Invoice line table (one row per invoice line, with PO reference and adjustment flags — used for enrichment)

---

## Query Type 1 — New Invoice Count

**Purpose**: Determine how many invoices fall within the user-specified received-date window, after applying exclusions. Used for the "New Invoices Scanned" figure in the executive summary.

**Logic:**
1. Filter the invoice header table to rows where received date is within `WINDOW_START` to `WINDOW_END` (inclusive)
2. Apply all standard exclusions:
   - Exclude rows where invoice amount is negative (credit notes)
   - Exclude rows where the payment type column matches the organization's direct debit payment type label
   - Exclude rows where the intercompany flag is true
   - Exclude rows matching any customer-configured low-value vendor exclusion patterns
3. COUNT the remaining rows

**Output grain:** A single count integer.

---

## Query Type 2 — Fuzzy Match Detection (New vs Historical)

**Purpose**: Find all invoice pairs — one new, one historical — from the same supplier that meet at least the minimum match threshold. This is the core detection query.

**Logic:**
1. Partition invoices into two sets, both with exclusions applied:
   - **New invoices**: received date within `WINDOW_START` to `WINDOW_END`
   - **Historical invoices**: received date before `WINDOW_START`, or received date is null
2. Join PO references from the invoice line table to both sets (aggregate PO numbers to a comma-separated list per invoice header)
3. Join adjustment flags and reasons from the invoice line table to both sets
4. Cross-join new invoices to historical invoices on the shared supplier identifier (same supplier only — never compare across suppliers)
5. Exclude pairs where both invoices are identical records
6. Exclude pairs where the invoice reference numbers are exact matches (exact duplicates of the reference field are not fuzzy matches)
7. For each pair, evaluate and score the four match criteria:
   - **Match A**: Jaro-Winkler similarity between the invoice reference numbers ≥ configured threshold
   - **Match B**: Absolute day difference between invoice dates ≤ configured tolerance
   - **Match C**: Invoice amount percentage difference ≤ configured tolerance (same currency), or reporting-currency amount percentage difference ≤ configured tolerance (cross-currency)
   - **Match D**: PO reference match (exact or contains) AND invoice date difference ≤ configured tolerance
8. Sum the four boolean match scores to compute total criteria matched (0–4)
9. Assign risk level: 4 = VERY HIGH, 3 = HIGH, 2 with Match A = MEDIUM
10. Apply risk filter: if the user selected HIGH+VERY HIGH only, discard pairs where total < 3
11. For each new invoice, retain only the single best historical match (highest criteria count; ties broken by smallest date difference)
12. Compute adjustment signals as a structured string:
    - Detect if the new invoice reference is a superset of the historical reference with a known adjustment suffix (e.g., trailing alphabetic or common correction codes)
    - Capture the adjustment flag and reason from either invoice's line data
    - Capture any adjustment/correction/reversal keywords from the header memo field

**Output grain:** One row per new invoice (deduped to best historical match), with all match scores, risk level, adjustment signals, and both full invoice records side by side.

---

## Query Type 3 — AI Classification (Lateral Apply)

**Purpose**: Classify each matched pair as LIKELY_DUPLICATE, LIKELY_ADJUSTMENT, LIKELY_RECURRING, or UNCERTAIN using an AI model. Applied to the output of Query Type 2 as a lateral/per-row operation.

**Logic:**
1. For each matched pair from Query Type 2, construct a structured prompt containing:
   - Supplier name
   - New invoice: reference, date, amount (transaction currency), amount (reporting currency), PO reference, status, received date
   - Historical invoice: same fields
   - Match conditions triggered (e.g., "A:InvoiceRef | B:InvoiceDate")
   - Adjustment signals detected (or "none")
2. Call the account's available Cortex LLM function with the prompt, requesting a response in the format:
   - Line 1: `VERDICT: <LIKELY_DUPLICATE|LIKELY_ADJUSTMENT|LIKELY_RECURRING|UNCERTAIN>`
   - Line 2: `REASON: <one sentence explanation>`
3. Parse the response to extract the verdict and reason
4. Fall back to UNCERTAIN verdict if the model is unavailable or the response cannot be parsed

**Output grain:** Same as Query Type 2 with two additional columns: AI_VERDICT and AI_REASON.

**Result ordering:**
1. LIKELY_DUPLICATE first, then UNCERTAIN, then LIKELY_ADJUSTMENT, then LIKELY_RECURRING
2. Within each verdict: VERY HIGH risk first, then HIGH, then MEDIUM
3. Within each risk level: highest reporting-currency amount first

---

## Pitfalls

1. **Received date vs invoice date** — The "new" vs "historical" partition uses the *received date* (when the invoice arrived for processing), not the *invoice date* (the date the supplier printed on the invoice). Some invoices have a null received date; these are always treated as historical. Never substitute invoice date for received date in the partitioning logic.

2. **Same supplier, not same invoice** — The cross-join must be scoped to the same supplier identifier. Never compare invoices across different suppliers, even if the amounts are identical.

3. **Deduplication before AI** — The deduplication step (one best historical match per new invoice) must run before the AI classification call. Running AI on un-deduped pairs wastes credits and produces confusing results.

4. **Currency handling for Match C** — If two invoices share the same currency code, compare amounts in that currency. If currencies differ, compare the reporting-currency (USD-equivalent) amounts. Never mix these two paths. If the reporting-currency column is unavailable, Match C applies same-currency comparison only.

5. **PO enrichment grain** — The invoice line table may have multiple lines per invoice header. Aggregate PO references to a list at the header level before joining; do not cross-join lines to produce duplicate invoice pairs.

6. **Exact reference matches** — Pairs where the invoice reference is an exact string match (after trimming and uppercasing) should be excluded from the fuzzy match results. Exact matches have their own detection path (exact duplicate check) and should not appear in the fuzzy results.
