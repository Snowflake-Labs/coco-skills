---
name: recon_qa
description: >
  Quality assurance for reconciliation workbooks and outputs. Validates formula
  accuracy, cross-sheet consistency, balance tie-outs, category completeness,
  and common spreadsheet errors. Companion skill to recon_analysis — run after
  export to verify output integrity. Triggers: QA recon, audit recon workbook,
  check recon output, validate reconciliation, sanity check recon, review recon
  export, reconciliation quality check, verify recon results.
data_domain: "reconciliation output validation, workbook audit, formula accuracy, balance tie-out, cross-sheet consistency, data integrity check"
created_date: 2026-05-20
version: 1.0.0
license: Proprietary. See LICENSE.txt for complete terms
---

# Reconcile QA

## Intent

Post-reconciliation quality assurance skill that validates the integrity of reconciliation outputs (Excel workbooks, CSV exports, or in-memory result sets). Designed as a companion to `recon_analysis` — run after a reconciliation export to verify the output is accurate, internally consistent, and ready for sign-off. For controllers, audit teams, and accounting managers who need confidence that recon outputs are correct before distribution.

This skill is for output validation only. Not for: running reconciliations (use `recon_analysis`), modifying data, creating journals, or building dashboards.

## Behavior

### Routing Logic

Detect scope and route to the appropriate validation depth:

| User Intent | Scope | Description |
|---|---|---|
| "QA this recon", "check my recon output" | **Standard** | Run formula + consistency checks on the recon workbook |
| "Full audit", "deep QA", "sign-off ready?" | **Deep** | Standard + balance tie-outs + completeness + reasonableness |
| "Quick check", "sanity check" | **Quick** | Summary-level consistency only (match rate math, row counts) |
| Points to a specific sheet or range | **Targeted** | Validate only the specified scope |

### Standard Validation Checks (All Scopes)

#### 1. Structural Integrity

| Check | What to look for | Severity |
|---|---|---|
| Sheet presence | Expected sheets exist (Summary, Discrepancies, Matched, Unmatched, Discrepancy Analysis, Metadata) | CRITICAL |
| Column headers | Required columns present in each sheet | CRITICAL |
| Row count consistency | Sum of Matched + Discrepancy + Unmatched rows = Total in Summary | CRITICAL |
| Empty sheets | Any data sheet with zero rows (unexpected) | WARNING |
| Data types | Numeric columns contain numbers, date columns contain dates | WARNING |

#### 2. Mathematical Accuracy

| Check | What to look for | Severity |
|---|---|---|
| Match rate calculation | (Matched + Within Threshold) / Total × 100 = reported match rate | CRITICAL |
| Category counts | Sum of per-category counts = total discrepancy count in summary | CRITICAL |
| Absolute diff correctness | For each discrepancy row: ABS(Source1 - Source2) = reported diff | CRITICAL |
| Percent diff correctness | Diff / Source2 × 100 = reported percent (handle div-by-zero) | WARNING |
| Threshold classification | Items marked "Within Threshold" actually fall within configured thresholds | CRITICAL |

#### 3. Cross-Sheet Consistency

| Check | What to look for | Severity |
|---|---|---|
| Key uniqueness | Each join key appears at most once per sheet (no accidental duplication) | CRITICAL |
| Discrepancy ↔ Analysis | Every row in Discrepancies sheet has a corresponding row in Discrepancy Analysis | WARNING |
| Source data coverage | Keys in Matched + Discrepancies + Unmatched = union of keys across source sheets | CRITICAL |
| Category labels | Categories in detail sheets match exactly the categories in Summary breakdown | WARNING |

#### 4. Metadata Validation

| Check | What to look for | Severity |
|---|---|---|
| Timestamp present | Generated timestamp exists and is within last 24 hours | INFO |
| Parameter recording | Date range, thresholds, profile name all recorded | INFO |
| Source identification | Both sources clearly identified | WARNING |
| Row count audit | Metadata row counts match actual sheet row counts | CRITICAL |

### Deep Validation Checks (Deep Scope Only)

#### 5. Balance Tie-Outs

