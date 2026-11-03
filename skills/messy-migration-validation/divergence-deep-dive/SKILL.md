---
name: divergence-deep-dive
parent_skill: messy-migration-validation
description: "Root cause analysis on divergences found during baseline comparison. Drills into specific divergent segments, generates diagnostic queries, isolates join mismatches, transformation logic differences, null handling, type coercion issues, and FX rate divergences. Triggers: deep dive, root cause, RCA, investigate, divergence analysis, why mismatch, FX check, exchange rate, currency conversion, USD difference, FX rate validation."
---

# Divergence Deep Dive

**You are using the divergence-deep-dive sub-skill. Follow these instructions exactly.**

## When to Use

- A baseline comparison has identified divergences that need root cause analysis
- Investigating why specific columns or segments don't match
- Classifying divergence patterns (null handling, type coercion, join fanout, filter mismatch)
- Investigating FX rate divergences in USD amount columns (dedicated workflow)
- Generating an RCA report with recommended fixes

## Workflow

### Prerequisites

1. Read `../references/conventions.md` — naming standards
2. A comparison report should exist from UC3 (`validation-workspace/comparisons/{mart-name}-comparison-{date}.md`)
3. A mapping artifact should exist from UC2 (`validation-workspace/mappings/{mart-name}-mapping.yaml`)
4. The validation semantic view deployed in UC2 (Snowflake object, YAML at `validation-workspace/semantic-views/{mart-name}-sv.yaml`)

### Step 1: Gather Context

Ask the analyst for:
1. **Which mart** is being investigated
2. **Which divergences** to drill into — default to those classified unexpected or unsure in UC3 (columns, row segments, or "all")

Read the comparison report and mapping artifact to understand:
- Which columns failed
- What the match score was
- Whether mismatches are concentrated in specific segments

**Create checkpoint** per `../references/conventions.md` — Checkpoint Initialization. The checkpoint must be written now, before the first [STOP], so a resumable record exists from session start.

**[STOP]** Confirm investigation scope with the analyst. **Update checkpoint.**

### Step 2: Classify Divergence Patterns

For each divergent column or row segment, classify the likely root cause:

| Pattern | Indicators | Diagnostic Approach |
|---------|-----------|---------------------|
| **Null handling** | One side has NULLs where other has values | Query rows where one is NULL and other is not |
| **Type coercion** | Values look similar but compare differently | Cast both to VARCHAR and compare; check trailing spaces, precision |
| **Float precision** | Numeric values very close but not exact | Reduce tolerance threshold; check rounding in transforms |
| **Join fanout** | Baseline row count > new mart row count for same key | Count rows per PK in each table; look for duplicates |
| **Missing join key** | Rows in one table with no match | Check for NULLs in PK columns; check filter exclusions |
| **Orphan / mart-only rows** | Keys exist in the new mart but not the baseline, and the filter does not explain them | Check whether the keys and their foreign keys exist anywhere in the raw legacy tables (Orphan Row Diagnostic); profile their attributes for synthetic or test patterns |
| **Transform logic** | Systematic pattern in differences | Sample mismatched rows; compare raw vs transformed values |
| **Date/time zone** | Date columns off by hours | Check for timezone-aware vs naive timestamps |
| **Filter mismatch** | Row count differs systematically | Review filter conditions on both sides |
| **FX rate divergence** | USD amount columns differ while local amounts match; currency code column present | Load `references/fx-patterns.md` and follow the FX workflow (Step 3a) |

### Step 3a: FX Rate Divergence Workflow

If the classification in Step 2 identifies **FX rate divergence** — or the analyst explicitly requests an FX check — load `references/fx-patterns.md` and follow its 7-phase workflow (Phases 0–7) instead of the general diagnostic queries below.

Trigger conditions (any one is sufficient):
- Divergent columns include USD amount columns (`*USD*`, `*EXTENDED_USD*`, `*UNIT_USD*`)
- The comparison report shows local amounts match but USD amounts differ
- A currency code column exists and the analyst mentions FX, exchange rate, or currency conversion

After the FX workflow completes (Phase 7 report generated), skip to Step 6 (Log to Testing Record).

### Step 3b: General Diagnostic Queries

For non-FX divergences, generate and execute targeted queries for each suspected root cause:

### Null Mismatch Diagnostic
```sql
SELECT
    {{PK_COLUMNS}},
    m.{{MART_COL}} AS mart_value,
    g.{{GOLDEN_COL}} AS golden_value
FROM {{MART_TABLE}} m
FULL OUTER JOIN {{GOLDEN_TABLE}} g ON {{PK_JOIN}}
WHERE (m.{{MART_COL}} IS NULL AND g.{{GOLDEN_COL}} IS NOT NULL)
   OR (m.{{MART_COL}} IS NOT NULL AND g.{{GOLDEN_COL}} IS NULL)
LIMIT 50;
```

### Duplicate Key Detection (run once per table)
```sql
SELECT {{PK_COLUMNS}}, COUNT(*) AS cnt
FROM {{MART_TABLE}}
GROUP BY {{PK_COLUMNS}}
HAVING COUNT(*) > 1
ORDER BY cnt DESC
LIMIT 20;
```
```sql
SELECT {{PK_COLUMNS}}, COUNT(*) AS cnt
FROM {{GOLDEN_TABLE}}
GROUP BY {{PK_COLUMNS}}
HAVING COUNT(*) > 1
ORDER BY cnt DESC
LIMIT 20;
```

