# FX Rate Validation Patterns

This reference file is loaded by the divergence-deep-dive sub-skill when investigating foreign exchange (FX) rate divergences. It provides a structured 7-phase workflow for validating USD currency conversions between a mart and baseline.

## When to Load This File

The deep-dive skill loads this file when:
- Divergent columns include USD amount columns (e.g., `*USD*`, `*EXTENDED_USD*`, `*UNIT_USD*`)
- The analyst explicitly requests an FX check, exchange rate validation, or currency conversion investigation
- The comparison report shows systematic differences in monetary columns where a currency code column exists

## Phase 0: Gather FX Configuration

Before executing any diagnostic queries, collect the following from the analyst. If a checkpoint YAML is open, pre-populate from it and ask to confirm.

### Questions

**Q1 — Scope:**
Which semantic view (or mart/baseline table pair) should I validate?

**Q2 — Baseline rate logic:**
How does the **baseline** compute USD amounts?
- (a) Pre-computed in fact table using a point-in-time rate date stored per row
- (b) Computed via a function call at query time
- (c) Unknown — investigate for me

**Q3 — Mart rate logic:**
How should the **mart** compute USD amounts?
- (a) Same point-in-time rate as the baseline (differences = bugs)
- (b) Periodically refreshed rate from ETL run date (differences = expected, quantify drift)
- (c) Current-month rate applied to all rows (differences = expected by design)
- (d) Unknown — investigate for me

**Q4 — Tolerances:**
What tolerance for USD differences? (default: 1% per-row, 2% aggregate net per currency)

**[STOP]** Confirm resolved configuration with the analyst before proceeding. **Update checkpoint.**

### Resolved Configuration

Build and confirm this config:

```yaml
fx_validation:
  semantic_view: <FULLY_QUALIFIED_NAME or null>
  mart_table: <FULLY_QUALIFIED_NAME>
  mart_entity: <entity name in SV, e.g. MART>
  golden_table: <FULLY_QUALIFIED_NAME>
  golden_entity: <entity name in SV, e.g. GOLDEN>
  fx_function: <FULLY_QUALIFIED_FUNCTION_NAME or null>
  currency_column: <e.g. ORDER_CURRENCY_CODE>
  local_amount_columns: [<list>]
  usd_amount_columns: [<list>]
  golden_rate_logic: <point_in_time | function_at_query_time | unknown>
  golden_rate_date_column: <column name or null>
  mart_rate_logic: <point_in_time | etl_refresh | current_month | unknown>
  tolerance:
    per_row_pct: 1
    per_row_abs: 0.01
    aggregate_net_pct: 2
  sample_limit: 2000
```

---

## Phase 1: Discovery

**Goal:** Identify all relevant FX columns from the semantic view and/or underlying tables.

### Steps

1. If a semantic view is provided, run `DESCRIBE SEMANTIC VIEW <semantic_view>` to find:
   - Currency code columns (dimension containing currency ISO codes)
   - Local amount columns (facts with monetary values in order currency)
   - USD amount columns (facts with `USD` in name)
   - Date columns (all date/timestamp dimensions)

2. Run `DESCRIBE TABLE` on both mart and baseline underlying tables to find:
   - Exchange rate columns (`*EXCH_RATE*`, `*USD_RATE*`, `*FX_RATE*`)
   - Rate date columns (`*RATE_DATE*`, `*EXCH_RATE_DATE*`)
   - Audit/ETL columns (`ETL_CREATED_AT`, `ETL_UPDATED_AT`)

3. If an FX function was specified, confirm its signature:
```sql
SHOW FUNCTIONS LIKE '{{FX_FUNCTION_NAME}}' IN SCHEMA {{SCHEMA}};
```
Expected signature: `(DATE, VARCHAR) RETURN NUMBER`

### Output

Column mapping summary:

| Role | Mart Column | Baseline Column |
|------|-------------|---------------|
| Currency code | ... | ... |
| Local unit price | ... | ... |
| USD unit price | ... | ... |
| Exchange rate | ... | ... |
| Rate date | ... | ... |
| Audit update date | ... | ... |

