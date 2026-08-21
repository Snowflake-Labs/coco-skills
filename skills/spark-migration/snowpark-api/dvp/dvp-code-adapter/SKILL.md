---
name: dvp-code-adapter
description: "Adapt source and migrated workloads for testing: inject session parameters, add enableHiveSupport(), defer env vars, and record adapted_source in entrypoints.json. Status: design phase."
---

# DVP Code Adapter

## Overview

Adapts workload copies in `dvp/01-source/`, `dvp/02-migrated/`, and `dvp/02-migrated_scos/` so they can be tested by the DVP test framework. All targets need the same core transformations: session injection and deferred environment variables. Source workloads additionally need Hive enablement.

After adapting each entrypoint, the skill writes `adapted_source` into `entrypoints.json` so downstream skills know exactly what callable to invoke.

## Status

**Design phase** -- the approach is defined but not yet implemented.

## Why This Skill?

| Challenge | Solution |
|-----------|----------|
| Original code creates its own session internally | Inject optional session parameter for test control |
| PySpark source uses `spark.table()` and `saveAsTable()` | Add `.enableHiveSupport()` so Hive metastore works locally (source only) |
| Module-level `os.getenv()` evaluates before test fixtures can set env vars | Defer env var reads to runtime (move into function, global, or init function) |
| `__main__` block may have inline logic instead of a function call | Extract logic to a named function so tests can invoke it directly |
| Downstream skills don't know what function to call after adaptation | Write `adapted_source` field in `entrypoints.json` |

## Design Principle

The code should remain as close to the original as possible. **No IO conversion** and **no refactoring** is performed -- the original logic, structure, and API calls are preserved. The only changes are the minimum required for testability:

1. Session injection (so tests can provide a pre-configured session)
2. `.enableHiveSupport()` (so `spark.table()` works locally with Hive) -- **source only, Python only**
3. Defer module-level env vars (so test fixtures can override paths)
4. Extract inline `__main__` logic to a named function (when needed)

This maximizes fidelity: tests validate the original logic, not an adapted version.

## Targets

### Python

| Target | Code | Session Type | Transformations |
|--------|------|-------------|-----------------|
| `01-source/` | PySpark (copy of original) | `SparkSession` | Session injection + enableHiveSupport + defer env vars |
| `02-migrated/` | Snowpark API (SMA output) | `Session` | Session injection + defer env vars |
| `02-migrated_scos/` | Snowpark Connect (SMA output, PySpark APIs on Snowflake) | `SparkSession` (via `snowpark_connect`) | Session injection + defer env vars |

The `.enableHiveSupport()` transformation only applies to `01-source/` because it's PySpark code that needs a local Hive metastore. `02-migrated/` and `02-migrated_scos/` run against Snowflake -- no Hive needed.

SCOS workloads keep PySpark APIs (`SparkSession`, `withColumn`, `write.csv`, etc.) but the session is created via `snowpark_connect` instead of `SparkSession.builder`. SMA handles the session creation change; the adapter only applies testability transformations.

### Scala

| Target | Code | Session Type | Transformations |
|--------|------|-------------|-----------------|
| `01-source/` | Spark Scala (copy of original) | `SparkSession` | Session injection + defer env vars |

Scala workloads live in `01-source/` only (SMA does not produce Scala→Snowpark migrations yet). The `source` field in `entrypoints.json` uses the `::Scope::method` format to identify the object and method.

## Output Format

Every time you begin a step, sub-step, or significant action, prefix the message with a timestamp in the format `[YYYY-MM-DD HH:MM:SS]`. Obtain the current time by running `date '+%Y-%m-%d %H:%M:%S'` in bash.

Example:
```
[2026-03-24 14:05:32] Starting Step 1: Adapt source workload...
[2026-03-24 14:05:45] Injected session parameters
[2026-03-24 14:05:46] Step 1 complete.
```

## Execution Order

### Step 0: Initialize Git

Ensure the workload directory has a git repository on the `sma/migration-process` branch. This is idempotent — if the orchestrator already initialized git, this is a no-op.

```python
result = sma_api.git_ensure_ready("<workload_path>")
```