| Check | What to look for | Severity |
|---|---|---|
| Source1 total | SUM of Source1 values across all matched rows = expected source total (if available) | CRITICAL |
| Source2 total | SUM of Source2 values across all matched rows = expected source total | CRITICAL |
| Net discrepancy | Total discrepancy amount = Source1 total - Source2 total (for numeric fields) | CRITICAL |
| Orphan value impact | Sum of Source1 Only amounts + Sum of Source2 Only amounts accounted for | WARNING |

#### 6. Category Completeness

| Check | What to look for | Severity |
|---|---|---|
| All discrepancies categorized | Zero rows with NULL or empty Category | CRITICAL |
| All categories have reasons | Zero rows with NULL or empty Comment/Reason | WARNING |
| Owner assignment | If triage fields configured: all discrepancies have an owner | WARNING |
| Action assignment | If triage fields configured: all discrepancies have an action | WARNING |

#### 7. Reasonableness

| Check | What to look for | Severity |
|---|---|---|
| Match rate vs history | If prior run metadata available: match rate within ±5% of historical average | INFO |
| Discrepancy concentration | Single entity/key accounting for >50% of total discrepancy value | WARNING |
| Threshold abuse | >30% of "matches" are actually "within threshold" (suggests thresholds too loose) | WARNING |
| Sign consistency | Discrepancies predominantly one-directional (Source1 > Source2 or vice versa) | INFO |
| Stale data | Source data dates significantly older than recon run date | WARNING |

### Output

Present findings as a validation report:

```
RECONCILIATION QA REPORT
=========================
Workbook: <filename>
Scope: <Standard | Deep | Quick | Targeted>
Checks run: N
Checks passed: N

FINDINGS:
| # | Sheet | Cell/Range | Severity | Category | Issue | Impact |
|---|---|---|---|---|---|---|

SUMMARY:
Critical: N | Warning: N | Info: N
Verdict: <CLEAN | ISSUES FOUND | BLOCKED>
```

**Severity definitions:**
- **CRITICAL** — Output is incorrect or misleading; must fix before distribution
- **WARNING** — Potential issue; review before sign-off
- **INFO** — Style or best-practice observation; no action required

**Verdict logic:**
- **CLEAN** = 0 Critical, 0 Warning
- **ISSUES FOUND** = 0 Critical, ≥1 Warning
- **BLOCKED** = ≥1 Critical — do NOT distribute until resolved

### Guardrails

- This skill validates — it does NOT fix. Report findings; let the user decide on corrections.
- NEVER modify the workbook or recon results. If fixes are needed, suggest re-running `recon_analysis` with corrected parameters.
- If the workbook structure is unrecognizable (not a recon output), inform the user and suggest pointing to the correct file.

### Confidence Handling

| Confidence | Behavior |
|---|---|
| HIGH (workbook structure matches expected recon output format) | Run all applicable checks for the selected scope |
| MEDIUM (workbook has some expected sheets but non-standard structure) | Run checks that apply; skip structural checks with a note; report what was validated |
| LOW (file is not a recognizable reconciliation output) | Report: "This doesn't appear to be a reconciliation workbook. Expected sheets: [list]. Found: [list]." Suggest correct file. |

### Error Handling

| Scenario | Action |
|---|---|
| File not found | Report path error; ask for correct file location |
| File is not Excel/CSV | Report format error; list supported formats |
| Password-protected workbook | Report: cannot open; ask user to provide unprotected version |
| Partial workbook (some sheets missing) | Run available checks; note missing sheets as CRITICAL finding |
| Very large workbook (>100K rows) | Sample-check rather than full-scan; note sampling in report |

## What to Provide

To run QA, point this skill at one of the following:

- **A reconciliation workbook** (Excel `.xlsx`) — produced by `recon_analysis` or any equivalent reconciliation tool. Expected sheets: Summary, Discrepancies, Matched, Unmatched, Discrepancy Analysis, Metadata. The skill will validate all sheets that are present.
- **A CSV export** — single-sheet export from a reconciliation run. The skill will run applicable checks for the available structure.
- **An in-memory result set** — if you just ran a reconciliation in this session, refer to it directly and the skill will validate the results in context.

Optionally, provide **prior run history** (a table or export of past reconciliation runs for the same profile) to enable reasonableness checks — e.g., flagging when the current match rate is significantly lower than historical average.