**[STOP]** Confirm the column mapping with the analyst. If columns are missing or misidentified, correct before proceeding. **Update checkpoint.**

---

## Phase 2: Rate Source Identification

**Goal:** Determine how each side computes its USD amounts.

### Baseline (if `golden_rate_logic = unknown`)

1. Check if the baseline has a stored rate column and rate date column (from Phase 1).
2. Sample non-USD rows and compute implied rate:

```sql
SELECT
    {{RATE_DATE_COL}},
    {{CURRENCY_COL}},
    {{USD_COL}} / NULLIF({{LOCAL_COL}}, 0) AS IMPLIED_RATE,
    {{FX_FUNCTION}}({{RATE_DATE_COL}}::DATE, {{CURRENCY_COL}}) AS FN_RATE,
    ABS(IMPLIED_RATE - FN_RATE) AS DIFF
FROM {{GOLDEN_TABLE}}
WHERE {{CURRENCY_COL}} != 'USD'
LIMIT 100;
```

3. If DIFF ≈ 0 for most rows, confirm the baseline uses point-in-time rate from that date column.

### Mart (if `mart_rate_logic = unknown`)

1. Check if all rows of a given currency share the same rate (suggests ETL refresh):

```sql
SELECT {{CURRENCY_COL}}, {{RATE_COL}}, COUNT(*) AS cnt,
    MIN({{ORDER_DATE_COL}}) AS min_date, MAX({{ORDER_DATE_COL}}) AS max_date
FROM {{MART_TABLE}}
WHERE {{CURRENCY_COL}} = '{{SAMPLE_CURRENCY}}' AND {{CURRENCY_COL}} != 'USD'
GROUP BY 1, 2
ORDER BY cnt DESC
LIMIT 20;
```

2. If many order dates share one rate, reverse-engineer which month it belongs to by calling the FX function for surrounding months.

3. Check `ETL_UPDATED_AT` — if all rows were recently updated, the rate is from the ETL refresh cycle.

4. If the rate matches the function output for a month unrelated to the order date, classify as `etl_refresh`.

### Output

State findings clearly:
- "Baseline uses `{{FX_FUNCTION}}({{RATE_DATE_COL}}, {{CURRENCY_COL}})` — point-in-time monthly rate"
- "Mart stores a rate that matches `{{FX_FUNCTION}}('YYYY-MM-01', {{CURRENCY_COL}})` — ETL refresh rate from month YYYY-MM"

**[STOP]** Present rate source findings and ask the analyst to confirm or correct the identified logic for each side. Update the resolved configuration (`golden_rate_logic`, `mart_rate_logic`) before proceeding. **Update checkpoint.**

---

## Phase 3: Function Validation

**Goal:** Confirm the FX function behavior and validate each side's rates against it.

### Steps

1. Confirm function returns monthly rates (same output for all dates within a calendar month):

```sql
SELECT
    {{FX_FUNCTION}}('{{YYYY_MM}}-01'::DATE, '{{CURRENCY}}') AS RATE_1ST,
    {{FX_FUNCTION}}('{{YYYY_MM}}-15'::DATE, '{{CURRENCY}}') AS RATE_15TH,
    {{FX_FUNCTION}}('{{YYYY_MM}}-28'::DATE, '{{CURRENCY}}') AS RATE_28TH;
```

2. For the baseline — validate stored rate matches function output on a sample per currency:

```sql
SELECT {{CURRENCY_COL}}, COUNT(*) AS total,
    SUM(CASE WHEN ABS({{STORED_RATE_COL}} - {{FX_FUNCTION}}({{RATE_DATE_COL}}::DATE, {{CURRENCY_COL}})) < 0.000001 THEN 1 ELSE 0 END) AS matches
FROM {{GOLDEN_TABLE}}
WHERE {{CURRENCY_COL}} != 'USD'
GROUP BY 1;
```

3. For mart — same validation using the identified rate date logic.

4. Flag any rows where the rate does NOT match any month's output from the function.

### Output

Validation summary per side: "X% of baseline rates match function exactly. Y% of mart rates match."

