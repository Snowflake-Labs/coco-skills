# Output Format Reference — AP Pulse

Templates, KPI guidance, status flag definitions, and export layout for both skill modes.

---

## KPI Indicator Rules

All thresholds below are **guidance defaults**. Customers should calibrate to their own AP SLA commitments. Numeric defaults are starting points only — no hardcoded dollar amounts.

### Section 2 — Overdue Invoices

| Indicator | Guidance |
|---|---|
| 🔴 | Any invoice is 90+ days overdue, OR total overdue amount exceeds a material threshold for the organization |
| 🟡 | Invoices in the 31–90 day range present, none in the 90+ bucket |
| 🟢 | All overdue items are within 30 days and below the organization's tolerance |

### Section 3 — Invoices on Hold

| Indicator | Guidance |
|---|---|
| 🔴 | Any invoice has been on hold for more than 14 days, OR total hold amount exceeds materiality threshold |
| 🟡 | Holds present, all within 7–14 days |
| 🟢 | Zero holds, or all holds are recent (< 7 days) with resolution in progress |

### Section 4 — Match Exceptions

| Indicator | Guidance |
|---|---|
| 🔴 | Any exception has been open more than 10 days, OR total exception amount exceeds threshold |
| 🟡 | Exceptions present, all within 5–10 days |
| 🟢 | Zero exceptions |

### Section 5 — Approval Pipeline

| Indicator | Guidance |
|---|---|
| 🔴 | Any invoice has been pending a single step for more than 7 days, OR more than 20% of the queue exceeds the aging threshold |
| 🟡 | Some items exceed the aging threshold (default: 3 days) but none exceed 7 days |
| 🟢 | All pending approvals within the threshold |

### Section 6 — Large Upcoming Payments

| Indicator | Guidance |
|---|---|
| 🔴 | Any large payment is due today or tomorrow |
| 🟡 | Large payments due within 2–5 days |
| 🟢 | No large payments within the lookahead window |

---

## Briefing Header

```
# AP Pulse — {DAY}, {MONTH} {DATE}, {YEAR}

> **AP Health Summary:** {1-2 sentence pulse — total overdue amount, number of holds,
  largest approval bottleneck, next large payment due date}

**Active sections:** {list}
{if optional sections skipped} *Skipped (data not available):* {list}

---
```

Skipped sections are noted once in the header — not repeated per section.

---

## Section 1: Key Takeaways (shown first)

```
## Key Takeaways

{🔴|🟡|🟢} **[Headline with specific metric — e.g., "$3.1M overdue 90+ days from 14 suppliers"]**
[One-sentence recommended action — concrete next step]

{🔴|🟡|🟢} **[Second insight — different section or cross-section connection]**
[Action]

{🔴|🟡|🟢} **[Third insight]**
[Action]

[Optional 4th and 5th insight — only if material]

{🟢} **[At least one positive signal — e.g., "Match exception count at 0 this week"]**
[Acknowledgement — no action needed]

---
```

---

## Section 2: Overdue Invoices

```
## Overdue Invoices  {KPI_INDICATOR}

**{count} invoices | {total_amount} total overdue**

### By Age Bucket

| Age Bucket | Count | Total Amount | Oldest Invoice |
|---|---|---|---|
| 90+ days | {n} | {amount} | {days} days |
| 61–90 days | {n} | {amount} | {days} days |
| 31–60 days | {n} | {amount} | {days} days |
| 1–30 days | {n} | {amount} | {days} days |

### By Payment Term

| Payment Term | Count | Total Amount | Avg Days Overdue | Max Days Overdue |
|---|---|---|---|---|
| {term} | {n} | {amount} | {avg}d | {max}d |
| ... | | | | |

> 🔍 Drill-down:
> - "Show all overdue [specific term] invoices"
> - "Show invoices overdue more than 60 days"
> - "Which suppliers have the most overdue invoices?"
```

---

## Section 3: Invoices on Hold

```
## Invoices on Hold  {KPI_INDICATOR}

**{count} invoices on hold | {total_amount} total**

| Hold Reason | Count | Total Amount | Oldest (days) |
|---|---|---|---|
| {reason} | {n} | {amount} | {days} |
| ... | | | |

> 🔍 Drill-down:
> - "Show all invoices on hold for [top reason from data]"
> - "Show holds older than 14 days"
> - "Which supplier has the most invoices on hold?"
```

Zero-hold state:
```
## Invoices on Hold  🟢

**No invoices currently on hold.**
```

---

## Section 4: Match Exceptions

```
## Match Exceptions  {KPI_INDICATOR}

**{count} invoices with match exceptions | {total_amount} total**

| Exception Reason | Count | Total Amount | Oldest (days) |
|---|---|---|---|
| {reason} | {n} | {amount} | {days} |
| ... | | | |

> 🔍 Drill-down:
> - "Show all invoices with [top exception reason from data]"
> - "Show exceptions older than 10 days"
> - "Which supplier has the most exceptions?"
```

---

## Section 5: Approval Pipeline

```
## Approval Pipeline  {KPI_INDICATOR}

**{total_pending} invoices pending | {count_overdue} pending more than {threshold} days**

| Approval Step | Pending | Over {threshold}d | Max Days | Total Amount |
|---|---|---|---|---|
| {step_name} | {n} | {n_over} | {max}d | {amount} |
| ... | | | | |

> 🔍 Drill-down:
> - "Show all invoices stuck in [top step name from data]"
> - "Show approvals pending more than 7 days"
> - "Who is approving the [step name] step?"
```

