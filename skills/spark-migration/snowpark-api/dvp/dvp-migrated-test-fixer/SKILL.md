---
name: dvp-migrated-test-fixer
description: "Fix migrated Snowpark test code until all tests PASS (not just run). Uses git-based iteration to diagnose and resolve PySpark-to-Snowpark API incompatibilities. Triggers: fix migrated tests, make migrated tests pass, snowpark test errors, migration test failures."
parent_skill: dvp-orchestrator
---

# DVP Migrated Test Fixer

## Overview

After `dvp-test-runner` (Step 14) ensures migrated tests **run**, this skill makes them **pass**. It iterates over failing migrated tests, diagnoses PySpark-to-Snowpark API incompatibilities in `dvp/02-migrated/` code (and test infrastructure in `dvp/03-tests/migrated/`), applies fixes, and retries — using **git commits** after each improvement to track changes and prevent infinite loops.

## Preconditions

1. DVP workspace exists (`dvp/01-source/`, `dvp/02-migrated/`, `dvp/03-tests/`, `dvp/04-results/`).
2. `dvp-test-runner` has completed: ALL migrated tests **execute** (collected and run by pytest). Tests may FAIL with assertion errors — that is the starting state for this skill.
3. Source tests have PASSED: baseline CSVs exist in `dvp/03-tests/data/expected_output/`.
4. Python environment is ready: `dvp/.venv/` with all required packages.
5. Snowflake connection is configured and working.
6. **Git is initialized** on the output folder (`<output>/`) with branch `sma/migration-process`. This is a **hard requirement** — the skill uses git commits to track each fix iteration and detect loops. If git is not configured, **STOP** and run:
   ```python
   result = sma_api.git_ensure_ready("<output>")
   ```

## Inputs

| Input | Required | Location |
|-------|----------|----------|
| Migrated code | Yes | `dvp/02-migrated/` |
| Migrated test files | Yes | `dvp/03-tests/migrated/` (or `migrated_scos/`) |
| Migrated conftest | Yes | `dvp/03-tests/migrated/conftest.py` |
| Root conftest | Yes | `dvp/03-tests/conftest.py` |
| Source test files | Yes | `dvp/03-tests/source/` (reference for correct IO declarations) |
| Baseline CSVs | Yes | `dvp/03-tests/data/expected_output/` (produced by source tests) |
| Synthetic data | Yes | `dvp/04-results/synthetic_data/` |
| Git repository | Yes | `<output>/` must be a git repo on `sma/migration-process` branch |

## Outputs

| Output | Format | Location |
|--------|--------|----------|
| Fixed migrated code | Python (.py) | `dvp/02-migrated/` (in-place) |
| Fixed test infrastructure | Python (.py) | `dvp/03-tests/migrated/conftest.py` (in-place) |
| Fixed test files | Python (.py) | `dvp/03-tests/migrated/` (in-place) |
| Git commit history | Git commits | `sma/migration-process` branch |

## Output Format

Every time you begin a step, sub-step, or significant action, prefix the message with a timestamp in the format `[YYYY-MM-DD HH:MM:SS]`. Obtain the current time by running `date '+%Y-%m-%d %H:%M:%S'` in bash.

## Acceptance Criteria (SUCCESS DEFINITION)

**The skill is considered successful ONLY when ALL of the following are met:**

1. **ALL migrated tests MUST PASS (green).** Every migrated test class must be collected, executed, and its assertions satisfied. Zero failures, zero errors. If any test fails, the skill MUST diagnose the root cause in the migrated code (not the test data or baselines), apply a fix, commit, and re-run.
2. **Test data MUST NOT be modified.** Input CSVs in `dvp/04-results/synthetic_data/` and baseline CSVs in `dvp/03-tests/data/expected_output/` are IMMUTABLE. The migrated code must produce the same results as source code using the same data.
3. **Every fix MUST be git-committed** before retrying. This creates a traceable history of changes and allows detecting loops (same error recurring after a fix).
4. **Per-test retry limit: 5 attempts.** Each individual test gets at most 5 fix attempts. If a test cannot be fixed after 5 tries, apply the skip policy (Step 4.5).