**[STOP]** Review validation results with the analyst. If match rates are unexpectedly low, investigate before proceeding to row-level comparison. The analyst may want to adjust tolerances or re-examine the rate source logic from Phase 2. **Update checkpoint.**

---

## Phase 4: Row-Level Comparison

**Goal:** Join mart to baseline and identify USD mismatches.

### Join Strategy

If a semantic view exists, use `SEMANTIC_VIEW()` syntax. Otherwise, join underlying tables directly.

Join on PK columns + currency + local amounts to isolate USD-only differences:

```sql
SELECT
    m.{{ORDER_NUM}}, m.{{LINE_NUM}}, m.{{CURRENCY_COL}},
    ROUND(m.{{LOCAL_PRICE}}, 4) AS LOCAL_PRICE,
    ROUND(m.{{USD_PRICE}}, 4) AS MART_USD_PRICE,
    ROUND(g.{{USD_PRICE}}, 4) AS GOLD_USD_PRICE,
    ROUND(m.{{USD_PRICE}} - g.{{USD_PRICE}}, 4) AS UNIT_USD_DIFF,
    ROUND(m.{{EXT_USD}}, 4) AS MART_EXT_USD,
    ROUND(g.{{EXT_USD}}, 4) AS GOLD_EXT_USD,
    ROUND(m.{{EXT_USD}} - g.{{EXT_USD}}, 4) AS EXT_USD_DIFF
FROM {{MART_TABLE}} m
JOIN {{GOLDEN_TABLE}} g
    ON {{PK_JOIN}}
    AND m.{{CURRENCY_COL}} = g.{{CURRENCY_COL}}
    AND ROUND(m.{{LOCAL_PRICE}}, 4) = ROUND(g.{{LOCAL_PRICE}}, 4)
    AND ROUND(m.{{EXT_AMT}}, 4) = ROUND(g.{{EXT_AMT}}, 4)
WHERE m.{{CURRENCY_COL}} != 'USD'
    AND (ROUND(m.{{USD_PRICE}}, 4) != ROUND(g.{{USD_PRICE}}, 4)
        OR ROUND(m.{{EXT_USD}}, 4) != ROUND(g.{{EXT_USD}}, 4))
ORDER BY ABS(m.{{EXT_USD}} - g.{{EXT_USD}}) DESC
LIMIT {{SAMPLE_LIMIT}};
```

### USD Sanity Check

Confirm USD-denominated rows match exactly (no conversion needed):

```sql
SELECT COUNT(*) AS usd_mismatches
FROM {{MART_TABLE}} m
JOIN {{GOLDEN_TABLE}} g ON {{PK_JOIN}}
WHERE m.{{CURRENCY_COL}} = 'USD'
    AND ROUND(m.{{EXT_USD}}, 2) != ROUND(g.{{EXT_USD}}, 2);
```

### Output

Top 20 mismatched rows sorted by absolute USD difference.

---

## Phase 5: Impact Quantification

**Goal:** Aggregate the total USD variance by currency.

### Per-Currency Breakdown

```sql
SELECT
    m.{{CURRENCY_COL}},
    COUNT(*) AS ROW_COUNT,
    SUM(m.{{EXT_USD}}) AS MART_TOTAL_EXT_USD,
    SUM(g.{{EXT_USD}}) AS GOLD_TOTAL_EXT_USD,
    SUM(m.{{EXT_USD}}) - SUM(g.{{EXT_USD}}) AS TOTAL_USD_DIFF,
    ROUND(100.0 * (SUM(m.{{EXT_USD}}) - SUM(g.{{EXT_USD}})) / NULLIF(SUM(g.{{EXT_USD}}), 0), 2) AS PCT_DIFF,
    SUM(CASE WHEN ROUND(m.{{EXT_USD}}, 2) != ROUND(g.{{EXT_USD}}, 2) THEN 1 ELSE 0 END) AS MISMATCHED_ROWS,
    SUM(ABS(m.{{EXT_USD}} - g.{{EXT_USD}})) AS GROSS_ABS_DIFF
FROM {{MART_TABLE}} m
JOIN {{GOLDEN_TABLE}} g ON {{PK_JOIN}} AND m.{{CURRENCY_COL}} = g.{{CURRENCY_COL}}
WHERE m.{{CURRENCY_COL}} != 'USD'
GROUP BY 1
ORDER BY ABS(SUM(m.{{EXT_USD}}) - SUM(g.{{EXT_USD}})) DESC;
```

