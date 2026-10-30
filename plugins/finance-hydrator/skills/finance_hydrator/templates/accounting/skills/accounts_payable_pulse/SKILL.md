---
name: accounts_payable_pulse
description: >
  AP health dashboard and invoice lookup for Accounts Payable teams. Two modes:
  (1) AP Pulse Briefing — comprehensive AP payments health snapshot: overdue invoices
  with age buckets, holds, match exceptions, approval pipeline aging, large upcoming
  payments, with optional cash balances, intake trend, and team performance; opens
  with AI-synthesized Key Takeaways. (2) Invoice Lookup — match uploaded supplier
  invoices (CSV/Excel/PDF) against system records to verify amounts, currency, and
  payment status. Triggers: AP pulse, AP dashboard, AP health, good morning AP,
  AP briefing, how are my invoices, AP status, payments health, match this invoice,
  check invoice status, reconcile invoices, upload invoice.
data_domain: "accounts payable invoices, supplier invoices, overdue invoices, invoice holds, match exceptions, approval workflow, large payments, purchase orders, AP operations, invoice matching, payment status, AP payments health"
dehydrated: true
created_date: 2026-05-28
dehydrated_by: Sakshi Baheti
version: 1.1.0
license: Proprietary. See LICENSE.txt for complete terms
---

# Accounts Payable Pulse

## Intent

Comprehensive AP payments health dashboard and on-demand invoice lookup for Accounts Payable teams. Tells the story of AP in one view: what is overdue, what is blocked, what is stuck in approval, and what large payments are coming up — followed by an option to match uploaded supplier invoices against system records.

For: AP analysts, AP managers, controllers, and auditors at any organization.

Not for: GL reporting, payroll, employee expense management, revenue accounting, or any data modification.

---

## Routing Logic

Classify the request into one of two modes before doing anything else.

| Signal | Mode |
|---|---|
| "AP pulse", "AP dashboard", "AP health", "AP status", "good morning AP", "AP briefing", "AP daily", "how are my invoices", "payments health", "show me AP" | Mode 1 — AP Pulse Briefing |
| "match this invoice", "check invoice status", "reconcile invoices", "upload invoice", user uploads a file (CSV / Excel / PDF) | Mode 2 — Invoice Lookup |
| Ambiguous | Use `ask_user_question`: "Would you like the AP Pulse Briefing (payments health overview) or Invoice Lookup (match a specific invoice against your records)?" |

---

## Mode 1: AP Pulse Briefing

### Section Plan

**Core sections** — run always (require only invoice header + approval tables):

| Order | Section | What it answers |
|---|---|---|
| — | *(run all queries first)* | |
| 1 | **Key Takeaways** | What are the top 3–5 things AP needs to act on right now? (shown first) |
| 2 | **Overdue Invoices** | What is past due, broken down by age and payment terms? |
| 3 | **Invoices on Hold** | What is blocked and why? |
| 4 | **Match Exceptions** | What failed automated matching? |
| 5 | **Approval Pipeline** | Where are invoices stuck waiting for approval? |
| 6 | **Large Upcoming Payments** | What big payments are due in the next N days? |

**Optional sections** — run only if the corresponding data resolves at runtime:

| Section | Required data | Degraded behavior |
|---|---|---|
| **Cash & Bank Balances** | Bank/treasury balance table | Skip; note "Cash balance data not available." |
| **Invoice Intake Trend** | Intake pipeline / daily processing table | Skip; note "Intake pipeline data not available." |
| **Team Performance** | Processor assignment + cycle time data | Skip; note "Processor assignment data not available." |
| **Support Ticket Queue** | AP support ticket / service desk table | Skip; note "Support ticket system not connected." |

Always end with: **Deep Dive** — suggested follow-up questions using actual values from the data.

---

### Step 1 — Confidence Check

Before running any queries:

1. Verify the invoice header table resolves with at minimum: a supplier identifier, an invoice amount, a due date or payment term, and a status or hold indicator. If it cannot be resolved: skip Mode 1 and output the data requirement guidance from the Context Queries section.
2. Verify the approval/workflow table resolves. If not: run Sections 2–6 only; skip Section 5 with note "Approval workflow data not available."
3. For each optional section: check if the corresponding table resolves. Record which are active.
4. Note skipped optional sections in a one-line banner at the top of the briefing — do not repeat per section.

