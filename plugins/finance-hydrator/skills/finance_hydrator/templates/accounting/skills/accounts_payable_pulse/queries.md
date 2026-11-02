# Query Reference — AP Pulse

All physical table and column references are resolved at runtime against the customer's Snowflake account. This file describes query types and logic patterns — no SQL, no FQNs.

---

## Data Shape (resolved at runtime)

**Primary data sources:**
- Invoice header table (one row per invoice — status, amounts, due dates, holds, exceptions, approval state)
- Invoice approval/workflow table (one row per pending approval action — step name, entry date, aging)

**Optional data sources (queries below run only if the table resolves):**
- Invoice line table (one row per invoice line — PO reference, adjustment flags)
- Purchase order header table (one row per PO — amounts, status, terms)
- Purchase order line table (one row per PO line — line amounts, invoiced vs uninvoiced)
- Invoice intake pipeline table (daily counts received / processed / pending)
- AP support ticket table (one row per support request)
- Bank / cash balance table (one row per account per snapshot)

---

## Core Briefing Queries

---

### Q1 — Overdue Invoice Age Bucket Summary

**Purpose**: Show how much is overdue and how long it has been overdue. Two-layer breakdown: first by age bucket, then by payment term. Used for Section 2 headline and age-bucket summary table.

**Logic — age bucket layer:**
1. Filter invoice header to records where:
   - Due date is before current date (past due)
   - Invoice is not fully paid (payment status is not paid / settled)
   - Intercompany flag is false (exclude inter-entity transactions)
2. Compute days overdue = current date minus due date
3. Assign each invoice to an age bucket:
   - **1–30 days overdue**
   - **31–60 days overdue**
   - **61–90 days overdue**
   - **90+ days overdue**
4. For each bucket: count of invoices, total amount in reporting currency (or transaction currency if reporting currency unavailable), oldest invoice age
5. Order buckets from 90+ down to 1–30 (most urgent first)

**Logic — payment term layer:**
1. Same filter as above
2. Group by payment term label
3. For each payment term: count, total amount (reporting currency), average days overdue, maximum days overdue
4. Order by average days overdue descending

**Output grain:** Two result sets — one row per age bucket, one row per payment term.

**Pitfall:** Use due date (when payment was contractually owed), not invoice date (when the supplier issued the invoice). These differ by the payment term length.

---

### Q2 — Overdue Invoice Detail

**Purpose**: Full list of overdue invoices for Section 2 detail table and drill-down.

**Logic:**
1. Same filter as Q1
2. Return: invoice reference (vendor-assigned), supplier name, invoice date, due date, days overdue, payment term, invoice amount, currency, reporting-currency amount, company / legal entity
3. Order by days overdue descending (oldest first)

**Parameters:**
- `payment_term` filter for drill-down scoped to a specific term
- `min_days_overdue` filter (e.g., `min_days_overdue=60` to show only 60+ day items)
- `supplier_keyword` filter for supplier-scoped drill-down

---

### Q3 — On-Hold Invoice Summary

**Purpose**: Count and total invoices currently on hold, grouped by hold reason. Used for Section 3 headline.

**Logic:**
1. Filter invoice header where the hold indicator is active (hold flag = true, or hold reason field is populated and non-empty, or status code indicates a held state)
2. Exclude fully paid invoices
3. Group by hold reason
4. For each reason: count, total amount in reporting currency, oldest hold age in days
5. Order by total amount descending

**Output grain:** One row per hold reason.

---

### Q4 — On-Hold Invoice Detail

**Purpose**: Full list of on-hold invoices for Section 3 detail table and drill-down.

**Logic:**
1. Same filter as Q3
2. Return: invoice reference, supplier name, invoice date, days since invoice date, company / legal entity, amount, currency, reporting-currency amount, hold reason
3. Order by days since invoice date descending (oldest first)

**Parameters:** `hold_reason` filter for drill-down to a specific reason.

---

### Q5 — Match Exception Invoice Summary

**Purpose**: Count and total invoices with match exceptions, grouped by exception reason. Used for Section 4 headline.

**Logic:**
1. Filter invoice header where the match exception indicator is active (exception flag = true, or exception reason field is populated and non-empty, or status indicates an exception state)
2. Exclude fully paid or resolved invoices
3. Group by exception reason
4. For each reason: count, total amount in reporting currency, oldest exception age in days
5. Order by total amount descending

**Output grain:** One row per exception reason.

---

### Q6 — Match Exception Invoice Detail

**Purpose**: Full list of exception invoices for Section 4 detail table and drill-down.

**Logic:**
1. Same filter as Q5
2. Return: invoice reference, supplier name, invoice date, days since invoice date, company / legal entity, amount, currency, reporting-currency amount, exception reason
3. Order by days since invoice date descending

