# Workflow Modes — Flux Analysis

Three modes handle different levels of scope. All share the same core engine (config resolution, materiality gate, dimension analysis, commentary generation) but differ in how they enumerate accounts and structure output.

---

## Single Account Mode

Analyze one specific GL account with full dimensional drill-down.

### Steps

**Step 1: Collect Requirements**

Gather via user interaction (combine into one prompt where possible):
1. **Account identifier** — GL account number or account name
2. **Comparison type** — MoM, QoQ, or YTD
3. **Periods** — Current period and prior period (format varies by comparison type)

If the user provides a natural-language request like "what changed in account 630003 this month", infer the comparison type and offer default periods based on the most recent closed period from the fiscal calendar.

**Step 2: Config Resolution**

Look up the account in the use case configuration:
- Determine which use case this account belongs to
- Load the use case config (table binding, flux type, filter, dimensions, commentary template)
- Check for per-account overrides

If the account is not in any configured use case, use the default table binding and dimension probe.

**Step 3: Summary Table**

Run the enumeration query scoped to just this account. Display:

| Metric | Current Period | Prior Period | Variance | Variance % |
|---|---|---|---|---|
| Total | (full number) | (full number) | +/- (full number) | +/- X.X% |
| **Materiality Threshold** | | | (configured $) | (configured %) |
| **Material?** | | | Yes/No | Yes/No |

If immaterial AND not on always-comment list: state "Variance is below materiality threshold — no commentary required." Offer to proceed anyway if user requests.

**Step 4: Dimension Selection**

Follow the [dimension_engine.md](dimension_engine.md) 3-layer process:
1. Check per-account override dimensions
2. Check use case default dimensions
3. If neither configured or set to "auto": run dimension probe

**Step 5: Flux Query + Theme Extraction**

Run the appropriate flux query (Activity or Balance depending on flux_type) with the selected dimensions. If results have sparse/blank dimension values (>20% of variance in blank rows), additionally run theme extraction on journal descriptions.

**Step 6: Commentary Generation**

Using the flux results:
1. Detect the activity type from data signals (see commentary_patterns.md)
2. Select the appropriate structural template (A-H)
3. Generate commentary following "why not what" principle
4. If driver is unclear: "Driver unclear from available data — flag for controller review"

**Step 7: Output**

Display commentary in chat. Generate Excel export with the account's analysis.

---

## Use Case Mode

Analyze all accounts in a logical grouping (e.g., "Outside Services", "Prepaid Expenses") with per-account materiality gating and a master summary table.

### Steps

**UC-1: Collect Requirements**

