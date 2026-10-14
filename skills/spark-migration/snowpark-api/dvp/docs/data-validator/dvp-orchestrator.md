# dvp-orchestrator

> Initialize the DVP workspace and orchestrate the validation pipeline.

## Overview

| Field | Value |
|-------|-------|
| **Category** | data-validator |
| **Status** | **Implemented** |
| **Output** | DVP workspace structure + orchestration of downstream skills |
| **Depends on** | All other data-validator skills |
| **SKILL.md** | [`dvp-orchestrator/SKILL.md`](../../dvp-orchestrator/SKILL.md) |

## Operator Tips (Cortex Code UX)

When running this skill in the Cortex Code CLI, you will see intermediate tool steps like `READ`, `EDIT`, and `BASH`. This is normal.

To reduce output noise:
- Prefer plan mode (`/plan`) for long workflows.
- Ask for a 1-2 line summary after each BASH.
- Avoid reading large files (show a 5-10 line preview instead).

## Execution Reporting (DVP)

When this orchestrator invokes downstream **DVP** skills, report progress after each step:

- What ran (step + skill name)
- What was generated (files/artifacts)
- Where it was saved (paths)
- Any warnings (including anomaly reports / partial detections)

End with a brief overall summary of what was produced.

## Responsibility

This is the **entry point for DVP operations**. It initializes the DVP workspace structure after an SMA migration is complete, then orchestrates downstream skills in sequence.

The orchestrator handles:
1. Detecting SMA paths (v1 `.snowma`, v2 `sma-output/`, v3 **`Conversion_SnowparkAPI`** / **`Conversion_SnowparkConnect`** + latest `sma-code-process-*` — see [`dvp-orchestrator/SKILL.md`](../../dvp-orchestrator/SKILL.md) Step 1)
2. Validating the SMA output structure
3. Creating the `<output>/dvp/` workspace with all subfolders
4. Copying source and migrated files into working copies
5. Invoking downstream skills starting with `dvp-notebook-to-script` (optional), then ASG generation, entrypoint detection, code adaptation, IO schema identification, synthetic data generation, and test setup generation (Steps 6-13).

## Workflow

### Step 1: Detect SMA paths (multi-format)

SMA may emit **v1** (`.snowma` + `Conversion-*`), **v2** (`sma-output/`), or **v3** (`Conversion_SnowparkAPI` / `Conversion_SnowparkConnect` with nested `sma-code-process-*`). The canonical algorithm lives in **`dvp-orchestrator` Step 1** in [`SKILL.md`](../../dvp-orchestrator/SKILL.md).

**v1 (`.snowma` present):** Read `internalConversionOutputPath` as `<output>` (or newest `Conversion-*` under `outputPath` if empty). Validate paths.

**Handling inconsistencies (v1):**

- If user-provided path doesn't match `.snowma`'s `inputPath`, warn and ask user to choose
- If `.snowma` found in `<output>/Output/`, cross-validate against user-provided `<input>`

**v2 / v3:** No paths in `.snowct` — user provides `<input>`; `<output>` is auto-detected per the skill (v2 checks **`sma-output/` before v3**).

**Once paths are resolved:**

```
SMA Project: <name>

Paths confirmed:
  - Input (original Spark):  <input>
  - Output (SMA conversion): <output>

Folders validated:
  ✓ <input> exists
  ✓ <output>/Output/ exists
  ✓ <output>/Reports/ exists (optional; if missing, continue without SMA inventories)

Proceeding with DVP workspace setup...
```

### Step 2: Validate SMA Structure

Verifies the conversion folder contains expected components:

| Path | Required | Used by |
|------|----------|---------|
| `<output>/Output/` | Yes | Migrated Snowpark code |
| `<output>/Reports/` | No | SMA inventories and reports (optional) |
| `<output>/Logs/` | No | SMA execution logs |
| `<output>/Reports/Issues.csv` | No | `dvp-ewi-extractor` |
| `<output>/Reports/Inventory.csv` | No | `dvp-entrypoint-identifier` |
| `<output>/Reports/IOInventory.csv` | No | `dvp-io-schema-identifier` |

### Step 3: Create DVP Workspace

Creates the folder structure at `<output>/dvp/` (inside the conversion folder, alongside `Output/`, `Reports/`, and `Logs/`):

```
dvp/
├── 01-source/
├── 02-migrated/          (if Snowpark API selected)
├── 02-migrated_scos/     (if SCOS selected)
├── 03-tests/
├── 04-results/
└── 05-assets/
    └── FromCustomer/
```

If `dvp/` already exists, asks the user: overwrite, merge, or abort.

### Step 4: Copy Source + Migrated Files

- Copies `<input>/*` to `dvp/01-source/` (preserving directory structure)
- Asks the user which migrated flavor the SMA `Output/` corresponds to (always ask; no auto-detect):
  - **Snowpark API** → copy to `dvp/02-migrated/`
  - **SCOS (Snowpark Connect)** → copy to `dvp/02-migrated_scos/`
- Copies `<output>/Output/*` into the selected migrated folder only (preserving directory structure)

### Step 5: Report Summary

Presents workspace status: file counts, available SMA reports, readiness.

### Step 6: Invoke dvp-entrypoint-identifier

Automatically invokes the entrypoint-identifier skill to generate the entrypoints inventory from the ASG.

### Step 7+: Continue Pipeline

After entrypoint detection, the orchestrator continues with the remaining skills:

| Step | Skill | Output |
|------|-------|--------|
| 7 | `dvp-entrypoint-identifier` | `04-results/entrypoints.json` |
| 8 | `dvp-asg-generation` | `04-results/XX_asg.json` (+ anomalies) |
| 9 | `dvp-entrypoint-identifier` | `04-results/entrypoints.json` |
| 10 | `dvp-code-adapter` | in-place edits in `01-source/` / `02-migrated/` / `02-migrated_scos/` |
| 11 | `dvp-io-schema-identifier` | `04-results/data_io_schema.json` |
| 12 | `dvp-synthetic-data-generator` | `04-results/synthetic_data/*.csv` |
| 13 | `dvp-test-setup-generator` | `03-tests/` |

All steps are invoked by loading the bundled sub-skill SKILL.md with the Read tool (see the orchestrator's "Bundled Sub-skill Loading Convention" — `<dvp_root>/dvp-<name>/SKILL.md`). Steps 10-13 are mandatory — not future work.

## Inputs

| Input | Required | Source |
|-------|----------|--------|
| `.snowma` file | No | Auto-detected in `<input>` or `<output>/Output/` |
| `<output>` path | Yes | From `.snowma` (`internalConversionOutputPath`) or user-provided |
| `<input>` path | Yes | From `.snowma` (`inputPath`) or user-provided |

## Outputs

| Output | Format | Location |
|--------|--------|----------|
| DVP folder structure | Directories | `<output>/dvp/` |
| Copied source files | Various | `dvp/01-source/` |
| Copied migrated files | Various | `dvp/02-migrated/` *(if Snowpark API selected)* |
| Copied migrated files (SCOS) | Various | `dvp/02-migrated_scos/` *(if SCOS selected)* |
| Entrypoints inventory | JSON | `dvp/04-results/entrypoints.json` (via dvp-entrypoint-identifier) |

## Stopping Points

- **Invalid SMA output structure** -- asks for correct path
- **Missing `<input>` path or no Python files found**
- **`.snowma` path mismatch** -- warns user and offers options
- **DVP workspace already exists** -- asks overwrite/merge/abort
- **File copy errors** -- reports failures and asks how to proceed
