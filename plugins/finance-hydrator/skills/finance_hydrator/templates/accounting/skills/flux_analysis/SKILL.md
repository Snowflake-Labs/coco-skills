---
name: flux_analysis
description: >
  Automated variance (flux) analysis for accounting close. Compares financial
  periods (Month-over-Month, Quarter-over-Quarter, Year-to-Date), identifies material variances, recommends
  drill-down dimensions, generates executive commentary with top drivers,
  and exports to Excel. Triggers: flux analysis, variance analysis, month over
  month, quarter over quarter, what changed, top drivers, accounting flux,
  close commentary, variance commentary, run flux, explain the variance.
data_domain: "general ledger journal lines, income statement activity, balance sheet period-end balances, fiscal calendar, variance analysis, flux commentary"
created_date: 2026-05-20
version: 1.0.0
license: Proprietary. See LICENSE.txt for complete terms
---

# Flux Analysis

## Intent

Automated variance analysis for accounting close, providing executive summaries, driver identification, and Excel exports. Designed for controllers, accounting managers, and FP&A analysts who need to explain period-over-period changes in financial statement line items for close packages, management reporting, and audit support.

This skill is for variance analysis and flux commentary generation. Not for: transaction-level lookups, journal entry creation, audit trail queries, or dashboard creation.

## Behavior

### Routing Logic

Detect user intent FIRST, before any other processing:

| User Intent | Mode | Description |
|---|---|---|
| Names a specific ledger account (e.g., "account 220000" or "Travel Expenses") | **Single Account** | Analyze one General Ledger (GL) account with full dimensional drill-down |
| Names a use case grouping (e.g., "Outside Services", "Prepaid Expenses") | **Use Case** | Analyze all accounts in a logical grouping with per-account materiality |
| "Run all", "batch run", "full close run", "all use cases" | **Batch All** | Process every configured use case in parallel, produce close workbook |
| Ambiguous | Ask user | Clarify scope before proceeding |

See [references/workflow_modes.md](references/workflow_modes.md) for detailed step-by-step workflows for each mode.

### Core Workflow (All Modes Share)

```
1. Scope Collection     → periods, comparison type, account/use case
2. Config Resolution    → look up use case config (table, filter, dimensions, template)
3. Enumeration          → list accounts with current and prior balances
4. Materiality Gate     → flag accounts exceeding threshold OR on always-comment list
5. Flux + Dimensions    → run dimensional analysis, probe dimensions, extract themes
6. Commentary           → generate driver-based commentary for each material account
7. Output               → chat summary + Excel export
```

### Comparison Types

| Type | What It Compares | Typical Use |
|---|---|---|
| MoM (Month-over-Month) | Current month vs prior month | Monthly close |
| QoQ (Quarter-over-Quarter) | Current quarter-end vs prior quarter-end | Quarterly close |
| YTD (Year-to-Date) | Year-to-date current vs year-to-date prior year | Annual reporting |

For Balance Sheet (BS) accounts, comparisons use **point-in-time balances** at period-end dates.
For Income Statement (IS) accounts, comparisons use **activity totals** within each period.

### Materiality Gate

A line item is flagged for commentary if EITHER condition is true:

**Condition A — Exceeds materiality threshold:**

Materiality thresholds are **customer-provided inputs during initial setup** — they vary by organization size, industry, and audit requirements. The skill MUST collect these from the customer as part of initial configuration. It does not assume default values.

During setup, ask the customer to provide:
- Dollar threshold per comparison type (MoM, QoQ, YTD)
- Percentage threshold per comparison type (or "dollar only" for YTD)
- Gate logic: whether BOTH thresholds must be exceeded, or EITHER suffices

Example configuration (mid-to-large enterprise):

| Comparison | $ Threshold | % Threshold | Gate Logic |
|---|---|---|---|
| MoM | $250,000 | 10% | BOTH must be exceeded |
| QoQ | $500,000 | 10% | BOTH must be exceeded |
| YTD | $2,500,000 | N/A | $ only |

Smaller organizations may use $50K / 15% for MoM. The skill adapts to whatever the customer defines.

**Condition B — On the "always comment" list:**

