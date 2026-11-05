# Query Reference — AR Tracker

All physical table/column references are resolved at runtime against the customer's Snowflake account. This file describes query types and logic patterns, plus reference SQL snippets with `{{placeholders}}` filled from `hydration_hints.yaml`. No FQNs.

**Parameter binding (all user-supplied search text):** never concatenate user text into SQL. Escape `\`, `%` and `_` in the user's term, then bind it:
`{{cols.account_name}} ILIKE '%' || :search || '%' ESCAPE '\\'`

---

## Data Shape (resolved at runtime)

**Primary sources:**
- AR aging table (one row per invoice or one row per account per snapshot date, with amounts and due dates)
- OR: Invoice header table + payments/cash receipts table (skill computes aging from due_date vs today)

**Optional sources:**
- Revenue / billing table (for DSO denominator)
- Collections / payment activity table (for MTD collection rate and cash receipt metrics)
- Dispute table (for Section 5)
- Payment plan / P2P table (for Section 8)
- Collector assignment table or column (for Section 6)

---

## Query Type 1 — AR Health Summary

**Purpose**: Single-row summary: Gross open AR, Unapplied cash / credits, Net AR, account count, invoice count, % current, % overdue, % 90+, and DSO.

**Logic**:
1. Filter AR table to open items only: balance ≠ 0, status not paid / void (keep open credit memos and unapplied cash — they carry credit balances)
2. COUNT(DISTINCT account_id) and COUNT(DISTINCT invoice_id) for totals (debit items)
3. Gross open AR = SUM(balance WHERE balance > 0); Unapplied cash / credits = −SUM(balance WHERE balance < 0) plus any bound unapplied-cash table; Net AR = Gross − Unapplied
4. Compute % current: SUM(balance WHERE not_yet_overdue) / SUM(balance) × 100
5. Compute % overdue: SUM(balance WHERE days_past_due > 0) / SUM(balance) × 100
6. Compute % 90+: SUM(balance WHERE days_past_due > 90) / SUM(balance) × 100
7. For DSO:
   - Preferred path: Net AR / (SUM(revenue in trailing 90 days) / 90)
   - Fallback path: Net AR / (SUM(invoiced in trailing 90 days) / 90)
   - Always output the basis: "DSO on Net AR; denominator = <revenue | billings>, 90 days"
   - If denominator is zero or no data: omit DSO

**Output grain**: single summary row (one per currency if no reporting-currency column)

**Reference SQL (headline AR figures):**

```sql
SELECT
  {{cols.currency}}                                             AS currency,
  SUM(IFF({{cols.balance}} > 0, {{cols.balance}}, 0))           AS gross_open_ar,
  -SUM(IFF({{cols.balance}} < 0, {{cols.balance}}, 0))          AS unapplied_cash_and_credits,
  SUM({{cols.balance}})                                         AS net_ar,
  COUNT(DISTINCT IFF({{cols.balance}} > 0, {{cols.account_id}}, NULL)) AS open_accounts,
  COUNT(DISTINCT IFF({{cols.balance}} > 0, {{cols.invoice_id}}, NULL)) AS open_invoices
FROM {{ar_fqn}}
WHERE {{cols.balance}} <> 0
  AND UPPER(COALESCE({{cols.status}}, '')) NOT IN ({{settings.excluded_statuses}})
