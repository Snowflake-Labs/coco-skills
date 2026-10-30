---
name: ar_tracker
description: >
  Company-level AR health dashboard. DSO, aging breakdown, collection rate,
  top overdue accounts, dispute exposure, and bad debt candidates.
  Triggers: AR health, accounts receivable status, DSO, aging report, how is
  our AR, collection rate, overdue accounts, AR dashboard, AR summary,
  AR tracker, what is our DSO, track AR, AR overview.
dehydrated: true
data_domain: "accounts receivable, AR aging, invoices, payments, collections, DSO, overdue accounts, disputes, payment plans, bad debt"
created_date: 2026-05-28
version: 0.1.0
license: Proprietary. See LICENSE.txt for complete terms
---

# AR Tracker

## Intent

Company-level AR health dashboard for CFOs, Controllers, and AR Managers. Covers: total AR balance, DSO, aging distribution, MTD collection rate, top overdue accounts, and optional modules for disputes, bad debt candidates, P2P (payment plan) status, and collector breakdown.

This skill is for company-wide AR health monitoring. Not for: collector-level work queues and daily call lists (use `good_morning_collector`), invoice-level transaction lookups, journal entries, or reconciliation.

## Behavior

### Routing Logic

Detect user intent first, before any queries:

| Trigger | Mode |
|---|---|
| "AR health", "how is our AR", "AR summary", "AR tracker", "AR overview" | Full dashboard — all sections |
| "DSO", "days sales outstanding" | Full dashboard; DSO section prominently displayed first |
| "top overdue", "who owes us the most", "largest overdue" | Overdue accounts section only |
| Named customer or account | Single-account drill-down (Query Type 6) |
| "by collector", "per collector", "collector breakdown" | Full dashboard + Section 6 collector breakdown (if collector assignment data available) |
| Ambiguous | Run full dashboard by default |

### Workflow

1. **Ask about collector assignment** (once per session, on first invocation only):
   - Ask via `ask_user_question`: "Do you have a collector-to-account assignment table or column? (This enables a per-collector breakdown in the dashboard.)"
   - If yes → include Section 6 (Collector Breakdown) using the resolved collector assignment binding
   - If no → omit Section 6; all sections remain company-wide
   - Store answer in session context so subsequent invocations skip this question

2. **Assess optional section availability** from hydration results:
   - Dispute section (Section 5) → include if dispute table hydrated successfully
   - P2P section (Section 8) → include if payment plan table hydrated successfully
   - Bad debt section (Section 7) → always attempt; falls back to aging-only logic if no payment activity table
   - Delta column in aging table (Section 2) → include if historical snapshot data available

3. **Run all available sections in parallel** — each section is independent.

4. **Synthesize dashboard** — headline + sections. Mark unavailable optional sections with a brief note rather than silently omitting them.

### Section Specifications

#### Section 1 — AR Health Headline (always present)

Show: total open AR ($), count of open accounts, count of open invoices, % current (not yet past due), % overdue (any bucket past due), % 90+ days past due, and DSO.

**DSO formula** (always state the formula used in output):
- Preferred: Total Open AR ÷ (Revenue in trailing 90 days ÷ 90)
- Fallback if revenue table missing: Total Open AR ÷ (Total invoiced in trailing 90 days ÷ 90). Flag output as "DSO (estimated — using billed amount as revenue proxy)."
- If neither revenue nor invoiced amount available for trailing period: omit DSO; note "DSO unavailable — revenue or billing table not found."

#### Section 2 — Aging Breakdown (always present)

Five buckets: Current (not yet past due), 1–30, 31–60, 61–90, 90+ days past due date.

Per bucket: $ balance, account count, invoice count, and delta vs prior snapshot (if historical snapshot data available).

Aging uses **due date**, not invoice date. If the table has both, use due date. If only invoice date exists, compute estimated due date using payment terms (if available) or flag aging as approximate.

#### Section 3 — Collection Rate MTD (always present)

- Primary: Cash collected this month ÷ (AR balance at start of month + amount invoiced this month) × 100
- Fallback if AR opening balance unavailable: Cash collected this month ÷ Amount invoiced this month × 100
- Also show: Cash received this week (for near-term pulse)
- State which formula was used in the output

#### Section 4 — Top 10 Overdue Accounts (always present)

Accounts with any balance past due (due date < today), ranked by overdue balance descending. Limit to 10 rows.

Per account: account name, total overdue balance, oldest invoice age (days), total open balance (including current).

#### Section 5 — Dispute Exposure (optional — requires dispute table)

Total $ in dispute, count of disputed invoices. New disputes opened this week. Disputes approaching SLA deadline (within 3 business days).

If dispute table not found: show "Dispute data unavailable — dispute table not resolved."

#### Section 6 — Collector Breakdown (optional — requires collector assignment)

Only shown if collector assignment confirmed available (see Workflow step 1).

Per collector: total portfolio AR ($), overdue balance ($), % overdue, % 90+ days. Sort by overdue balance descending.

#### Section 7 — Bad Debt / Write-off Candidates (optional — inferred from aging + payment activity)

Invoices 90+ days past due with no payment activity in the last 60 days. Group by customer; show: customer name, balance, last payment date (or "never paid"), days since last contact (if available).

If payment activity table not found: use aging data alone — show all accounts with 90+ day balance above a meaningful threshold (top 10 by balance).

#### Section 8 — Payment Plan (P2P) Status (optional — requires P2P table)

Active payment plans by status: on-track / overdue / completed this month. % of total overdue AR covered by active P2P plans.

If P2P table not found: show "Payment plan data unavailable — P2P table not resolved."