## Procedure

### Step 0: Verify Git Readiness

Verify the output folder is a git repository on the `sma/migration-process` branch:

```bash
cd <output> && git branch --show-current
```

**Expected:** `sma/migration-process`

**If git is not initialized**, STOP and report:
```
Git repository not initialized. This skill requires git for tracking fix iterations.

Run the dvp-test-runner skill first (Step 14), which initializes git via sma_api.git_ensure_ready().
```

Check for uncommitted changes and commit them before starting:
```bash
cd <output> && git status --porcelain
```

If there are uncommitted changes, commit them:
```bash
cd <output> && git add -A && git commit -m "Pre-fixer state: migrated tests run but may not pass"
```

### Step 1: Run Full Migrated Test Suite (Baseline)

Run all migrated tests to establish the current pass/fail baseline:

```bash
cd <workload_path>/dvp/03-tests && \
SNOWFLAKE_CONNECTION_NAME=<conn> \
SNOWFLAKE_TEST_DATABASE=<db> \
SNOWFLAKE_TEST_SCHEMA=<schema> \
SNOWFLAKE_TEST_ROLE=<role> \
<workload_path>/dvp/.venv/bin/python -m pytest <migrated_flavor>/ -v --tb=long 2>&1
```

Parse the output:
- Count total tests, passed, failed, errored
- For each failing test, capture the full traceback
- Record the failing test name and error message

**If ALL tests already pass → DONE.** Proceed to Final Summary.

### Step 2: Classify Errors and Prioritize

Group failing tests by error type. Fix errors in this priority order:

1. **Shared utility/function errors** (fix once, many tests benefit) — errors in `dvp/02-migrated/utils/` or `dvp/02-migrated/pipelines/`
2. **Test infrastructure errors** — errors in `dvp/03-tests/migrated/conftest.py` (e.g., `_read_output` issues)
3. **Per-job errors** — errors in individual `dvp/02-migrated/jobs/` files
4. **Test file errors** — errors in `dvp/03-tests/migrated/` test files (IO declarations, `_call_main` wrappers)

### Step 3: Fix-and-Retry Loop

For each group of errors:

#### 3.1 Diagnose the Root Cause

Read the full traceback and match against the **Snowpark Migration Error Patterns** table (below). Multiple errors in the same file should be fixed together in a single pass.

#### 3.2 Apply the Fix

Edit the migrated code in `dvp/02-migrated/` or the test infrastructure in `dvp/03-tests/migrated/`. **Rules:**
- Fix the **migrated code**, not the test data or baselines
- Fix in the **correct layer**: API incompatibilities go in `dvp/02-migrated/`, test plumbing goes in `dvp/03-tests/migrated/`
- When fixing a shared function (e.g., in `utils/transformations.py`), verify the fix doesn't break other callers
- Reference the **source tests** (`dvp/03-tests/source/`) to see the correct IO declarations (INPUT_FILES, OUTPUT_FILES, key_columns) — copy them to migrated tests when they differ

#### 3.3 Git Commit

After each logical fix (one or more related file changes), commit:

```bash
cd <output> && git add -A && git commit -m "Fix <entrypoint>: <brief description of what was fixed>"
```

Example commit messages:
- `Fix transformations: use F.when() chain instead of create_map Column indexing`
- `Fix fraud_detection: use Snowflake timestamp format YYYY-MM-DD HH24:MI:SS`
- `Fix conftest: _read_output detects parquet vs CSV from OUTPUT_FILES format`
- `Fix product_affinity: use Snowpark alias column names PRODUCT_IDA/PRODUCT_IDB`

#### 3.4 Re-run Tests

Run the full migrated test suite again:

```bash
cd <workload_path>/dvp/03-tests && \
SNOWFLAKE_CONNECTION_NAME=<conn> \
SNOWFLAKE_TEST_DATABASE=<db> \
SNOWFLAKE_TEST_SCHEMA=<schema> \
SNOWFLAKE_TEST_ROLE=<role> \
<workload_path>/dvp/.venv/bin/python -m pytest <migrated_flavor>/ -v --tb=long 2>&1
```

