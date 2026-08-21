---
name: dvp-orchestrator
description: "Initialize DVP workspace and orchestrate validation pipeline. Triggers: start DVP, initialize workspace, setup validation, run DVP, create DVP structure."


# DVP Orchestrator

## Overview

Initializes the DVP workspace structure and orchestrates the data validation pipeline. This is the entry point for DVP operations after an SMA migration is complete.

## Notes

For Cortex Code UX/operator tips, see `../docs/data-validator/dvp-orchestrator.md`.

## Bundled Sub-skill Loading Convention

All `dvp-*` sub-skills invoked from this orchestrator are **bundled** under
`snowpark-api/dvp/` — they are NOT registered as top-level skills in the
Cortex Code registry. **Do NOT call `skill("dvp-*")`** — that lookup will
fail because these skills are deliberately not registered (so they don't
collide with their parent `spark-migration`'s triggers).

To invoke a bundled DVP sub-skill, resolve its path relative to this
orchestrator's directory (`<dvp_root>` = the directory containing this
SKILL.md's parent, i.e. `snowpark-api/dvp/`), then **Read** that file with
the Read tool and follow its instructions verbatim, passing the
orchestrator context (input/output paths, conversion type, etc.) inline:

| Sub-skill | Bundled path |
|---|---|
| `dvp-notebook-to-script` | `<dvp_root>/dvp-notebook-to-script/SKILL.md` |
| `dvp-asg-generation` | `<dvp_root>/dvp-asg-generation/SKILL.md` |
| `dvp-entrypoint-identifier` | `<dvp_root>/dvp-entrypoint-identifier/SKILL.md` |
| `dvp-code-adapter` | `<dvp_root>/dvp-code-adapter/SKILL.md` |
| `dvp-io-schema-identifier` | `<dvp_root>/dvp-io-schema-identifier/SKILL.md` |
| `dvp-synthetic-data-generator` | `<dvp_root>/dvp-synthetic-data-generator/SKILL.md` |
| `dvp-test-setup-generator` | `<dvp_root>/dvp-test-setup-generator/SKILL.md` |
| `dvp-test-runner` | `<dvp_root>/dvp-test-runner/SKILL.md` |
| `dvp-migrated-test-fixer` | `<dvp_root>/dvp-migrated-test-fixer/SKILL.md` |

If any of these files is missing, **STOP** and report:
> The bundled `<name>` sub-skill is missing at `<expected_path>`.
> Reinstall the `spark-migration` skill.

## Execution Reporting (DVP)

When this orchestrator invokes downstream **DVP** skills, report progress after completing each step:

- What ran (step + skill name)
- What was generated (files/artifacts)
- Where it was saved (paths)
- Any warnings (including anomaly reports / partial detections)

End with a brief overall summary of what was produced.

## Output Format

Every time you begin a step, sub-step, or significant action, prefix the message with a timestamp in the format `[YYYY-MM-DD HH:MM:SS]`. Obtain the current time by running `date '+%Y-%m-%d %H:%M:%S'` in bash.

Example:
```
[2026-03-24 14:05:32] Starting Step 1: Detect SMA Paths...
[2026-03-24 14:05:33] Found SMA output at /path/to/output
[2026-03-24 14:05:34] Step 1 complete.
```

## Workflow

**IMPORTANT: Execute ALL steps (1 through 15) sequentially without stopping. Do NOT ask the user if they want to continue, if this is a "good stopping point", or whether to proceed with remaining steps. The entire pipeline must run end-to-end. Only stop if a step explicitly fails with an error that blocks downstream steps (see Error Handling).**

### Step 1: Detect SMA Paths

SMA has multiple output formats. The orchestrator must detect which format is present and resolve paths accordingly. **v2 and v3 can both exist in the ecosystem** — use the detection order below (v2 is checked before v3 so projects that still use `sma-output/` keep working unchanged).

#### Format Detection Algorithm

```
1. Search for .snowma file in <input>
   |-- Found -> v1 format: Use internalConversionOutputPath as <output>
   |            Both <input> and <output> are auto-resolved from .snowma
   +-- Not found -> Continue to step 2

2. Look for sma-output/ in the sibling output folder
   |-- Found -> v2 format: Use sma-output/ as <output>
   |            Note: .snowct file may exist but has NO paths
   |            <input> must be provided by the user
   +-- Not found -> Continue to step 3

3. v3 format: dual conversion folders under the SMA output root
   Look in the sibling output folder for directories named exactly:
     Conversion_SnowparkAPI
     Conversion_SnowparkConnect
   |-- At least one exists AND contains at least one subdirectory matching sma-code-process-*
   |   -> v3 format:
   |       a) Choose `Conversion_SnowparkAPI` or `Conversion_SnowparkConnect` (see "v3 folder selection" below)
   |       b) Under that folder, set <output> to the MOST RECENT sma-code-process-<timestamp>/
   |          (parse timestamps from the folder name; same "pick latest run" idea as v1's
   |           most recent Conversion-* folder)
   |       c) <input> must be provided by the user (.snowct has no paths, same as v2)
   +-- Not satisfied -> Continue to step 4

4. Search for legacy v1 Conversion-* folders in the sibling output folder
   |-- EXCLUDE directories named exactly Conversion_SnowparkAPI or Conversion_SnowparkConnect
   |   (those are v3 only — do not treat them as legacy v1 timestamp folders)
   |-- Found among the remainder -> v1 format (no .snowma): Use most recent Conversion-* as <output>
   |            <input> must be provided by the user
   +-- Not found -> Continue to step 5

5. Ask user for both <input> and <output> paths
```

**v3 folder selection** (`Conversion_SnowparkAPI` vs `Conversion_SnowparkConnect`, before picking the latest `sma-code-process-*`):

1. If the spark-migration orchestrator provided `conversion_type`, use it:
   - `"snowpark_api"` → require `Conversion_SnowparkAPI/` (error clearly if missing or has no `sma-code-process-*`)
   - `"scos"` → require `Conversion_SnowparkConnect/` (same)
2. Else if only one of the two folders exists with at least one `sma-code-process-*`, use that folder.
3. Else if both exist, ask once:
   ```
   SMA v3 format detected (Conversion_SnowparkAPI / Conversion_SnowparkConnect).

   > Which conversion output do you want to validate?
   > (1) Snowpark API — Conversion_SnowparkAPI
   > (2) Snowpark Connect (SCOS) — Conversion_SnowparkConnect
   ```
4. Under the chosen `Conversion_SnowparkAPI` or `Conversion_SnowparkConnect` folder, list subdirectories named `sma-code-process-*`. Folder names use:
   `sma-code-process-YYYY-MM-DD-HH-mm-ss`. Pick the **lexicographically greatest** name (zero-padded fields ⇒ chronological order), i.e. the **most recent execution**.

**v3 naming:** `<output>` is always the resolved `.../Conversion_SnowparkAPI/sma-code-process-<timestamp>/` or `.../Conversion_SnowparkConnect/sma-code-process-<timestamp>/` directory (same inner layout as v2: `Output/`, `Reports/`, `Logs/`, etc.). `Assessment/` at the SMA output root is ignored for DVP.

#### 1.1 v1 Format: .snowma File Present

The legacy SMA generates a `.snowma` file (JSON) in both `<input>` and `<output>/Output/` that contains the project configuration including paths.

When the user provides an input folder path:

1. **Search for `.snowma` file** in the provided `<input>` folder
2. **If `.snowma` found**, read the JSON and extract:
   ```json
   {
     "name": "project-name",
     "inputPath": "/path/to/input",
     "outputPath": "/path/to/output",
     "internalConversionOutputPath": "/path/to/output/Conversion-X-XX-XXXXTXX XX"
   }
   ```

3. **Validate paths from `.snowma`:**
   - `inputPath` must match the folder the user provided
   - `internalConversionOutputPath` becomes `<output>`
   - If `internalConversionOutputPath` is empty, find the most recent `Conversion-*` folder inside `outputPath`

4. **Verify folders exist:**
   - Check `<input>` exists (user-provided folder)
   - Check `<output>` exists (`internalConversionOutputPath` from `.snowma`)
   - Check `<output>/Output/` exists (migrated code)
   - Check `<output>/Reports/` exists (SMA inventories, optional)

#### 1.2 v2 Format: sma-output/ Folder (No .snowma)

Current SMA versions do NOT generate a `.snowma` file. Instead, they create:
- `.snowct` project file (YAML) in the output root -- contains only a project ID, **no paths**
- `sma-output/` folder containing the latest conversion
- `results/sma-code-processes/sma-code-process-<timestamp>/` for historical runs

When no `.snowma` file is found:

1. **Look for `sma-output/`** in the expected output location (sibling `output/` folder or user-provided)
2. **If `sma-output/` found:**
   - Use `sma-output/` as `<output>`
   - Verify `<output>/Output/` exists (migrated code). If `<output>/Reports/` exists, use it; otherwise continue without SMA inventories.
   - **`<input>` must be provided by the user** (`.snowct` does not contain paths)
   - Project name = folder name of `<input>` (or from `.snowct` project ID)

3. **If `<input>` was not provided, ask user:**
   ```
   SMA v2 format detected (sma-output/ found).
   The .snowct project file does not contain input/output paths.

   > Please provide the path to the original source code (<input>):
   ```

4. **Display detected configuration (keep it brief):**
   - SMA Format: v2 (sma-output/)
   - Input:  <input>
   - Output: <output>

#### 1.2b v3 Format: Conversion_SnowparkAPI / Conversion_SnowparkConnect

Current SMA layouts may omit `sma-output/` and instead place each conversion flavor under its own folder, with **per-run** directories:

- SMA output root (sibling of `<input>` or user-provided): contains optional `.snowct` (no paths), optional `Assessment/`, and:
  - `Conversion_SnowparkAPI/` — Snowpark API runs
  - `Conversion_SnowparkConnect/` — Snowpark Connect (SCOS) runs
- Each **`Conversion_SnowparkAPI/`** or **`Conversion_SnowparkConnect/`** folder contains one or more **`sma-code-process-YYYY-MM-DD-HH-mm-ss/`** execution folders (same inner tree as v2: `Output/`, `Reports/`, `Logs/`, etc.).

**Resolve `<output>`:** pick **`Conversion_SnowparkAPI`** or **`Conversion_SnowparkConnect`** (see **v3 folder selection** in the algorithm above), then the **most recent** `sma-code-process-*` under that folder. That path is `<output>` = `workload_path` for all later steps.

**Display detected configuration (keep it brief):**
- SMA Format: v3 (`Conversion_SnowparkAPI` or `Conversion_SnowparkConnect` + latest `sma-code-process-*`)
- Input:  <input>
- Output: <output> (full path to the chosen execution folder)

#### 1.3 Handle Inconsistencies (v1 format)

**If user-provided path doesn't match `inputPath` in `.snowma`:**
```
Warning: The folder you provided doesn't match the inputPath in .snowma

  You provided:     /path/user/provided
  .snowma says:     /path/from/snowma

> Do you want to:
  1. Use the path from .snowma
  2. Continue with your provided path (will need to specify <output> manually)
```

**If user chooses (2), ask for `<output>`:**
```
> Please provide the path to the SMA output folder (<output>):
```

**If provided `<output>` doesn't match `.snowma`:**
```
Warning: The output path doesn't match the .snowma configuration

  You provided:     /path/user/output
  .snowma says:     /path/from/snowma/output

> Continue anyway? (yes/no)
```

#### 1.4 Fallback: No Format Detected

If none of `.snowma`, `sma-output/`, or v3 (`Conversion_SnowparkAPI` / `Conversion_SnowparkConnect` with nested `sma-code-process-*`) matched:

1. Search for legacy `Conversion-*` folders in the output location (**exclude** `Conversion_SnowparkAPI` and `Conversion_SnowparkConnect`)
2. If found, use the most recent one as `<output>`
3. If not found, ask user for both paths:
   ```
   Could not detect SMA output format.
   
   > Please provide the path to the original source code (<input>):
   > Please provide the path to the SMA output folder (<output>):
   ```

#### 1.5 Display Confirmed Configuration

Once paths are resolved and validated:

**v1 format (brief):**
- SMA Format: v1 (Conversion-<timestamp>/)
- Project: <name>
- Input:  <input>
- Output: <output>

**v3 format (brief):**
- SMA Format: v3 (latest `sma-code-process-*` under `Conversion_SnowparkAPI` or `Conversion_SnowparkConnect`)
- Input:  <input>
- Output: <output>

### Step 2: Validate SMA Structure

Verify the SMA output folder contains expected components:

| Path | Required | Description |
|------|----------|-------------|
| `<output>/Output/` | Yes | Migrated Snowpark Python code |
| `<output>/Reports/` | No | SMA inventories and reports (optional) |
| `<output>/Logs/` | No | SMA execution logs |
| `<output>/Reports/Issues.csv` | No | EWI issues (used by ewi-extractor) |
| `<output>/Reports/Inventory.csv` | No | Entrypoints inventory (used by entry-point-identifier) |
| `<output>/Reports/IOInventory.csv` | No | I/O mappings (used by io-identifier) |

**Stopping point:** If required folders are missing (notably `<output>/Output/`), warn the user and ask to confirm or provide a different path. Do not stop if `<output>/Reports/` is missing.

### Step 3: Create DVP Workspace

Create the DVP folder structure. The location depends on the SMA format:

| Format | DVP Location | Rationale |
|--------|--------------|-----------|
| **v1** | `<output>/dvp/` (inside `Conversion-*/`) | DVP is scoped to a specific conversion run |
| **v2** | `<output>/dvp/` (inside `sma-output/`) | Consistent with v1; `workload_path/dvp/` always works |
| **v3** | `<output>/dvp/` (inside latest `sma-code-process-*/`) | Same contract as v2; `<output>` is the execution folder |

#### Terminology & Canonical Structure

The example 00 structure is the canonical reference:

```
input/                               <-- <sma-input>  (original source — PySpark or Scala)
    workload.py
output/                              <-- <sma-output> (SMA output root)
    sma-output/                      <-- SMA conversion output = <output> = workload_path
        Output/                      <-- migrated code (either Snowpark API or SCOS)
        Reports/                     <-- SMA inventories
        sma_storage.sqlite3          <-- SMA database
        sma-dashboard/               <-- dashboard
        dvp/                         <-- DVP workspace (inside <output>)
            01-source/               <-- adapted by dvp-code-adapter
            02-migrated/             <-- Snowpark API migrated code (if selected)
            02-migrated_scos/        <-- SCOS migrated code (if selected)
            03-tests/                <-- test framework
            04-results/              <-- skill outputs
    results/                         <-- historical SMA runs
```

- `input/` = `<sma-input>`: the original source code — PySpark or Scala (unchanged)
- `output/` = `<sma-output>`: everything SMA produces
- `output/sma-output/` = `<output>` = `workload_path`: the conversion folder used by all skills
- `output/sma-output/Output/` = the SMA-migrated code (either Snowpark API **or** SCOS, depending on what you choose below)
- `output/sma-output/dvp/` = DVP workspace with **adapted** copies of source and the selected migrated flavor

> **Important:** `01-source/`, `02-migrated/`, and `02-migrated_scos/` do NOT contain raw copies of the original files. They contain files that have been **adapted by `dvp-code-adapter`** for testability (session injection, `.enableHiveSupport()` for source, deferred env vars). The original unmodified source stays in `<sma-input>`, and the unmodified SMA output stays in `sma-output/Output/`.

**v1 format:** `Conversion-<timestamp>/dvp/`
**v2 format:** `sma-output/dvp/`
**v3 format:** `<sma-output-root>/Conversion_SnowparkAPI/sma-code-process-<ts>/dvp/` (or `Conversion_SnowparkConnect/...`)

**DVP folder structure:**
```
dvp/
    01-source/                   # Source files adapted by dvp-code-adapter
    02-migrated/                 # Migrated Snowpark API files (if selected)
    02-migrated_scos/            # Migrated SCOS files (if selected)
    03-tests/                    # Generated pytest files
    04-results/                  # Skill outputs (inventories, schemas, reports)
    05-assets/
        FromCustomer/            # Customer-provided schemas, data, etc.
```

**Folders to create (in order):**
1. `dvp/01-source/`
2. Create **exactly one** migrated folder based on the user's choice (v1/v2) **or** from the resolved v3 folder (`Conversion_SnowparkAPI` vs `Conversion_SnowparkConnect`):
   - `dvp/02-migrated/` (Snowpark API)
   - `dvp/02-migrated_scos/` (SCOS / Snowpark Connect)
   - **v3:** If `<output>` is under `Conversion_SnowparkAPI/`, create only `dvp/02-migrated/`. If under `Conversion_SnowparkConnect/`, create only `dvp/02-migrated_scos/`.
3. `dvp/03-tests/`
4. `dvp/04-results/`
5. `dvp/05-assets/FromCustomer/`

**Actions:**
1. Determine DVP location based on format (v1 / v2 / v3: always `<output>/dvp/` where `<output>` is the resolved conversion or execution folder)
2. Create `dvp/` if it doesn't exist
3. Create all subfolders as listed above
4. If `dvp/` already exists, ask user whether to:
   - Overwrite (delete and recreate)
   - Merge (keep existing, create missing)
   - Abort

### Step 4: Copy Source Files

1. **Copy original source:**
   ```
   <input>/* -> dvp/01-source/
   ```
   - Preserve directory structure
   - Copy all files (not just `.py`)
   - **Exclude:** `*.snowma`, `*.snowct`, `checkpoints.json`

2. **Select migrated flavor:**

   **If SMA format is v3:** `<output>` already lies under **`Conversion_SnowparkAPI/`** or **`Conversion_SnowparkConnect/`**. Do **not** ask for flavor again if **`Conversion_SnowparkAPI`** vs **`Conversion_SnowparkConnect`** was already chosen in Step 1. Map copy destination directly:
   - `<output>` path includes `Conversion_SnowparkAPI` → copy migrated code into `dvp/02-migrated/` only (Snowpark API)
   - `<output>` path includes `Conversion_SnowparkConnect` → copy migrated code into `dvp/02-migrated_scos/` only (SCOS)

   **If SMA format is v1 or v2:** If the conversion type was provided by the spark-migration orchestrator (via pre-configured context), use it directly:
   - `"scos"` → copy into `dvp/02-migrated_scos/`
   - `"snowpark_api"` → copy into `dvp/02-migrated/`

   If **not** provided (running standalone), ask the user which migrated flavor they want to validate:
   - **Snowpark API** (Snowpark Python) → copy into `dvp/02-migrated/`
   - **SCOS** (Snowpark Connect for Spark / PySpark-on-Snowflake) → copy into `dvp/02-migrated_scos/`

3. **Copy migrated code** (`<output>/Output/*` → `dvp/02-migrated/` or `dvp/02-migrated_scos/`). Preserve directory structure. **Exclude:** `*.snowma`, `*.snowct`, `checkpoints.json`.

> **Note:** After Step 6, converted scripts are added alongside originals. After Step 9, folders contain **adapted** code. Originals stay in `<sma-input>` and `<output>/Output/`.

### Step 5: Report Summary

Present a brief summary to the user:

- DVP workspace: `dvp/`
- Source: `dvp/01-source/`
- Migrated: `dvp/02-migrated/` or `dvp/02-migrated_scos/` (selected)
- Next: notebook conversion (if any)

### Step 5.5: Initialize Git Repository

**Before invoking any sub-skill**, initialize git and switch to the working branch:

```python
result = sma_api.git_ensure_ready("<output>")
```

This idempotent call handles `git init` + initial commit + create/switch to `sma/migration-process`.

After the full pipeline completes, verify both branches:
```python
result = sma_api.git_verify_branches("<output>")
```

**At the end there MUST be exactly two branches:** `main` (original, unmodified) and `sma/migration-process` (all DVP changes).

### Step 6: Invoke dvp-notebook-to-script

**⛔ MANDATORY:** load `<dvp_root>/dvp-notebook-to-script/SKILL.md` with the Read tool and follow it inline (see [Bundled Sub-skill Loading Convention](#bundled-sub-skill-loading-convention)). Converts `.ipynb` and Databricks `.py` files to testable Python scripts (`.ipynb.py`, `.dbx.py`). If no notebooks found, continue to next step.

> **⛔ CRITICAL: Do NOT delete files from `01-source/` or the migrated folder.** The script only **creates** new files alongside the originals.

### Step 7: Dependency Bootstrap (shared venv)

Ensure `dvp/.venv` exists with required packages before WARP scripts or pytest.

1. **Preflight:** `python -c "import pydantic; import sqlglot; import pytest; import pytest_subtests; import pyspark; import snowflake.snowpark"`
2. **If fails:** `cd dvp && uv venv .venv && uv pip install -r requirements.txt`
3. Run subsequent commands via `dvp/.venv/bin/python`

### ⛔ CRITICAL RULE — Steps 6-13 MUST load bundled sub-skills inline

**NEVER** use the Task tool, `run_in_background`, or manual Python/bash to perform what these skills do. Each sub-skill is loaded via the [Bundled Sub-skill Loading Convention](#bundled-sub-skill-loading-convention) (Read tool → follow inline, sequentially, in the foreground) and produces files in specific formats that downstream tools depend on.

### Step 8: Invoke dvp-asg-generation

**⛔ HARD GATE — ASG Generation is MANDATORY.** If it fails, **STOP the pipeline immediately** — all downstream skills depend on the ASG. Show the Final Summary with ASG as `Failed` and remaining steps as `Blocked`.

**⛔ MANDATORY:** load `<dvp_root>/dvp-asg-generation/SKILL.md` with the Read tool and follow it inline.

**Wait for completion. Verify `dvp/04-results/*_asg.json` exists before proceeding. If it does not exist, STOP.**

### Step 9: Invoke dvp-entrypoint-identifier

**⛔ MANDATORY:** load `<dvp_root>/dvp-entrypoint-identifier/SKILL.md` with the Read tool and follow it inline. DO NOT generate `entrypoints.json` manually — the skill produces a specific format that `register_tests.py`, `conftest.py`, and the test tracker depend on.

**Wait for completion before proceeding to Step 10.**

### Step 10: Invoke dvp-code-adapter

**⛔ MANDATORY:** load `<dvp_root>/dvp-code-adapter/SKILL.md` with the Read tool and follow it inline. DO NOT adapt workload code manually — the skill writes `adapted_source` into `entrypoints.json` which test generation depends on.

**Wait for completion before proceeding to Step 11.**

### Step 11: Invoke dvp-io-schema-identifier

**⛔ MANDATORY:** load `<dvp_root>/dvp-io-schema-identifier/SKILL.md` with the Read tool and follow it inline. DO NOT generate `data_io_schema.json` manually — the skill produces column schemas and key_columns that test setup and synthetic data depend on.

**Wait for completion. Verify `dvp/04-results/data_io_schema.json` exists before proceeding to Step 12.**

### Step 12: Invoke dvp-synthetic-data-generator

**⛔ MANDATORY:** load `<dvp_root>/dvp-synthetic-data-generator/SKILL.md` with the Read tool and follow it inline. DO NOT generate synthetic data manually.

**Wait for completion before proceeding to Step 13.**

### Step 13: Invoke dvp-test-setup-generator

**⛔ MANDATORY:** load `<dvp_root>/dvp-test-setup-generator/SKILL.md` with the Read tool and follow it inline. DO NOT generate test files or conftest.py manually — the skill copies 700+ line template conftest files with pytest hooks that auto-record test results in the database.

**Wait for completion before proceeding to Step 14.**

### Step 14: Invoke dvp-test-runner

**⛔ MANDATORY:** load `<dvp_root>/dvp-test-runner/SKILL.md` with the Read tool and follow it inline, in the current conversation. DO NOT use the Task tool. DO NOT launch as a background agent. DO NOT run pytest manually.

See [STEP14_REFERENCE.md](./STEP14_REFERENCE.md) for detailed description of what this skill does.

**Wait for completion before proceeding to Step 15.**

### Step 15: Invoke dvp-migrated-test-fixer

**⛔ MANDATORY:** load `<dvp_root>/dvp-migrated-test-fixer/SKILL.md` with the Read tool and follow it inline, in the current conversation. DO NOT use the Task tool. DO NOT launch as a background agent.

After Step 14 ensures migrated tests **run**, this skill makes them **pass**. It iterates over failing migrated tests, diagnoses PySpark-to-Snowpark API incompatibilities in `dvp/02-migrated/` code, applies fixes, commits each change to git, and retries until all tests pass.

**Precondition:** Git must be initialized on the output folder (done by Step 5.5). The skill uses git commits to track each fix iteration and detect loops.

**Wait for completion — proceed to the Final Summary.**

## ⛔ MANDATORY: Complete ALL Steps Before Showing Final Summary

**DO NOT show the Final Summary until ALL steps (1 through 15) have been attempted.**
Steps 10-15 are REQUIRED — they are NOT optional or future work.
If you are about to show the Final Summary and have not completed Steps 10-15, STOP and go back to Step 10.

## Final Summary

See [STEP14_REFERENCE.md](./STEP14_REFERENCE.md) for the full summary table template and rules.

Present the DVP Pipeline Complete table with a row per step (DVP Workspace, Notebook Conversion, ASG Generation, Entrypoint Detection, Code Adapter, IO Schema, Synthetic Data, Test Setup, Test Execution, Migrated Test Fixer) showing Status (`Done`/`Skipped`/`Failed`) and Details with actual counts. End with output location and git branches (verified via `sma_api.git_verify_branches()`).

## Stopping Points

- **Invalid SMA output:** If the current directory or provided path doesn't have `Output/`, stop and ask for correct path. `Reports/` is optional.
- **Missing input path:** If `<input>` doesn't exist or has no Python files, stop and ask user to verify
- **DVP already exists:** Ask user how to proceed (overwrite/merge/abort)
- **Copy errors:** If file copy fails, report which files failed and ask how to proceed
- **⛔ ASG generation failed:** If `dvp-asg-generation` fails (library not available or parsing error), **STOP the entire pipeline**. Do NOT continue to steps 9-14. Show the Final Summary with ASG as `Failed` and remaining steps as `Blocked`.

## Inputs

| Input | Required | Source |
|-------|----------|--------|
| `<input>` path | Yes | v1: from `.snowma` (`inputPath`) or user-provided. v2/v3: **always user-provided** (`.snowct` has no paths) |
| `<output>` path | Yes | v1: from `.snowma` (`internalConversionOutputPath`). v2: auto-detected (`sma-output/`). v3: auto-detected (latest `sma-code-process-*` under chosen `Conversion_SnowparkAPI` or `Conversion_SnowparkConnect`). Fallback: user-provided |
| `.snowma` file | No | Auto-detected in `<input>/` (v1 format only) |
| `.snowct` file | No | May exist in output root (v2/v3) -- contains project ID only, not useful for paths |

## Outputs

| Output | Format | Location |
|--------|--------|----------|
| DVP folder structure | Directories | `dvp/` |
| Copied source files | Various | `dvp/01-source/` |
| Copied migrated files (Snowpark API) | Various | `dvp/02-migrated/` (if selected) |
| Copied migrated files (SCOS) | Various | `dvp/02-migrated_scos/` (if selected) |
| Entry points inventory | JSON | `dvp/04-results/entrypoints.json` (via `dvp-entrypoint-identifier`) |
| Converted notebook scripts | `.py` | `dvp/01-source/*.dbx.py`, `dvp/01-source/*.ipynb.py`, `dvp/02-migrated/*.dbx.py`, `dvp/02-migrated/*.ipynb.py` (via `dvp-notebook-to-script`) |