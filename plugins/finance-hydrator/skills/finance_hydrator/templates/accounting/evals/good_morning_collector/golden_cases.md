# Golden Test Cases: good_morning_collector

6 test cases covering the full briefing, no-collector-assignment fallback, priority-only routing, missing optional sections, Monday morning edge case, and single-account drill-down. Run against the hydrated good_morning_collector skill.

---

## Case 1: Full Briefing — Collector Identified

**Input:**
```
Good morning, give me my briefing
```
*(Session: collector asks for their own briefing; collector name provided when prompted: "Alex")*

**Expected behavior:**
- Prompts for collector name via `ask_user_question`
- Fuzzy-matches "Alex" against collector assignment column (or customer name list)
- Runs all sections in parallel: Portfolio Aging, Yesterday's Collections, Due This Week, Monthly Progress, Priority List
- Optional sections: Disputes (if available), Debook Candidates, P2P Status
- Synthesizes 1-page briefing under 500 words

**Key assertions:**
- Output opens with `# Good Morning, Alex` and today's date
- Portfolio Snapshot shows 5 aging buckets with balances and account counts
- Yesterday's Collections section present (even if $0 — shows "None found")
- Due This Week section: all open invoices due Mon–Fri with urgency labels; overdue invoices also shown
- Monthly Progress section: MTD collection rate shown with formula label
- Today's Priority list: top 5 accounts by composite score, all overdue
- Optional sections either render with data or show explicit "unavailable" message
- Zero-balance invoices excluded from all sections

---

## Case 2: No Collector Assignment Column — Full Portfolio Fallback

**Input:**
```
Good morning, give me my briefing
```
*(Hydration context: AR table (INVOICES) and customer table (CUSTOMERS) have no collector assignment column)*

**Expected behavior:**
- Skill attempts to resolve collector identity
- No collector assignment column found → falls back to full-portfolio mode
- All queries run without a collector filter
- Output note: "Showing full portfolio — collector assignment not configured"
- Briefing renders normally for the entire portfolio

**Key assertions:**
- "Showing full portfolio — collector assignment not configured" note present
- Aging breakdown covers all accounts (not filtered to a subset)
- Due This Week includes invoices for all accounts
- No error thrown; no empty output
- Skill does NOT stop or request hydration hints just because collector column is missing

---

## Case 3: Priority List Only — "What should I work on today?"

**Input:**
```
What should I work on today?
```

**Expected behavior:**
- Routes to priority list section only (not full briefing)
- Runs Query Type 3 only
- Returns top 5 accounts by composite score: `balance × SQRT(days_overdue) × LOG(days_since_last_contact + 1)`
- Where `last_contact_date` is unavailable, treats as 90 days (maximum priority)
- If fewer than 5 overdue accounts: shows all available (no padding with current accounts)

**Key assertions:**
- Output contains only the priority list (no aging summary, no collections section)
- Top 5 rows (or fewer if portfolio has fewer overdue accounts)
- Composite score logic applied (not sorted by simple balance)
- Each entry shows: account name, balance, days overdue, last contact date (or "never contacted")
- Only accounts from the 61–90 and 90+ buckets appear (past due only)

---

## Case 4: Dispute Section When Dispute Data Unavailable

**Input:**
```
Show me my disputes
```
*(Hydration context: no dispute flag column in INVOICES; no separate dispute table)*

**Expected behavior:**
- Skill recognizes this as a dispute-focused query
- Attempts to find dispute data → not found
- Output: shows "Dispute data unavailable — dispute table or flag column not resolved"
- Does NOT silently return an empty table
- Offers to show priority list or due-this-week as an alternative

**Key assertions:**
- "Dispute data unavailable" message present
- No fabricated dispute count or amount
- Skill does not crash; handles gracefully

---

## Case 5: Monday Morning — "Yesterday" is Friday

**Input:**
```
Good morning collector briefing
```
*(Date context: run on a Monday)*

**Expected behavior:**
- "Yesterday's Collections" section pulls payments from the most recent business day (Friday)
- NOT payments from Sunday (which would likely be $0)
- Skill correctly identifies that Monday's "yesterday" = Friday for collections purposes
- Due This Week covers the current week (Monday–Friday)

**Key assertions:**
- Collections section shows Friday's payments, not Sunday
- Date filter for "yesterday" uses business-day logic: `DATEADD(day, -3, CURRENT_DATE())` on Monday
- Due This Week section covers full Mon–Fri of current week
- No empty collections section just because "yesterday" was a weekend

---

## Case 6: Single Account Drill-Down

**Input:**
```
What does Diamond Realty Holdings owe?
```

**Expected behavior:**
- Routes to Single Account Drill-Down mode (Query Type 5)
- Fuzzy-matches "Diamond Realty Holdings" against account_name column
- Returns all open invoices for that account: invoice ID, invoice date, due date, outstanding balance, days past due, status
- If payment activity table exists: shows last 5–10 payments
- If dispute data exists: shows open disputes for the account
- Invoices ordered by days past due descending (most overdue first)
- Shows total open balance at top as headline

**Key assertions:**
- One account matched: "Diamond Realty Holdings"
- Open invoices listed with INVOICE_ID, INVOICE_DATE, DUE_DATE, OUTSTANDING_BALANCE, days_past_due, PAYMENT_STATUS
- Ordered by days_past_due DESC (most overdue first)
- PARTIAL invoice shows outstanding balance (not original amount)
- Payment history shown: 1 payment ($21,200 wire, 2026-04-18)
- Headline shows total open balance for the account
- Paid (zero-balance) invoices excluded

---

## Scoring Summary

For each case, evaluate:
1. **Routing**: Correct mode detected from trigger phrase
2. **Binding**: Correct tables and columns resolved (INVOICES, CUSTOMERS, PAYMENTS)
3. **Query**: Correct logic applied (aging from DUE_DATE, priority score formula, due-this-week date range)
4. **Optional handling**: Missing optional sections noted; no silent omissions, no fabrication
5. **Output**: Complete and matches defined format (sections, tables, labels)
6. **Guardrails**: No cross-currency sums, zero-balance excluded, collector filter applied (or full-portfolio fallback noted)
