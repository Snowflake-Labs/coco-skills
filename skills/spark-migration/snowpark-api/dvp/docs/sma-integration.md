# SMA Integration and Folder Structure

How the DVP (Data Validation Pipeline) integrates with the SMA (Snowpark Migration Accelerator) and the folder layout it operates on.

## What is SMA?

The **Snowpark Migration Accelerator (SMA)** is a tool that converts workloads written in PySpark (Python, sometimes with Pandas), Scala Spark, or a mix of both to Snowflake using the Snowpark API. DVP currently supports PySpark or Scala workloads (not mixed).

SMA requires two paths from the user:

| Path | Description |
|------|-------------|
| `<input>` | The folder containing the original Python Spark source code. **These files are immutable** -- SMA never modifies them. |
| SMA output root | The folder where SMA writes all its results. |

## SMA Output Formats

SMA uses different output folder structures depending on the tool version. **v2 and v3 can both appear in the field** — DVP detects **v2 first** (`sma-output/`) so existing projects behave unchanged, then **v3** (`Conversion_SnowparkAPI` / `Conversion_SnowparkConnect` + `sma-code-process-*`).

### Format v1 (Legacy): Timestamped Conversion Folder

The legacy format creates a **timestamped conversion subfolder** inside the output root:

```
sma-output-root/
└── Conversion-<M>-<DD>-<YYYY>T<HH> <MM>/    ← This becomes <output>
    ├── Logs/
    ├── Output/
    └── Reports/
```

**Characteristics:**
- Creates a `.snowma` project file in `<input>/` and `<output>/Output/`
- Each conversion run creates a new timestamped folder
- Path resolution via `internalConversionOutputPath` in `.snowma`

### Format v2: sma-output + Historical Results

This format uses a **flat `sma-output/` folder** for the latest conversion, plus a `results/` folder for historical runs:

```
sma-output-root/
├── <project-name>.snowct                              ← Project file (no paths, only project ID)
├── sma-output/                                        ← This becomes <output>
│   ├── Logs/
│   ├── Output/
│   └── Reports/
└── results/
    └── sma-code-processes/
        └── sma-code-process-<YYYY>-<MM>-<DD>-<HH>-<mm>-<ss>/   ← Historical
            ├── Logs/
            ├── Output/
            └── Reports/
```

**Characteristics:**
- Generates a `.snowct` project file (YAML) in the output root, but it does **NOT** contain input/output paths
- Does **NOT** generate a `.snowma` project file
- `sma-output/` always contains the latest/current conversion
- `results/sma-code-processes/` contains timestamped historical runs
- DVP uses `sma-output/` as `<output>` by default
- **`<input>` must be provided by the user** (cannot be auto-resolved from `.snowct`)

### Format v3: Conversion_SnowparkAPI / Conversion_SnowparkConnect

Newer SMA layouts may omit `sma-output/` and place each flavor under a dedicated folder, with **timestamped executions** under each:

```
sma-output-root/
├── <project-name>.snowct                              ← Optional; no paths (same as v2)
├── Assessment/                                        ← Optional; not used for DVP path resolution
├── Conversion_SnowparkAPI/
│   └── sma-code-process-<YYYY>-<MM>-<DD>-<HH>-<mm>-<ss>/   ← Candidate <output> (latest when `Conversion_SnowparkAPI` is chosen)
│       ├── Logs/
│       ├── Output/
│       └── Reports/
└── Conversion_SnowparkConnect/
    └── sma-code-process-<YYYY>-<MM>-<DD>-<HH>-<mm>-<ss>/   ← Candidate <output> (latest when `Conversion_SnowparkConnect` is chosen)
        ├── Logs/
        ├── Output/
        └── Reports/
```

**Characteristics:**

