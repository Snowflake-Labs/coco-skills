# Spark Migration — Architecture & Flow

This document is the human-readable companion to `SKILL.md`. It shows the post-redesign layout, the routing decision tree, and a migration table for files that moved during the redesign.

## High-Level Layout

```
spark-migration/                                    ← parent (thin router)
├── SKILL.md                                        (~375 lines — routing only)
├── Diagram.md                                      (this file)
├── scripts/config_manager.py                       (shared config manager)
├── configurations/<project>.json                   (per-project state)
├── config.json                                     (global state, e.g. sma_cli_path)
│
├── snowflake-notebook-migration/                   (shared by both paths)
│
├── snowpark-connect/                               ← DEFAULT PATH (SCOS)
│   ├── SKILL.md                                    (entry point for SCOS)
│   ├── migrate-pyspark-to-snowpark-connect/
│   ├── migrate-spark-scala-to-snowpark-connect/
│   ├── validate-pyspark-to-snowpark-connect/
│   ├── recipes/                                    (rewrite/annotate recipes)
│   └── scripts/, examples/
│
└── snowpark-api/                                   ← OPT-IN PATH (SMA CLI)
    ├── SKILL.md                                    (~160 lines — router)
    ├── scripts/sma_api.py                          (SMA SQLite + git helpers)
    ├── references/                                 (extracted long-form docs)
    │   ├── sma-cli-options.md
    │   ├── output-layouts.md
    │   ├── post-conversion-options.md
    │   ├── final-summary-template.md
    │   ├── example-workflows.md
    │   ├── sma-api-reference.md
    │   └── configuration-schema.md
    ├── migrate-pyspark-to-snowpark-api/SKILL.md    (~210 lines — SMA CLI invocation)
    ├── validate-pyspark-to-snowpark-api/SKILL.md   (~324 lines — post-conversion tail)
    ├── sma-dashboard-generator/                    (moved here from parent)
    ├── stage-conversion/                           (moved here from parent)
    └── dvp/                                        (moved here from parent)
        ├── README.md
        ├── docs/
        ├── dvp-orchestrator/
        ├── dvp-asg-generation/
        ├── dvp-code-adapter/
        ├── dvp-entrypoint-identifier/
        ├── dvp-ewi-fixer/
        ├── dvp-io-schema-identifier/
        ├── dvp-migrated-test-fixer/
        ├── dvp-notebook-to-script/
        ├── dvp-synthetic-data-generator/
        ├── dvp-test-runner/
        └── dvp-test-setup-generator/
```

## Routing Decision Tree

```
User request arrives at spark-migration
  │
  ├─ Step 0: Prerequisites (Git always; SMA CLI only if snowpark-api needed)
  │
  ├─ Step 1: Load / create project config
  │           (aliases scos / snowpark_api normalized to canonical on load+save)
  │
  ├─ Step 2: Review / edit 18-key config
  │
  ├─ Step 3: Determine route
  │           ┌──────────────────────────────────────────────┐
  │           │  conversion_type   │  migration_status       │  → Path
  │           ├────────────────────┼─────────────────────────┼──────────────────────────────┐
  │           │  snowpark-connect  │  migrate                │  snowpark-connect/SKILL.md   │
  │           │   (DEFAULT)        │                         │   (full SCOS conversion)     │
  │           │                    │  already_migrated       │  snowpark-connect/SKILL.md   │
  │           │                    │                         │   (already-migrated entry)   │
  │           │  snowpark-api      │  migrate                │  snowpark-api/SKILL.md       │
  │           │   (explicit opt-in)│                         │   → migrate-pyspark-to-      │
  │           │                    │                         │     snowpark-api/SKILL.md    │
  │           │                    │  already_migrated       │  snowpark-api/SKILL.md       │
  │           │                    │                         │   → validate-pyspark-to-     │
  │           │                    │                         │     snowpark-api/SKILL.md    │
  │           └────────────────────┴─────────────────────────┴──────────────────────────────┘
  │
  ├─ Step 4: (already_migrated only) validate <output>/Output and <output>/Reports
  │
  └─ Step 5: Dispatch — load the chosen SKILL.md with the Read tool, follow inline.
            The child sub-skill owns the rest of the pipeline incl. final summary.
            Control does NOT return to parent.
```

## snowpark-api Internal Flow