---

### Step 2 — Run All Queries in Parallel

Run all active section queries simultaneously before presenting anything. See [queries.md](queries.md) for query type descriptions.

**Configuration defaults (use these unless the customer has configured overrides):**

| Parameter | Default | Description |
|---|---|---|
| Approval aging threshold | 3 days | Invoices pending a single approval step longer than this are flagged |
| Large payment threshold | Configurable per org | Minimum invoice amount to appear in Large Upcoming Payments; no universal default — ask or configure at setup |
| Payment lookahead window | 5 days | Days ahead to look for upcoming large payments |
| Overdue age buckets | 1–30d, 31–60d, 61–90d, 90+d | Overdue aging tiers for grouping |

---

### Step 3 — Present Briefing

**Key Takeaways first.** Present Section 1 (Key Takeaways) at the very top of the response — before any detail tables. Then present Sections 2–6, then active optional sections, then Deep Dive.

For each section:
- One-line summary with KPI indicator (🔴 / 🟡 / 🟢), count, and total amount
- Detail table (up to 15 rows; if more: "Showing top 15 of N — ask to filter by supplier / reason / date")
- `> 🔍` drill-down block with 2–3 follow-up prompts using **actual data values** from the results

See [references/output_format.md](references/output_format.md) for section templates and KPI threshold guidance.

---

### Section 1: Key Takeaways

**Run last, present first.** After all queries complete, generate 3–5 AI-synthesized insights from the full dataset.

Rules:
- Order by dollar impact (largest first)
- Each insight: severity badge (🔴 / 🟡 / 🟢) + bold headline + one-sentence recommended action
- Cross-section connections: if the same supplier appears in overdue AND holds AND exceptions, consolidate into one insight ("Supplier X has $1.2M spread across holds, exceptions, and overdue")
- Flag large payments due today or tomorrow as 🔴 regardless of amount
- Note if overdue total exceeds a material amount (org-specific; ask at setup or note "exceeds a material threshold")
- Include at least one 🟢 positive signal when the data supports it
- **Never invent figures.** Every number in Key Takeaways must come from a query result.

---

### Section 2: Overdue Invoices

**Goal:** Show the full overdue picture — how much is late, how late, and which payment terms are most at risk.

Display in two layers:
1. **Age bucket summary** — total count and amount by bucket (1–30d, 31–60d, 61–90d, 90+d)
2. **Payment term breakdown** — count and amount by payment term, ordered by average days overdue

KPI logic:
- 🔴 if any invoice is 90+ days overdue OR total overdue exceeds the org's materiality threshold
- 🟡 if invoices are 31–90 days overdue
- 🟢 if all overdue items are within 30 days and below threshold

Drill-down: `"Show all overdue [Net 30 / specific term] invoices"` | `"Show invoices overdue more than 60 days"` | `"Which suppliers have the most overdue invoices?"`

---

### Section 3: Invoices on Hold

**Goal:** Show what is blocked and why.

Display:
- Hold reason breakdown (count + amount per reason, ordered by amount)
- Detail table: invoice reference, supplier, amount, entity, days on hold, hold reason

KPI logic:
- 🔴 if any invoice has been on hold for more than 14 days OR total hold amount exceeds materiality threshold
- 🟡 if holds present, all within 7–14 days
- 🟢 if zero holds, or all holds are recent (< 7 days)

Drill-down: `"Show invoices on hold for [top reason]"` | `"Show holds older than 14 days"` | `"Which supplier has the most on hold?"`

---

### Section 4: Match Exceptions

**Goal:** Show what failed automated 2-way or 3-way matching.

Display:
- Exception reason breakdown (count + amount per reason)
- Detail table: invoice reference, supplier, amount, entity, days open, exception reason

KPI logic:
- 🔴 if any exception has been open more than 10 days OR total exception amount exceeds threshold
- 🟡 if exceptions present, all within 5–10 days
- 🟢 if zero exceptions

Drill-down: `"Show invoices with [top exception reason]"` | `"Show exceptions older than 10 days"` | `"Which supplier has the most exceptions?"`

---