- **`<output>`** for DVP is the **most recent** `sma-code-process-*` directory under **either** `Conversion_SnowparkAPI/` **or** `Conversion_SnowparkConnect/`, depending on user/orchestrator choice (same “latest run” rule as picking the newest legacy `Conversion-*` folder in v1).
- Inner layout under that execution folder matches v2 (`Output/`, `Reports/`, `Logs/`, etc.).
- **`Assessment/`** is ignored for path resolution.
- **`<input>` must be provided by the user** (same as v2).

### Format Detection

DVP automatically detects which format is present:

| Check | Format | `<input>` resolution | `<output>` resolution |
|-------|--------|----------------------|----------------------|
| `.snowma` file exists in `<input>/` | v1 | From `.snowma` `inputPath` | From `.snowma` `internalConversionOutputPath` |
| `sma-output/` folder exists (+ optional `.snowct`) | v2 | **User must provide** | Auto-detected (`sma-output/`) |
| `Conversion_SnowparkAPI` or `Conversion_SnowparkConnect` exists with nested `sma-code-process-*` | v3 | **User must provide** | Latest `sma-code-process-*` under the chosen **`Conversion_SnowparkAPI`** or **`Conversion_SnowparkConnect`** folder |
| Legacy `Conversion-*` folder exists (excluding `Conversion_SnowparkAPI` / `Conversion_SnowparkConnect`) | v1 (no `.snowma`) | **User must provide** | Most recent matching `Conversion-*` |

## SMA Project Files

### `.snowma` Project File (v1 Format Only)

SMA v1 generates a **`.snowma` project file** (JSON) in both `<input>` and `<output>/Output/`. This file contains the full project configuration including all resolved paths. DVP uses it as the **primary mechanism for path resolution** in v1 format.

#### `.snowma` File Structure

```json
{
  "name": "project-name",
  "inputPath": "/path/to/input",
  "outputPath": "/path/to/sma-output-root",
  "internalConversionOutputPath": "/path/to/sma-output-root/Conversion-X-XX-XXXXTXX XX",
  "platform": "SnowConvertPython",
  "conversionProgress": { "isSuccessful": true },
  ...
}
```

#### Key Fields for DVP

| Field | Description | DVP Usage |
|-------|-------------|-----------|
| `name` | Project name | Display in DVP reports |
| `inputPath` | Path to the original source code | Becomes `<input>` |
| `outputPath` | SMA output root folder | Parent of the conversion folder |
| `internalConversionOutputPath` | The specific conversion folder path | **Becomes `<output>`** -- this is the key path for DVP |

### `.snowct` Project File (v2 / v3 Format)

SMA may generate a **`.snowct` project file** (YAML) in the **SMA output root** (sibling of `sma-output/` in v2, or sibling of **`Conversion_SnowparkAPI`** / **`Conversion_SnowparkConnect`** in v3). Unlike `.snowma`, this file does **NOT** contain input/output paths -- it only holds a reference to the project in the SMA platform.

#### `.snowct` File Structure

```yaml
version: "1.0"
route: /projects/MIGRATION_PROJECT_<uuid>
queryParameters:
  projectId: MIGRATION_PROJECT_<uuid>
```

#### `.snowct` vs `.snowma` Comparison

| Aspect | `.snowma` (v1) | `.snowct` (v2 / v3) |
|--------|----------------|----------------|
| Format | JSON | YAML |
| Location | `<input>/` and `<output>/Output/` | SMA output root (sibling of `sma-output/`, or of **`Conversion_SnowparkAPI`** / **`Conversion_SnowparkConnect`** in v3) |
| Contains `inputPath`? | Yes | **No** |
| Contains `outputPath`? | Yes | **No** |
| Contains `internalConversionOutputPath`? | Yes | **No** |
| Contains project ID? | Yes (`name`) | Yes (`projectId`) |
| Useful for DVP path resolution? | **Yes** -- primary mechanism | **No** -- only identifies the project |