### Grand Total

```sql
SELECT
    COUNT(*) AS TOTAL_NON_USD_ROWS,
    SUM(CASE WHEN ROUND(m.{{EXT_USD}}, 2) != ROUND(g.{{EXT_USD}}, 2) THEN 1 ELSE 0 END) AS MISMATCHED_ROWS,
    ROUND(100.0 * SUM(CASE WHEN ROUND(m.{{EXT_USD}}, 2) != ROUND(g.{{EXT_USD}}, 2) THEN 1 ELSE 0 END) / COUNT(*), 1) AS PCT_ROWS_MISMATCHED,
    ROUND(SUM(m.{{EXT_USD}}), 2) AS MART_TOTAL_USD,
    ROUND(SUM(g.{{EXT_USD}}), 2) AS GOLD_TOTAL_USD,
    ROUND(SUM(m.{{EXT_USD}}) - SUM(g.{{EXT_USD}}), 2) AS NET_USD_DIFF,
    ROUND(SUM(ABS(m.{{EXT_USD}} - g.{{EXT_USD}})), 2) AS GROSS_ABS_USD_DIFF
FROM {{MART_TABLE}} m
JOIN {{GOLDEN_TABLE}} g ON {{PK_JOIN}} AND m.{{CURRENCY_COL}} = g.{{CURRENCY_COL}}
WHERE m.{{CURRENCY_COL}} != 'USD';
```

### Output

Two tables — per-currency breakdown and grand total.

**[STOP]** Review impact numbers with the analyst. Key decision point: are the magnitudes consistent with the rate logic identified in Phase 2, or do they suggest an additional root cause? The analyst may choose to skip anomaly detection (Phase 6) if impact is fully explained. **Update checkpoint.**

---

## Phase 6: Anomaly Detection

**Goal:** Flag data quality issues beyond simple rate date differences.

### Checks

**1. Zero-rate rows:**
```sql
SELECT '{{SIDE}}' AS source, {{CURRENCY_COL}}, COUNT(*) AS cnt
FROM {{TABLE}}
WHERE {{RATE_COL}} = 0 AND {{CURRENCY_COL}} != 'USD'
GROUP BY 1, 2;
```
Zero rates produce $0 USD amounts and are likely bugs.

**2. Unknown/unusual currencies:**
```sql
SELECT {{CURRENCY_COL}}, COUNT(*) AS cnt
FROM {{TABLE}}
WHERE {{CURRENCY_COL}} NOT IN ('USD','EUR','GBP','JPY','CAD','AUD','CHF','CNY','SEK','NZD','MXN','SGD','HKD','NOK','KRW','TRY','INR','RUB','BRL','ZAR','DKK','PLN','TWD','THB','MYR','IDR','CZK','ILS','CLP','PHP','AED','COP','SAR','RON','HUF','BGN','HRK','PEN','ARS','EGP','PKR','NGN','UAH','VND','KZT','QAR','KWD','BHD','OMR')
GROUP BY 1;
```

**3. Identity rate for non-USD:**
```sql
SELECT {{CURRENCY_COL}}, COUNT(*) AS cnt
FROM {{TABLE}}
WHERE {{CURRENCY_COL}} != 'USD' AND {{RATE_COL}} = 1.0
GROUP BY 1;
```
May indicate a defaulting/fallback that masks missing rate data.

**4. Rate matches no known month:**
For a sample of rows, check if the stored rate matches the FX function for ANY month in a reasonable window around the order date.

