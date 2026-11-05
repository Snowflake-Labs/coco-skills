# Query Reference — Duplicate Invoice Check

All physical table and column references are resolved at runtime against the customer's Snowflake account. This file describes query types and logic patterns, plus one reference SQL (Query Type 2) written with `{{placeholders}}` that are filled from `hydration_hints.yaml`. No customer FQNs.

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
   - Exclude rows whose status is in the configured excluded-status list (void / cancelled / reversed)
   - Count credit memos (negative amounts) separately; they are only matched against other credit memos
   - Exclude rows where the payment type column matches the organization's direct debit payment type label
   - Exclude rows where the intercompany flag is true
   - Exclude rows matching any customer-configured low-value vendor exclusion patterns
3. COUNT the remaining rows

**Output grain:** A single count integer.

---

## Query Type 2 — Fuzzy Match Detection (New vs Historical, and New vs New)

**Purpose**: Find all invoice pairs from the same supplier — new vs historical, and new vs other new invoices in the window — that meet at least the minimum match threshold. This is the core detection query.

**Logic:**
1. Partition invoices into two sets, both with exclusions applied:
   - **New invoices**: received date within `WINDOW_START` to `WINDOW_END`
   - **Historical invoices**: received date before `WINDOW_START`, or received date is null
   - Excluded statuses (void / cancelled / reversed) are removed from BOTH sets
   - Normalize `vendor_invoice_reference`: `UPPER`, strip non-alphanumerics, strip leading zeros
2. Join PO references from the invoice line table to both sets (aggregate PO numbers to a comma-separated list per invoice header)
3. Join adjustment flags and reasons from the invoice line table to both sets
4. Join each new invoice to (a) historical invoices and (b) other new invoices on `supplier_id` (same supplier only — never compare across suppliers). For new-vs-new, keep only pairs where the other invoice's `invoice_id` is lower, so each pair appears once.
5. Exclude pairs where both sides are the same record (`invoice_id` equal)
6. Compare credit memos only with credit memos (both amounts < 0), using `ABS(amount)`; invoices only with invoices
7. For each pair, evaluate and score the four match criteria:
   - **Match A**: normalized references are an exact match, OR Jaro-Winkler similarity between normalized references ≥ configured threshold
   - **Match B**: Absolute day difference between invoice dates ≤ configured tolerance
   - **Match C**: Invoice amount percentage difference ≤ configured tolerance (same currency), or reporting-currency amount percentage difference ≤ configured tolerance (cross-currency)
   - **Match D**: PO reference match (exact or contains) AND invoice date difference ≤ configured tolerance
8. Sum the four boolean match scores to compute total criteria matched (0–4)
9. Assign risk level: exact normalized reference match = VERY HIGH (regardless of count); 4 = VERY HIGH; 3 = HIGH; same amount + same invoice date with a different reference = HIGH; 2 with Match A = MEDIUM
9a. Netting check: if a same-supplier credit memo with `ABS(amount)` equal to the pair amount (within tolerance) is dated within the configured netting window, set `RESOLUTION_STATUS = RESOLVED_NETTED`; otherwise `OPEN`
10. Apply risk filter: if the user selected HIGH+VERY HIGH only, discard pairs where total < 3
11. For each new invoice, retain only the single best match (exact reference first, then highest criteria count; ties broken by smallest date difference)
12. Compute adjustment signals as a structured string:
    - Detect if the new invoice reference is a superset of the historical reference with a known adjustment suffix (e.g., trailing alphabetic or common correction codes)
    - Capture the adjustment flag and reason from either invoice's line data
    - Capture any adjustment/correction/reversal keywords from the header memo field

**Output grain:** One row per new invoice (deduped to best historical match), with all match scores, risk level, adjustment signals, and both full invoice records side by side.


### Reference SQL (Query Type 2)

Placeholders: `{{invoice_header_fqn}}`, `{{invoice_line_fqn}}`, `{{cols.*}}` and
`{{settings.excluded_statuses}}` come from `hydration_hints.yaml`. Runtime
parameters (`:window_start`, `:window_end`, `:jw_threshold`, `:date_tolerance_days`,
`:amount_tolerance_pct`, `:netting_window_days`) are bound, never concatenated.
Cross-currency (reporting-currency) Match C and the low-value vendor / direct
debit exclusions follow the same pattern and are omitted for brevity.