GROUP BY 1;
```

---

## Query Type 2 — Aging Buckets

**Purpose**: Distribution of open AR across five aging buckets with counts and optional delta. These buckets are the canonical AR aging definition shared with `good_morning_collector`.

**Logic**:
1. Filter AR table to open debit items (balance > 0) for the buckets; report credit balances (balance < 0) as a separate "Unapplied cash / credits" line rather than dropping them
2. If a pre-computed aging bucket column exists, use it directly
3. If no bucket column, compute: DATEDIFF(day, due_date, CURRENT_DATE()) → assign to bucket
   - Current: computed_days <= 0 (not yet past due)
   - 1–30 days: computed_days BETWEEN 1 AND 30
   - 31–60 days: computed_days BETWEEN 31 AND 60
   - 61–90 days: computed_days BETWEEN 61 AND 90
   - 90+ days: computed_days > 90
4. GROUP BY bucket: SUM(balance), COUNT(DISTINCT account_id), COUNT(DISTINCT invoice_id)
5. For delta: if the table has a snapshot_date column, run the same query for yesterday's (or prior period's) snapshot and compute change in balance per bucket

**Output grain**: five rows (one per bucket), ordered Current → 90+

---

## Query Type 3 — Collection Rate MTD

**Purpose**: Month-to-date collection rate and weekly cash pulse.

**Logic for collection rate**:
1. Cash collected MTD = SUM(payment_amount WHERE payment_date >= first day of current month)
2. Amount invoiced MTD = SUM(invoice_amount WHERE invoice_date >= first day of current month)
3. If AR opening balance at month start is available:
   - Rate = Cash collected MTD / (AR opening balance + Amount invoiced MTD) × 100
4. If not available:
   - Rate = Cash collected MTD / Amount invoiced MTD × 100
5. Note which formula was used in the output

**Logic for cash received this week**:
1. SUM(payment_amount WHERE payment_date >= Monday of current calendar week)
2. COUNT(DISTINCT account_id) that made payments this week

**Output grain**: single summary row for MTD rate + single summary row for this week

---

## Query Type 4 — Top Overdue Accounts

**Purpose**: Rank customers by total overdue balance (past-due invoices only) to surface the highest-priority collection targets.

**Logic**:
1. Filter AR table to invoices WHERE days_past_due > 0 (or due_date < CURRENT_DATE())
2. GROUP BY account_id / account_name
3. SUM(balance) as overdue_balance per account
4. MAX(days_past_due) as oldest_invoice_days per account
5. Also compute total open balance per account (including current invoices) with a second pass or subquery
6. ORDER BY overdue_balance DESC
7. LIMIT 10

**Output grain**: 10 rows, one per customer, ranked by overdue balance

---

## Query Type 5 — Dispute Summary

**Purpose**: Dispute exposure overview. Only runs if dispute table hydrates successfully.

**Logic**:
1. COUNT(*) and SUM(dispute_amount) for all open disputes
2. New disputes this week = filter by dispute_opened_date >= Monday of current week, COUNT and SUM
3. Approaching SLA = dispute_sla_date between today and the **3rd business day** after today, COUNT and SUM. Use the bound fiscal / holiday calendar if available; otherwise exclude weekends only and disclose that holidays were not considered (see reference SQL below)
4. Optional: GROUP BY dispute_status to show breakdown by status type

**Output grain**: summary counts and amounts; optional status breakdown

**Reference SQL (SLA within N business days):**

```sql
WITH business_days AS (
  -- Preferred: bound holiday / fiscal calendar
  --   SELECT {{cols.calendar_date}} AS d FROM {{calendar_fqn}}
  --   WHERE {{cols.is_business_day}} AND {{cols.calendar_date}} > CURRENT_DATE()
  -- Fallback (no calendar bound): weekdays only — disclose in output
  SELECT d FROM (
    SELECT DATEADD(day, ROW_NUMBER() OVER (ORDER BY SEQ4()), CURRENT_DATE()) AS d  -- gap-free
    FROM TABLE(GENERATOR(ROWCOUNT => 30))
  )
  WHERE DAYOFWEEKISO(d) < 6
),
sla_cutoff AS (
  SELECT d AS cutoff_date
  FROM business_days
  QUALIFY ROW_NUMBER() OVER (ORDER BY d) = :sla_business_days   -- default 3
)
SELECT COUNT(*) AS disputes_near_sla, SUM(x.{{cols.dispute_amount}}) AS amount_near_sla
FROM {{dispute_fqn}} x
CROSS JOIN sla_cutoff c
WHERE x.{{cols.dispute_sla_date}} BETWEEN CURRENT_DATE() AND c.cutoff_date;
```

---

## Query Type 6 — Single Account Drill-Down (on-demand)

**Purpose**: Full AR profile for a specific customer when user names an account. Triggered by routing logic, not part of the standard dashboard.

**Logic**:
1. Filter AR table by account name using the bound-parameter form `account_name ILIKE '%' || :search || '%' ESCAPE '\\'` (user text escaped and bound — never concatenated into the SQL string). If several accounts match, ask the user to pick one
2. Return all open invoices for that account: invoice ID, invoice date, due date, amount, days_past_due, status
3. If payment activity table exists: show last 5–10 payments for the account (payment date, amount)
4. If dispute table exists: show any open disputes for the account
5. ORDER invoices by days_past_due DESC

**Output grain**: per-invoice rows for one customer

---

## Query Type 7 — Bad Debt / Write-off Candidates

**Purpose**: Identify accounts that are likely uncollectible based on age and payment inactivity.

**Logic**:
1. Primary path (if payment activity table exists):
   - Filter AR table: days_past_due > 90
   - LEFT JOIN to payment activity: get MAX(payment_date) per account
   - Filter: DATEDIFF(day, MAX(payment_date), CURRENT_DATE()) > 60 OR no payment record at all
   - GROUP BY account: SUM(balance), MAX(days_past_due), MAX(payment_date)
2. Fallback path (aging data only):
   - Filter AR table: days_past_due > 90
   - GROUP BY account: SUM(balance), MAX(days_past_due)
   - Return top 10 by balance
3. ORDER BY balance DESC

**Output grain**: per-account rows for write-off candidates, top 10

---

## Query Type 8 — Payment Plan (P2P) Status

**Purpose**: Overview of active payment plans by status. Only runs if P2P table hydrates successfully.

**Logic**:
1. GROUP BY plan_status (on_track / overdue / completed): COUNT(*) and SUM(scheduled_payment_amount)
2. Filter completed to current month: plan_status = 'completed' AND completion_date >= first day of current month
3. Compute coverage ratio: SUM(scheduled_amount for on_track + overdue plans) / total overdue AR from Query Type 1

**Output grain**: one row per status bucket (3 rows) + coverage ratio

---

## Pitfalls

1. **Aging uses due date, not invoice date** — A common and costly confusion. An invoice issued 90 days ago with net-60 terms is only 30 days overdue. Always compute aging from due_date. If only invoice_date exists and payment terms are available, estimate due_date = invoice_date + payment_terms_days. If neither is available, flag the aging as approximate.

2. **DSO denominator must match AR numerator in currency** — Never compute DSO by dividing USD AR by EUR revenue. If a company operates in multiple currencies, either use the reporting-currency columns throughout, or compute DSO per currency and report separately.

3. **"Current" means not yet past due, not "recently invoiced"** — An invoice issued yesterday with net-30 terms is current but will appear in the 1–30 bucket in 30 days. Do not conflate invoice age with aging bucket.

4. **Multi-currency portfolios** — If the AR table contains invoices in multiple currencies, never SUM across currencies. Group by currency column and display each currency as a separate table. If a reporting-currency amount column exists (e.g., all amounts translated to USD), use that column for aggregation.

5. **Payment activity date ambiguity** — Use the payment received or deposit date, not the payment applied or posted date. These can differ by days or weeks in some ERP systems. The deposit date is what matters for collection rate and cash receipt metrics.

6. **Zero-balance invoices** — Always exclude invoices with balance = 0 (fully paid) from aging and overdue calculations. Including them inflates account counts and distorts percentages. Do **not** use `balance > 0` as the open-item filter for totals — it silently drops credit memos and unapplied cash; use `balance <> 0` and split debit / credit balances.

7. **Open vs closed invoices** — Some AR systems keep all invoices in one table with a status column (open / closed / void / credit). Always filter to open/unpaid status before any aggregation. Voided invoices should never appear in AR totals. Open credit memos and unapplied cash are reported as Unapplied cash / credits and reduce Net AR; fully applied credits (balance = 0) drop out naturally.
8. **Business days** — "Within N business days" uses the bound fiscal / holiday calendar; if none is bound, exclude weekends only and disclose it. Never substitute calendar days.