```
dvp-orchestrator
      |
      v
stage-conversion  (if stage paths)
      |
      v
dvp-notebook-to-script  (if notebooks)
      |
      v
dvp-asg-generation  ->  XX_asg.json
      |
      v
dvp-entrypoint-identifier  ->  entrypoints.json  (source field set)
      |
      v
dvp-code-adapter  <-- THIS SKILL
      |   adapts 01-source/ + selected migrated folder
      |   writes adapted_source to entrypoints.json
      v
dvp-io-schema-identifier  ->  data_io_schema.json
```

## Entrypoint Cases (Python)

The `__main__` block in Python workloads falls into three patterns. The adapter handles each differently.

### Case A — `__main__` delegates to existing function

The `__main__` simply calls a function that already exists.

**Before:**
```python
def main():
    spark = create_spark_session()
    ...

if __name__ == "__main__":
    sys.exit(main())
```

**After (adapted):**
```python
def main(spark: SparkSession = None):
    should_stop = False
    if spark is None:
        spark = create_spark_session()
        should_stop = True
    ...
    # in finally: if should_stop: spark.stop()

if __name__ == "__main__":
    sys.exit(main())
```

**`adapted_source`:** `workload.py:163::main`

The adapter injects the session parameter into the existing function. The `__main__` block is unchanged.

### Case B — `__main__` creates session + delegates

The `__main__` block creates a session, calls function(s), and does cleanup.

**Before:**
```python
if __name__ == "__main__":
    spark = get_spark_session("DailyReport")
    try:
        generate_daily_report(spark)
    finally:
        spark.stop()
```

**After (adapted):**
```python
def generate_daily_report(spark: SparkSession = None):
    should_stop = False
    if spark is None:
        spark = get_spark_session("DailyReport")
        should_stop = True
    ...
    # in finally: if should_stop: spark.stop()

if __name__ == "__main__":
    generate_daily_report()
```

**`adapted_source`:** `jobs/daily_report.py:10::generate_daily_report`

The adapter injects the session parameter into the called function, moves the session guard there, and simplifies `__main__`.

### Case C — `__main__` has inline logic (no function)

All orchestration is inside `__main__` with no function to call.

**Before:**
```python
if __name__ == "__main__":
    spark = create_spark_session()
    input_dfs = read_inputs(spark)
    output_dfs = run_pipeline(spark, input_dfs)
    write_outputs(spark, output_dfs)
    spark.stop()
```

**After (adapted):**
```python
def main_entrypoint(spark: SparkSession = None):
    should_stop = False
    if spark is None:
        spark = create_spark_session()
        should_stop = True
    try:
        input_dfs = read_inputs(spark)
        output_dfs = run_pipeline(spark, input_dfs)
        write_outputs(spark, output_dfs)
    finally:
        if should_stop and spark is not None:
            spark.stop()

if __name__ == "__main__":
    main_entrypoint()
```

**`adapted_source`:** `workload.py:295::main_entrypoint`

The adapter **extracts** all inline logic into a new function, adds the session guard, and reduces `__main__` to a single call.

**Naming convention for the extracted function:**
- Python: `main_entrypoint` (PEP 8 snake_case)
- Scala: `mainEntrypoint` (Scala camelCase)

If `main_entrypoint` already exists in the module, append an incrementing suffix: `main_entrypoint_02`, `main_entrypoint_03`, etc. until there is no collision.

### Case detection

| Signal | Case | Action |
|--------|------|--------|
| `__main__` calls a single function that creates a session internally | A | Inject session param into that function |
| `__main__` creates session then calls function(s) | B | Move session guard into the called function, simplify `__main__` |
| `__main__` has multi-line logic without delegating to a function | C | Extract to new `main_entrypoint()` function |

The `reason` field from the entrypoint detection can help orient, but the adapter must inspect the actual code (via AST) to determine the case.

## Entrypoint Cases (Scala)

### Object with `def main`

**Before:**
```scala
object GlobalTransactions {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder
      .appName("GlobalTransactions")
      .getOrCreate()
    // ...
    spark.stop()
  }
}
```

**After (adapted):**
```scala
object GlobalTransactions {
  def main(args: Array[String], spark: SparkSession = null): Unit = {
    val shouldStop = spark == null
    val _spark = if (spark != null) spark
                 else SparkSession.builder
                   .appName("GlobalTransactions")
                   .getOrCreate()
    // ... (all references to spark become _spark)
    if (shouldStop) _spark.stop()
  }
}
```