```
snowpark-api/SKILL.md                  (router)
  │
  ├─ Detect language
  │    ├─ Python  → continue
  │    └─ Scala   → STOP, suggest snowpark-connect
  │
  └─ Route by <intent>
       ├─ migrate            → migrate-pyspark-to-snowpark-api/SKILL.md
       │                          │
       │                          ├─ M1: validate SMA CLI path (global config)
       │                          ├─ M2: collect CLI fields (sql_flavor, jupyter, ckpt)
       │                          ├─ M3: run SMA CLI in background
       │                          ├─ M4: monitor via bash_output
       │                          ├─ M5: resolve <output> (v1/v2/v3 layout)
       │                          └─ M6: load validate-pyspark-to-snowpark-api/ inline
       │                                  (control transferred — never returns)
       │
       └─ already_migrated   → validate-pyspark-to-snowpark-api/SKILL.md
                                  │
                                  ├─ V0: resolve <output> (if not pre-resolved)
                                  ├─ V1: git init + verify SMA output structure
                                  ├─ V2: sma-dashboard-generator (bundled)
                                  ├─ V3: snowflake-notebook-migration  [optional]
                                  ├─ V4: dvp-ewi-fixer                 [optional]
                                  ├─ V5: stage-conversion              [optional]
                                  ├─ V6: dvp-orchestrator (steps 1–13) [optional]
                                  ├─ V7: re-open dashboard
                                  └─ V8: print Final Summary
```

The validator is ALSO the direct entry point for individual intents:

| User intent | Validator step subset |
|---|---|
| `open sma dashboard` | V0, V1, V2 (only if stale), V7, V8 |
| `fix ewis` | V0, V1, V2 (if missing), V4, V7, V8 |
| `run stage conversion` | V0, V1, V5, V7, V8 |
| `resume dvp` | V0, V1, V6, V7, V8 |

## Migration Table — File Moves in This Redesign

| Old path | New path |
|---|---|
| `spark-migration/scripts/sma_api.py` | `spark-migration/snowpark-api/scripts/sma_api.py` |
| `spark-migration/sma-dashboard-generator/` | `spark-migration/snowpark-api/sma-dashboard-generator/` |
| `spark-migration/stage-conversion/` | `spark-migration/snowpark-api/stage-conversion/` |
| `spark-migration/dvp/` (entire tree) | `spark-migration/snowpark-api/dvp/` |
| (none) | `spark-migration/snowpark-api/SKILL.md` (NEW — router) |
| (none) | `spark-migration/snowpark-api/migrate-pyspark-to-snowpark-api/SKILL.md` (NEW) |
| (none) | `spark-migration/snowpark-api/validate-pyspark-to-snowpark-api/SKILL.md` (NEW) |
| (none) | `spark-migration/snowpark-api/references/*.md` (NEW — 7 files) |

### What Did NOT Move

| Stayed at parent root | Why |
|---|---|
| `spark-migration/SKILL.md` | Routing entry point (now ~375 lines instead of ~1400) |
| `spark-migration/scripts/config_manager.py` | Shared by both SCOS and Snowpark API paths |
| `spark-migration/snowflake-notebook-migration/` | Shared post-conversion step on both paths |
| `spark-migration/snowpark-connect/` | Default conversion path (untouched by this redesign) |
| `spark-migration/configurations/` | Per-project configs are path-agnostic |
| `spark-migration/config.json` | Global state (e.g. `sma_cli_path`) is path-agnostic |
| `spark-migration/tests/` | Test paths updated to new conftest locations |

### Loading Convention Changes

All `skill("dvp-*")` registry calls inside the `dvp/` tree were replaced with bundled-path `Read` operations. The DVP orchestrator now contains an explicit "Bundled Sub-skill Loading Convention" section near the top with the full path table. Same change applied to the `skill("snowflake-notebook-migration")` calls in the snowpark-connect migrate sub-skills.

### Backward-Compatibility Notes

- Existing `configurations/<project>.json` files with `conversion_type` values of `scos`, `snowpark_connect`, or `snowpark_api` continue to work — they are normalized to `snowpark-connect` / `snowpark-api` on first load AND immediately re-persisted, so the canonical form takes hold on the very next read.
- Existing tests that pointed at `tests/sma_dashboard_generator/` and `tests/dvp_stage_conversion/` still pass — only their `conftest.py` `sys.path` entries were updated to the new `snowpark-api/` locations.
- The old `spark-migration/scripts/sma_api.py` import path is **no longer valid**. Any external code referencing it must update to `spark-migration/snowpark-api/scripts/sma_api.py`. There is **no filesystem shim** at the old location.
- The old `spark-migration/dvp/` path is **no longer valid**. There is **no stub README** — see this migration table for the new location.

## Database & Git Access Module

```
sma_api.py — Unified database + git access module
Location: spark-migration/snowpark-api/scripts/sma_api.py

- All EWI/DB functions receive workload_path: str as their first parameter
- DB path: {workload_path}/sma_storage.sqlite3
- Git helpers: git_ensure_branch, git_commit, git_verify_branches

Full function reference:
  spark-migration/snowpark-api/references/sma-api-reference.md
```

Owners / consumers of `sma_api.py`:

- `snowpark-api/sma-dashboard-generator/` — read all EWI/file functions, write file_validation
- `snowpark-api/dvp/dvp-ewi-fixer/` — read EWIs, write fix results
- `snowpark-api/stage-conversion/` — git_ensure_branch, git_commit
- `snowpark-api/dvp/dvp-orchestrator/` — git_verify_branches for the Final Summary
- `snowpark-api/dvp/dvp-entrypoint-identifier/` and others — read schema/EWI tables
