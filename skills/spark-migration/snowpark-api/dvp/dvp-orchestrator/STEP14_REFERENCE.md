# Step 14-15 Reference: Test Runner, Migrated Test Fixer & Pipeline Completion

This file contains detailed reference material extracted from `SKILL.md` to keep the main orchestrator file under the 500-line convention.

## Step 14: Invoke dvp-test-runner

Invoke the `dvp-test-runner` skill to execute the generated test suites and validate the migration.

**What this skill does:**
1. Verifies the test environment is ready (Python venv, Java 17+, packages, Snowflake credentials)
2. **Creates the Python environment from scratch if needed** (venv + pip install)
3. Runs source tests (`dvp/03-tests/source/`) — these **must all pass** (green). If they fail, the skill diagnoses the root cause, applies fixes, and retries (up to 3 cycles)
4. Runs migrated tests (`dvp/03-tests/migrated/` or `migrated_scos/`) — they must all execute (assertion failures are expected and acceptable)
5. Cross-checks: source test fixes are proactively applied to migrated code before running migrated tests
6. Records all test results in `sma_storage.sqlite3` via `sma_api.insert_test_run()`
7. Exports results to `dvp/04-results/testing-results/`

**⛔ MANDATORY:** load `<dvp_root>/dvp-test-runner/SKILL.md` with the Read tool and follow it inline, in the current conversation (see the orchestrator's "Bundled Sub-skill Loading Convention").
DO NOT use the Task tool. DO NOT launch as a background agent.
DO NOT run pytest manually. The skill handles environment verification, result parsing, database recording, and error diagnosis.

**Wait for completion before proceeding to Step 15.**

## Step 15: Invoke dvp-migrated-test-fixer

After Step 14 ensures migrated tests **run**, this skill makes them **pass**. It iterates over failing migrated tests, diagnoses PySpark-to-Snowpark API incompatibilities in `dvp/02-migrated/` code, applies fixes, commits each change to git, and retries until all tests pass.

**What this skill does:**
1. Runs the full migrated test suite to establish a pass/fail baseline
2. Classifies errors by type and prioritizes shared-code fixes first
3. Applies fixes to migrated code (`dvp/02-migrated/`) and test infrastructure (`dvp/03-tests/migrated/`)
4. Git-commits each fix for traceability and loop detection
5. Re-runs tests after each fix, iterating until all pass
6. References a comprehensive Snowpark migration error pattern table covering 15+ API incompatibilities
7. Uses source tests as ground truth for correct IO declarations

**Precondition:** Git must be initialized on the output folder (done by Step 5.5).

**⛔ MANDATORY:** load `<dvp_root>/dvp-migrated-test-fixer/SKILL.md` with the Read tool and follow it inline, in the current conversation (see the orchestrator's "Bundled Sub-skill Loading Convention").
DO NOT use the Task tool. DO NOT launch as a background agent.

**Wait for completion — proceed to the Final Summary.**

## ASG-Centric Execution (Steps 8-12)

Step 8 generates the ASG (Abstract Semantic Graph) from the original source code (`<sma-input>`). It produces `XX_asg.json` (where `XX` is the project prefix) — a structured representation of the data flow consumed by all downstream skills instead of raw source code. See [ASG Strategy](../docs/asg-strategy.md) for the full design rationale.

Step 10 adapts workload code for testability: session injection, `.enableHiveSupport()` (source only), and deferred env vars. This step operates on source code directly, not the ASG.

Step 11 (`dvp-io-schema-identifier`) creates `data_io_schema.json` with entries for every input/output (name, type, role, path, columns, key_columns). It combines IO detection and schema extraction in a single pass, consuming the ASG for deterministic detection and using AI to resolve anomalies.

## Anomaly Feedback Loop

Each WARP-based skill (Steps 8, 11-12) produces an **anomaly report** alongside its primary output. When anomalies are present, the orchestrator instructs the AI to:

1. Review the anomaly report
2. Attempt to resolve each case using the ASG context
3. Fall back to inspecting specific source code locations (referenced in the ASG) for unresolvable cases
4. Update the primary output with resolved values

This real-time resolution is critical because the client's source code is only available during skill execution. Anomaly patterns are also collected for improving the deterministic tools over time.

## Final Summary Template

**MANDATORY**: After all sub-skills complete (or when running standalone), ALWAYS present a results table. This table must be shown regardless of whether the orchestrator was invoked from `spark-migration` or run directly.

```
DVP Pipeline Complete

┌────────────────────────┬──────────┬─────────────────────────────────────────────────┐
│ Step                   │ Status   │ Details                                         │
├────────────────────────┼──────────┼─────────────────────────────────────────────────┤
│ DVP Workspace          │ Done     │ Initialized with source + migrated files        │
├────────────────────────┼──────────┼─────────────────────────────────────────────────┤
│ Notebook Conversion    │ Done     │ Converted N notebooks to scripts                │
├────────────────────────┼──────────┼─────────────────────────────────────────────────┤
│ ASG Generation         │ Done     │ Generated ASG from N source files               │
├────────────────────────┼──────────┼─────────────────────────────────────────────────┤
│ Entrypoint Detection   │ Done     │ Identified N entrypoints                        │
├────────────────────────┼──────────┼─────────────────────────────────────────────────┤
│ Code Adapter           │ Done     │ Adapted N files for testing                     │
├────────────────────────┼──────────┼─────────────────────────────────────────────────┤
│ IO Schema              │ Done     │ N inputs, M outputs identified                  │
├────────────────────────┼──────────┼─────────────────────────────────────────────────┤
│ Synthetic Data         │ Done     │ Generated N CSV files                           │
├────────────────────────┼──────────┼─────────────────────────────────────────────────┤
│ Test Setup             │ Done     │ Generated test project with N test files         │
├────────────────────────┼──────────┼─────────────────────────────────────────────────┤
│ Test Execution         │ Done     │ Source: N passed; Migrated: X ran, Y failed      │
├────────────────────────┼──────────┼─────────────────────────────────────────────────┤
│ Migrated Test Fixer    │ Done     │ X passed (Z fix commits on sma/migration-process)│
└────────────────────────┴──────────┴─────────────────────────────────────────────────┘

Output location: <output>/

Git branches:
• main — original SMA output (unmodified)
• sma/migration-process — all DVP changes applied
```

**Rules:**
- Replace `N`, `M` with actual counts from each sub-skill's output
- Status is `Done`, `Skipped`, or `Failed`
- If a sub-skill was skipped (e.g., no notebooks found), show `Skipped` with reason
- If a sub-skill failed, show `Failed` with brief error
- Only include rows for sub-skills that were part of the pipeline run
- The git branches section uses `sma_api.git_verify_branches()` to confirm both branches exist
