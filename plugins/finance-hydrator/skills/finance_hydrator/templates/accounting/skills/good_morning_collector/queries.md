# Query Reference — Good Morning Collector (Dehydrated)

All physical table/column references are resolved at runtime from `hydration_hints.yaml`. Reference SQL below uses `{{ar_fqn}}`, `{{cols.*}}` and `{{settings.*}}` placeholders filled from that file. `:collector` is the resolved collector value, always a bound parameter.

**Parameter binding (all user-supplied search text):** never concatenate user text into SQL. Escape `\`, `%` and `_` in the user's term, then bind it:
`{{cols.customer}} ILIKE '%' || :search || '%' ESCAPE '\\'`

**"Yesterday"** = the prior business day (Monday → Friday; holiday calendar if bound, else weekends only — disclosed).

---

## Data Shape (resolved at runtime)

**Primary sources:**
- AR aging table (one row per account per snapshot_date, or one current-state row per account)
- (Optional) Daily collections table (one row per payment received)
- (Optional) Payment plan table (one row per active plan)
- (Optional) Semantic view over AR aging for Cortex Analyst follow-up

---

## Query Type 1 — Portfolio Aging Summary

**Purpose**: Aging bucket totals for the collector's portfolio.

**Logic**:
1. Filter AR table by collector_assignment = <resolved collector>
2. GROUP BY aging bucket — canonical buckets shared with `ar_tracker`: Current (≤ 0), 1–30, 31–60, 61–90, 90+ days past due date
3. SUM(balance), COUNT(DISTINCT account_id) per bucket
4. For delta: compare current snapshot vs the prior business day's snapshot (same query, different date filter)

**Output grain**: one row per aging bucket (5 rows total)

**Reference SQL (portfolio aging; use `{{cols.aging_bucket}}` directly instead of the CASE when it is bound):**

```sql
SELECT
  CASE
    WHEN DATEDIFF(day, {{cols.due_date}}, CURRENT_DATE()) <= 0  THEN '1. Current'
    WHEN DATEDIFF(day, {{cols.due_date}}, CURRENT_DATE()) <= 30 THEN '2. 1-30'
    WHEN DATEDIFF(day, {{cols.due_date}}, CURRENT_DATE()) <= 60 THEN '3. 31-60'
    WHEN DATEDIFF(day, {{cols.due_date}}, CURRENT_DATE()) <= 90 THEN '4. 61-90'
    ELSE '5. 90+'
  END                                   AS aging_bucket,
  {{cols.currency}}                     AS currency,
  SUM({{cols.balance}})                 AS balance,
  COUNT(DISTINCT {{cols.customer_id}})  AS accounts
FROM {{ar_fqn}}
WHERE {{cols.collector_id}} = :collector
  AND {{cols.balance}} > 0
GROUP BY 1, 2
ORDER BY 2, 1;
```

Report credit balances (`{{cols.balance}} < 0`) as a separate "Unapplied cash / credits" line (see Pitfalls).

---

## Query Type 2 — Yesterday's Collections

**Purpose**: Cash received yesterday.

**Logic**:
1. Filter collections table by payment_date = prior business day (Monday → Friday) AND account in collector's portfolio
2. SUM(payment_amount), COUNT(DISTINCT account_id)
3. MAX(payment_amount) for "largest payment"

**Output grain**: single summary row

---

## Query Type 3 — Today's Priority List

**Purpose**: Top 5 accounts to call based on composite risk score. **This is the single authoritative priority formula** — SKILL.md references it; no other variant is used.

**Priority formula** (per account):

```
priority_score = balance
               * SQRT(GREATEST(days_past_due, 0))
               * LN(COALESCE(days_since_last_contact, :never_contacted_days) + 1)
```

- `balance` = account open balance; `days_past_due` = days past due of the account's oldest open invoice
- `:never_contacted_days` (setting, default 90) is used when there is no last-contact date
- Accounts not yet past due score 0 and therefore never outrank past-due accounts

**Logic**:
1. Filter AR table by collector_assignment AND balance > 0 AND days_past_due > 0
2. Compute `priority_score` with the formula above
3. ORDER BY priority_score DESC LIMIT 5
4. Include: account_name, balance, days_past_due, last_contact_date

**Output grain**: 5 rows ranked by priority score

**Reference SQL:**

```sql
WITH accounts AS (
  SELECT
    {{cols.customer_id}}                                       AS customer_id,
    ANY_VALUE({{cols.customer}})                           AS account_name,
    SUM({{cols.balance}})                                      AS balance,
    MAX(DATEDIFF(day, {{cols.due_date}}, CURRENT_DATE()))      AS days_past_due,
    MAX({{cols.last_contact_date}})                            AS last_contact_date
  FROM {{ar_fqn}}
  WHERE {{cols.collector_id}} = :collector
    AND {{cols.balance}} > 0
  GROUP BY 1
)
SELECT
  account_name, balance, days_past_due, last_contact_date,
  balance
    * SQRT(GREATEST(days_past_due, 0))
    * LN(COALESCE(DATEDIFF(day, last_contact_date, CURRENT_DATE()),
                  :never_contacted_days) + 1)                  AS priority_score