> **Important:** Because `.snowct` does not contain paths, DVP **cannot auto-resolve `<input>`** in v2 or v3 format. The user must provide it explicitly. `<output>` is auto-detected from `sma-output/` (v2) or from the latest `sma-code-process-*` under the chosen **`Conversion_SnowparkAPI`** or **`Conversion_SnowparkConnect`** folder (v3).

### Path Resolution

The DVP orchestrator resolves paths using this algorithm:

```
1. Search for .snowma file in <input>
   ├─ Found → v1 format: Use internalConversionOutputPath as <output>
   └─ Not found → Continue to step 2

2. Look for sma-output/ in the sibling output folder
   ├─ Found → v2 format: Use sma-output/ as <output>
   │          Note: <input> must be provided by the user (.snowct has no paths)
   └─ Not found → Continue to step 3

3. v3: Look for Conversion_SnowparkAPI and/or Conversion_SnowparkConnect with nested sma-code-process-*
   ├─ Found → v3: Choose Conversion_SnowparkAPI or Conversion_SnowparkConnect, then set <output> to the most recent sma-code-process-* under that folder
   │          Note: <input> must be provided by the user
   └─ Not found → Continue to step 4

4. Search for legacy Conversion-* folders in the sibling output folder (exclude Conversion_SnowparkAPI and Conversion_SnowparkConnect)
   ├─ Found → v1 format (no .snowma): Use most recent remaining Conversion-* as <output>
   └─ Not found → Continue to step 5

5. Ask user for both <input> and <output> paths
```

> **Important:** In DVP documentation, **`<output>` refers to the conversion or execution folder** (e.g., a timestamped `Conversion-<M>-<DD>-<YYYY>T<HH> <MM>/`, `sma-output/`, or `.../Conversion_SnowparkAPI/sma-code-process-.../`), NOT the SMA output root. This means `<output>/Output/`, `<output>/Reports/`, `<output>/Logs/`, and `<output>/dvp/` are all siblings inside that folder.

## SMA Output Structure

### v1 Format Structure

After running SMA (legacy versions), the output root contains a **timestamped conversion subfolder**:

```
sma-output-root/
└── Conversion-<M>-<DD>-<YYYY>T<HH> <MM>/    ← This is <output> in DVP terms
    ├── Logs/                                  # SMA log files
    │   ├── GenericInfrastructureController/
    │   │   └── Controller-Log-<timestamp>.log
    │   └── PythonSnowConvert-Log-<timestamp>.log
    ├── Output/                                # Snowpark migrated Python code
    │   ├── <mirrors input structure>
    │   └── <project>.snowma                   # Copy of project file
    └── Reports/                               # Inventories (.csv) and reports (.json)
        ├── Issues.csv
        ├── AssessmentReport.json
        ├── tool_execution.csv
        ├── SparkUsagesInventory.csv
        ├── DataFramesInventory.csv
        ├── ExecutionFlowInventory.csv
        ├── IOFilesInventory.csv
        ├── InputFilesInventory.csv
        ├── ImportUsagesInventory.csv
        ├── PackagesInventory.csv
        ├── ArtifactDependencyInventory.csv
        ├── JoinsInventory.csv
        ├── CheckpointsInventory.csv
        ├── NotebookSizeInventory.csv
        ├── NotebookCellsInventory.csv
        ├── PandasUsagesInventory.csv
        ├── ThirdPartyUsagesInventory.csv
        ├── DbxElementsInventory.csv
        ├── SqlElementsInventory.csv
        ├── SqlFunctionsInventory.csv
        ├── SqlEmbeddedUsageInventory.csv
        ├── TechnicalDiscoveryInventory.csv
        └── SnowConvertReports/                # SnowConvert-specific reports
            ├── Assessment.<timestamp>.csv
            ├── Assessment.<timestamp>.json
            ├── Elements.<timestamp>.csv
            ├── Issues.<timestamp>.csv
            ├── ObjectReferences.<timestamp>.csv
            ├── TopLevelCodeUnits.<timestamp>.csv
            ├── MissingObjectReferences.<timestamp>.csv
            ├── SqlFunctionsUsage.<timestamp>.csv
            └── SQLTableProperties.<timestamp>.csv
```

