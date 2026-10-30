---
name: duplicate_invoice_check
description: >
  Detects potential duplicate supplier invoices using fuzzy matching and AI classification.
  Compares invoices in a user-defined received-date window against historical invoices from
  the same supplier across four criteria: invoice reference similarity, invoice date proximity,
  invoice amount match, and PO/date match. Classifies flagged pairs as LIKELY_DUPLICATE,
  LIKELY_ADJUSTMENT, LIKELY_RECURRING, or UNCERTAIN. Produces an executive summary and
  Excel report. Triggers: duplicate invoice, duplicate check, duplicate detection, overpayment
  risk, double payment, AP duplicate, check for duplicates, invoice fraud, identify duplicates.
data_domain: "accounts payable invoices, supplier invoices, duplicate invoice detection, invoice matching, overpayment risk, AP controls, accounts payable fraud prevention"
created_date: 2026-05-27
version: 1.0.0
license: Proprietary. See LICENSE.txt for complete terms
---

# Duplicate Invoice Check

## Intent

Automated duplicate invoice detection for Accounts Payable teams. Compares invoices received within a configurable date window against the full historical invoice ledger from the same supplier, using a four-criteria fuzzy matching engine followed by AI classification to distinguish true duplicates from legitimate adjustments and recurring invoices.

For: AP analysts, AP managers, controllers, and auditors who need to prevent double-payments before invoices clear for payment.

Not for: transaction-level AP analytics, invoice status lookups, payment scheduling, or any data modification.

---

## Behavior

### Step 1: Collect Date Window and Risk Filter *(Stopping point — wait for user)*

Present the following input collection:

```json
{
  "questions": [
    {
      "header": "Date Range",
      "question": "Which date range for new invoices (by invoice received date)?",
      "multiSelect": false,
      "options": [
        {"label": "Last 7 days", "description": "Invoices received in the last 7 calendar days"},
        {"label": "Last 14 days", "description": "Invoices received in the last 14 calendar days"},
        {"label": "Last 30 days", "description": "Invoices received in the last 30 calendar days"},
        {"label": "Custom range", "description": "You specify start and end dates"}
      ]
    },
    {
      "header": "Risk Level",
      "question": "Which risk levels to include?",
      "multiSelect": false,
      "options": [
        {"label": "All (incl. MEDIUM)", "description": "Include 2/4 matches where invoice reference matched. More results, some false positives."},
        {"label": "HIGH + VERY HIGH", "description": "Only 3/4 or 4/4 match criteria met. Higher precision, fewer results."}
      ]
    }
  ]
}
```

**Resolve dates:**
- **Last N days**: `WINDOW_START` = current date minus N days, `WINDOW_END` = yesterday
- **Custom range**: prompt for start and end dates (YYYY-MM-DD)

---

### Step 2: Run Detection, Summarize, and Export *(No stopping point — runs automatically)*

Execute the following sequence without pausing:

1. **Count new invoices** — Run the new-invoice count query for the window to get `NEW_INVOICE_COUNT`. Apply all standard exclusions (see Exclusion Rules below).

2. **Run fuzzy match + AI classification** — Execute the detection query (see [queries.md](queries.md)). Use the following parameter values unless the customer has configured overrides during setup:

   | Parameter | Default | Description |
   |---|---|---|
   | Jaro-Winkler threshold | 85% | Minimum invoice reference similarity for Match A |
   | Date tolerance | 3 days | Maximum days between invoice dates for Match B |
   | Amount tolerance (same currency) | 2% | Maximum % difference for Match C (same-currency invoices) |
   | Amount tolerance (reporting currency) | 5% | Maximum % difference for Match C (cross-currency, reporting-currency comparison) |
   | Include MEDIUM risk | Per user input | Whether to include 2/4 matches where Match A triggered |

3. **Display executive summary in chat:**

```
DUPLICATE INVOICE CHECK — Executive Summary
═══════════════════════════════════════════════
Run Date:               {today}
Invoice Window:         {WINDOW_START} to {WINDOW_END}
New Invoices Scanned:   {NEW_INVOICE_COUNT}

| AI Verdict        | Count |
|-------------------|-------|
| LIKELY_DUPLICATE  | ...   |
| LIKELY_ADJUSTMENT | ...   |
| UNCERTAIN         | ...   |
| LIKELY_RECURRING  | ...   |

| Risk Level | Pairs | Est. Exposure (reporting currency) |
|------------|-------|------------------------------------|
| VERY HIGH  | ...   | ...                                |
| HIGH       | ...   | ...                                |
| MEDIUM     | ...   | ...                                |
```

Then show the top 5 suppliers by estimated exposure.