### Section 5: Approval Pipeline

**Goal:** Show where invoices are stuck waiting for approval, by step and aging.

Display:
- Step-level breakdown: count pending, count beyond threshold (default: 3 days), max days pending, total amount
- Detail: invoices pending beyond threshold with step name, approver, days waiting, amount

KPI logic:
- 🔴 if any invoice has been pending a single step for more than 7 days OR more than 20% of the queue exceeds threshold
- 🟡 if some items exceed threshold but none exceed 7 days
- 🟢 if all pending approvals are within threshold

Drill-down: `"Show all invoices stuck in [top step name]"` | `"Show approvals pending more than 7 days"` | `"Who is the approver for [step name]?"`

---

### Section 6: Large Upcoming Payments

**Goal:** Surface large payments due in the next N days so AP can prepare cash or flag issues.

Display:
- Payments above the configured threshold, due within the lookahead window
- Columns: supplier, due date, days until due, amount (local currency), amount (reporting currency), entity, payment type
- Order by due date ascending, then amount descending

KPI logic:
- 🔴 if any large payment is due today or tomorrow
- 🟡 if large payments are due within 2–5 days
- 🟢 if no large payments within the window (show "No payments above threshold due in the next N days")

Note: The large payment threshold is organization-specific. During setup, collect this value from the customer (e.g., "$500K" or "1M EUR"). If not yet configured, surface all upcoming payments above a reasonable default and note the threshold used.

Drill-down: `"Show all payments due this week"` | `"Show payments from [supplier name]"` | `"Break down by legal entity"`

---

### Optional Section: Cash & Bank Balances

Latest balances for AP-managed bank accounts. Show: account name, entity, currency, local balance, reporting-currency balance. Order by reporting-currency balance descending.

---

### Optional Section: Invoice Intake Trend

7-day daily trend of invoices received and processed through the intake pipeline. Show: date, received count, processed/pushed count, pending count. Note: only available if the organization uses a separate AP intake or processing queue system.

---

### Optional Section: Team Performance

Rolling 30-day processor performance. Show: team total row first, then per-processor: invoice count, average cycle time (received to approved), sent-back rate. Flag sent-back rates above the configured threshold.

---

### Optional Section: Support Ticket Queue

Open AP support tickets grouped by status (active / on-hold / unassigned) with aging bucket distribution. Note: only available if an AP service desk or ticketing system is connected.

---

### Deep Dive

End with a table of suggested follow-up questions covering: overdue drill-downs, hold reasons, exception types, approval steps, upcoming payments, and any optional sections that were active. Use **actual values from the data** (real supplier names, real hold reasons, real step names) — not generic placeholders.

---

### Step 4 — Post-Briefing Actions

After presenting all sections, use `ask_user_question`:

```json
{
  "header": "Next",
  "question": "What would you like to do?",
  "options": [
    {"label": "Drill into a section", "description": "Deep-dive into one of the sections above"},
    {"label": "Match an invoice", "description": "Upload a supplier invoice to check its status (switch to Invoice Lookup mode)"},
    {"label": "Export to Excel", "description": "Download the full AP pulse data as a spreadsheet"},
    {"label": "Done", "description": "Wrap up — no further action needed"}
  ]
}
```

**If "Match an invoice":** transition directly to Mode 2 (Invoice Lookup) — no re-greeting, continue the conversation.

**If "Export to Excel":** generate `ap_pulse_{YYYY-MM-DD}.xlsx` with one sheet per active section. Save to Downloads folder; confirm path.

---

## Mode 2: Invoice Lookup

Match uploaded supplier invoices against internal procurement records. Supports CSV, Excel, and PDF inputs.

---

### Step 1 — Collect File (mandatory stopping point)

Use `ask_user_question` to request the supplier invoice file. Do not proceed until the file is provided.

Accepted formats: CSV, Excel (.xlsx / .xls), PDF (single invoice or vendor statement with multiple line items).

Also ask — or infer from context — the preferred matching mode:
- **Supplier invoice reference** (default): match by the reference number the supplier put on the invoice
- **Internal header number**: match by the system-assigned invoice ID
- **Purchase order number**: match by the PO referenced on the invoice

---

### Step 2 — Parse the Uploaded File

