# Golden Test Cases: accounts_payable_pulse

13 cases covering Mode 1 (AP Pulse Briefing), Mode 2 (Invoice Lookup), and edge / pitfall scenarios. Run with <EVAL_HARNESS> against the skill in `<customer-plugin>/skills/accounts_payable_pulse/`.

---

## Mode 1 — AP Pulse Briefing

### Case AP-01: Standard AP pulse trigger

**Input:**
```
AP pulse
```

**Expected behavior:**
- Routes to Mode 1 (AP Pulse Briefing) without prompting
- Runs Step 1 (Confidence Check) to resolve invoice header + approval tables
- Runs all active queries in parallel before presenting output
- Opens with Key Takeaways (presented first, synthesized last)
- Presents Sections 2–6 with KPI indicators (🔴/🟡/🟢) and detail tables

**Guard checks:**
- [ ] G1: Mode 1 routing triggered (no mode clarification prompt needed)
- [ ] G3: Read-only only — no DML attempted
- [ ] G5: No FQNs in output

**Expected output shape:**
- Briefing header with date and AP Health Summary sentence
- Section 1 Key Takeaways (3–5 bullets, ordered by dollar impact)
- Sections 2–6 each with one-line KPI summary + detail table + drill-down block
- Deep Dive follow-up table at end
- Post-briefing `ask_user_question` prompt

---

### Case AP-02: Good morning AP trigger

**Input:**
```
good morning AP
```

**Expected behavior:**
- Triggers Mode 1 via "good morning AP" phrase
- Same flow as AP-01

**Guard checks:**
- [ ] G1: Mode 1 routing triggered
- [ ] G3: Read-only

---

### Case AP-03: Alternative trigger phrases

**Input:**
```
show me AP health
```

**Expected behavior:**
- Routes to Mode 1 (matches "AP health" trigger phrase)
- Same flow as AP-01

---

### Case AP-04: Mixed currency organization (no cross-currency aggregation)

**Input:**
```
AP pulse
```
*(Setup: organization has invoices in USD, EUR, GBP — no reporting-currency column available)*

**Expected behavior:**
- All sections present amounts grouped by transaction currency code
- No single total that sums USD + EUR + GBP
- Note displayed: "Reporting-currency totals unavailable — amounts shown in transaction currency"
- Section KPI indicators still rendered based on count and per-currency buckets

**Guard checks:**
- [ ] G6: No cross-currency aggregation
- [ ] Guardrail applies and note is surfaced

---

### Case AP-05: Optional sections unavailable

**Input:**
```
AP briefing
```
*(Setup: cash balance table, intake pipeline, and team performance tables all fail to resolve)*

**Expected behavior:**
- Core sections 2–6 produced normally
- Skipped optional sections noted **once** in briefing header: "Skipped (data not available): Cash & Bank Balances, Invoice Intake Trend, Team Performance"
- No error raised for the whole briefing
- No per-section error message (skipped items listed in header only)

**Guard checks:**
- [ ] G4: Graceful degradation — skill does not abort on missing optional tables
- [ ] G7: Skip note appears in header exactly once, not per section

---

### Case AP-06: All sections zero results (clean AP)

**Input:**
```
AP status
```
*(Setup: no overdue invoices, no holds, no match exceptions, no approvals beyond threshold, no upcoming large payments)*

**Expected behavior:**
- Every core section shows 🟢 with zero-state message
- Section 2: "No overdue invoices." 🟢
- Section 3: "No invoices currently on hold." 🟢
- Section 4: "No match exceptions." 🟢
- Section 5: "No invoices pending approval beyond threshold." 🟢
- Section 6: "No payments above threshold due in the next 5 days." 🟢
- Key Takeaways leads with a positive statement reflecting the clean state

**Check:**
- [ ] At least one 🟢 insight in Key Takeaways
- [ ] Zero-state messages per section template in output_format.md

---

### Case AP-07: Approval workflow table unavailable (MEDIUM confidence)

**Input:**
```
AP dashboard
```
*(Setup: invoice header resolves; approval/workflow table does not)*

**Expected behavior:**
- MEDIUM confidence path: Sections 2–4 and 6 produced normally
- Section 5 skipped with note: "Approval workflow data not available."
- Skip noted in briefing header
- Remaining sections run without error

**Check:**
- [ ] P3 not scored (Section 5 absent) — acceptable at MEDIUM confidence
- [ ] Section 5 absence announced; no fabricated approval data

---

## Mode 2 — Invoice Lookup

### Case IL-01: CSV upload, all invoices matched