#### 3.5 Evaluate Progress

Compare results with the previous run:
- **New tests passing?** → Progress is being made. Continue.
- **Same errors recurring?** → Check git log to see if the same fix was already attempted. Try a different approach.
- **New errors introduced?** → The fix broke something. Revert the commit (`git revert HEAD`) and try a different approach.
- **All tests pass?** → DONE. Proceed to Final Summary.

#### 3.6 Repeat

Go back to 3.1 with the new set of failing tests. Continue until all tests pass or all failing tests have hit their per-test retry limit.

### Step 4: Per-Test Tracking

Maintain a mental or explicit count of fix attempts per test. Track by test class name (e.g., `TestFraudDetection`, `TestRetailPipeline`).

#### 4.5 Unfixable Tests (LAST RESORT)

If a specific test has been attempted **5 times** and still cannot pass:

1. **Verify it is truly unfixable** — not just hard. The error must be:
   - A fundamental Snowpark limitation with no workaround
   - A semantic difference between PySpark and Snowpark that cannot be bridged
   - NOT a data issue, schema issue, or API pattern issue (those ARE fixable)

2. **Add `pytest.mark.skip` with a detailed reason:**
   ```python
   # DVP-SKIP: This test cannot pass because <detailed reason>.
   # Attempted fixes: <list what was tried>.
   # Root cause: <explain the fundamental issue>.
   @pytest.mark.skip(reason="DVP: <Snowpark limitation | semantic difference> — <brief explanation>")
   class TestXxx(BaseMigratedWorkloadTest):
   ```

3. **Commit the skip** with a descriptive message:
   ```bash
   git commit -m "Skip <test_name>: <reason> (5 attempts exhausted)"
   ```

4. Mark as `Partial` in the final summary.

### Step 5: Final Summary

**MANDATORY**: After all tests pass (or all failing tests are resolved/skipped), present:

```
Migrated Test Fixer Complete

┌──────────────────────────┬──────────┬──────────────────────────────────────────┐
│ Metric                   │ Value    │ Details                                  │
├──────────────────────────┼──────────┼──────────────────────────────────────────┤
│ Total tests              │ N        │ N tests across M entrypoints             │
│ Passed                   │ X        │                                          │
│ Skipped (unfixable)      │ Y        │ <list reasons if any>                    │
│ Fix iterations           │ Z        │ Z git commits on sma/migration-process   │
│ Errors resolved          │ W        │ <summary of error categories>            │
└──────────────────────────┴──────────┴──────────────────────────────────────────┘

Output location: <output>/
Git branch: sma/migration-process (Z new commits)
```

## Snowpark Migration Error Patterns

This is the comprehensive reference of PySpark-to-Snowpark API incompatibilities discovered through real migrations. When diagnosing a failing migrated test, match the error against this table.

### API Incompatibilities

