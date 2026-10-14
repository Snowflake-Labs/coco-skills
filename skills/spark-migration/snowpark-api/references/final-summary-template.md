# Final Summary Template (Snowpark API path)

⛔ **CRITICAL — COPY THIS TEMPLATE EXACTLY** at the end of every Snowpark API run (whether triggered by `migrate-pyspark-to-snowpark-api/SKILL.md` or `validate-pyspark-to-snowpark-api/SKILL.md`).

Do NOT improvise your own table format. Do NOT omit the `#` column. Do NOT rename sections. Do NOT skip the Dashboard section.

## Pre-Display Checklist

Before printing the summary, verify:

1. The table has a `#` column with numbers like `1, 1.1, 1.2, 2, 2.1, 3, ...`
2. Main rows use the **actual skill name** (e.g., `dvp-ewi-fixer`, not "EWI Fixer")
3. There is a **Dashboard** section with `cd ... && python3 start_server.py`
4. All `N`, `M`, `<output>`, `<project_name>`, `<duration>`, `<port>` are replaced with real values
5. Skipped skills show only the parent row (no sub-steps)
6. ⛔ **DVP sub-skills are numbered 6.1–6.8 under `dvp-orchestrator` (step 6)** — NOT separate top-level steps. The ONLY top-level steps are:
   - 1 — `spark-migration (SMA CLI)`
   - 2 — `sma-dashboard-generator`
   - 3 — `snowflake-notebook-migration`
   - 4 — `dvp-ewi-fixer`
   - 5 — `stage-conversion`
   - 6 — `dvp-orchestrator`
   - 7 — `Open Dashboard`

## Template

```
═══════════════════════════════════════════════════════════════════════════
  Snowflake Migration Complete — <project_name>
═══════════════════════════════════════════════════════════════════════════

┌──────┬────────────────────────────────────────────────┬──────────┬──────────────────────────────────────────────┐
│  #   │ Step                                           │ Status   │ Details                                      │
├──────┼────────────────────────────────────────────────┼──────────┼──────────────────────────────────────────────┤
│  1   │ spark-migration (SMA CLI)                      │ Done     │ N files converted (Snowpark API)             │
│  1.1 │   Run SMA CLI                                  │ Done     │ sma convert completed                        │
│  1.2 │   Jupyter Conversion                           │ Done     │ N notebooks converted                        │
│  2   │ sma-dashboard-generator                        │ Done     │ Generated at sma-dashboard/                  │
│  2.1 │   Verify Requirements                          │ Done     │ CSV files validated                          │
│  2.2 │   Run SMA Dashboard Manager                    │ Done     │ SQLite DB created, dashboard built           │
│  2.3 │   Report Results                               │ Done     │ N EWIs, N files, N dependencies              │
│  3   │ snowflake-notebook-migration                   │ Done     │ N notebooks converted to Snowflake format    │
│  3.1 │   Scan for Notebooks                           │ Done     │ N notebooks found in Output/                 │
│  3.2 │   Convert Notebooks                            │ Done     │ N notebooks converted in-place               │
│  3.3 │   Git Commit                                   │ Done     │ Changes committed on sma/migration-process   │
│  4   │ dvp-ewi-fixer                                  │ Done     │ Fixed N/M EWIs                               │
│  4.1 │   Load EWI Context                             │ Done     │ N EWIs loaded from database                  │
│  4.2 │   Apply Fixes                                  │ Done     │ N files processed                            │
│  4.3 │   Update Database                              │ Done     │ Results saved to SQLite                      │
│  5   │ stage-conversion                               │ Skipped  │ User opted out                               │
│  5.1 │   Scan for Embedded Paths                      │ Done     │ N paths found in M files                     │
│  5.2 │   Preview Changes                              │ Done     │ Dry run completed                            │
│  5.3 │   Apply Replacements                           │ Done     │ N paths replaced with @stage_name            │
│  6   │ dvp-orchestrator                               │ Done     │ Validation pipeline completed                │
│  6.1 │   Create DVP Workspace                         │ Done     │ dvp/ structure created                       │
│  6.2 │   dvp-notebook-to-script                       │ Skipped  │ No notebooks found                           │
│  6.3 │   dvp-asg-generation                           │ Done     │ Generated ASG from N source files            │
│  6.4 │   dvp-entrypoint-identifier                    │ Done     │ N entrypoints detected                       │
│  6.5 │   dvp-code-adapter                             │ Done     │ N files adapted for testing                  │
│  6.6 │   dvp-io-schema-identifier                     │ Done     │ N inputs, M outputs mapped                   │
│  6.7 │   dvp-synthetic-data-generator                 │ Done     │ Test data generated for N inputs             │
│  6.8 │   dvp-test-setup-generator                     │ Done     │ N test suites registered                     │
│  7   │ Open Dashboard                                 │ Done     │ Dashboard opened in browser                  │
└──────┴────────────────────────────────────────────────┴──────────┴──────────────────────────────────────────────┘

Output location: <output>/

Git branches:
  • main — original conversion output (unmodified)
  • sma/migration-process — all fixes applied

Duration: Start: <start_time> | End: <end_time> | Duration: <duration>

Dashboard:
  To open the SMA Dashboard and review EWI issues and migration status:
    cd "<output>/sma-dashboard" && python3 start_server.py
  This starts a local server and opens the dashboard in your default browser.
  If the server is already running, open http://localhost:<port> in your browser.

Next steps:
  • Run tests with: cd "<output>/dvp/03-tests" && pytest source/ -v
  • Fix remaining EWIs by invoking the dvp-ewi-fixer skill
  • Review and validate converted code in <output>/Output/
```

## Rules

- Replace `N`, `M` with actual counts from each sub-skill's output
- Replace `<project_name>`, `<output>`, `<start_time>`, `<end_time>`, `<duration>`, `<port>` with real values
- `<start_time>` is recorded by the parent at Step 1 start (or by `migrate-pyspark-to-snowpark-api/SKILL.md` when this sub-skill is the first runtime entry)
- `<end_time>` is current time; `<duration>` is the difference (e.g., `~13 minutes`)
- Status is `Done`, `Skipped`, or `Failed`
- If a sub-skill was skipped: show only the parent row with `Skipped`, omit sub-steps
- If a sub-skill failed: show `Failed` with a brief error message
- Sub-step rows (`N.1`, `N.2`, ...) only appear if the parent was executed
- The git branches section uses `sma_api.git_verify_branches()` (from `<snowpark_api_root>/scripts/sma_api.py`) to confirm both `main` and `sma/migration-process` exist
- Duration is wall-clock time from the parent's recorded `<start_time>` to summary display

## already_migrated Variant

When the validator is invoked directly with `<intent> = already_migrated` (no SMA CLI run), replace row 1 with:

```
│  1   │ spark-migration (already-migrated input)        │ Done     │ Validated existing SMA output at <output>    │
│  1.1 │   Validate Output Structure                    │ Done     │ Output/ and Reports/ exist                   │
│  1.2 │   Initialize Git                                │ Done     │ Branch sma/migration-process checked out     │
```

Everything else (rows 2 through 7) is unchanged.
