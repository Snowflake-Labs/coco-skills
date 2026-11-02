# Golden Test Cases: ar_tracker

6 test cases covering all routing modes, DSO degraded path, multi-currency handling, and single-account drill-down. Run against the hydrated ar_tracker skill and score with eval metrics.

---

## Case 1: Full Dashboard — All Sections

**Input:**
```
Show me our AR health
```

**Expected behavior:**
- Routes to Full Dashboard mode
- Asks (once) whether a collector assignment table exists; answer: "no" for this case
- Runs all 4 required sections in parallel: AR Health Headline, Aging Breakdown, Collection Rate MTD, Top 10 Overdue Accounts
- Discovers and runs optional sections based on hydration: Disputes (if available), Bad Debt Candidates, P2P (if available)
- Synthesizes single dashboard output under the defined output format
- DSO formula shown explicitly in headline

**Guard checks:**
- [ ] G1: Hydration sources execute without error
- [ ] G2: AR aging or invoice table resolved
- [ ] G3: Due date column identified and used for aging (not invoice date)
- [ ] G4: Amount column in reporting currency found and summed
- [ ] G5: No fabricated tables or columns

**Key assertions:**
- Output includes all five aging buckets with $ balances (no empty/missing buckets)
- DSO appears in headline with formula shown (numerator ÷ denominator × days)
- Top 10 Overdue Accounts table contains customer names and ranked overdue balances
- Collection rate shows which formula was used (with or without AR opening balance)
- Zero-balance accounts not present in aging or overdue tables
- Optional sections either render with data or show "unavailable" message; no silent omissions

---

## Case 2: DSO Focus

**Input:**
```
What is our DSO?
```

**Expected behavior:**
- Routes to Full Dashboard mode (DSO is a section of the dashboard, not a standalone query)
- DSO section displayed prominently at top of output
- Formula shown: Total Open AR ÷ (trailing 90-day revenue or billed amount ÷ 90)
- Rest of dashboard follows

**Guard checks:** G1–G5 pass

**Key assertions:**
- DSO formula explicitly stated in output (not just a number)
- If revenue table was used: no "estimated" flag
- If billed amount was used as proxy: "DSO (estimated — using billed amount as revenue proxy)" label present
- DSO denominator > 0 (trailing 90-day period has invoiced amounts)
- No cross-currency DSO if multi-currency account

---

## Case 3: Top Overdue Accounts Only

**Input:**
```
Who are our top overdue accounts?
```

**Expected behavior:**
- Routes to Overdue Accounts section only (not full dashboard)
- Runs Query Type 4 only
- Returns up to 10 accounts with: account name, overdue balance, oldest invoice days, total open balance
- Sorted by overdue balance descending

**Guard checks:** G1–G5 pass

**Key assertions:**
- Only accounts with days_past_due > 0 appear (no current-only accounts)
- Ranked by overdue balance, not total balance
- Table has correct columns (name, overdue balance, oldest days, total open)
- Row count is ≤ 10
- If fewer than 10 overdue accounts exist, all are shown (no padding with current accounts)

---

## Case 4: DSO Degraded — No Revenue Table

**Input:**
```
Show me our AR health
```
*(Hydration context: revenue/billing table not found during hydration)*

**Expected behavior:**
- Full dashboard runs normally
- DSO falls back to estimated path: Total Open AR ÷ (invoiced in trailing 90 days ÷ 90)
- Output labels DSO clearly as "estimated"
- If trailing 90-day invoiced amount is also unavailable: DSO omitted with message "DSO unavailable — revenue or billing table not found"
- All other sections run normally

**Guard checks:** G1–G5 pass (G5 specifically: no invented revenue table)

**Key assertions:**
- "DSO (estimated — using billed amount as revenue proxy)" label present in output
- OR "DSO unavailable" message present if both paths fail
- No fabricated DSO number if denominator is unavailable
- Sections 2–4 (aging, collection rate, top overdue) unaffected by missing revenue table

---

## Case 5: Multi-Currency Portfolio

**Input:**
```
AR health
```
*(Hydration context: AR table contains invoices in USD and EUR; no reporting-currency translation column)*

**Expected behavior:**
- Skill detects multiple currency values in the amount column
- Does NOT produce a single consolidated total (no cross-currency sum)
- Renders separate aging tables for USD and EUR (or per-currency sections)
- DSO computed per currency separately, or omitted for currencies where revenue denominator is unavailable
- Top 10 Overdue Accounts table grouped or labeled by currency

**Guard checks:** G1–G5 pass

**Key assertions:**
- No single combined AR total that mixes USD + EUR
- Output explicitly notes that results are shown per currency
- Aging breakdown appears twice (or once per currency) with currency labels
- No DSO that divides USD AR by EUR revenue

---

## Case 6: Single Account Drill-Down

**Input:**
```
What does Acme Corp owe us?
```

**Expected behavior:**
- Routes to Single Account Drill-Down mode (Query Type 6)
- Fuzzy match on "Acme Corp" against account_name column
- If one match: show all open invoices for that account
- If multiple matches: present options before proceeding
- If no match: report "No account found matching 'Acme Corp'" and suggest closest names

**Guard checks:** G1–G5 pass

**Key assertions:**
- Account-level invoices listed with: invoice ID, invoice date, due date, amount, days_past_due, status
- Invoices ordered by days_past_due descending (most overdue first)
- If payment activity table exists: last 5–10 payments shown for the account
- If dispute table exists: open disputes shown for the account
- Zero-balance invoices excluded (paid invoices not shown in open AR list)
- Total open balance for the account shown as a summary at the top

---

## Scoring Summary

For each case, evaluate:
1. **Routing**: Correct mode detected from trigger phrase
2. **Binding**: Correct table and column resolved (AR aging vs invoice+payment path)
3. **Query**: Appropriate query type executed (aging from due_date, DSO formula correct, overdue ranked correctly)
4. **Optional handling**: Missing optional tables noted with messages; not silently omitted
5. **Output**: Complete and matches defined format (headlines, tables, labels)
6. **Guardrails**: No cross-currency sums, no fabricated DSO, aging uses due date, zero-balance accounts excluded