> **Note:** The conversion folder name typically includes a date and time (e.g., `Conversion-2-12-2026T12 23/`). Multiple conversions of the same project create separate timestamped folders. The `.snowma` file's `internalConversionOutputPath` points to the specific conversion used.

### v2 Format Structure

Current SMA versions use a different structure with a flat `sma-output/` folder:

```
sma-output-root/
├── <project-name>.snowct                              ← Project file (no paths)
├── sma-output/                                        ← This is <output> in DVP terms
│   ├── Logs/
│   │   ├── GenericScanner/
│   │   └── GenericInfrastructureController/
│   ├── Output/                                        # Snowpark migrated Python code
│   │   └── <mirrors input structure>
│   └── Reports/                                       # Inventories and reports (same as v1)
│       ├── Issues.csv
│       ├── AssessmentReport.json
│       ├── ... (same inventory files as v1)
│       └── SnowConvertReports/
└── results/
    └── sma-code-processes/
        └── sma-code-process-<YYYY>-<MM>-<DD>-<HH>-<mm>-<ss>/
            ├── Logs/
            ├── Output/
            └── Reports/
```

**Key differences from v1:**
- No `.snowma` project file
- `.snowct` project file exists but only contains a project ID (no paths)
- `sma-output/` is always the "current" conversion (overwrites on re-run)
- Historical runs are preserved in `results/sma-code-processes/`
- DVP uses `sma-output/` as `<output>` by default
- **`<input>` must be provided by the user** (`.snowct` cannot resolve it)

> **Note:** If you need to work with a historical conversion from v2 format, you can manually specify the path to the timestamped folder under `results/sma-code-processes/`.

### Key SMA Reports

| Report | Description | Used by DVP |
|--------|-------------|-------------|
| `Issues.csv` | EWI issues found during migration | `dvp-ewi-extractor` |
| `SparkUsagesInventory.csv` | All PySpark API usages with support/automation status | `dvp-entrypoint-identifier` |
| `ExecutionFlowInventory.csv` | Caller/callee relationships between modules and functions | `dvp-entrypoint-identifier` |
| `IOFilesInventory.csv` | File I/O operations detected in the code | `dvp-io-schema-identifier` |
| `InputFilesInventory.csv` | Input files inventory (file paths, sizes, parse results) | `dvp-entrypoint-identifier` |
| `DataFramesInventory.csv` | DataFrame operations and lineage | `dvp-io-schema-identifier` |
| `PackagesInventory.csv` | Package/import dependencies | `dvp-entrypoint-identifier` |
| `ArtifactDependencyInventory.csv` | Dependencies between artifacts | `dvp-entrypoint-identifier` |
| `JoinsInventory.csv` | Join operations detected | `dvp-io-schema-identifier` |
| `AssessmentReport.json` | Overall migration assessment | `dvp-testing-status-manager` |
| `SnowConvertReports/Issues.<ts>.csv` | Detailed SnowConvert issues | `dvp-ewi-extractor` |
| `SnowConvertReports/ObjectReferences.<ts>.csv` | Object dependency references | `dvp-entrypoint-identifier` |
| `SnowConvertReports/TopLevelCodeUnits.<ts>.csv` | Top-level code unit inventory | `dvp-entrypoint-identifier` |

## Where DVP Lives

Once the SMA migration is complete, DVP creates its workspace. The location depends on the SMA format:

| Format | DVP Location | Rationale |
|--------|--------------|-----------|
| **v1** | `<output>/dvp/` (inside `Conversion-*/`) | DVP is scoped to a specific conversion run |
| **v2** | `<output>/dvp/` (inside `sma-output/`) | Consistent with v1; `workload_path/dvp/` always works |
| **v3** | `<output>/dvp/` (inside latest `sma-code-process-*/`) | Same contract as v2 once `<output>` is resolved |