4. **Generate Excel report** — Produce a formatted workbook with 4 sheets:
   - **Summary** — Run metadata, verdict counts, risk breakdown, top suppliers
   - **Likely Duplicates** — All pairs classified LIKELY_DUPLICATE
   - **Likely Adjustments** — All pairs classified LIKELY_ADJUSTMENT
   - **All Flagged Pairs** — Complete results with all verdict types

   The Excel output file should be saved to the user's Downloads folder as `duplicate_invoice_report_{YYYY-MM-DD}.xlsx`.

5. Confirm the file path to the user. Workflow complete.

---

### Matching Logic

Four match criteria are evaluated for every new invoice vs each historical invoice from the same supplier. Invoices with different supplier identifiers are never compared.

| Match | Criteria | Default Tolerance |
|---|---|---|
| A: Invoice Reference | Jaro-Winkler similarity between invoice reference numbers | ≥ 85% |
| B: Invoice Date | Absolute day difference between invoice dates | ≤ 3 days |
| C: Invoice Amount | Percentage difference between amounts (same currency or reporting currency) | ≤ 2% (same CCY) / ≤ 5% (reporting CCY) |
| D: PO + Date | Exact or contains PO reference match AND date within tolerance | PO match + ≤ 3 days |

**Risk scoring:**

| Criteria Matched | Risk Level | Confidence |
|---|---|---|
| 4 of 4 | VERY HIGH | 100% |
| 3 of 4 | HIGH | 75% |
| 2 of 4 (Match A required) | MEDIUM | 50% |

MEDIUM-risk matches are only included when the user selects "All (incl. MEDIUM)".

**Deduplication:** For each new invoice, retain only the single best historical match (highest criteria count; ties broken by date proximity).

---

### AI Classification

