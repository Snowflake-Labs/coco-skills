# Scripts

Usage reference for the Python scripts under `scripts/`. Invoked from `SKILL.md`, `references/standalone-mode.md`, and `references/orchestrator-mode.md`.

### Script: detect_and_parse_notebook.py

**Description**: Detect Databricks notebook format and parse cells into structured JSON. Handles `.ipynb`, native JSON (`.python`, `.scala`, `.sql`), and exported text (`.py`, `.scala`) formats.

**Usage:**
```bash
uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/detect_and_parse_notebook.py <file_path>
uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/detect_and_parse_notebook.py --scan <directory>
```

**Arguments:**
- `path`: File path (single file) or directory (with `--scan`).
- `--scan`: Scan directory recursively for notebooks instead of parsing a single file.
- `--compact`: Omit full cell source content (show truncated preview only).

**`--scan` output includes (per notebook entry):**
- `file`: path relative to the scanned directory (stable key, safe to display in logs).
- `abs_path`: absolute filesystem path — **use this when passing a path back to this script or any other tool**, since the agent's cwd is not guaranteed between commands.
- `format`, `language`: as reported by the detector.

**When to use:** In orchestrator mode (Step 2) to detect which files are notebooks and parse them. In standalone mode during the scan step.

### Script: validate_notebook.py

**Description**: Validate a converted Snowflake Workspace notebook against migration quality criteria.

**Usage:**
```bash
uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/validate_notebook.py <notebook.ipynb>
uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/validate_notebook.py <notebook.ipynb> --expected-cells 15
uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/validate_notebook.py <notebook.ipynb> --run-targets ./config.py.ipynb --run-targets ./utils.py.ipynb
uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/validate_notebook.py <notebook.ipynb> --finalize <original_source_file>
```

**Arguments:**
- `notebook`: Path to the `.ipynb` file to validate.
- `--expected-cells`: Minimum expected cell count (original notebook cell count).
- `--run-targets`: Expected `%run` target path (post-conversion collision-safe name, e.g. `./config.py.ipynb`). Repeat for each expected target. When provided, `%run` paths not in the set are reported as warnings.
- `--finalize`: Path to the original source file. If validation passes, checks the naming convention and deletes the original non-`.ipynb` file (e.g. `--finalize my_notebook.python`). If validation fails, the original is NOT deleted.

**When to use:** After converting each notebook, as part of the validation feedback loop. Use `--finalize` to atomically validate and delete the original in one step.

### Script: scan_dependencies.py

**Description**: Scan a directory for notebook `%run` dependencies and Python imports, build a dependency graph, and output a recommended leaf-first conversion order.

**Usage:**
```bash
uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/scan_dependencies.py <directory>
```

**Arguments:**
- `directory`: Directory to scan for notebooks.

**Output includes:**
- `notebooks[].converted_name`: Post-conversion filename (e.g. `config.py.ipynb`).
- `dependencies[].target_converted`: Post-conversion filename for `%run` dependency targets.
- `conversion_order`: Recommended order using original filenames (leaf-first).
- `conversion_order_converted`: Same order with post-conversion filenames.

**When to use:** In standalone directory workflow (steps 1–3) to trace dependencies and recommend conversion order.

### Script: validate_directory.py

**Description**: Validate post-conversion directory state. Checks that no stale original notebook source files remain and all converted `.ipynb` files exist.

**Usage:**
```bash
uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/validate_directory.py <directory>
uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/validate_directory.py <directory> --scan-output scan_results.json
```

**Arguments:**
- `directory`: Directory to validate.
- `--scan-output`: Path to `scan_dependencies.py` JSON output for cross-referencing. When provided, checks that all expected converted notebooks exist.

**When to use:** After all notebooks in a directory have been converted, as a final validation step.