```sql
WITH inv AS (
  SELECT
    {{cols.invoice_id}}                         AS invoice_id,
    {{cols.supplier_id}}                        AS supplier_id,
    {{cols.vendor_invoice_reference}}           AS vendor_invoice_reference,
    NULLIF(LTRIM(REGEXP_REPLACE(UPPER({{cols.vendor_invoice_reference}}),
                                '[^A-Z0-9]', ''), '0'), '') AS norm_ref,
    {{cols.invoice_date}}                       AS invoice_date,
    {{cols.received_date}}                      AS received_date,
    {{cols.invoice_amount}}                     AS amount,
    ABS({{cols.invoice_amount}})                AS abs_amount,
    {{cols.currency}}                           AS currency,
    {{cols.invoice_amount}} < 0                 AS is_credit_memo,
    COALESCE({{cols.received_date}} BETWEEN :window_start AND :window_end, FALSE) AS is_new
  FROM {{invoice_header_fqn}}
  WHERE UPPER(COALESCE({{cols.status}}, '')) NOT IN ({{settings.excluded_statuses}})
    AND COALESCE({{cols.intercompany_flag}}, FALSE) = FALSE
),
po AS (
  SELECT {{cols.line_invoice_id}} AS invoice_id,
         ARRAY_AGG(DISTINCT {{cols.po_reference}}) AS po_refs
  FROM {{invoice_line_fqn}}
  WHERE {{cols.po_reference}} IS NOT NULL
  GROUP BY 1
),
pairs AS (
  SELECT
    n.invoice_id AS new_invoice_id, h.invoice_id AS match_invoice_id,
    n.supplier_id, n.vendor_invoice_reference AS new_ref, h.vendor_invoice_reference AS match_ref,
    n.invoice_date AS new_invoice_date, h.invoice_date AS match_invoice_date,
    n.amount AS new_amount, h.amount AS match_amount, n.currency,
    h.is_new AS match_is_new,
    (n.norm_ref = h.norm_ref)                                          AS is_exact_ref,
    IFF(n.norm_ref = h.norm_ref
        OR JAROWINKLER_SIMILARITY(n.norm_ref, h.norm_ref) >= :jw_threshold, 1, 0) AS match_a,
    IFF(ABS(DATEDIFF(day, n.invoice_date, h.invoice_date)) <= :date_tolerance_days, 1, 0) AS match_b,
    IFF(n.currency = h.currency
        AND ABS(n.abs_amount - h.abs_amount)
            <= :amount_tolerance_pct / 100 * GREATEST(n.abs_amount, h.abs_amount), 1, 0) AS match_c,
    IFF(ARRAYS_OVERLAP(np.po_refs, hp.po_refs)
        AND ABS(DATEDIFF(day, n.invoice_date, h.invoice_date)) <= :date_tolerance_days, 1, 0) AS match_d,
    (n.currency = h.currency AND n.abs_amount = h.abs_amount
     AND n.invoice_date = h.invoice_date
     AND COALESCE(n.norm_ref <> h.norm_ref, TRUE))                    AS same_amt_date_diff_ref,
    ABS(DATEDIFF(day, n.invoice_date, h.invoice_date))                 AS date_diff_days
  FROM inv n
  JOIN inv h
    ON  h.supplier_id    = n.supplier_id
    AND h.invoice_id    <> n.invoice_id
    AND h.is_credit_memo = n.is_credit_memo          -- credit memos only vs credit memos
    AND (NOT h.is_new OR h.invoice_id < n.invoice_id) -- new-vs-new counted once
  LEFT JOIN po np ON np.invoice_id = n.invoice_id
  LEFT JOIN po hp ON hp.invoice_id = h.invoice_id
  WHERE n.is_new
),
scored AS (
  SELECT p.*,
    match_a + match_b + match_c + match_d AS criteria_matched,
    CASE
      WHEN is_exact_ref                                  THEN 'VERY HIGH'
      WHEN match_a + match_b + match_c + match_d = 4     THEN 'VERY HIGH'
      WHEN match_a + match_b + match_c + match_d = 3     THEN 'HIGH'
      WHEN same_amt_date_diff_ref                        THEN 'HIGH'
      WHEN match_a + match_b + match_c + match_d = 2
           AND match_a = 1                               THEN 'MEDIUM'
    END AS risk_level
  FROM pairs p
  WHERE p.match_a + p.match_b + p.match_c + p.match_d >= 2
     OR p.is_exact_ref OR p.same_amt_date_diff_ref
),
netted AS (
  -- Same-supplier credit memo offsetting the duplicate amount within N days
  SELECT DISTINCT s.new_invoice_id, s.match_invoice_id
  FROM scored s
  JOIN inv cm
    ON  cm.supplier_id = s.supplier_id
    AND cm.is_credit_memo
    AND cm.currency    = s.currency
    AND ABS(cm.abs_amount - ABS(s.new_amount))
        <= :amount_tolerance_pct / 100 * ABS(s.new_amount)
    AND DATEDIFF(day, GREATEST(s.new_invoice_date, s.match_invoice_date), cm.invoice_date)
        BETWEEN 0 AND :netting_window_days
  WHERE s.new_amount > 0
)
SELECT s.*,
  IFF(nt.new_invoice_id IS NOT NULL, 'RESOLVED_NETTED', 'OPEN') AS resolution_status
FROM scored s
LEFT JOIN netted nt
  ON nt.new_invoice_id = s.new_invoice_id AND nt.match_invoice_id = s.match_invoice_id
WHERE s.risk_level IS NOT NULL
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY s.new_invoice_id
  ORDER BY s.is_exact_ref DESC, s.criteria_matched DESC, s.date_diff_days ASC
) = 1;
```

Apply the user's risk filter (drop MEDIUM when "HIGH + VERY HIGH" is selected)
on the result, then pass rows to Query Type 3.

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

2. **Same supplier, not same invoice** — The join must be scoped to `supplier_id`. Never compare invoices across different suppliers, even if the amounts are identical.

3. **Deduplication before AI** — The deduplication step (one best historical match per new invoice) must run before the AI classification call. Running AI on un-deduped pairs wastes credits and produces confusing results.

4. **Currency handling for Match C** — If two invoices share the same currency code, compare amounts in that currency. If currencies differ, compare the reporting-currency amounts. Never mix these two paths. If the reporting-currency column is unavailable, Match C applies same-currency comparison only.

5. **PO enrichment grain** — The invoice line table may have multiple lines per invoice header. Aggregate PO references to a list at the header level before joining; do not cross-join lines to produce duplicate invoice pairs.

6. **Exact reference matches** — Pairs whose normalized `vendor_invoice_reference` matches exactly (same supplier) are the strongest duplicate signal: they set Match A = 1 and are always VERY HIGH risk. Never exclude them from results.
7. **Bill number vs system number** — Match on the supplier's bill number (`vendor_invoice_reference`), never the ERP-generated invoice number, which is unique by construction.
8. **Credit memos and netting** — Credit memos are matched only against credit memos (`ABS(amount)`). A same-supplier credit memo that offsets a duplicate within the netting window downgrades the pair to Resolved/Netted; it is still shown, but excluded from open exposure.
