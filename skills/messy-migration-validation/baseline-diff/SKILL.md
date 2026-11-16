---
name: baseline-diff
parent_skill: messy-migration-validation
description: "Baseline comparison — aggregate and row-level divergence detection between a new mart and its legacy baseline. Uses PK-based FULL OUTER JOIN with column-level mismatch detection and float tolerance. Triggers: compare, comparison, diff, divergence, match rate, baseline comparison."
---

# Baseline Comparison

**You are using the baseline-diff sub-skill. Follow these instructions exactly.**

## When to Use

- Comparing a data mart against a baseline for aggregate and row-level divergences
- Running HASH_AGG per-column exact-match checks
- Detecting missing/extra rows via PK-based FULL OUTER JOIN
- Generating a combined semantic view for natural-language follow-up questions

## Workflow

### Prerequisites

1. Read `references/sql-patterns.md` — contains all comparison SQL templates
2. Read `../references/conventions.md` — naming standards and table schemas
3. A semantic view must exist in Snowflake from UC2. If not found, ask the analyst to run UC2 first.

### Step 1: Gather Inputs

Ask the analyst for:
1. **Semantic view**: Fully qualified name of the semantic view deployed in UC2 (e.g., `DB.SCHEMA.MART_NAME_SV`)
2. **Group-by columns** (optional): Are there specific dimensions the analyst wants to slice comparisons by (e.g., REGION, SOURCE_SYSTEM, PRODUCT_LINE)? These will be used in both row count and aggregate metric breakdowns (Patterns 1.2 and 1.3b). Accept a list of column names or "none" (default — totals only).
3. **Column scope** (optional): Does the analyst want to compare all mapped columns or a specific subset? Accept a list of column names or "all" (default). Useful when the analyst wants to focus on known problem areas or high-priority columns first.

Retrieve the semantic view definition from Snowflake (`DESCRIBE SEMANTIC VIEW`) to extract the mart table, baseline view, primary keys, and column pairs. The semantic view does not carry transforms or baseline filters — read those from the mapping artifact (`validation-workspace/mappings/{mart-name}-mapping.yaml`). If the analyst specified a column scope, filter the column pairs list to only those in scope. Primary key columns are always included regardless of scope.

If the semantic view is not found, ask the analyst to run UC2 first.

**Create checkpoint** per `../references/conventions.md` — Checkpoint Initialization. The checkpoint must be written now, before the first [STOP], so a resumable record exists from session start.

**[STOP]** Confirm comparison scope — which columns to compare — with analyst. **Update checkpoint.**

## Step 1b: Surface Transforms (Advisory)

Before running any comparison, check the mapping artifact for transforms between baseline and mart columns. If transforms exist, surface them:

```
Note: The following transforms are defined between baseline and mart columns:
- AMOUNT: ROUND(baseline.AMT, 2) → mart.AMOUNT
- CUSTOMER_NAME: UPPER(TRIM(baseline.CUST_NM)) → mart.CUSTOMER_NAME
```

This is advisory only — let the analyst decide if transforms are relevant to interpreting divergences. Do not auto-classify divergences based on transform presence.

### Step 2: Phase 1 — Aggregate Comparison

**FIRST**: Read `references/sql-patterns.md` and execute the Phase 1 patterns in sequence on **in-scope columns only** (if a column scope was specified in Step 1):

1. **Row Count Comparison** (Pattern 1.1) — total rows in each table
2. **Row Count by Dimension** (Pattern 1.2) — compare counts by `{{GROUP_BY_COLUMNS}}` if provided by the analyst; skip if none
3. **Aggregate Comparison — Numeric** (Pattern 1.3) — one batched query over all numeric non-key column pairs: sum, avg, min, max with `_DIFF` and `_DIFF_PCT`. If `{{GROUP_BY_COLUMNS}}` were provided, also run Pattern 1.3b (aggregate by dimension) to surface where divergences concentrate.
4. **HASH_AGG Comparison** — Pattern 1.4 if row counts match; Pattern 1.4b (matched keys only) if they differ

For each query, substitute placeholders from the semantic view definition and mapping artifact.

### Interpreting Phase 1 Results

| Observation | Meaning | Action |
|-------------|---------|--------|
| Row counts match, all HASH_AGG match | Tables are identical | Report pass, skip Phase 2 |
| Row counts match, some HASH_AGG mismatch | Same rows, different values | Proceed to Phase 2 for mismatched columns only |
| Row counts differ | Missing or extra rows | Run Key Match Analysis (Pattern 2.1b), then Phase 2 on matched keys |
| Aggregate sums diverge significantly | Magnitude error or missing data | Proceed to Phase 2 with focus on divergent columns |

Present a summary table of Phase 1 findings to the analyst.

**[STOP]** Analyst reviews aggregate findings and decides whether to proceed to Phase 2. **Update checkpoint.**

### Step 3: Phase 2 — Row-Level Diff

Only run on divergent columns/segments from Phase 1. If a column scope was specified, Phase 2 is further limited to in-scope columns.