Certain account types receive commentary regardless of materiality because they are always scrutinized by management and auditors. Default always-comment categories:
- Revenue lines (all)
- Headcount and payroll cost
- Cash and cash equivalents

The always-comment list is customer-configurable. If an account matches this list, it bypasses the materiality gate entirely.

**Immaterial accounts** (neither condition met) receive a summary row in the output table with "Below threshold" — no commentary generated.

### Commentary Principles

Three core rules govern all commentary generation:

**1. "Why Not What" (MANDATORY)**

Every driver must explain the *business reason* for the movement — not restate the number.

Good: "Cloud hosting up $1.2M on incremental GPU reservations for product launch"
Bad: "Cloud hosting increased $1.2M (18%)"

Good: "Headcount cost decreased $3.4M due to 47 attrition-related departures not backfilled"
Bad: "Headcount cost decreased $3.4M vs prior month"

**2. "Flag for Controller" (MANDATORY)**

If the data does not clearly explain the driver — sparse dimensions, generic journal descriptions, ambiguous patterns — write:

> "Driver unclear from available data — flag for controller review"

NEVER invent or hallucinate an explanation. NEVER use an LLM to generate speculative commentary. Commentary must be grounded in the data returned by queries.

**3. Data-Driven Pattern Detection**

Commentary structure is determined by what the data contains, not by the account name. Detect the activity type from dimension values and journal descriptions, then apply the matching commentary template.

See [references/commentary_patterns.md](references/commentary_patterns.md) for the full pattern detection logic and 8 structural templates.

### Config Resolution Pattern

The skill uses a **use case configuration** system to determine how to analyze each account grouping:

| Config Field | Purpose | Example |
|---|---|---|
| Table binding | Which General Ledger table to query | (resolved at runtime) |
| Flux type | `activity` (Income Statement) or `balance` (Balance Sheet) — determines query family | `balance` |
| Filter | WHERE clause scoping to this use case's accounts | (account category filter) |
| Dimensions | Preferred drill-down dimensions for this use case | `["supplier", "cost_center"]` |
| Commentary template | Which structural template to use (A through H) | `C` |
| Per-account overrides | Specific accounts needing different dimensions or templates | (optional) |
| Fluxed-together groups | Accounts that must be analyzed as a combined unit | (optional) |
| Always-comment flag | Whether this use case always gets commentary | `false` |

**Resolution order** (most specific wins):
```
per_account[account].dimensions  >  use_case.dimensions  >  dimension_probe_result
per_account[account].template    >  use_case.commentary_template
```

Customers maintain their own use case configurations mapping their chart of accounts to these settings. The skill resolves the physical table bindings at runtime.

### Dimension Selection

Before running flux queries, the skill must determine which dimensions provide meaningful drill-down for the specific account being analyzed. This uses a 3-layer engine:

1. **Account category inference** — determine the account's economic category (Asset, Liability, Revenue, Expense) by querying the chart of accounts table (resolved at runtime). This narrows which dimensions are relevant.
2. **Static config** — use customer-configured dimensions for the use case
3. **Live data probe** — query the table to discover which columns have meaningful cardinality

See [references/dimension_engine.md](references/dimension_engine.md) for the full 3-layer logic.

### Theme Extraction (Sparse Data Fallback)

When dimensional analysis produces drivers with blank or generic values (common for balance sheet accounts and single-account drill-downs), the skill falls back to **theme extraction**: mining journal line descriptions for business-meaningful keywords, grouping them into themes, and computing net change per theme across periods.

This produces commentary like "Restricted cash decreased $40.8M, primarily from final distributions on liquidation" even when the dimensional columns are empty.

### Fluxed-Together Accounts

Some accounts are economically linked and must be analyzed as a combined unit rather than individually. Examples:
- Prepaid Expenses (short-term) + Prepaid Expenses (long-term)
- Accounts Receivable + Allowance for Doubtful Accounts

When accounts are configured as fluxed-together:
- Their balances are summed for materiality assessment
- A single combined commentary is generated
- Individual rows still appear in the summary table, each showing the combined commentary

### Confidence Handling