**Parameters:** `exception_reason` filter.

---

### Q7 — Approval Pipeline Summary by Step

**Purpose**: Show the approval pipeline health by step name. Used for Section 5 headline and step breakdown.

**Logic:**
1. Join invoice header to approval/workflow table on invoice identifier
2. Filter to approval actions that are currently pending (no completion date, or status = pending/open)
3. Compute days pending = current date minus the date the invoice entered this approval step
4. Group by approval step name
5. For each step:
   - Total invoices pending
   - Count pending beyond the aging threshold (default: 3 days)
   - Maximum days pending
   - Total invoice amount pending (reporting currency)
6. Order by total amount descending

**Output grain:** One row per approval step.

**Pitfall — grain:** The approval table may have one row per step, not per invoice. An invoice with three pending approvers appears three times. Use COUNT(DISTINCT invoice_identifier) for the invoice count figure; count step rows for step-level breakdown.

**Pitfall — alternate data shape:** In some systems the pending approval step and entry date are columns on the invoice header, not in a separate table. Adapt accordingly.

---

### Q8 — Approval Pipeline Aging Detail

**Purpose**: Full list of invoices pending beyond the aging threshold. Used for Section 5 detail table and drill-down.

**Logic:**
1. Same join and filter as Q7
2. Filter to invoices where days pending exceeds the aging threshold
3. Return: invoice reference, supplier name, invoice amount, currency, approval step name, approver identifier / name, date entered step, days pending
4. Order by days pending descending

**Parameters:**
- `step_name` filter for drill-down to a specific step
- `min_days` override for the aging threshold

---

### Q9 — Large Upcoming Payments Summary

**Purpose**: Surface large payments due within the lookahead window. Used for Section 6.

**Logic:**
1. Filter invoice header to records where:
   - Due date is between today and today + N days (lookahead window, default N = 5)
   - Invoice is approved but not yet paid (status = approved / ready for payment)
   - Intercompany flag is false
   - Amount in reporting currency exceeds the configured large-payment threshold (if not configured, apply no threshold and note it)
2. Return: invoice reference, supplier name, due date, days until due (due date minus today), invoice amount (transaction currency), currency, reporting-currency amount, company / legal entity, payment type
3. Order by due date ascending, then reporting-currency amount descending

**Output grain:** One row per upcoming large payment.

**Parameters:**
- `days_ahead`: lookahead window (default 5)
- `amount_threshold`: minimum reporting-currency amount (org-specific; omit if not configured)
- `supplier_keyword`: for supplier-scoped drill-down

**Pitfall:** If the reporting-currency amount column is unavailable, do not filter by threshold — return all upcoming payments and group by currency instead.

---

### Q10 — Large Upcoming Payments Detail

**Purpose**: Drill-down for a specific supplier or entity in the Large Upcoming Payments section.

**Logic:** Same as Q9 with additional `supplier_keyword` or `entity` filter.

---

## Optional Briefing Queries

Skip these queries if the corresponding data source does not resolve at runtime.

---

### Q11 — Cash & Bank Balances (optional)

**Purpose**: Show current balances for AP-relevant bank accounts. Used for optional Cash & Bank Balances section.

**Logic:**
1. Filter the bank balance table to the most recent snapshot date per account
2. Exclude accounts with zero balance if desired
3. Return: account name, company / legal entity, currency, balance in local currency, balance in reporting currency
4. Order by reporting-currency balance descending

**Degraded behavior:** Skip if the cash balance table does not resolve.

---

### Q12 — Invoice Intake Daily Trend (optional)

**Purpose**: Show the 7-day trend of invoices received and processed. Used for optional Invoice Intake Trend section.

**Logic:**
1. Filter intake pipeline table to last 7 calendar days
2. Return one row per day: date, count received, count processed / pushed to ERP, count pending
3. Order by date ascending

**Degraded behavior:** Skip if the intake pipeline table does not resolve.

---

### Q13 — Team Performance by Processor (optional)

**Purpose**: Show AP team processing volume and efficiency. Used for optional Team Performance section.

**Logic:**
1. Filter to invoices processed within the last 30 days
2. Group by processor name / identifier
3. For each processor:
   - Count of invoices processed
   - Average cycle time: received date to approved date (in days)
   - Sent-back count and rate (invoices rejected or sent back at least once, as % of total processed)
4. Include a team total as the first row (clearly labeled)
5. Order by invoice count descending

**Pitfall:** If processor assignment is a column on the invoice header (not a separate table), use that column for grouping.

**Degraded behavior:** Skip if processor assignment data does not resolve.

---

### Q14 — Support Ticket Queue Status (optional)