**`adapted_source`:** `App.scala:5::GlobalTransactions::main`

The `source` and `adapted_source` have the same `::Scope::method` structure since the method already existed.

### Deferred environment variables (Scala)

Scala equivalents of Python's `os.getenv()`:

| Pattern | Deferral Strategy |
|---------|------------------|
| `val X = sys.env.getOrElse("KEY", "default")` at object level | Move into method or lazy val |
| `val X = System.getenv("KEY")` at object level | Move into method or lazy val |

## Transformations (common)

### 1. Session Injection

Detect entrypoint functions that create their own session and add an optional session parameter. Applies to all targets. See Cases A/B/C above for Python, and the Scala section.

The type hint matches the session type used by the workload:
- `SparkSession` for source and SCOS
- `Session` for Snowpark API (02-migrated)

### 2. Enable Hive Support (Python Source Only)

Add `.enableHiveSupport()` to the session builder so `spark.table()` and `saveAsTable()` work with a local Hive metastore. **Only applies to `01-source/` Python workloads.**

**Before:**
```python
spark = SparkSession.builder \
    .appName(app_name) \
    .getOrCreate()
```

**After:**
```python
spark = SparkSession.builder \
    .appName(app_name) \
    .enableHiveSupport() \
    .getOrCreate()
```

### 3. Defer Module-Level Environment Variables

Module-level `os.getenv()` (Python) or `sys.env` (Scala) calls evaluate at import/load time, before test fixtures can override values. These must be deferred to runtime. Applies to all targets.

Three strategies for Python, depending on the situation:

#### Strategy A: Move into consuming function

Best when only one function uses the variable.

**Before:**
```python
INPUT_PATH = os.getenv("INPUT_DATA_PATH", "input")

def run_pipeline(spark):
    raw_txns = spark.read.csv(f"{INPUT_PATH}/raw_transactions.csv")
```

**After:**
```python
def run_pipeline(spark):
    INPUT_PATH = os.getenv("INPUT_DATA_PATH", "input")
    raw_txns = spark.read.csv(f"{INPUT_PATH}/raw_transactions.csv")
```

#### Strategy B: Placeholder + global in entrypoint

Best when the variable is used by multiple functions across the module.

**Before:**
```python
INPUT_PATH = os.getenv("INPUT_DATA_PATH", "input")

def run_pipeline(spark): ...   # uses INPUT_PATH
def verify_outputs(spark): ... # also uses INPUT_PATH
```

**After:**
```python
INPUT_PATH = None

def main(spark=None):
    global INPUT_PATH
    INPUT_PATH = os.getenv("INPUT_DATA_PATH", "input")
    ...
```

#### Strategy C: Init function for many env vars

Best when there are many environment variables.

**Before:**
```python
INPUT_PATH = os.getenv("INPUT_DATA_PATH", "input")
OUTPUT_PATH = os.getenv("OUTPUT_DATA_PATH", "output")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "1000"))
```

**After:**
```python
INPUT_PATH = None
OUTPUT_PATH = None
BATCH_SIZE = None

def _init_config():
    global INPUT_PATH, OUTPUT_PATH, BATCH_SIZE
    INPUT_PATH = os.getenv("INPUT_DATA_PATH", "input")
    OUTPUT_PATH = os.getenv("OUTPUT_DATA_PATH", "output")
    BATCH_SIZE = int(os.getenv("BATCH_SIZE", "1000"))

def main(spark=None):
    _init_config()
    ...
```

## Updating `entrypoints.json`

After adapting each entrypoint, the skill writes `adapted_source` into the corresponding entry in `dvp/04-results/entrypoints.json`.

The `adapted_source` field uses the same hybrid format as `source` (see [entrypoints-source-spec.md](../docs/entrypoints-source-spec.md)):

```
<path>:<lineno>(::segment)*
```

This field tells `dvp-test-setup-generator` exactly what to invoke, without any guesswork.

**Before (from dvp-entrypoint-identifier):**
```json
{
  "name": "__main__",
  "source": "workload.py:291",
  "type": "main_block",
  "reason": "main_guard"
}
```