**For CSV / Excel:**
- Identify columns for: invoice reference number, supplier name, amount, currency
- If column mapping is ambiguous, ask via `ask_user_question` before proceeding

**For PDF — single invoice format:**
- Extract: invoice reference, supplier name, total amount, currency, invoice date
- One row per PDF

**For PDF — vendor statement with multiple line items:**
- Extract multiple invoice rows from a single PDF
- Normalize PO references to the account's standard PO identifier format
- Strip supplier legal suffixes (", Inc.", "LLC", "Corp.", "Ltd.", "GmbH", etc.) before fuzzy name matching
- Infer currency from symbols if not explicit: $ → USD, € → EUR, £ → GBP

**PDF fallback for CID-encoded fonts:**
- If extracted text contains mostly `(cid:XX)` sequences or is otherwise unreadable, convert pages to PNG images at 200 DPI and read visually to extract invoice data

---

### Step 3 — Run Matching Cascade

Run matching queries in order. Stop at the first query that returns results.

1. **Primary match** — invoice reference number + supplier name (fuzzy supplier name match)
2. **Header number match** — system invoice header identifier
3. **PO via invoice lines** — match PO reference in invoice line table (finds invoices already entered)
4. **PO direct lookup** — query purchase order tables directly (use when invoice has not yet been entered; shows PO exists + invoiced vs uninvoiced amounts)

See [queries.md](queries.md) for query type descriptions Q14–Q18.

---

### Step 4 — Present Match Results

**Summary counts** at top: Total uploaded | Matched | Amount Mismatch | Currency Mismatch | Unmatched

Per-invoice result row:

| Column | Source |
|---|---|
| Invoice Reference (uploaded) | User file |
| Invoice Reference (system) | Invoice header table |
| Supplier Name (system) | Invoice header table |
| Amount (uploaded) | User file |
| Amount (system) | Invoice header table |
| Currency Match | Compare uploaded vs system |
| Payment Status | Invoice header table |
| Match Status | Status flag (see below) |

**Status flags:**
- `Matched` — found in system; amount and currency agree
- `Amount Mismatch` — found; amount differs beyond tolerance
- `Currency Mismatch` — found; currency differs
- `Unmatched` — not found in system after all matching steps
- `PO Found / Not Yet Invoiced` — PO exists but invoice not yet entered; flag for AP to process

For PO direct lookup results, additionally show: PO number, PO status, total PO amount, amount invoiced, uninvoiced amount, payment terms, requester.

---

### Step 5 — Offer Next Steps (mandatory stopping point)

Use `ask_user_question`:

```json
{
  "header": "Next",
  "question": "What would you like to do?",
  "options": [
    {"label": "Export to Excel", "description": "Download match results as a spreadsheet"},
    {"label": "Try different matching mode", "description": "Re-run using PO number or system header ID"},
    {"label": "Run AP Pulse Briefing", "description": "Switch back to the AP health overview"},
    {"label": "Done", "description": "End the workflow"}
  ]
}
```

---

## Confidence Handling

### Mode 1

| Confidence | Condition | Behavior |
|---|---|---|
| HIGH | Invoice header + approval table resolve with all critical columns | Full briefing: all 6 core sections + any resolved optional sections |
| MEDIUM | Invoice header resolves; approval table missing | Sections 2–4, 6 only; skip Section 5 with note |
| LOW — non-critical | Reporting-currency amount column missing | All sections; show amounts in transaction currency; note "Reporting-currency totals unavailable — amounts shown in transaction currency" |
| LOW — critical | Invoice header not found | Skip Mode 1; output data requirement descriptions; suggest configuration |

### Mode 2

| Confidence | Condition | Behavior |
|---|---|---|
| HIGH | Invoice header + invoice line + PO tables all resolve | Full 4-step matching cascade |
| MEDIUM | Invoice header resolves; line or PO tables missing | Steps 1–2 only; note "PO matching unavailable" |
| LOW — critical | Invoice header not found | Skip Mode 2; output data requirement descriptions |

---

## Guardrails