| Confidence | Behavior |
|---|---|
| HIGH (General Ledger table resolved, fiscal calendar found, dimension columns identified) | Full analysis with all sections |
| MEDIUM (General Ledger table resolved but some dimension columns missing or ambiguous) | Run analysis with available dimensions; note degraded sections; suggest configuration hints |
| LOW on critical (no General Ledger journal table found, or no fiscal calendar) | Skip skill execution; output data requirements and recommended configuration |

### Guardrails

- NEVER use an LLM (Cortex Complete or equivalent) to generate commentary text. All commentary must be derived from query results using pattern-based templates.
- NEVER invent drivers not supported by data. If unclear, flag for controller.
- NEVER sum amounts across different currencies. Group and display by currency.
- NEVER assume a fiscal calendar convention. The fiscal calendar binding is resolved at runtime — respect whatever period structure it defines (Feb-start, Jan-start, custom).
- ALWAYS ask for periods AND comparison type when starting a new analysis — never assume from prior context.
- ALWAYS generate both chat output AND Excel export after completing analysis.

### Error Handling

| Scenario | Action |
|---|---|
| Account not found in General Ledger table | Ask user to verify account number; suggest similar accounts if fuzzy match available |
| No data for requested period | Report "no transactions found for period X"; suggest most recent available period |
| Dimension probe returns no useful dimensions | Fall back to theme extraction from journal descriptions |
| All drivers below 5% of total variance | Collapse into "Other — multiple small items"; note in commentary |
| Query timeout on large dataset | Offer to narrow scope (single entity, top N accounts, shorter date range) |

### Output

See [references/output_format.md](references/output_format.md) for full format specifications.

**Chat output**: Summary table (markdown) showing all accounts with Current, Prior, Variance, Var%, Material? columns — followed by commentary sections for each material account.

**Excel output**: Structured workbook with Flux Commentary sheet (the master table with Driver column) and Metadata sheet (analysis parameters, periods, thresholds, timestamp).

## Context Queries

- A table containing Income Statement (Profit & Loss) activity at the journal line level, with one row per transaction or aggregated journal entry. Contains columns for: General Ledger account identifier (number and/or name), posting date, amount (in reporting currency), and dimensional attributes such as cost center, department, supplier/vendor, project, spend category, revenue category, program, and journal line description/memo. Used for activity-based flux queries (Month-over-Month, Quarter-over-Quarter, Year-to-Date comparisons of period totals). Grain: one row per journal line or aggregated entry per fiscal day.

- A table containing Balance Sheet account balances, with point-in-time snapshots at period-end dates. Contains columns for: General Ledger account identifier, effective/snapshot date, balance amount (in reporting currency), period-end flags (quarter-end, month-end), and dimensional attributes such as legal entity, cost center, supplier, project, and journal line description/memo. Used for balance comparisons at specific dates. Grain: one row per account per day (or per period-end date).

- A fiscal calendar reference table mapping calendar dates to fiscal periods, fiscal quarters, and fiscal years. Contains columns for: calendar date, fiscal month-end date, fiscal quarter identifier, and fiscal year identifier. Used to join transaction dates to fiscal periods and determine period boundaries. Grain: one row per calendar date.

- (Optional) A chart of accounts or account master table containing the account-to-category mapping. Contains columns for: account number/code, account name/description, account type or category (Asset, Liability, Equity, Revenue, Expense), financial statement assignment (Balance Sheet vs Income Statement), and optionally a hierarchical grouping (e.g., FSLI, account summary level). Used to classify accounts for dimension inference and to identify which accounts belong to which use case groupings. Grain: one row per General Ledger account.

- (Optional) A pre-computed results/state table storing previously generated flux commentary for batch runs, with columns for: account identifier, period pair, commentary text, status (draft/approved/final), and generation timestamp. If present, the skill checks for existing approved commentary before re-running analysis. If absent, the skill always runs fresh analysis.

## References

- [queries.md](queries.md) — Natural-language query type descriptions
- [references/commentary_patterns.md](references/commentary_patterns.md) — Commentary templates and pattern detection
- [references/workflow_modes.md](references/workflow_modes.md) — Detailed workflow steps for each mode
- [references/dimension_engine.md](references/dimension_engine.md) — Smart dimension recommendation engine
- [references/output_format.md](references/output_format.md) — Output format specifications