Execute from `references/sql-patterns.md`:

1. **Key Match Analysis** (Pattern 2.1b) — rows in the new mart but not the baseline, and vice versa, summarized by segment (use Pattern 2.1 only to export full key lists)
2. **Column-Level Row Diff** (Pattern 2.2) — for each divergent column:
   - Use the non-float pattern for VARCHAR, DATE, BOOLEAN columns
   - Use the float pattern (with `{{FLOAT_TOLERANCE_PCT}}` default 1%) for FLOAT/NUMBER columns
3. **Full Column Comparison Summary** (Pattern 2.3) — aggregate mismatch counts
4. **Overall Match Score** (Pattern 2.4) — percent passed

### Float Tolerance

Default: values within 1% are considered matching. The analyst can adjust this. Float comparison uses the `DIV0NULL` approach from the `show_diff()` pattern to handle zeros and NULLs safely.

**[STOP]** Present Phase 2 row-level diff results to the analyst before classifying divergences. **Update checkpoint.**

## Step 3b: Classify Divergences

Before classifying any divergence as a defect, present each one to the analyst. Divergences come in two forms; use the matching prompt.

**Column divergence** (values differ on matched keys):

```
Column {{COLUMN_NAME}} shows a divergence ({{MISMATCH_COUNT}} rows, {{DIFF_PCT}}% difference).
Is this divergence:
  a) Unexpected — investigate as a potential defect
  b) Expected / by design — document and move on
  c) Unsure — flag for further review
```

**Row-segment divergence** (keys present on one side only, from Pattern 2.1b):

```
{{ROW_COUNT}} {{MART_ONLY | BASELINE_ONLY}} rows in segment {{SEGMENT}} ({{AMOUNT_IMPACT}}).
Is this divergence:
  a) Unexpected — investigate as a potential defect
  b) Expected / by design — document and move on
  c) Unsure — flag for further review
```

Record each classification in the checkpoint with `scope: column` or `scope: row_segment` (see conventions). Only divergences classified as (a) or (c) proceed to UC4 deep dive.

**[STOP]** Confirm all divergence classifications are recorded before generating the comparison report. **Update checkpoint.**

## Step 4: Generate Comparison Report

Before generating, ask the analyst:

```
Who is the primary audience for this report?
  a) Business user — executive summary, plain language, key metrics only
  b) Technical user — full detail, SQL references, column-level breakdowns
  c) Other — (free text: describe the audience)
```

Tailor the report depth and language accordingly. Business reports emphasize match scores and action items; technical reports include SQL, sample rows, and pattern references.

Create a markdown report at `validation-workspace/comparisons/{mart-name}-comparison-{YYYY-MM-DD}.md`:

1. **Header**: Mart name, baseline, comparison date, RUN_ID
2. **Phase 1 Summary**: Row counts, HASH_AGG matches/mismatches, aggregate divergences
3. **Phase 2 Details** (if run):
   - Key match: rows only in mart, rows only in the baseline
   - Column mismatch summary: columns ranked by mismatch count
   - Sample mismatched rows (top 20 per column)
4. **Overall Score**: Percent of columns passing, percent of rows matching

### Step 5: Write to COMPARISON_RESULTS

Create the table if needed (DDL in `../references/conventions.md`), then insert one row per check using Pattern 3.1 in `references/sql-patterns.md`. Record expected divergences with `STATUS = 'EXPECTED'`.

### Step 6: Log to Testing Record

Log per `../references/conventions.md` — Logging to VALIDATION_TEST_LOG:
- `TEST_TYPE`: 'comparison'
- `RESULT_SUMMARY`: Match score, column pass/fail counts, row count delta
- `STATUS`: 'pass' if 100% match, 'fail' if mismatches found, 'warning' if within tolerance

## Step 7: Offer Next Steps

```
Comparison complete. Match score: XX.X%

Divergences classified as "unexpected" or "unsure": {{COUNT}}

Would you like to:
a) Deep dive on divergences (UC4 — root cause analysis)
b) Ask natural-language comparison questions via the semantic view (deployed in UC2)
c) View/update the testing record
```

## Output

- `validation-workspace/comparisons/{mart-name}-comparison-{date}.md` — comparison report
- `COMPARISON_RESULTS` table populated
- `VALIDATION_TEST_LOG` entry

## Important Rules

1. Use the SQL patterns from `references/sql-patterns.md` — do not generate custom comparison SQL
2. Always run Phase 1 first — only proceed to Phase 2 on divergent segments
3. Float tolerance is configurable — default 1%, let the analyst override
4. Limit row-level diffs to 1000 rows per column to prevent query runaway
5. The semantic view from UC2 is assumed to exist — do not create one in this use case
6. Respect column scope — if the analyst limited which columns to compare, only run patterns for those columns (PK columns always included)
7. Never classify a divergence as a defect without asking the analyst first
8. At every [STOP] point, emit a one-line session hygiene reminder and update the session checkpoint per `../references/conventions.md` — Session Hygiene. Checkpoint writes are mandatory — never skip them.
