---
name: good_morning_collector
description: >
  Daily AR collector briefing: portfolio aging summary, collections progress,
  dispute status, debook exposure, P2P status, and priority actions for today.
  Personalized to the logged-in collector. Triggers: morning summary, daily AR
  update, collection priorities, good morning collector, my portfolio, what should
  I work on today.
dehydrated: true
data_domain: "accounts receivable collections, AR aging, disputes, payment plans, collector portfolio"
created_date: 2026-05-19
version: 0.1.0
license: Proprietary. See LICENSE.txt for complete terms
---

# Good Morning Collector

## Intent

Personalized daily briefing for AR collectors. Shows: aging buckets for their assigned portfolio, yesterday's collections, open disputes, debook candidates, payment plan (P2P) status, and the top 5 priority accounts for today's calls. Designed to replace the morning routine of running 3-4 aging reports and cross-referencing dispute logs.

This skill is for daily operational planning. Not for: strategic AR analytics (use `dso_aging_analytics`), account reconciliation, or invoice-level lookups.

## Behavior

### Routing Logic

**MANDATORY CHECKPOINT — Identify the collector before any query.**

Determine who the collector is:
- If the user's identity can be resolved from the session (e.g., current_user maps to a collector name), use it automatically.
- If the session user doesn't map to a known collector, ask via `ask_user_question`: "Which collector portfolio should I brief? [list of active collectors]"
- If a specific account or customer is named, switch to single-account drill-down mode (see Query Type 5).

### Workflow

1. **Resolve collector identity** (see Routing Logic above).

2. **Run all sections in parallel** — each section hits the same underlying AR table but with different filters/aggregations:
   - **Portfolio aging summary** — current, 30, 60, 90, 120+ day buckets with balance and account count per bucket. Include delta vs prior day.
   - **Yesterday's collections** — total cash received, number of accounts that paid, largest payment.
   - **Open disputes** — count and total $ of disputed invoices, new disputes opened yesterday, disputes approaching SLA.
   - **Debook exposure** — accounts where balance > threshold AND age > 120 days AND no activity in last 60 days. These are write-off candidates.
   - **P2P status** — active payment plans: on-track vs overdue vs completed this week.
   - **Today's priority list** — top 5 accounts by composite score: balance × days_overdue × days_since_last_contact.
   - **Due This Week** — open invoices due Monday–Friday of current week plus all overdue invoices, urgency-labeled (P2P date > Overdue > Due Today > Due Tomorrow > weekday name).
   - **Monthly collection progress** — MTD cash collected, MTD invoiced, and collection rate. Uses opening-balance formula if available; otherwise falls back to collected ÷ invoiced MTD.

3. **Synthesize 1-page briefing** under 500 words. Lead with portfolio health headline (total AR, % current, % 90+).

### Query Construction Rules

**Collector filtering** (always required):
- Filter all queries by the collector assignment column matching the resolved collector identity
- Never show another collector's accounts unless explicitly asked

**Aging bucket computation**:
- Current = not yet past due
- 30/60/90/120+ = days past due date, not days since invoice date
- If aging is pre-computed in the table (bucket column), use it directly
- If raw dates, compute: `DATEDIFF(day, due_date, CURRENT_DATE())`

**Priority scoring** (for today's priority list):
- Composite = balance × SQRT(days_overdue) × LOG(COALESCE(days_since_last_contact, 90) + 1)
- NULL last_contact_date is treated as 90 days (maximum priority — never contacted)
- Rank descending, take top 5
- This formula weights large balances AND neglected accounts

**Delta computation** (portfolio aging vs prior day):
- Compare today's snapshot to yesterday's snapshot (or use a table with snapshot_date for time-travel)
- Show the movement: "2 accounts moved from 60→90 bucket yesterday"

### Confidence Handling

| Confidence | Behavior |
|---|---|
| HIGH (all columns resolve, collector identity maps) | Full briefing with all sections |
| MEDIUM (aging resolves but dispute or P2P columns missing) | Briefing without dispute/P2P sections; note "dispute data unavailable" |
| No collector assignment column found | Fall back to full-portfolio mode — show all accounts; note "Showing full portfolio — collector assignment not configured" |
| LOW on critical (no AR aging table) | Skip skill; output guidance to add hydration hints |

### Output Format

```
# Good Morning, <Collector Name>

**Portfolio health:** $<total AR> across <N> accounts. <X%> current, <Y%> 90+ days.

## Aging Summary
| Bucket | Balance | Accounts | Δ Yesterday |
|--------|---------|----------|-------------|
| Current | $X | N | +/- |
| 30 days | $X | N | +/- |
| 60 days | $X | N | +/- |
| 90 days | $X | N | +/- |
| 120+ days | $X | N | +/- |

## Yesterday's Wins
- Collected $<amount> from <N> accounts
- Largest: <Account> paid $<amount>

## Due This Week (+ Overdue)
| Account | Invoice | Balance | Due Date | Urgency |
|---------|---------|---------|----------|---------|
| ...     |         |         |          |         |

## Monthly Progress
- Collected $X MTD (<Z%> collection rate, <formula note>)
- Still outstanding this month: $X

## Today's Priority (call these first)
1. <Account> — $<balance>, <days> days overdue, last contact <date>
2. ...

## Open Disputes (<N>, $<total>)
- <N> new yesterday | <N> approaching SLA deadline
- Largest: <Account> — $<amount>, opened <date>

## Debook Candidates (<N>, $<total>)
- <Account> — $<balance>, 120+ days, no activity since <date>

## Payment Plans
- <N> active | <N> on track | <N> overdue | <N> completed this week
```

### Guardrails

- Never expose customer SSN, bank account, or payment instrument details
- Display all amounts in the customer's configured currency
- If a collector has 0 accounts (new hire or reassignment), say so clearly rather than showing empty tables
- Dispute SLA dates should be highlighted if within 3 business days

### Error Handling

| Scenario | Action |
|---|---|
| Collector not found in assignment column | Ask user to clarify; suggest available collector names |
| No snapshot_date column (can't compute delta) | Omit the "Δ Yesterday" column; note "daily delta unavailable" |
| P2P table doesn't exist | Skip Payment Plans section silently |
| Zero accounts in 90+ bucket | Celebrate: "No accounts in 90+ — strong portfolio health." |

## Context Queries

- A table at one row per invoice (or one row per account per snapshot date) with columns:
  account ID, account name, invoice ID, invoice date, due date, outstanding balance, payment status,
  aging bucket (current/30/60/90/120+ days past due) or raw dates to compute it,
  and optionally: last payment date, last contact date, dispute flag, dispute amount,
  payment plan status, snapshot date.
  Grain: one row per invoice, or one row per (account, snapshot_date) for delta computation.
  The collector assignment column may be on this table directly, or on a separate customer dimension
  table (e.g., a `collector_name` or `assigned_collector` column joinable via account_id).

- (Optional) A daily collections activity table: one row per payment received with
  account ID, payment amount, payment date, payment method. Used for "Yesterday's Wins" and
  "Monthly Progress" sections.

- (Optional) A payment plan (P2P) table: one row per active plan with account ID,
  plan start date, monthly amount, next due date, status (on_track/overdue/completed).

- (Optional) A semantic view over the AR aging table for Cortex Analyst follow-up
  questions like "why did account X move to 90+ bucket?"
