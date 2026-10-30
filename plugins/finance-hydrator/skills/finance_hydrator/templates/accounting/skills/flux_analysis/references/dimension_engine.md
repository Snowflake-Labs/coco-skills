# Dimension Engine — Flux Analysis

## Purpose

Before running flux queries, the skill must determine which dimensions provide meaningful drill-down for the specific account being analyzed. The wrong dimensions produce commentary with blank values or a single dominant catch-all bucket — both useless for the controller.

The dimension engine uses a 3-layer priority system to select the best dimensions, with a fallback to theme extraction when no dimensions resolve well.

---

## Layer 1: Account Category Inference

Infer the type of account from its category (Asset, Liability, Revenue, Expense, etc.) to narrow the candidate dimension set before any data is queried.

**IMPORTANT: Chart of accounts numbering is NOT standardized.** There is no universal GAAP or IFRS requirement for how account numbers are structured. Companies use different prefix schemes (1xx, 1000-1999, alphanumeric, etc.) depending on their ERP system (Workday, SAP, Oracle, NetSuite) and internal conventions.

The account-to-category mapping is resolved at runtime by discovering the customer's chart of accounts table (see Context Queries in SKILL.md), which typically contains an account type or category column that classifies each account as Asset, Liability, Equity, Revenue, or Expense. If no chart of accounts table is found, the skill asks the customer to provide their classification rule during setup.

Once the account category is known, the following natural dimension associations apply:

| Account Category | Statement | Natural Dimensions |
|---|---|---|
| Asset | Balance Sheet | Legal entity, counterparty, clearing source, project |
| Liability | Balance Sheet | Vendor, contract timing, accrual type, legal entity |
| Equity | Balance Sheet | Instrument type — rarely needs flux |
| Revenue | Income Statement | Customer, product type, revenue category, region |
| Cost of Sales | Income Statement | Vendor, platform component, cost type |
| Operating Expense | Income Statement | Vendor, department/cost center, spend category |
| Allocations/Other | Income Statement | Cost center, allocation method, intercompany entity |
| Non-Operating | Income Statement | Instrument type, legal entity, tax jurisdiction |

This layer produces a **candidate list** of 3-5 dimensions likely to be relevant based on the economic nature of the account. It does NOT guarantee they exist or are populated in the data.

---

## Layer 2: Static Configuration

Check the customer's use case configuration for explicitly defined dimensions:

**Priority order:**
1. `per_account[account_number].dimensions` — Most specific. If defined, use these directly. Skip probe.
2. `use_case.dimensions` — Use case-level default. If defined and not set to "auto", use directly. Skip probe.

If either resolves to a concrete list (e.g., `["supplier", "cost_center"]`), skip Layer 3 entirely and use those dimensions in the flux query.

If the value is `"auto"` or not configured: proceed to Layer 3.

---

## Layer 3: Live Data Probe

Query the actual table to discover which dimensional columns have meaningful data for this specific account in the analysis periods.

### Probe Logic

For each candidate dimension column (from Layer 1's category inference):

| Metric | How to Compute | Threshold |
|---|---|---|
| Cardinality | COUNT(DISTINCT column_value) | Must be >= 2 (otherwise no drill-down value) |
| Max cardinality | Same as above | Must be <= 500 (otherwise too fragmented to summarize) |
| Population rate | COUNT(non-null, non-blank) / COUNT(*) | Must be > 50% (otherwise too sparse) |
| Dominance | MAX(count per value) / COUNT(*) | Must be < 95% (otherwise one value dominates) |
| Amount coverage | SUM(amount WHERE value IS NOT NULL) / SUM(amount) | Must be > 70% (otherwise most variance is in blanks) |

### Scoring

Each dimension that passes ALL thresholds is scored:
```
Score = population_rate * (1 - dominance) * LOG(cardinality + 1)
```

Higher score = better drill-down potential. Select the top 1-3 dimensions by score.

### Probe Output

| Dimension | Cardinality | Population % | Dominance % | Amount Coverage % | Score | Use? |
|---|---|---|---|---|---|---|
| supplier | 47 | 98% | 12% | 99% | 3.4 | Yes |
| cost_center | 8 | 100% | 45% | 100% | 1.1 | Yes |
| project | 2 | 15% | 80% | 14% | 0.1 | No (low population) |
| line_memo | 312 | 100% | 3% | 100% | 5.6 | No (>500 — use theme extraction instead) |

---

## Fallback: Theme Extraction

When the probe returns NO usable dimensions (all fail thresholds), or when the top dimension produces >20% of variance in blank/null rows, fall back to **theme extraction**:

1. Use the journal line description/memo column (typically high cardinality but 100% populated)
2. Extract keywords and patterns from descriptions
3. Group into business-meaningful themes (see queries.md Query Type 5)
4. Use themes as pseudo-dimensions for commentary

This is the most common path for:
- Balance sheet accounts (often have sparse supplier/cost_center data)
- Single large accounts with few transactions
- Accounts where most activity comes from automated journal entries (no human-entered dimensional attributes)

---

## Decision Flowchart

```
Start
  │
  ├─ per_account override exists? ──Yes──▶ Use those dimensions (skip probe)
  │
  ├─ use_case default exists and != "auto"? ──Yes──▶ Use those dimensions (skip probe)
  │
  └─ Neither configured or "auto"
       │
       ▼
     Run dimension probe (Layer 3)
       │
       ├─ 1+ dimensions pass thresholds? ──Yes──▶ Use top-scoring dimensions
       │
       └─ No dimensions pass?
            │
            ▼
          Run theme extraction on journal descriptions
            │
            ├─ Themes found with clear patterns? ──Yes──▶ Use themes as drivers
            │
            └─ No clear themes (sparse/generic memos)?
                 │
                 ▼
               Run journal detail drill-down (raw transactions)
               Flag for controller: "Driver unclear from available data"
```

---

## Multiple Dimensions

When multiple dimensions are selected (e.g., `["supplier", "cost_center"]`):
- The flux query should GROUP BY ALL selected dimensions simultaneously
- Commentary should reference the top drivers using the most explanatory dimension first
- If combining dimensions produces too many rows (>100), drop the least-scoring dimension and re-run

The goal is to identify the 3-7 largest drivers that together explain >80% of the total variance (cumulative percentage tracking).