| Error / Symptom | PySpark Code | Snowpark Fix | Details |
|---|---|---|---|
| `TypeError: Unexpected item type: <class 'Column'>` on `create_map` | `create_map(pairs)[F.col(key)]` | Replace with `F.when(F.col(key) == k1, v1).otherwise(F.when(...))` chain | Snowpark `create_map` does not support `Column`-based indexing — only string/int literal keys |
| `datediff() takes 2 positional arguments but 3 were given` or wrong result | `F.datediff(col1, col2)` | `F.datediff("day", start_col, end_col)` | PySpark takes 2 Column args (end, start); Snowpark takes 3: unit string + start + end. Result = end - start |
| `unexpected keyword argument 'ignorenulls'` | `F.last_value(col, ignorenulls=True)` | `F.last_value(col, ignore_nulls=True)` | Snowpark parameter has underscore: `ignore_nulls` |
| `SnowparkSQLException: cannot cast TIMESTAMP to BIGINT/LONG` | `F.col("ts").cast("long")` | `F.unix_timestamp(F.col("ts"))` | Snowpark cannot cast TIMESTAMP to numeric types directly |
| Column name becomes `"SUM(COL)"` instead of `sum(col)` | `groupBy().sum("col")` or `.withColumnRenamed("sum(col)", ...)` | `.agg(F.sum("col").alias("desired_name"))` | Snowpark generates uppercase function-wrapped names for aggregations |
| `Window function cannot be nested` | `F.last_value(F.when(...).over(window))` in another window | Materialize intermediate: `df.withColumn("_temp", expr.over(window1))` then `F.last_value(F.col("_temp"), ...).over(window2)` | Snowpark does not allow nested window function expressions |
| `invalid identifier '"a.product_id"'` | `df.alias("a"); F.col("a.product_id")` | For self-joins: use auto-generated names `PRODUCT_IDA`, `PRODUCT_IDB`. For non-self-joins: rename columns explicitly before join | Snowpark does not support dotted alias notation `alias.column` |
| `invalid value ['true'] for parameter 'PARSE_HEADER'` | `option("PARSE_HEADER", "true")` | `option("PARSE_HEADER", True)` | Snowpark requires Python boolean `True`, not string `"true"` |
| `invalid parameter 'HEADER'` on CSV write | `write.mode("overwrite").option("header", "true").csv(path)` | `write.copy_into_location(path, file_format_type="csv", header=True, overwrite=True)` | Snowpark DataFrameWriter does not support `header` option for CSV — use `copy_into_location` |
| `INFER_SCHEMA` does not work on CSV | `option("INFER_SCHEMA", "true").csv(path)` | Define explicit schema: `.schema(StructType([StructField(...)]))` | Snowpark CSV reader does not support schema inference — must provide explicit StructType |
| `Can't parse 'yyyy-MM-dd HH:mm:ss'` or wrong timestamp | `to_timestamp(col, "yyyy-MM-dd HH:mm:ss")` | `to_timestamp(col, "YYYY-MM-DD HH24:MI:SS")` | Snowpark uses Snowflake timestamp formats (SQL-style), not Java SimpleDateFormat |
| EWI marker comments out valid code | `# SPRKPY1101: ...` with code on next line commented | Remove EWI comment line and uncomment the code below it | SMA EWI markers (e.g., `SPRKPY1101`) sometimes comment out valid code that needs manual restoration |
| Column names are quoted lowercase (`"customer_id"`) | `PARSE_HEADER=True` on CSV read | Uppercase after read: `for col in df.columns: df = df.with_column_renamed(col, col.upper())` | Snowpark CSV reader with PARSE_HEADER preserves original case wrapped in quotes |

### Parquet Write/Read Issues

| Error / Symptom | PySpark Code | Snowpark Fix | Details |
|---|---|---|---|
| Parquet columns become `_COL_0`, `_COL_1`, etc. | `write.mode("overwrite").parquet(path)` | `write.copy_into_location(path, file_format_type="parquet", header=True, overwrite=True)` | `write.parquet()` in Snowpark uses COPY INTO which strips column names. Must use `copy_into_location` with `header=True` to preserve them |
| DECIMAL columns stored as scaled integers in parquet | `F.avg("col")` → writes to parquet | Cast to DOUBLE before writing: `F.avg("col").cast("double")`, `F.round(...).cast("double")` | Snowpark writes DECIMAL(38,6) to parquet as scaled integers (e.g., 150.0 → 150000000). Casting to DOUBLE avoids this |
| Parquet column order differs from baseline | Joins/withColumn change column order | In `_read_output`, reorder columns using baseline CSV header: `df.select(*upper_baseline)` | Snowpark may produce different column order than PySpark after joins. Use baseline CSV to reorder |

### Test Infrastructure Fixes

These fixes go in `dvp/03-tests/migrated/conftest.py` or individual test files, NOT in `dvp/02-migrated/`.