**v1 Format:**
```
Conversion-<timestamp>/          ← <output>
├── Logs/
├── Output/                      ← migrated code
├── Reports/                     ← SMA inventories
└── dvp/                         ← DVP workspace (inside conversion folder)
```

**v2 Format:**
```
sma-output/                      ← <output> = workload_path
├── Logs/
├── Output/                      ← migrated code
├── Reports/                     ← SMA inventories
├── sma_storage.sqlite3
├── sma-dashboard/
└── dvp/                         ← DVP workspace (inside sma-output)
```

**v3 Format:**
```
.../Conversion_SnowparkAPI/
    sma-code-process-<timestamp>/   ← <output> when validating Snowpark API (latest)
    ├── Logs/
    ├── Output/
    ├── Reports/
    └── dvp/
```

> **Note:** In v1, v2, and v3, DVP lives inside `<output>`. In v1, `<output>` is legacy `Conversion-*/`. In v2, `<output>` is `sma-output/`. In v3, `<output>` is the latest `sma-code-process-*` under **`Conversion_SnowparkAPI`** or **`Conversion_SnowparkConnect`**. This keeps `workload_path/dvp/` consistent across formats.

## DVP Folder Structure

```
<output>/dvp/
├── 01-source/
├── 02-migrated/          (if Snowpark API selected)
├── 02-migrated_scos/     (if SCOS selected)
├── 03-tests/
├── 04-results/
└── 05-assets/
    └── FromCustomer/
```

### Folder Details

| Folder | Description | Details |
|--------|-------------|---------|
| `01-source/` | Spark source code | A **copy** of the original Python Spark source code from `<input>`. Files may be modified for testing/validation purposes. |
| `02-migrated/` | Snowpark migrated code *(if selected)* | A **copy** of the migrated Snowpark API code from `<output>/Output/`. Files may be modified for testing, EWI fixing, etc. |
| `02-migrated_scos/` | SCOS migrated code *(if selected)* | A **copy** of the migrated SCOS code from `<output>/Output/`. Files may be modified for testing, EWI fixing, etc. |
| `03-tests/` | Test files | Python test files (pytest-based), one test per pipeline. Generated by the test generation skills. |
| `04-results/` | Skill outputs | Output generated by DVP skill runs (inventories, reports, schemas, data). |
| `05-assets/FromCustomer/` | Customer-provided assets | Any files the customer provides to improve test accuracy: schemas, input data, CSV files, configuration, etc. |

## Why Copies?

### `01-source/` -- Copy of `<input>`

The original source files in `<input>` must remain **immutable**. However, for testing and validation purposes, DVP may need to modify the source code (e.g., instrumenting it, adding test hooks, adjusting configurations). The copy in `01-source/` serves as the working copy that DVP can freely modify while preserving the originals.

### `02-migrated/` / `02-migrated_scos/` -- Copy of `<output>/Output/`

Similarly, the migrated code in `<output>/Output/` is the direct output of SMA. DVP may need to modify these files (e.g., fixing EWIs, converting stage paths, adding test wrappers). The copy in the selected migrated folder (`02-migrated/` or `02-migrated_scos/`) is the working version for DVP operations.

## DVP Requires Both Paths

DVP needs to know **both** the `<input>` path and the conversion folder (`<output>`):

| Path | Why DVP needs it |
|------|-----------------|
| `<input>` | To copy the original Spark source code into `dvp/01-source/`. Also used for comparison between original and migrated code. |
| `<output>` (conversion folder) | Contains the migrated code (`Output/`), reports and inventories (`Reports/`), and is the parent location for the `dvp/` workspace. |