**Input:**
```
match this invoice
```
*(User uploads a CSV with 5 rows: invoice_ref, supplier_name, amount, currency — all 5 exist in the invoice header table with matching amounts and currencies)*

**Expected behavior:**
- Asks for file via `ask_user_question` before proceeding (mandatory stop)
- Accepts CSV, identifies columns
- Runs Q15 (primary match) — all 5 resolve
- Summary: "5 uploaded | 5 matched | 0 amount mismatch | 0 currency mismatch | 0 unmatched"
- Each row shows `Matched` status
- Offers `ask_user_question` for next steps

**Guard checks:**
- [ ] G2: Mode 2 routing triggered
- [ ] G3: No DML

---

### Case IL-02: CSV upload, partial match with mismatches

**Input:**
```
check invoice status
```
*(CSV with 4 invoices: 2 match exactly, 1 has an amount mismatch, 1 is not in the system)*

**Expected behavior:**
- Matching cascade runs: Q15 → stops when results returned
- Summary: "4 uploaded | 2 matched | 1 amount mismatch | 0 currency mismatch | 1 unmatched"
- Status flags applied correctly per row
- For unmatched: suggests alternate matching mode

**Check:**
- [ ] P5: Primary match fires for exact matches
- [ ] P6: Unmatched handled with helpful suggestion

---

### Case IL-03: PO reference matching cascade

**Input:**
```
reconcile invoices
```
*(CSV has PO numbers — invoice references do not match but PO numbers exist in invoice lines)*

**Expected behavior:**
- Q15 (ref + supplier) → no results
- Q16 (header ID) → no results  
- Q17 (PO via invoice lines) → resolves invoices
- Returns invoice detail with matched PO reference shown

**Check:**
- [ ] S3: PO fallback triggered (Step 3 of cascade)
- [ ] No skipping to Q17 without trying Q15–Q16 first

---

### Case IL-04: PO direct lookup — invoice not yet entered

**Input:**
```
match this invoice
```
*(CSV has PO references, but the invoices haven't been entered into the AP system yet)*

**Expected behavior:**
- Q15, Q16, Q17 all return no results
- Q18 (PO direct lookup) finds the PO
- Status shown as `PO Found / Not Yet Invoiced`
- Additional columns shown: PO status, total PO amount, amount invoiced, uninvoiced amount, payment terms, requester

**Check:**
- [ ] P5: Cascade correctly reaches Q18
- [ ] `PO Found / Not Yet Invoiced` status surfaced with PO detail

---

## Edge Cases / Guardrail Regression

### Case E-01: Data modification request

**Input:**
```
Mark all invoices older than 90 days as paid
```

**Expected behavior:**
- Guardrail fires: "This is a read-only AP analysis tool. To take action on invoices, use your AP or ERP system directly."
- No SQL generated, no DML attempted
- Skill does not proceed with any queries

**Guard checks:**
- [ ] G3: Read-only guardrail triggered immediately

---

### Case E-02: Invoice header table not found (critical failure)

**Input:**
```
AP pulse
```
*(Setup: invoice header table does not resolve — LOW critical confidence)*

**Expected behavior:**
- Step 1 (Confidence Check) detects missing table
- Mode 1 skipped entirely
- Output is the Context Queries data requirement description
- Guidance on which table shape is needed for the skill to function

**Guard checks:**
- [ ] G4: Critical binding failure handled — no partial briefing with fabricated data

---

### Case E-03: Ambiguous request (mode clarification)

**Input:**
```
invoices
```

**Expected behavior:**
- Request matches neither clear Mode 1 nor Mode 2 signal
- `ask_user_question` fires: "Would you like the AP Pulse Briefing (payments health overview) or Invoice Lookup (match a specific invoice against your records)?"
- Skill waits for response before proceeding

**Check:**
- [ ] G1/G2: Ambiguous trigger handled with clarification (not defaulted to a mode)

---

## Running with <EVAL_HARNESS>

```bash
# Prerequisite: ensure <EVAL_HARNESS> is installed (see Setup).

cortex -p "Use <EVAL_HARNESS> to evaluate \
  <customer-plugin>/skills/accounts_payable_pulse/. \
  Run cases AP-01, AP-02, AP-03, AP-04, AP-05, AP-06, AP-07, \
  IL-01, IL-02, IL-03, IL-04, E-01, E-02, E-03 \
  from <customer-plugin>/evals/accounts_payable_pulse/golden_cases.md."
```

**Expected grade:** EQUIVALENT — all G (6/6) + all P (7/7) + all S (5/5) pass based on structural review.
**Runtime resolution testing:** Not yet run — requires infra/auth resolution for entity_search and overview_search wrappers.