**Purpose**: Show the AP support ticket queue split by status with aging. Used for optional Support Ticket Queue section.

**Logic:**
1. Filter AP support ticket table to open / active tickets (not closed or resolved)
2. Group by status category
3. For each category: count, count by aging bucket (0–1d, 2–3d, 4–7d, 8–14d, 14+d), average and maximum age
4. Order by average age descending

**Degraded behavior:** Skip if the ticket table does not resolve.

---

## Invoice Lookup Queries (Mode 2)

---

### Q15 — Match by Supplier Invoice Reference + Supplier Name

**Purpose**: Find system invoice records matching the vendor-assigned invoice reference, scoped to a supplier.

**Logic:**
1. Filter invoice header where the vendor-assigned invoice reference number matches the uploaded value (exact match first; fall back to case-insensitive contains if no exact match)
2. Additionally filter by supplier name using a fuzzy / contains match (legal suffix stripped before comparison)
3. Return: system invoice identifier, vendor invoice reference, supplier name, invoice date, invoice amount, currency, invoice status, payment status, paid date, amount paid, amount outstanding
4. Order by invoice date descending

**Parameters:** `invoice_reference`, `supplier_keyword`.

---

### Q16 — Match by System Invoice Header Identifier

**Purpose**: Look up an invoice by its system-assigned identifier.

**Logic:**
1. Filter invoice header where the system identifier matches the uploaded value exactly
2. Return same fields as Q15

**Parameters:** `header_identifier`.

---

### Q17 — Match by PO Reference via Invoice Lines

**Purpose**: Find invoices containing a specific PO reference in their line items.

**Logic:**
1. Join invoice header to invoice line table on system invoice identifier
2. Filter invoice lines where the PO reference field contains the uploaded PO number (case-insensitive contains)
3. Filter additionally by supplier name if provided
4. Return distinct invoice header records (all fields from Q15 plus matched PO reference)
5. Order by invoice date descending

**Parameters:** `po_reference`, `supplier_keyword` (optional).

**Pitfall:** The invoice line table may have multiple lines per invoice. Use DISTINCT on the invoice identifier to avoid returning the same invoice multiple times.

---

### Q18 — PO Direct Lookup — Header

**Purpose**: Look up a PO from the purchase order table when the invoice has not yet been entered into the system.

**Logic:**
1. Filter purchase order header where the PO identifier matches the uploaded PO reference (normalize the uploaded PO format to match the account's PO identifier format before comparing)
2. Return: PO identifier, supplier name, PO status, total PO amount (local currency), total PO amount (reporting currency), transaction currency, payment terms, invoicing status
3. Order by creation date descending

**Parameters:** `po_reference` (normalized).

---

### Q19 — PO Direct Lookup — Lines

**Purpose**: Show line-level detail for a matched PO — invoiced vs uninvoiced amounts.

**Logic:**
1. Filter purchase order line table to lines belonging to the PO from Q18
2. Return: PO identifier, line identifier, description, line amount (local), amount invoiced to date, uninvoiced amount remaining, line payment status, cancellation flag, requester
3. Order by line identifier ascending

**Parameters:** `po_identifier` (from Q18 result).

---

## Pitfalls

1. **Due date vs invoice date** — Overdue calculations must use the due date (when payment was contractually owed), not the invoice date (when the supplier issued the invoice). Substituting one for the other produces significantly wrong overdue figures.

2. **Hold vs exception are separate concepts** — An invoice on hold has been blocked by an AP action; an invoice with a match exception has failed automated matching. Present them in separate sections with separate reason breakdowns. Never combine into a single "problematic invoices" bucket.

3. **Reporting-currency aggregation** — Only sum amounts across suppliers or entities when all have been converted to a common reporting currency. If unavailable, present amounts grouped by transaction currency code.

4. **Approval step grain** — The approval table may have one row per step, not per invoice. Count distinct invoice identifiers for the headline figure. Count step rows for step-level breakdown.

5. **Intercompany exclusion** — Overdue and large payment totals should exclude intercompany invoices. These are managed separately and distort the external payables picture.

6. **Large payment threshold is org-specific** — Do not apply a hardcoded dollar threshold. Use the configured value; if not set, show all upcoming payments and explicitly note the threshold has not been configured.

7. **PO enrichment grain** — When joining invoice lines to get PO references, aggregate PO references to a list at the invoice header level before joining. Do not cross-join lines to produce duplicate invoice rows.

8. **Age bucket boundary** — An invoice that is exactly 30 days overdue belongs in the 1–30d bucket, not the 31–60d bucket. Use `BETWEEN 1 AND 30`, `BETWEEN 31 AND 60`, etc. — avoid off-by-one errors at bucket boundaries.