**5. Massive single-row outliers:**
```sql
SELECT {{PK_COLUMNS}}, m.{{CURRENCY_COL}},
    m.{{EXT_USD}} AS MART_EXT_USD,
    g.{{EXT_USD}} AS GOLD_EXT_USD,
    ABS(m.{{EXT_USD}} - g.{{EXT_USD}}) AS ABS_DIFF
FROM {{MART_TABLE}} m
JOIN {{GOLDEN_TABLE}} g ON {{PK_JOIN}}
WHERE ABS(m.{{EXT_USD}} - g.{{EXT_USD}}) > {{OUTLIER_THRESHOLD}}
ORDER BY ABS_DIFF DESC
LIMIT 20;
```

### Output

Anomaly summary table with counts and sample rows for each category.

---

## Phase 7: Classification & Reporting

**Goal:** Produce the final FX RCA report and update the checkpoint YAML.

### Classification Logic

| User Expectation | Finding | Classification |
|------------------|---------|----------------|
| Mart = same rate as the baseline | Rates differ | **(a) Unexpected — mart using wrong date** |
| Mart = ETL refresh rate | Rates differ by monthly FX movement | **(b) Expected / by design — ETL refresh rate drift** |
| Mart = current month | Rates differ, mart rate = current month | **(b) Expected / by design** |
| Unknown (investigated) | Depends on Phase 2 findings | State what was found |
| Zero-rate or missing-rate rows | Either side | **(c) Data quality issue — flag for fix** |
| Unknown currencies with $0 on one side | | **(c) Data quality issue** |

For each divergent USD column, present the classification and ask the analyst to confirm before writing.

**[STOP]** Analyst reviews classification before report generation. **Update checkpoint.**

### RCA Report Template

Write to `validation-workspace/deep-dives/rca_fx_<mart_short_name>_<user>_YYMMDDHH.md`:

```markdown
---
user: <CURRENT_USER()>
role: <CURRENT_ROLE()>
mart: <mart table>
timestamp: <YYYY-MM-DD HH:MI:SS>
session_id: UC4-<date>-<entity>-FX
---

# RCA Report — FX Rate Divergence

**Columns:** `<usd_columns>`
**Mart:** `<mart>`
**Baseline:** `<baseline>`
**Investigation Date:** <today>
**Analyst:** <user>
**Audience:** <from audience prompt>

---

## Summary

<Total mismatches, overall classification, 2-3 sentence executive summary>

## Root Cause

### Rate Date Logic Difference

| System | Rate Logic | Date Used | FX Function |
|--------|-----------|-----------|-------------|
| Baseline | ... | ... | ... |
| Mart | ... | ... | ... |

### Evidence
<Key SQL results proving the root cause>

## Impact by Currency

<Phase 5 per-currency table>

## Grand Total Impact

<Phase 5 grand total row>

## Anomalies

<Phase 6 findings>

## Classification

| Column | Classification | Rationale |
|--------|---------------|-----------|
| ... | (a/b/c) ... | ... |

## Recommended Next Steps

<Numbered list based on findings>
```

### Checkpoint YAML Update

Update the checkpoint YAML's `divergence_classifications` section:
```yaml
<USD_COLUMN>: "<classification> — <one-line summary>"
```

Add to `findings_so_far`:
```yaml
- "UC4 FX: <summary of finding with key numbers>"
```

Add to `artifacts_generated`:
```yaml
- rca_report_fx: "<path to RCA report>"
```

---

## Guardrails

- **Row limits:** Never query more than `sample_limit` rows in exploratory queries. Use `LIMIT` always. For aggregations, let them run on the full filtered dataset but warn the user if the join is expected to exceed 10M rows.
- **Function calls:** When calling the FX function in bulk, limit to ~200 calls per query (use UNION ALL pattern or join to a date spine).
- **Semantic view syntax:** When querying a semantic view, use `SEMANTIC_VIEW(<sv_name> FACTS <entity>.<col>, ...)` syntax. Column references inside the SEMANTIC_VIEW clause must be entity-qualified. Column references OUTSIDE (in WHERE, JOIN, ORDER BY on the aliased result) use the unqualified alias.
- **No destructive operations:** This workflow only reads data and writes markdown/YAML files. It never creates, alters, or drops database objects.
- **Confirm before writing:** Always show the analyst the classification and summary before writing the RCA report and updating the checkpoint.
