# Golden Test Cases: flux_analysis

6 test cases covering all 3 workflow modes + commentary generation + dimension probe + theme extraction. Run against the hydrated flux_analysis skill and score with eval metrics.

---

## Case 1: Single Account — IS Activity (MoM)

**Input:**
```
Run flux analysis for Outside Services, account 710001, MoM
```

**Expected behavior:**
- Routes to Single Account Mode
- Resolves IS activity table binding
- Asks for current and prior period (or infers from fiscal calendar)
- Runs dimension probe (expects supplier/vendor to score highest)
- Runs activity flux query grouped by top dimensions
- Generates commentary using Template C (Vendor/Supplier List) or Template A (Enumerated)
- Commentary follows "why not what" principle
- Produces summary table + top drivers + Excel

**Guard checks:**
- [ ] G1: Hydration succeeded (IS table resolved)
- [ ] G2: Fiscal calendar joined correctly
- [ ] G3: Amount column identified and summed
- [ ] G5: No fabricated data

**Key assertions:**
- Dimension probe returns supplier/vendor as top dimension
- Commentary mentions specific vendor names from data
- Summary table shows materiality assessment

---

## Case 2: Single Account — BS Balance (QoQ)

**Input:**
```
What changed in Prepaid Expenses quarter over quarter?
```

**Expected behavior:**
- Routes to Single Account Mode
- Resolves BS balance table binding
- Uses point-in-time balance comparison at quarter-end dates
- Dimension probe likely returns sparse results (BS accounts often have fewer populated dimensions)
- Falls back to theme extraction from journal descriptions
- Generates commentary using Template E (Timing/Accrual) or Template H (Net Movement)
- If driver unclear, flags for controller

**Guard checks:** G1-G5 pass

**Key assertions:**
- Uses balance (not activity) query family
- Filters to quarter-end dates only
- Theme extraction produces meaningful categories
- Handles sparse dimensions gracefully

---

## Case 3: Use Case Mode — Multi-Account

**Input:**
```
Run flux for the Accrued Expenses use case, MoM, current month vs prior month
```

**Expected behavior:**
- Routes to Use Case Mode
- Runs enumeration query to list all accounts in the use case
- Displays master summary table with per-account materiality
- Processes material accounts in parallel
- Generates per-account commentary + use case total
- Produces Excel with master table

**Guard checks:** G1-G5 pass

**Key assertions:**
- Multiple accounts enumerated (typically 5-20 for an accrual use case)
- Materiality gate correctly applied (some pass, some fail)
- Always-comment list checked
- Material accounts get full commentary
- Immaterial accounts show "Below threshold"
- Use case total commentary generated
- Excel output contains all accounts

---

## Case 4: Batch All Mode

**Input:**
```
Run all use cases, full close run, QoQ
```

**Expected behavior:**
- Routes to Batch All Mode
- Enumerates all configured use cases
- Processes each sequentially
- Produces cross-use-case summary at end
- Generates consolidated Excel workbook

**Guard checks:** G1-G5 pass

**Key assertions:**
- Mode correctly detected from trigger phrase
- All configured use cases processed (or graceful handling if config not available)
- Cross-use-case summary identifies top 5 largest variances
- Flagged-for-controller count reported

---

## Case 5: Commentary Quality — "Why Not What"

**Input:**
```
Explain the variance in Employee Expenses for March vs February
```

**Expected behavior:**
- Routes to Single Account or Use Case Mode (depending on whether "Employee Expenses" is a use case or account)
- Generates commentary that explains WHY (business reason)
- Does NOT just restate numbers

**Guard checks:** G1-G5 pass

**Key assertions:**
- Commentary references specific dimension values (cost centers, comp components, or headcount changes)
- Commentary does NOT contain patterns like "X increased $Y (Z%)" as the sole explanation
- If data is sparse/unclear: outputs "Driver unclear — flag for controller review"
- Amounts in commentary match query results exactly (Q2 quality rule)
- No percentages in commentary prose (Q4 quality rule)

---

## Case 6: Dimension Probe + Theme Extraction Fallback

**Input:**
```
Analyze account 120200 (Restricted Cash) QoQ
```

**Expected behavior:**
- Routes to Single Account Mode
- Resolves BS balance table
- Runs dimension probe — expects sparse results (cash accounts typically have limited dimensional data)
- Falls back to theme extraction from journal descriptions
- Identifies business themes (e.g., investment distributions, letter of credit releases, reclassifications)
- Applies netting rules if applicable (e.g., reclassification in/out nets to zero)
- Generates commentary using themes as drivers
- If no clear themes: "Driver unclear — flag for controller review"

**Guard checks:** G1-G5 pass

**Key assertions:**
- Dimension probe correctly identifies sparse data condition
- Theme extraction runs as fallback
- Themes are business-meaningful (not raw text fragments)
- Netting rules applied where applicable
- Commentary references themes by name
- Amount coverage noted (what % of variance is explained by identified themes)

---

## Scoring Summary

For each case, evaluate:
1. **Routing**: Correct mode detected
2. **Binding**: Correct table resolved (IS vs BS)
3. **Query**: Appropriate query type executed (activity vs balance, with correct dimensions)
4. **Commentary**: Follows patterns, quality rules, "why not what" principle
5. **Output**: Complete (summary table + commentary + Excel)
6. **Guardrails**: No hallucinated drivers, no cross-currency sums, fiscal calendar respected