In v1 format, the `.snowma` file provides both paths, so the user only needs to point DVP at the `<input>` folder. In v2 format, the `.snowct` file does **not** contain paths, so the user must provide `<input>` explicitly; `<output>` is auto-detected via `sma-output/`. In v3 format, `<input>` is also user-provided; `<output>` is auto-detected as the latest `sma-code-process-*` under `Conversion_SnowparkAPI` or `Conversion_SnowparkConnect` (see `dvp-orchestrator`).

```
┌──────────────────────┐          ┌─────────────────────────────────────────────────────┐
│    <input>            │          │    <output> (Conversion-<timestamp>/)               │
│                      │          │                                                     │
│  Original source     │──copy──► │  ├── Logs/                                          │
│  source code         │          │  ├── Output/   (migrated Snowpark code)             │
│  (IMMUTABLE)         │          │  ├── Reports/  (inventories, assessment)            │
│                      │          │  │                                                   │
│  .snowma ─────────────────────► │  └── dvp/      (created by DVP)                     │
│  (project config)    │          │       ├── 01-source/   ◄── copy of <input>           │
│                      │          │       ├── 02-migrated*/ ◄── copy of Output/           │
└──────────────────────┘          │       ├── 03-tests/                                  │
                                  │       ├── 04-results/                                │
                                  │       └── 05-assets/                                 │
                                  └─────────────────────────────────────────────────────┘
```

## How Skills Map to DVP Folders

Each DVP skill reads from and writes to specific folders within the DVP workspace:

| Skill | Reads from | Writes to |
|-------|-----------|-----------|
| `dvp-entrypoint-identifier` | `dvp/01-source/`, `dvp/02-migrated*/`, `Reports/` | `dvp/04-results/` |
| `dvp-io-schema-identifier` | `dvp/01-source/`, `dvp/02-migrated*/`, `Reports/` | `dvp/04-results/` |
| `dvp-synthetic-data-generator` | `dvp/04-results/` (schemas) | `dvp/04-results/` |
| `stage-conversion` | `dvp/02-migrated*/` | `dvp/02-migrated*/` (modifies in place) |
| `dvp-test-setup-generator` | `dvp/04-results/` (inventories, schemas, data) | `dvp/03-tests/` |
| `dvp-test-execution-generator` | `dvp/04-results/` (inventories) | `dvp/03-tests/` |
| `dvp-test-validation-generator` | `dvp/04-results/` (inventories, schemas) | `dvp/03-tests/` |
| `dvp-testing-status-manager` | `dvp/04-results/`, `dvp/03-tests/` | `dvp/04-results/` |
| `dvp-ewi-extractor` | `Reports/Issues.csv` | `dvp/04-results/` |
| `dvp-ewi-tracking-manager` | `dvp/04-results/` | `dvp/04-results/` |
| `dvp-ewi-fixer` | `dvp/02-migrated*/` | `dvp/02-migrated*/` (modifies in place) |

> **Note:** `Reports/` refers to `<output>/Reports/`, which is the sibling folder to `dvp/` inside the conversion folder.

## Full Picture: SMA + DVP

### v1 Format (with .snowma)