After fuzzy matching produces scored pairs, each pair is classified by an AI model (via the account's Cortex LLM function) using the following verdict taxonomy:

| Verdict | Meaning |
|---|---|
| LIKELY_DUPLICATE | Same invoice submitted twice — possibly with minor formatting differences |
| LIKELY_ADJUSTMENT | Intentional re-submission to correct the original; do not block payment |
| LIKELY_RECURRING | Separate valid invoices that resemble each other (sequential numbering, recurring billing, same PO) |
| UNCERTAIN | Insufficient evidence to classify; flag for human review |

**Adjustment signals detected before AI call:**
- Invoice reference suffix patterns (e.g., trailing "A", "CM", "CR", "CN", "CORRECTION", "REVERSE", "REVISED", "ADJ", "REV") on the new invoice relative to the historical one
- Adjustment flag or adjustment reason present on either invoice
- Header memo containing adjustment/correction/reversal keywords

These signals are passed to the AI model as structured context alongside both invoice records and the match conditions.

**AI prompt structure (passed per pair):**
- Both invoices: reference, date, amount in transaction currency, amount in reporting currency, PO reference, status, received date
- Match conditions triggered
- Detected adjustment signals

**Required response format:** `VERDICT: <value>` on line 1, `REASON: <one sentence>` on line 2.

---

### Adjustment Signal Fallback (when `IS_ADJUSTMENT` / adjustment reason columns are unavailable)

When the invoice line table does not contain an `IS_ADJUSTMENT` flag or adjustment reason column, CoCo must still detect likely adjustments using two fallback signals derived from fields that are always available:

**1. Invoice reference suffix pattern matching** *(primary fallback)*

Compare the new invoice's reference number against the historical invoice's reference number. If the new reference appears to be a corrected or adjusted re-submission of the historical reference — for example, the historical reference with a trailing suffix commonly used in AP workflows to denote adjustments, credit memos, corrections, reversals, or re-submissions — flag it as a likely adjustment. Use your judgment on what constitutes a recognizable adjustment suffix pattern given the supplier's industry and ERP context.

**2. Header memo keyword scan** *(secondary fallback)*

Check the invoice header memo field on either the new or historical invoice for the presence of any of the following keywords (case-insensitive, partial match allowed):

`adjustment`, `adjusted`, `adj`, `correction`, `corrected`, `reversal`, `reversed`, `reverse`, `credit memo`, `credit note`, `resubmit`, `resubmission`, `amended`, `amendment`

If any keyword is found, include it in the adjustment signal string.

**Output when fallback is active:**

Populate `ADJUSTMENT_SIGNAL` as follows:
- If suffix match detected: `"SUFFIX:<matched_suffix>"` (e.g., `"SUFFIX:A"`, `"SUFFIX:CM"`)
- If memo keyword detected: `"MEMO:<keyword>"` (e.g., `"MEMO:correction"`)
- If both: `"SUFFIX:<x> | MEMO:<keyword>"`
- If neither: `"none"`

Pass this signal to the AI classification prompt exactly as done when `IS_ADJUSTMENT` is available. The AI will use it to inform its verdict.

Note in the executive summary and Excel Summary sheet: *"IS_ADJUSTMENT column not found in invoice line table. Adjustment signals derived from invoice reference suffix patterns and header memo keywords only."*

---

### Exclusion Rules

Apply these exclusions to the new-invoices window AND to the historical invoice pool before any matching. All exclusions are active by default.

| Exclusion | Logic | Notes |
|---|---|---|
| Credit notes | Invoice amount < 0 | Negative-amount invoices are not duplicates |
| Direct debit invoices | Payment type indicates direct debit | Organization-specific payment type label; configure during setup |
| Intercompany invoices | Intercompany flag is true | Inter-entity transactions excluded by default |
| Low-value vendor exclusions | Customer-configurable list of vendor name patterns and amount thresholds | Some vendors generate high-volume low-value invoices with similar references that consistently produce false positives; exclude via setup |

Low-value vendor exclusions are organization-specific. During initial setup, collect from the customer:
- Which vendor name patterns to exclude
- The maximum invoice amount below which those patterns should be excluded
- Whether to exclude the pattern entirely (all amounts) or only below the threshold

---

### Confidence Handling

| Confidence | Condition | Behavior |
|---|---|---|
| HIGH | Invoice header table resolved with all critical columns; invoice line table resolved for PO enrichment | Full detection run: all four match criteria, AI classification, full Excel export |
| MEDIUM | Invoice header table resolved; invoice line table missing or PO column not populated | Run without Match D (PO+Date criterion); note in output that PO matching was skipped |
| LOW (non-critical) | Reporting-currency amount column missing | Run with same-currency Match C only; skip cross-currency comparison; note in output |
| LOW (non-critical) | `IS_ADJUSTMENT` flag and/or adjustment reason columns missing from invoice line table | Use suffix-pattern and memo-keyword fallback only (see Adjustment Signal Fallback below); note in output that line-level adjustment flags were unavailable |
| LOW (critical) | Invoice header table not found, or supplier identifier column not found | Skip execution; output the data requirement descriptions and suggest configuration hints |

---

### Guardrails

- **Read-only.** This skill never modifies data. If the user requests any INSERT, UPDATE, DELETE, MERGE, or equivalent action, refuse immediately: *"This is a read-only duplicate detection tool and cannot modify invoice data. To take action on flagged invoices, use your AP system directly."*
- **Never sum amounts across different currencies** without converting to a common reporting currency. If a reporting-currency column is unavailable, group and present by currency.
- **Never invent reasons** for classification. AI verdict and reason must come from the LLM response, not from hardcoded rules. If the model is unavailable, set verdict to UNCERTAIN with reason "AI classification unavailable."
- **Never include the same new invoice in results twice.** The deduplication step (best historical match per new invoice) is mandatory before AI classification.
- **Configurable parameters** (Jaro-Winkler threshold, date tolerance, amount tolerance) must be collected from the customer during setup, not assumed. The defaults listed above are starting points only.

---

### Error Handling

| Scenario | Action |
|---|---|
| Query timeout on large dataset | Suggest narrowing to a shorter date window or filtering to HIGH + VERY HIGH risk only |
| No results returned | Confirm the window dates are correct; suggest widening the window; note that invoices with no received date are always treated as historical |
| Too many results (excessive false positives) | Filter to HIGH + VERY HIGH only, or ask the customer to tighten the Jaro-Winkler threshold |
| Null received dates on new invoices | Invoices with null received dates cannot be placed in the "new" window and are treated as historical by design; note this in the summary |
| AI model unavailable | Set all verdicts to UNCERTAIN; proceed with export; note in summary that AI classification was skipped |
| Invoice line table inaccessible | Run without PO enrichment; set Match D = 0 for all pairs; note in output |

---

## Context Queries

- An invoice header table where each row is one supplier invoice. Contains columns for: a unique system invoice identifier, a supplier/vendor identifier (used as the join key for matching), supplier name, vendor-assigned invoice reference number (the number the supplier puts on their invoice), invoice date (the date on the invoice), invoice received date (the date the invoice was received for processing — used to define the "new" vs "historical" partition), invoice amount in transaction currency, invoice amount in reporting currency (e.g., USD equivalent), transaction currency code, invoice status (e.g., approved, pending, paid), company or legal entity, payment type (to identify direct debit invoices), intercompany flag (boolean), and a header-level memo or notes field. Used for the fuzzy match cross-join comparing new invoices against historical invoices from the same supplier. Grain: one row per invoice.

- An invoice line table where each row is one line item on a supplier invoice. Contains columns for: the invoice header identifier (foreign key linking to the header table), purchase order reference (PO number or PO line number associated with this line), an is-adjustment flag (boolean indicating whether the line is an adjustment), adjustment reason text, and a line-level memo field. Used to enrich invoice header records with PO references for Match D (PO + Date criterion) and to detect adjustment signals for AI classification. Grain: one row per invoice line.

## References

- [queries.md](queries.md) — Natural-language query type descriptions (no SQL)