**After (updated by dvp-code-adapter):**
```json
{
  "name": "__main__",
  "source": "workload.py:291",
  "type": "main_block",
  "reason": "main_guard",
  "adapted_source": "workload.py:295::main_entrypoint"
}
```

### Commit Changes to Git

After all entrypoints are adapted and `entrypoints.json` is updated, commit the changes:

```python
result = sma_api.git_commit("<workload_path>", """DVP Code Adapter: Adapted N files for testing

Entrypoints adapted: N
Targets: 01-source/, 02-migrated/ (or 02-migrated_scos/)
Transformations: session injection, deferred env vars, enableHiveSupport (source only)
Updated: dvp/04-results/entrypoints.json (adapted_source field)""")
```

Verify branches:
```python
result = sma_api.git_verify_branches("<workload_path>")
```

## What This Skill Does NOT Do

- **Does NOT convert tables to files** -- the original IO strategy is preserved
- **Does NOT refactor code** -- no helper extraction, no DRY changes, no formatting (the only structural change is Case C: extracting `__main__` inline logic to a function, which is the minimum for testability)
- **Does NOT change API calls** -- Snowpark stays Snowpark, PySpark stays PySpark

## Detection Strategy

This is an IA-driven skill. The AI uses these patterns as guidance to locate and transform the relevant code:

### Python

| Pattern | What to look for | Action |
|---------|-----------------|--------|
| `if __name__ == "__main__":` | Guard block | Classify as Case A, B, or C |
| `def function()` matching entrypoint | Function definition | Inject session parameter + guard |
| `SparkSession.builder...getOrCreate()` | Builder chain | Insert `.enableHiveSupport()` (source only) |
| `X = os.getenv(...)` at module level | Top-level assignment | Defer to runtime (strategy A, B, or C) |

### Scala

| Pattern | What to look for | Action |
|---------|-----------------|--------|
| `object X { def main(...) }` | Object with main method | Inject session parameter + guard |
| `val X = sys.env.getOrElse(...)` at object level | Top-level val | Move to method or lazy val |

## Inputs

| Input | Location |
|-------|----------|
| Source code (copy) | `dvp/01-source/` |
| Migrated code (Snowpark API) | `dvp/02-migrated/` |
| Migrated code (SCOS) | `dvp/02-migrated_scos/` |
| Entrypoints inventory | `dvp/04-results/entrypoints.json` |

## Outputs

| Output | Location |
|--------|----------|
| Adapted source code | `dvp/01-source/` (in-place modification) |
| Adapted migrated code (Snowpark API) | `dvp/02-migrated/` (in-place modification) |
| Adapted migrated code (SCOS) | `dvp/02-migrated_scos/` (in-place modification) |
| Updated entrypoints | `dvp/04-results/entrypoints.json` (`adapted_source` field added) |

## Notes

- The original unmodified source remains in the `<sma-input>` folder (e.g., `input/`)
- The original unmodified SMA output remains in `<output>/Output/` (v2: `sma-output/Output/`; v3: under the resolved `sma-code-process-*/Output/`)
- Table names are used without any database/schema prefix (basic cases)
- All targets get the same transformations except `.enableHiveSupport()` which is source-only and Python-only
- SCOS session creation (`snowpark_connect`) is handled by SMA, not by this skill
- Scala support is limited to `01-source/` (no Scala migration output from SMA yet)

## Final Summary

**MANDATORY**: After completing all steps (whether running standalone or invoked from the orchestrator), ALWAYS present this summary table:

```
Code Adapter Complete

┌────────────────┬──────────┬──────────────────────────────────────────────────────┐
│ Step           │ Status   │ Details                                              │
├────────────────┼──────────┼──────────────────────────────────────────────────────┤
│ Code Adapter   │ Done     │ Adapted N files for testing                          │
└────────────────┴──────────┴──────────────────────────────────────────────────────┘

Output location: <output>/

Git branches:
• main — original code (unmodified)
• sma/migration-process — code adaptation changes applied
```

**Rules:**
- Replace `N` with actual count of files adapted
- Status is `Done`, `Skipped`, or `Failed`
- If no files needed adaptation, show `Skipped` with reason
- If adaptation failed, show `Failed` with brief error
- The git branches section uses `sma_api.git_verify_branches()` to confirm both branches exist