| Issue | Symptom | Fix |
|---|---|---|
| `_read_output` always reads CSV | Parquet outputs read as CSV → parse errors or wrong data | Check `OUTPUT_FILES[].format` in the test class; use `session.read.parquet()` for parquet, `.csv()` for CSV |
| Parquet `_COL_N` fallback | Column names lost in parquet | In `_read_output`, detect `_COL_N` pattern and rename using baseline CSV headers from `dvp/03-tests/data/expected_output/` |
| Column order mismatch | `test_all_outputs_match_baseline` fails on column order | In `_read_output`, after reading, reorder columns to match baseline CSV header order |
| Dependent jobs need parquet inputs | Jobs like `customer_churn`, `daily_summary` read from another job's parquet output (e.g., `sales/`) | Add `_prepare_parquet_inputs()` to `BaseMigratedWorkloadTest`: convert CSV inputs declared as `format: "parquet"` to actual parquet on the Snowflake stage before running the workload |
| Parquet input columns lowercase | `_prepare_parquet_inputs` writes lowercase columns | After reading CSV with `PARSE_HEADER=True`, uppercase all columns before writing parquet |
| OUTPUT_FILES name mismatch | Test uses wrong output names (e.g., `OUTPUT_INVENTORY_PATH` vs `inventory`) | Copy correct `OUTPUT_FILES` declarations from the corresponding source test file. Names must match actual filesystem subdirectory names |
| `_call_main` returns DataFrame, test expects 0 | Migrated `run()` returns DataFrame; `test_validate_pipeline_runs` asserts `result == 0` | In `_call_main`, wrap: `result = fn(session, path); return 0`. For chained pipelines (where downstream code uses the return), keep the DataFrame return in the migrated code but return 0 from `_call_main` |
| INPUT_FILES missing parquet columns | Dependent jobs expect parquet inputs with specific columns | Add `INPUT_FILES` entries with `format: "parquet"` and the correct column list from the source test. Add `_INPUT_TO_OUTPUT_MAP` to map config variable names to actual stage subdirectory names |

### Cross-Reference: Source Tests as Ground Truth

**CRITICAL RULE:** When migrated test IO declarations (INPUT_FILES, OUTPUT_FILES, key_columns) differ from source tests, the **source test is always correct**. Copy the exact IO declarations from `dvp/03-tests/source/test_<entrypoint>.py` to the corresponding migrated test file.

This is because source tests have already been validated (they pass), so their IO declarations accurately reflect the entrypoint's actual inputs and outputs.

## Stopping Points

- **Git not initialized** → STOP. Run `sma_api.git_ensure_ready()` first.
- **No migrated tests found** → STOP. Run `dvp-test-runner` first.
- **Snowflake connection not configured** → STOP. Cannot run migrated tests without a Snowflake connection.
- **All tests already pass** → DONE immediately.

**These are NOT stopping points:**
- Test assertion failures → diagnose and fix (this is the entire purpose of the skill)
- Snowpark API errors → apply the pattern from the error table
- Multiple consecutive failures → keep iterating as long as each commit makes progress

## Git Workflow

The skill uses git as a safety net and progress tracker:

1. **Before each fix**: Verify you're on `sma/migration-process` branch
2. **After each fix**: `git add -A && git commit -m "Fix <entrypoint>: <description>"`
3. **If a fix breaks things**: `git revert HEAD` and try a different approach
4. **Loop detection**: If `git log --oneline -5` shows the same error being fixed repeatedly, stop and try a fundamentally different approach or skip the test
5. **Final state**: All commits on `sma/migration-process` form a traceable history of every change made

## Anti-Patterns

| Anti-Pattern | Why It's Bad | Correct Approach |
|---|---|---|
| Modifying baseline CSVs to match Snowpark output | Defeats the purpose of validation — source is ground truth | Fix the migrated code to produce correct output |
| Modifying synthetic input data | Both source and migrated tests use the same data | Fix the migrated code's data processing logic |
| Fixing without committing | No way to track progress or revert broken changes | Always commit after each logical fix |
| Fixing test assertions instead of migrated code | Hides real migration bugs | Fix the root cause in `dvp/02-migrated/` |
| Applying same fix repeatedly | Infinite loop — wastes iterations | Check git log, try a different approach, or skip |
| Modifying source code (`dvp/01-source/`) | Source is the ground truth, must not change | Only modify `dvp/02-migrated/` and `dvp/03-tests/migrated/` |