FROM accounts
WHERE days_past_due > 0
ORDER BY priority_score DESC
LIMIT 5;
```

---

## Query Type 4 — Open Disputes

**Purpose**: Dispute status summary.

**Logic**:
1. Filter AR table by collector_assignment AND dispute_flag = true
2. COUNT(*), SUM(dispute_amount)
3. For "new yesterday": filter additionally by dispute_opened_date = prior business day
4. For "approaching SLA": dispute_sla_date between today and the 3rd business day after today (same business-day logic and reference SQL as `ar_tracker` Query Type 5)

**Output grain**: summary counts + flagged items

---

## Query Type 5 — Single Account Drill-Down (on-demand)

**Purpose**: When user asks about a specific account by name.

**Logic**:
1. Filter AR table by account name using the bound-parameter form `account_name ILIKE '%' || :search || '%' ESCAPE '\\'` (never concatenate user text); if several accounts match, ask the user to pick one
2. Return all aging/dispute/payment details for that account
3. If collections table exists, show last 10 payments

**Output grain**: full account profile

---

## Query Type 6 — Due This Week

**Purpose**: Open invoices due during the current calendar week (Monday–Friday), plus any currently overdue invoices, labeled by urgency. The core daily prioritization list.

**Logic**:
1. Filter AR table to open invoices (balance > 0, not paid/voided)
2. Include invoices where due_date falls between Monday of current week AND Friday of current week
3. Also include invoices where due_date < CURRENT_DATE() (overdue — these are always shown)
4. On Fridays, extend the look-ahead through the following Monday to avoid an empty weekend list
5. If a P2P date column exists: invoices with a P2P date this week take sort priority over plain due date
6. If collector assignment is available: filter to the collector's portfolio. If not: show all open accounts.
7. Assign urgency label per invoice:
   - P2P date = today or tomorrow → `'P2P: <weekday>'` (e.g., `'P2P: Wed'`)
   - due_date < CURRENT_DATE() → `'Overdue'`
   - due_date = CURRENT_DATE() → `'Due Today'`
   - due_date = tomorrow → `'Due Tomorrow'`
   - due_date within current week → day name (e.g., `'Thursday'`)
   - due_date next calendar week → `'Next Week'`
8. ORDER BY: P2P date (ascending, nulls last), then effective due date ascending, then balance descending
9. Columns: account_name, invoice_id, outstanding_balance, due_date, urgency

**Output grain**: one row per open invoice, ordered by urgency

---

## Query Type 7 — Monthly Collection Progress (MTD)

**Purpose**: Month-to-date collection rate for the collector's portfolio (or full AR portfolio if no collector assignment). Shows how much cash has been collected against what was invoiced this month.

**Logic**:
1. Cash collected MTD = SUM(payment_amount WHERE payment_date >= first day of current month)
   — filtered to collector's accounts if collector assignment is available
2. Amount invoiced MTD = SUM(invoice_amount WHERE invoice_date >= first day of current month)
   — same portfolio filter
3. If an AR opening balance at month start is available:
   - Rate = Cash collected MTD ÷ (AR opening balance + Amount invoiced MTD) × 100
4. If not available (fallback):
   - Rate = Cash collected MTD ÷ Amount invoiced MTD × 100
5. Note which formula was used in the output
6. If prior-month data is available, compute the same metrics for the prior calendar month and show the delta (e.g., "+3.2 pp vs last month")

**Output grain**: single summary row — MTD collected $, MTD invoiced $, collection rate %, and prior-month comparison if available

---

## Pitfalls

1. **Aging = days past DUE date, not invoice date** — a common confusion. If the table has both, use due_date for aging.
2. **Collector assignment changes** — some systems reassign mid-month. Use the current assignment, not historical.
3. **Weekend effect** — "yesterday" is the prior business day everywhere in this skill: on Monday it is Friday. Use the holiday calendar if bound; otherwise weekends only, disclosed.
4. **Multi-currency portfolios** — if a collector handles USD + EUR accounts, group by currency in the summary. Don't sum across currencies.
5. **Zero-balance accounts** — exclude from aging summary (they paid, they're done). Credit balances (unapplied cash, open credit memos) are shown as a separate line, as in `ar_tracker`, not silently dropped.
