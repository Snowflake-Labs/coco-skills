# Orchestrator Mode

This skill is invoked by the `spark-migration` orchestrator with a context message containing pre-filled paths and configuration. The skill scans `<output>/Output/` for notebook files and converts them **in-place** (modifying files directly, not creating copies). Supports both **Snowpark Connect (SCOS)** and **Snowpark API** conversions.

## Detecting Orchestrator Mode

When the skill loads, check if the first message after load contains the orchestrator context pattern:

```
The following context was configured by the spark-migration orchestrator:
- SMA output directory: <output>
- Notebooks source: <output>/Output/
...
```

If this context is present → **orchestrator mode**. If not → **standalone mode** (load `references/standalone-mode.md` instead).

## Orchestrator Mode Workflow

1. **Parse the context message** to extract:
   - `<output>` — SMA output directory
   - `conversion_type` — `scos` or `snowpark_api`
   - `enable_jupyter_conversion` — `yes` or `no` (only relevant for `snowpark_api`)

2. **Scan `<output>/Output/`** recursively for notebook files using the detection script:

   ```bash
   uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/detect_and_parse_notebook.py --scan <output>/Output/
   ```

   The script detects all Databricks notebook formats defined by `detect_and_parse_notebook.scan_directory` (see the File Naming Convention table in `SKILL.md` for the authoritative list) and filters out plain scripts that aren't notebooks. It returns a JSON array of detected notebooks. Each entry contains `file` (path relative to the scanned directory), `abs_path` (absolute path), `format`, and `language`.

   **This scan result is the definitive list of notebooks to convert in Step 5.** Do NOT scan for files again using `find`, `ls`, `glob`, or any other method — use this output as the single source of truth for which files to process.

3. **Determine conversion sub-mode** based on the context:

   | Conversion Type | Jupyter Conversion | Notebooks State in `<output>/Output/` | Skill Action |
   |---|---|---|---|
   | `scos` | N/A | Verbatim copies of originals in the formats listed in `SKILL.md` File Naming Convention (SCOS copied all files but only processed `.py`/`.scala` scripts). | Full migration: magic commands, dbutils, display(), `_sqldf`, etc. Uses **Snowpark Connect** setup cell (PySpark kept as-is). Same behavior as standalone mode. |
   | `snowpark_api` | `yes` | SMA-generated `.ipynb` files — every Databricks notebook format in `SKILL.md` File Naming Convention has been converted to `.ipynb`; non-notebook files are copied as-is. PySpark is already converted to Snowpark API but Snowflake notebook formatting is imperfect. | Polish/fix Snowflake notebook compatibility. No setup cell needed — SMA already converted the code. |
   | `snowpark_api` | `no` | Original Databricks formats (see `SKILL.md` File Naming Convention) with PySpark code already converted to Snowpark API by SMA. | Convert non-`.ipynb` files to `.ipynb` format AND apply Snowflake compatibility fixes. No setup cell needed — SMA already converted the code. |

4. **Setup cell selection** based on conversion sub-mode:
   - **`scos` mode**: Use the **Snowpark Connect setup cell** defined in the main SKILL.md "PySpark Setup Cell" section. PySpark code is kept as-is.
   - **`snowpark_api` mode** (both `jupyter=yes` and `jupyter=no`): **No setup cell needed.** The SMA CLI already converted PySpark to Snowpark API, so the converted code already has session setup in place. Only apply Snowflake notebook compatibility fixes.

5. **Convert each notebook from the Step 2 scan results** in-place, following the conversion rules in `references/transformation-rules.md`. Iterate over the JSON array returned by the `--scan` command — do not re-scan the directory. When passing a path back to any script, use the entry's `abs_path` field (the `file` field is relative to `<output>/Output/` and will break if the agent's cwd differs):
   - **Parse each notebook** using the detection/parsing script to get normalized cells:
     ```bash
     uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/detect_and_parse_notebook.py <abs_path>
     ```
     The script handles all Databricks formats (ipynb, native JSON, exported text) and returns a uniform JSON structure with `format`, `language`, `cell_count`, and `cells[]` (each cell has `cell_type` and `source`). Use this parsed output as the basis for transformation — do not parse Databricks formats manually.
   - Apply the same cell-by-cell processing (magic commands, dbutils, display(), `_sqldf`, etc.)
   - For `snowpark_api` + `jupyter=yes`: focus on polishing — the code is already converted, fix only Snowflake notebook formatting issues
   - For `snowpark_api` + `jupyter=no`: convert the Databricks file format to `.ipynb` first, then apply compatibility fixes
   - For `scos`: full migration as in standalone mode
   - Modify files directly in `<output>/Output/` — do NOT create copies in a separate directory
   - **After successfully converting a non-`.ipynb` file** (any source extension from `SKILL.md` File Naming Convention) **to `.ipynb`, run `validate_notebook.py --finalize`** to validate the output and delete the original source file:
     ```bash
     uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/validate_notebook.py <converted.ipynb> --finalize <original_path>
     ```
     The converted file uses the naming convention from the main SKILL.md "File Naming Convention" section — `.ipynb` is appended to the full original filename (e.g., `config.py` becomes `config.py.ipynb`, not `config.ipynb`). The script validates the output and deletes the original if validation passes. The output folder should only contain the converted `.ipynb` notebooks and any non-notebook files (e.g., `.py` modules that are not Databricks notebooks). Do NOT leave both the original Databricks format file and the new `.ipynb` side by side.

6. **Validate each converted notebook** using the validation script and feedback loop:

   After converting each notebook to `.ipynb`, validate it:
   ```bash
   uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/validate_notebook.py <output.ipynb>
   ```
   If validation reports errors (exit code 1), fix the issues and re-validate (maximum 3 attempts). If validation still fails after 3 attempts, log the remaining errors in the migration summary cell and continue to the next notebook.

7. **Skip standalone-only steps**: Do not trace `%run` dependencies, do not ask for conversion order, do not offer upload to Snowflake Workspace. These are standalone-mode features.

## Git Integration (Orchestrator Mode Only)

When running in orchestrator mode on the `sma/migration-process` branch, use `sma_api.git_commit()` for all git operations.

After converting all notebooks, commit the changes:

```python
sma_api.git_commit("<output>", "Notebook migration: converted N notebooks to Snowflake Workspace format")
```

The `sma_api.git_commit()` function stages all changes and commits on the current branch (`sma/migration-process`). The originals are preserved on `main`.

**Standalone mode has no git integration** — this is new capability for orchestrator mode only.
