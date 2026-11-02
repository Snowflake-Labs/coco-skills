# Eval Plan: flux_analysis (Hydration Test via cortex-context-sql-v10)

## Purpose

Verify that `<customer-plugin>/skills/flux_analysis/` (dehydrated) can be hydrated against a Snowflake account containing general ledger data. The hydrator must discover the GL journal tables, fiscal calendar, and dimensional columns from natural-language Context Query descriptions alone.

## Verification Harness

```bash
# Step 1 — Hydrate against <SNOWFLAKE_CONNECTION> with <ROLE_WITH_FINANCE_READ>
cortex -p "Use <HYDRATOR_SKILL> to hydrate <customer-plugin>/skills/flux_analysis/. \
  Use connection <SNOWFLAKE_CONNECTION> with role <ROLE_WITH_FINANCE_READ>."

# Step 2 — Run golden cases against hydrated skill
cortex -p "Run the golden cases from <customer-plugin>/evals/flux_analysis/golden_cases.md \
  against the hydrated flux_analysis skill."
```

---

## Metrics

### Guard Metrics (must pass for valid eval)

| ID | Metric | Pass Condition |
|---|---|---|
| G1 | Hydration sources execute without error | At least 3/5 sources return results |
| G2 | Critical binding resolved: IS activity table | A table with journal-line-level IS data discovered at HIGH or MEDIUM confidence |
| G3 | Critical binding resolved: fiscal calendar | A date-to-period mapping table discovered |
| G4 | Fiscal period convention identified | Framework identifies quarter/month boundaries |
| G5 | No fabricated tables or columns | Framework reports what it found; doesn't invent |

### Primary Metrics (correctness)

| ID | Metric | Pass Condition |
|---|---|---|
| P1 | IS table discovered with activity grain | Table has per-transaction or per-day amounts joinable to dates |
| P2 | BS table discovered with period-end balances | Table has balance amounts with period-end date flags or snapshots |
| P3 | Dimensional columns identified | At least 3 dimension columns (cost_center, supplier, etc.) resolved on IS table |
| P4 | Amount column identified | Numeric amount column suitable for SUM aggregation found |
| P5 | Account identifier column found | GL account number or name column identified for filtering |

### Secondary Metrics (output quality)

| ID | Metric | Pass Condition |
|---|---|---|
| S1 | Column names stable across retries | Same columns resolved on repeated hydration runs |
| S2 | Semantic-view path available | If a semantic view exists over GL data, it's discovered as Priority 0 path |
| S3 | Fiscal calendar join column obvious | Date column on GL table matches date column on fiscal calendar |
| S4 | Multiple dimensions discoverable | Probe logic would find 3+ useful dimensions on the resolved table |
| S5 | Latency acceptable | All successful sources return in < 60 seconds |

---

## Equivalence Threshold

- **EQUIVALENT** = all G pass + all P pass + >= 4/5 S pass
- **DEGRADED** = all G pass + >= 3 P pass + < 4 S pass — iterate on Context Queries
- **INCOMPATIBLE** = any G fails OR < 3 P pass — needs rewrite of Context Queries or data domain description

---

## Expected Challenges

1. **Two separate tables (IS vs BS)** — The hydrator may find one but not the other, or may find a single unified GL table that serves both purposes. Either outcome is acceptable.

2. **Fiscal calendar as a separate table** — Some organizations embed fiscal period info directly in the GL table (as columns). The hydrator should identify this alternative structure.

3. **Dimension column naming** — Column names vary widely across organizations (supplier vs vendor vs vendor_name, cost_center vs department vs org_unit). The hydrator must resolve semantic equivalents.

4. **Period-end flags** — BS queries need to filter to period-end dates. This might be a flag column, a separate period-end table, or derivable from the fiscal calendar. The hydrator may not surface this distinction clearly.
