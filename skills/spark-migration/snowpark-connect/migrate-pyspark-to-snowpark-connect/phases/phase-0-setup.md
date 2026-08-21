# Phase 0: Collect Info and Create Conversion Folder

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

1. **Collect project info** from the user if not already provided: input path, output path, email, company, project name.

2. **Create timestamped conversion folder and copy source** — portable
   across macOS / Linux / Windows (no `date`, `mkdir -p`, or `cp -r`
   needed):

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/prepare_conversion_dirs.py \
  --output-root "<$OUTPUT or ${ARGUMENTS}_scos>" \
  --source "$ARGUMENTS"
```

The helper creates `<OUTPUT_ROOT>/Conversion-SCOS-<TIMESTAMP>/{Output,Reports,Logs}`,
copies `$ARGUMENTS` (file or directory) into `Output/` via `shutil.copytree`,
and prints the resolved paths as `KEY=value` lines (`CONVERSION`, `OUTPUT_DIR`,
`REPORTS_DIR`, `LOGS_DIR`, `TIMESTAMP`). Use those for the subsequent
placeholders.

> Prefer the helper over raw `date` / `mkdir -p` / `cp -r`. Those commands
> are not available in native Windows `cmd.exe` / PowerShell and would break
> the skill on Windows hosts.

3. **(Optional) reach for the legacy shell form only inside the CoCo bash
   sandbox** — if you are running *inside* the agent sandbox and not on the
   user's host, this Unix-only shortcut still works and the helper above
   produces the same layout:

   ```bash
   # Runs in the CoCo bash sandbox (Linux). Safe on any host OS.
   OUTPUT_ROOT="<$OUTPUT or ${ARGUMENTS}_scos>"
   TIMESTAMP=$(date +"%m-%d-%YT%H-%M-%S")
   CONVERSION="${OUTPUT_ROOT}/Conversion-SCOS-${TIMESTAMP}"
   mkdir -p "${CONVERSION}/Output" "${CONVERSION}/Reports" "${CONVERSION}/Logs"
   cp -r "$ARGUMENTS"/* "${CONVERSION}/Output/"
   ```

4. **Build the file manifest + notebook_index in one pass** — Python sources plus every notebook format (`.ipynb`, Databricks-native `.python`/`.scala`/`.sql`, Databricks exported `.py`/`.scala`). <!-- SNOW-3383535: Sort by relative path for deterministic chunk boundaries -->

Call `orchestrate_phases.py --build-notebook-index` to walk the tree once and produce both the notebook metadata and the per-cell language histogram in a single pass. It uses `notebook_io.scan_and_parse_notebooks` internally, so every notebook is detected and parsed exactly once — no redundant tree walks, no double-parsing. The Python-source (`.py`) list is built with a plain `os.walk` alongside. `notebook_io` has **zero third-party dependencies** (stdlib only), so invoke it directly with `python3` — do NOT wrap in `uv run --project`.

```bash
# First, write migration_state.json skeleton to <CONVERSION>/ (see step 7).
# Then build the combined manifest + notebook_index:
python3 <SKILL_DIRECTORY>/scripts/orchestrate_phases.py \
  --state <CONVERSION>/migration_state.json \
  --build-notebook-index <CONVERSION>/Output

# Plain-Python sources (skipping Databricks exported-text .py, which the
# notebook_index already covers):
python3 -c "
import json, os, sys
sys.path.insert(0, '<SKILL_DIRECTORY>/scripts')
import notebook_io as ni

root = '<CONVERSION>/Output'
py_files = []
for dp, _, files in os.walk(root):
    for f in files:
        if f.endswith('.py'):
            p = os.path.join(dp, f)
            if not ni.is_notebook(p):
                py_files.append(os.path.relpath(p, root))
print(json.dumps(sorted(py_files), indent=2))
"
```

The manifest for `migration_state.json` combines the `.py` list and every absolute path in the persisted `notebook_index`. The index carries `format`, `language`, `rel_path`, and `code_cells_by_language` (per-language cell counts) for every notebook, so Phase 2 orchestration can size chunks without re-parsing.

4a. **Unpack .dbc archives** (if present) — portable via the same helper.
    Use the `--unpack-dbc` flag against an existing `<CONVERSION>` folder:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/prepare_conversion_dirs.py \
  --output-root <OUTPUT_ROOT> --timestamp <TIMESTAMP> --unpack-dbc
```

The helper walks `<CONVERSION>/Output/` with `pathlib.rglob("*.dbc")` and
extracts each archive into a sibling `<name>_unpacked/` using the standard
`zipfile` module. No `find`, no POSIX `for` loop, works on Windows.
After unpacking, re-run `orchestrate_phases.py --build-notebook-index` so the index picks up the new notebooks.

5. **Determine dispatch mode**: Phase 2 fixing runs through a **fixer worker pool** (parallel sub-agents), sized by `max_parallel_fixers` (default: 4).
   - If `len(manifest) == 1`: set `coordinator_mode = false` — process the single file inline; a pool buys nothing.
   - If `len(manifest) >= 2`: set `coordinator_mode = true` — `orchestrate_phases.py` splits the manifest into balanced chunks and the coordinator dispatches up to `max_parallel_fixers` fixer sub-agents **concurrently per wave** (see Phase 2). `DISPATCH_THRESHOLD` (default 100) no longer gates parallelism; it only marks workloads large enough to expect multiple re-chunking waves.

6. **Initialize git** and tag the original source so Phase 1.2 can render the report from the customer's UNMODIFIED code:
```bash
cd <CONVERSION> && git init && git add . && git commit -m "Initial commit: source copied for SCOS migration" && git branch -M main && git tag phase-0-source
```

7. **Write `migration_state.json`** to `<CONVERSION>/`:
```json
{
  "phase": 0,
  "manifest": ["<relative paths for Python sources AND notebooks, sorted alphabetically>"],
  "file_order": ["<relative paths sorted alphabetically — mirrors manifest order for auditability>"],
  "notebook_files": {
    "ipynb":            ["<.ipynb files>"],
    "native_python":    ["<.python Databricks JSON files>"],
    "native_scala":     ["<.scala Databricks JSON files>"],
    "native_sql":       ["<.sql Databricks JSON files>"],
    "exported_python":  ["<.py files with '# Databricks notebook source' header>"],
    "exported_scala":   ["<.scala files with '// Databricks notebook source' header>"]
  },
  "dbc_archives": ["<list of .dbc files>"],
  "conversion_root": "<CONVERSION>",
  "migrated_dir": "<CONVERSION>/Output/",
  "skill_directory": "<SKILL_DIRECTORY>",
  "coordinator_mode": true,
  "dispatch_threshold": 100,
  "max_parallel_fixers": 4,
  "context_budget_tokens": 160000,
  "chunk_size": 20,
  "chunks": [],
  "processed_files": [],
  "pending_files": [],
  "phases_completed": {},
  "metadata": {"email": "...", "company": "...", "project": "..."}
}
```