### Transform Verification
```sql
SELECT
    g.{{GOLDEN_COL}} AS raw_source_value,
    {{TRANSFORM_EXPRESSION}} AS transformed_value,
    m.{{MART_COL}} AS mart_value,
    CASE WHEN {{TRANSFORM_EXPRESSION}}::VARCHAR = m.{{MART_COL}}::VARCHAR THEN 'MATCH' ELSE 'MISMATCH' END AS status
FROM {{GOLDEN_TABLE}} g
JOIN {{MART_TABLE}} m ON {{PK_JOIN}}
WHERE {{TRANSFORM_EXPRESSION}}::VARCHAR <> m.{{MART_COL}}::VARCHAR
LIMIT 50;
```

### Orphan Row Diagnostic
```sql
SELECT
    COUNT(*) AS mart_only_rows,
    COUNT_IF(r.{{LEGACY_PK}} IS NOT NULL) AS found_in_raw_legacy,
    COUNT_IF(d.{{LEGACY_FK}} IS NOT NULL) AS fk_found_in_legacy_dimension,
    COUNT_IF(m.{{PK_COL}} IS NULL) AS null_keys,
    MIN(m.{{PK_COL}}) AS min_key,
    MAX(m.{{PK_COL}}) AS max_key
FROM {{MART_TABLE}} m
LEFT JOIN {{GOLDEN_TABLE}} g ON m.{{PK_COL}} = g.{{PK_COL}}
LEFT JOIN {{LEGACY_FACT_TABLE}} r ON m.{{PK_COL}} = r.{{LEGACY_PK}}
LEFT JOIN {{LEGACY_DIM_TABLE}} d ON m.{{MART_FK_COL}} = d.{{LEGACY_FK}}
WHERE g.{{PK_COL}} IS NULL;
```

If `found_in_raw_legacy = 0` and `fk_found_in_legacy_dimension = 0`, the rows have no legacy source. Sample them (LIMIT 50) and look for synthetic patterns: sequential IDs outside the legacy range, placeholder names, single-value attributes.

### Filter Exclusion Check
```sql
SELECT 'Mart excluded by baseline filter' AS check_type, COUNT(*) AS cnt
FROM {{MART_TABLE}} m
WHERE NOT ({{GOLDEN_FILTER_CONDITION}})

UNION ALL

SELECT 'Baseline excluded by mart filter' AS check_type, COUNT(*) AS cnt
FROM {{GOLDEN_TABLE}} g
WHERE NOT ({{MART_FILTER_CONDITION}});
```

Present diagnostic results to the analyst with an explanation of what each finding means.

For each diagnosed divergence, before classifying it as a defect, ask:

```
Column {{COLUMN_NAME}} — Root cause: {{CLASSIFICATION}} ({{SAMPLE_COUNT}} sample rows examined).
Is this divergence:
  a) Unexpected — escalate as a potential defect
  b) Expected / by design — document and close
  c) Unsure — flag for further review
```

Record the analyst's classification in the RCA report.

**[STOP]** Analyst reviews diagnostic findings and classifies each divergence. **Update checkpoint.**

### Step 4: Leverage Semantic View

The UC2 validation semantic view (logical tables `MART` and `BASELINE`) can be used with `cortex analyst query` CLI for exploratory follow-up questions:

```
The validation semantic view can answer natural-language questions via:
  cortex analyst query "<question>" --view=<semantic_view>

Examples:
- cortex analyst query "Where does total revenue diverge between mart and baseline by region?" --view=<view>
- cortex analyst query "Show me the top 10 customers where invoice amounts don't match" --view=<view>
- cortex analyst query "What's the distribution of mismatches by date?" --view=<view>
```

### Step 5: Generate RCA Report

Before generating, ask the analyst:

```
Who is the primary audience for this report?
  a) Business user — executive summary, plain language, key findings only
  b) Technical user — full detail, diagnostic SQL, sample rows, root cause evidence
  c) Other — (free text: describe the audience)
```

Tailor the report depth and language accordingly.

Create a report at `validation-workspace/deep-dives/{mart-name}-rca-{YYYY-MM-DD}.md`:

1. **Header**: Mart name, investigation date, columns investigated
2. **Findings per column**:
   - Root cause classification
   - Sample mismatched rows
   - Diagnostic query used
   - Recommended fix (if applicable)
3. **Summary**: Overall assessment of divergence causes

### Step 6: Log to Testing Record

Log per `../references/conventions.md` — Logging to VALIDATION_TEST_LOG:
- `TEST_TYPE`: 'deep_dive'
- `RESULT_SUMMARY`: Root causes found, columns investigated
- `STATUS`: 'info'

## Output

- `validation-workspace/deep-dives/{mart-name}-rca-{date}.md` — RCA report
- `VALIDATION_TEST_LOG` entry
- Diagnostic query results presented inline

## Important Rules

1. Always start from the comparison report — don't re-run comparison
2. Classify before querying — avoid expensive exploratory queries without a hypothesis
3. Limit diagnostic queries (LIMIT 50) to prevent large result sets
4. If the semantic view exists, offer it as an exploration tool — don't force it
5. Present findings with recommended actions, not just raw data
6. At every [STOP] point, emit a one-line session hygiene reminder and update the session checkpoint per `../references/conventions.md` — Session Hygiene. Checkpoint writes are mandatory — never skip them.