---

## Section 6: Large Upcoming Payments

```
## Large Upcoming Payments  {KPI_INDICATOR}
*(next {N} days | threshold: {threshold or "not configured"})*

**{count} payments | {total_amount} total due**

| Supplier | Due Date | Days Until Due | Amount | Currency | Reporting CCY Amt | Entity |
|---|---|---|---|---|---|---|
| {supplier} | {date} | {n} | {amount} | {ccy} | {reporting_amt} | {entity} |
| ... | | | | | | |

> 🔍 Drill-down:
> - "Show all payments due this week"
> - "Show payments from [supplier name from data]"
> - "Break down by legal entity"
```

Zero-result state:
```
## Large Upcoming Payments  🟢

**No payments above threshold due in the next {N} days.**
```

---

## Optional Sections

### Cash & Bank Balances

```
## Cash & Bank Balances
*(latest available snapshot)*

| Account | Entity | Currency | Balance (Local) | Balance (Reporting CCY) |
|---|---|---|---|---|
| {account} | {entity} | {ccy} | {local} | {reporting} |
| ... | | | | |
```

### Invoice Intake Trend (7 days)

```
## Invoice Intake Trend  (last 7 days)

| Date | Received | Processed | Pending |
|---|---|---|---|
| {date} | {n} | {n} | {n} |
| ... | | | |
```

### Team Performance (last 30 days)

```
## Team Performance

| Processor | Invoices | Avg Cycle Time | Sent-Back Rate |
|---|---|---|---|
| == TEAM TOTAL == | {n} | {avg}d | {pct}% |
| {name} | {n} | {avg}d | {pct}% |
| ... | | | |

*Sent-back rate > {threshold}% flagged 🔴 (threshold configurable per org)*
```

### Support Ticket Queue

```
## AP Support Ticket Queue

**{total_open} open tickets**

| Status | Total | 0–1d | 2–3d | 4–7d | 8–14d | 14+d | Avg Age |
|---|---|---|---|---|---|---|---|
| {status} | {n} | {n} | {n} | {n} | {n} | {n} | {avg}d |
| ... | | | | | | | |
```

---

## Deep Dive Section

```
---

## Deep Dive — Suggested Follow-Up Questions

| Topic | Ask |
|---|---|
| Overdue | "Show all invoices from [top overdue supplier name from data] that are overdue" |
| Overdue | "Show invoices overdue more than 90 days" |
| Holds | "Why are invoices on hold for [top hold reason from data]?" |
| Exceptions | "Show all exceptions of type [top exception reason from data]" |
| Approvals | "Show everything stuck in [top step name from data]" |
| Payments | "Show all payments from [specific supplier] due this week" |
| Invoice Lookup | "Let me upload an invoice to check its status" |
```

Replace bracketed values with **actual data values** from the query results — real supplier names, real reason text, real step names.

---

## Invoice Lookup Result Table

```
## Invoice Lookup Results

**{total} uploaded | {matched} matched | {mismatch} amount mismatch | {currency} currency mismatch | {unmatched} unmatched**

| Uploaded Ref | System Ref | Supplier (System) | Amt (Uploaded) | Amt (System) | CCY Match | Payment Status | Status |
|---|---|---|---|---|---|---|---|
| {ref} | {sys_ref} | {supplier} | {amt_up} | {amt_sys} | ✅/❌ | {status} | {flag} |
| ... | | | | | | | |
```

**Status flags:**
- `Matched` — found in system; amount and currency agree
- `Amount Mismatch` — found; amount differs
- `Currency Mismatch` — found; currency differs
- `Unmatched` — not found in system after all matching steps
- `PO Found / Not Yet Invoiced` — PO exists; invoice not yet entered

For PO direct lookup results, add: PO Number | PO Status | PO Total | Amount Invoiced | Uninvoiced | Payment Terms | Requester.

---

## Excel Export Layout

### AP Pulse Export (`ap_pulse_{YYYY-MM-DD}.xlsx`)

| Sheet | Contents |
|---|---|
| Summary | Run date, active sections, KPI indicators, count + total per section, Key Takeaways text |
| Overdue | Full overdue detail (Q2 output) |
| On Hold | Full on-hold detail (Q4 output) |
| Exceptions | Full exception detail (Q6 output) |
| Approval Aging | Invoices pending beyond threshold (Q8 output) |
| Large Payments | Upcoming large payments (Q9 output) |
| [optional sheets] | One sheet per active optional section with section name as tab name |

### Invoice Lookup Export (`invoice_lookup_{YYYY-MM-DD}.xlsx`)

| Sheet | Contents |
|---|---|
| Summary | Run date, match counts by status flag, top unmatched suppliers |
| Matched | Invoices with Matched status |
| Mismatches | Amount Mismatch + Currency Mismatch invoices |
| Unmatched | Unmatched invoices |
| PO Lookup | Invoices resolved via PO direct lookup |
| All Results | Complete result set |

---

## Currency Formatting

- Use locale-standard formatting: `$1,234,567.89`
- Abbreviate millions: `$1.2M` when amount ≥ 1,000,000
- Always include currency code as a column when amounts span multiple currencies
- Never aggregate across currency codes without a reporting-currency column

## Row Limits

- Chat display: 15 rows maximum per section detail table
- If result set > 15 rows: "Showing top 15 of {N} — ask to filter by supplier / reason / date"
- Excel export: all rows, no limit