```
<input>/                                     (user's original Spark source -- IMMUTABLE)
│
├── workload.py                              (or organized in subdirectories)
├── data/
│   ├── raw_transactions.csv
│   └── ...
└── project-name.snowma                      (SMA project file -- contains all paths)

sma-output-root/                             (SMA output root -- chosen by user)
│
└── Conversion-<timestamp>/                  (<output> -- resolved from .snowma)
    │
    ├── Logs/
    │   ├── GenericInfrastructureController/
    │   │   └── Controller-Log-20260212.122352.log
    │   └── PythonSnowConvert-Log-20260212.122357.log
    │
    ├── Output/                              (migrated Snowpark code)
    │   ├── workload.py                      (converted to Snowpark, with EWI comments)
    │   ├── data/
    │   │   └── ...
    │   └── project-name.snowma              (copy of project file)
    │
    ├── Reports/                             (SMA inventories and reports)
    │   ├── Issues.csv
    │   ├── AssessmentReport.json
    │   ├── SparkUsagesInventory.csv
    │   ├── ExecutionFlowInventory.csv
    │   ├── IOFilesInventory.csv
    │   ├── InputFilesInventory.csv
    │   ├── DataFramesInventory.csv
    │   ├── PackagesInventory.csv
    │   ├── ... (other inventory CSVs)
    │   └── SnowConvertReports/
    │       ├── Issues.<timestamp>.csv
    │       ├── ObjectReferences.<timestamp>.csv
    │       └── ... (other timestamped reports)
    │
    └── dvp/                                 (DVP workspace -- created by DVP)
        │
        ├── 01-source/                       (copy of <input>)
        │   ├── workload.py
        │   └── data/
        │       └── ...
        │
        ├── 02-migrated/                     (copy of <output>/Output/; if Snowpark API selected)
        ├── 02-migrated_scos/                (copy of <output>/Output/; if SCOS selected)
        │   ├── workload.py                  (migrated code -- may be modified by DVP)
        │   └── data/
        │       └── ...
        │
        ├── 03-tests/                        (generated pytest files)
        │   └── test_workload.py
        │
        ├── 04-results/                      (skill outputs)
        │   ├── entrypoints.json
        │   ├── data_io_schema.json
        │   └── data/
        │
        └── 05-assets/
            └── FromCustomer/                (customer-provided schemas, data, etc.)
```

### v2 Format (without .snowma)

```
<input>/                                     (user's original Spark source -- IMMUTABLE)
│
├── notebook1.ipynb                          (or .py files)
├── notebook2.ipynb
└── data/
    └── ...

sma-output-root/                             (SMA output root -- chosen by user)
│
├── <project-name>.snowct                    (project file -- no paths, only project ID)
│
├── sma-output/                              (<output> = workload_path)
│   ├── Logs/
│   │   ├── GenericScanner/
│   │   └── GenericInfrastructureController/
│   ├── Output/                              (migrated Snowpark code)
│   │   ├── notebook1.py                     (converted from .ipynb to .py)
│   │   ├── notebook2.py
│   │   └── data/
│   │       └── ...
│   ├── Reports/                             (SMA inventories and reports)
│   │   ├── Issues.csv
│   │   ├── AssessmentReport.json
│   │   ├── ... (same inventory CSVs as v1)
│   │   └── SnowConvertReports/
│   │       └── ...
│   ├── sma_storage.sqlite3
│   ├── sma-dashboard/
│   └── dvp/                                 (DVP workspace -- inside sma-output)
│       ├── 01-source/
│       ├── 02-migrated*/
│       ├── 03-tests/
│       ├── 04-results/
│       └── 05-assets/
│
├── results/
│   └── sma-code-processes/
│       └── sma-code-process-2026-02-13-18-21-10/   (historical run)
│           ├── Logs/
│           ├── Output/
│           └── Reports/
```

### v3 Format (dual conversion branches)

```
<input>/                                     (user's original source -- IMMUTABLE)
│
└── ...                                      (.py, .ipynb, etc.)

sma-output-root/                             (SMA output root -- chosen by user)
│
├── Sample.snowct                             (optional -- no paths)
├── Assessment/                             (optional -- ignored for DVP paths)
│
├── Conversion_SnowparkAPI/
│   └── sma-code-process-2026-04-15-10-35-34/    (<output> when `Conversion_SnowparkAPI` is chosen -- latest)
│       ├── Logs/
│       ├── Output/
│       └── Reports/
│
└── Conversion_SnowparkConnect/
    └── sma-code-process-2026-04-15-10-35-18/    (<output> when `Conversion_SnowparkConnect` is chosen -- latest)
        ├── Logs/
        ├── Output/
        └── Reports/
```

For the full DVP workspace layout under `<output>/dvp/`, see the v1 diagram in this document — it is the same once `<output>` is resolved.