- **Read-only.** Never INSERT, UPDATE, DELETE, MERGE, or call any procedure that modifies data. If the user requests any data modification: *"This is a read-only AP analysis tool. To take action on invoices, use your AP or ERP system directly."*
- **No cross-currency aggregation without conversion.** If a reporting-currency column is unavailable, group and display amounts by currency code. Never sum USD and EUR together.
- **No invented reasons.** Display hold reasons, exception reasons, and approval step names exactly as they appear in the data — never paraphrase or fill in from assumptions.
- **No hardcoded dollar thresholds.** The large payment threshold and materiality thresholds are organization-specific. Use configured values; if not configured, surface all items and note the threshold is not yet set.
- **Approval aging threshold is a starting point.** The default of 3 days is guidance. Customers should calibrate to their own SLA commitments.

---

## Error Handling

| Scenario | Action |
|---|---|
| Core section returns 0 rows (holds, exceptions, or overdue) | Show "No [holds / exceptions / overdue invoices]" with 🟢 |
| Approval table returns 0 rows | Show "No invoices pending approval beyond threshold" with 🟢 |
| Large payments returns 0 rows | Show "No payments above threshold due in next N days" with 🟢 |
| Query timeout on large dataset | Suggest filtering to a specific supplier, entity, date range |
| Optional table inaccessible | Skip section with one-line explanatory note; never error the whole briefing |
| PDF parsing failure | Ask user to provide data as CSV / Excel instead |
| Invoice lookup — no match found | Suggest alternate matching mode; confirm dates and supplier name |
| Invoice lookup — too many results | Suggest narrowing by supplier name or date range |

---

## Context Queries

Described in natural language — no table names, no column names. Resolved at runtime.

**Critical (skill cannot run Mode 1 without this):**

- An invoice header table where each row is one supplier invoice. Contains: a unique system invoice identifier, a supplier identifier used as a join key, supplier name, vendor-assigned invoice reference number, invoice date, due date, invoice received date, invoice status (e.g., approved / pending / paid), payment status, invoice amount in transaction currency, invoice amount in reporting currency, transaction currency code, company or legal entity, a hold flag or hold reason field, a match exception flag or exception reason field, payment type, and an intercompany flag. Grain: one row per invoice.

**Important — enables Approval Pipeline section:**

- An invoice approval or workflow action table where each row is one pending approval action. Contains: invoice identifier, approval step name, approver identifier or name, and the date the invoice entered this approval step. Optionally: a step completion date. Grain: one row per invoice per pending approval step. In some systems this is a separate workflow table; in others, the pending step and entry date are columns on the invoice header itself.

**Optional — enables Invoice Lookup Mode 2 PO matching:**

- An invoice line table where each row is one line item on a supplier invoice. Contains: invoice header identifier, purchase order reference (PO number or line reference), adjustment flag (boolean), adjustment reason text, optional line memo. Grain: one row per invoice line.

- A purchase order header table: PO identifier, supplier identifier, supplier name, PO status, total PO amount in local and reporting currency, transaction currency, payment terms, invoicing status. Grain: one row per PO.

- A purchase order line table: PO identifier, line identifier, description, line amount (local), amount invoiced to date, uninvoiced amount, line payment status, cancellation flag, requester. Grain: one row per PO line.

**Optional — enables Cash & Bank Balances section:**

- A bank account balance or treasury cash table: account identifier, account name, company or legal entity, currency, balance in local currency, balance in reporting currency, balance date. Grain: one row per account per snapshot (or current-state row per account). If unavailable, section is skipped.

**Optional — enables Invoice Intake Trend section:**

- An invoice intake or processing pipeline table: date, count of invoices received into the intake queue, count pushed or approved into the ERP system, count pending in the queue. Grain: one row per day. If the organization does not use a separate AP intake system, section is skipped.

**Optional — enables Team Performance section:**

- A processor assignment or invoice processing table that can be used to identify who processed each invoice and when. May be the invoice header table itself (with a processor/assignee column and a processed date), or a separate activity table. Used to compute processing volume, cycle time, and sent-back rate per processor. If processor assignment data is not available, section is skipped.

**Optional — enables Support Ticket Queue section:**

- An AP support ticket or service request table: ticket identifier, status (active / on-hold / closed), assignee, created date, closed date, ticket category or type. Grain: one row per ticket. If no AP ticket system is connected, section is skipped.