### Confidence Handling

| Confidence | Behavior |
|---|---|
| HIGH — AR aging table or invoice+payment tables resolved; DSO computable | Full dashboard with all available sections |
| MEDIUM — AR aging resolved but revenue/billing table missing | Dashboard with estimated DSO; label clearly as estimated |
| LOW on critical — No AR table found | Skip skill execution; output data requirements and recommended configuration |

### Guardrails

- Never sum amounts across different currencies. If multiple currencies are detected, group by currency throughout and display each currency separately. Do not produce a single consolidated total.
- Aging always uses due date, not invoice date. State this in Section 2 output.
- DSO formula is always shown explicitly in output (numerator, denominator, days) so the customer can validate the calculation.
- Zero-balance accounts are excluded from all sections (they are settled).
- Never attempt to filter by collector unless the user confirmed a collector assignment source exists.
- If an account has $0 overdue but a large current balance, it appears in Section 4 only if it has a past-due component; do not surface current-only accounts in the overdue section.
- Weekend / holiday effect on "this week" metrics: "this week" means Monday through the current day of the current calendar week.

### Error Handling

| Scenario | Action |
|---|---|
| No AR aging table found and no invoice table found | Stop; output message explaining what data is needed and recommended table structure |
| DSO denominator is zero (no revenue or invoiced amount in trailing 90 days) | Omit DSO; note "DSO unavailable — no revenue data in trailing 90 days" |
| No overdue accounts found | Show "No overdue accounts — clean portfolio." Do not show an empty table. |
| All AR is current | Show positive note in headline; still render aging breakdown with all balance in Current bucket |
| Collector asked for but assignment table not resolved | Note "Collector breakdown unavailable — collector assignment not resolved" |
| Query returns no rows for an optional section | Show section header with "No data" rather than silently omitting |

### Output Format

```
# AR Health — <Company or Account Name> | <Date>

**$<total AR>** open across <N> accounts (<M> invoices).
DSO: <X> days (<formula used>).
<Y%> current | <Z%> overdue | <W%> 90+ days.

## Aging Breakdown
| Bucket      | Balance | Accounts | Invoices | Δ Prior Snapshot |
|-------------|---------|----------|----------|------------------|
| Current     | $X      | N        | N        | +/- or n/a       |
| 1–30 days   | $X      | N        | N        | +/-              |
| 31–60 days  | $X      | N        | N        | +/-              |
| 61–90 days  | $X      | N        | N        | +/-              |
| 90+ days    | $X      | N        | N        | +/-              |

## Collection Rate (Month-to-Date)
- Collected $X this month (<Z%> collection rate, <formula note>)
- Cash received this week: $X from <N> accounts

## Top 10 Overdue Accounts
| Rank | Account   | Overdue Balance | Oldest Invoice | Total Open |
|------|-----------|-----------------|----------------|------------|
| 1    | Account A | $X              | N days         | $X         |
...

## Dispute Exposure  [omit section if unavailable]
- $X across <N> invoices in dispute
- <N> new this week | <N> approaching SLA deadline

## Collector Breakdown  [omit section if not requested or unavailable]
| Collector | Portfolio AR | Overdue | % Overdue | % 90+ |
|-----------|-------------|---------|-----------|-------|
...

## Bad Debt Candidates
- <N> accounts, $X | 90+ days past due, no payment in 60+ days
- Top candidates: <Account A> ($X, last payment <date>), ...

## Payment Plans  [omit section if unavailable]
- <N> active | <N> on track | <N> overdue | <N> completed this month
- Active P2P covers <Z%> of total overdue AR
```

## Context Queries

- A table with one row per open invoice (or one row per account per snapshot date) containing: customer or account identifier, customer or account name, invoice amount in reporting currency, invoice due date, and either a pre-computed aging bucket (Current / 30 / 60 / 90 / 120+ days past due) or a days-past-due numeric column. This is the core AR aging or AR detail table. Grain: one row per invoice, or one row per (account, snapshot_date). This binding is required; the skill cannot run without it.

- (Alternate primary) An invoice header table with one row per invoice, containing: invoice identifier, customer identifier, customer name, invoice date, due date, invoice total amount in reporting currency, payment status (paid / unpaid / partial / void). Combined with a payments or cash receipts table (one row per payment applied: invoice reference, payment amount, payment date). The skill derives aging from due_date vs CURRENT_DATE() when this alternate path is used.

- (Optional — for DSO) A revenue recognition or billing table with one row per revenue event or invoice: recognition or billing date, amount in reporting currency. Used to compute trailing 90-day revenue for the DSO denominator. Without this, DSO is estimated using billed invoice amounts.

- (Optional — for collection rate and cash receipt metrics) A collections or payment activity table with one row per payment received: payment date, payment amount in reporting currency, customer or account reference, and optionally invoice reference. Used for MTD collection rate and weekly cash receipt metrics.

- (Optional — for dispute section) A dispute or invoice exception table with one row per disputed invoice: invoice reference, disputed amount, dispute opened date, dispute status, and optionally SLA date. If absent, Section 5 is omitted.

- (Optional — for P2P section) A payment plan or promise-to-pay table with one row per active plan: account reference, plan start date, scheduled payment amount, next due date, plan status (on_track / overdue / completed). If absent, Section 8 is omitted.

- (Optional — for collector breakdown) A collector assignment table or column mapping account identifiers to collector names. May be a standalone table (one row per account with a collector name column) or a column present on the AR aging table itself. If absent, Section 6 is omitted.

## References

- [queries.md](queries.md) — Natural-language query type descriptions (no SQL, no FQNs)