Gather:
1. **Use case name** — the configured grouping (matches the customer's use case config)
2. **Comparison type** — MoM, QoQ, or YTD
3. **Periods** — Current and prior

**UC-2: Config Resolution + Enumeration**

Load the use case config. Run the account enumeration query to get all GL accounts matching the use case filter with their current and prior period totals.

After enumeration:
1. Apply fluxed-together rules (merge balances for grouped accounts)
2. Compute per-account materiality: exceeds threshold OR on always-comment list
3. Count material vs immaterial accounts

**UC-3: Master Summary Table**

Display all accounts in one table:

| GL Account | Account Name | Current Period | Prior Period | Variance | Var % | Material? | Driver |
|---|---|---|---|---|---|---|---|
| (number) | (name) | (amount) | (amount) | +/- amount | +/- % | Yes/No | (filled after UC-5) |
| ... | ... | ... | ... | ... | ... | ... | ... |
| | **Use Case Total** | **(sum)** | **(sum)** | **(sum)** | **(%)** | Yes/No | |

Rules:
- Sort by account number ascending (material and immaterial interleaved naturally)
- Show full unformatted numbers in the table
- Materiality column shows Yes (exceeds threshold OR on always-comment list) or No
- Driver column is populated after per-account processing completes
- Total row: compute from query results using SUM, not by manually summing displayed rows
- Fluxed-together groups: show individual rows; if either is material, both get combined commentary

**UC-4: Parallel Per-Account Processing**

For each material account (in parallel where possible):
1. Determine dimensions (per-account override > use case default > probe)
2. Run flux query with selected dimensions
3. Run theme extraction if needed (sparse data)
4. Generate commentary using pattern detection + appropriate template

For immaterial accounts: Driver = "Below threshold"

**UC-5: Use Case Total Commentary**

After all accounts processed, generate a total-level commentary:
- Summarize the 2-3 largest drivers across all accounts
- Note the overall direction and magnitude
- Reference which specific accounts drove the total change

**UC-6: Output**

1. Re-display the master summary table with Driver column filled in
2. Show per-account commentary sections in chat for material accounts
3. Generate Excel workbook (master table + metadata)

---

## Batch All Mode

Process every configured use case in sequence, producing a complete close workbook.

### Steps

**BA-1: Collect Requirements**

Gather:
1. **Comparison type** — MoM, QoQ, or YTD
2. **Periods** — Current and prior
3. (Optional) Subset of use cases to include (default: all configured)

**BA-2: Enumerate Use Cases**

Read the customer's use case configuration. List all configured use cases with their flux type (activity/balance).

**BA-3: Sequential Use Case Processing**

For each use case, execute the full Use Case Mode workflow (UC-1 through UC-6). Track progress and report completion per use case.

Processing order: IS use cases first (activity-based), then BS use cases (balance-based). This mirrors the typical close package order.

**BA-4: Cross-Use-Case Summary**

After all use cases complete, generate a high-level summary:
- Total number of accounts analyzed
- Number flagged as material
- Top 5 largest variances across all use cases
- Any accounts where driver was unclear (flagged for controller)

**BA-5: Output**

Generate a consolidated Excel workbook:
- One sheet per use case (master summary table with Driver column)
- Summary sheet with cross-use-case overview
- Metadata sheet with run parameters

---

## Fluxed-Together Accounts

Some accounts are economically linked and must be analyzed as a combined unit:

**Concept**: Two or more GL accounts that share a common economic reality (e.g., gross asset + accumulated depreciation, short-term + long-term portions of the same prepaid).

**Processing rules**:
1. During enumeration, their individual balances appear as separate rows
2. For materiality assessment, their variances are summed together
3. If the combined variance is material, a single combined flux query runs against all accounts in the group
4. Commentary references the group explicitly: "Accounts X and Y are analyzed together..."
5. In the master summary table, individual rows appear but share the same Driver text
6. If neither account individually nor combined is material, both show "Below threshold"

**Configuration**: The customer defines which accounts are fluxed-together in their use case config. Common patterns:
- Prepaid (current) + Prepaid (long-term)
- Gross asset + Accumulated depreciation
- AR Gross + Allowance for doubtful accounts
- Deferred revenue current + Deferred revenue non-current

---

## Config Pattern (Customer-Defined)

Each customer maintains a use case configuration that maps their chart of accounts to the skill's workflow. The config contains:

| Field | Purpose | Required? |
|---|---|---|
| Use case name | Human-readable grouping name | Yes |
| Table binding | Which GL table (IS or BS) | Yes (resolved at runtime) |
| Flux type | `activity` or `balance` | Yes |
| Account filter | Which accounts belong to this use case | Yes |
| Default dimensions | Preferred drill-down columns | Yes |
| Commentary template | Which structural template (A-H) | Yes |
| Per-account overrides | Exceptions for specific accounts | No |
| Fluxed-together groups | Accounts analyzed as combined units | No |
| Always-comment | Whether this use case always gets commentary | No |
| Custom materiality | Override default thresholds for this use case | No |

The skill does not prescribe the format of this config (JSON, YAML, or table-based). It describes the semantic contract — the customer implements it in whatever format their environment supports.
