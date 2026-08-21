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

3. **(Optional) Unix-only shortcut inside the CoCo bash sandbox:**

   ```bash
   # Runs in the CoCo bash sandbox (Linux). Safe on any host OS.
   OUTPUT_ROOT="<$OUTPUT or ${ARGUMENTS}_scos>"
   TIMESTAMP=$(date +"%m-%d-%YT%H-%M-%S")
   CONVERSION="${OUTPUT_ROOT}/Conversion-SCOS-${TIMESTAMP}"
   mkdir -p "${CONVERSION}/Output" "${CONVERSION}/Reports" "${CONVERSION}/Logs"
   cp -r "$ARGUMENTS"/* "${CONVERSION}/Output/"
   ```

4. **Build the file manifest + notebook_index in one pass** — enumerate `.scala` source files, build files, AND every notebook format (`.ipynb`, Databricks-native `.python`/`.scala`/`.sql`, Databricks exported `.py`/`.scala`). <!-- SNOW-3383535: Sort by relative path for deterministic chunk boundaries -->

Call `orchestrate_phases.py --build-notebook-index` to walk the tree once and produce both the notebook metadata and the per-cell language histogram in a single pass. It uses `notebook_io.scan_and_parse_notebooks` internally, so every notebook is detected and parsed exactly once — no redundant tree walks, no double-parsing. The Scala source and build-file lists are gathered with a plain `os.walk` alongside. `notebook_io` has **zero third-party dependencies** (stdlib only), so invoke it directly with `python3` — do NOT wrap in `uv run --project`.

```bash
# First, write migration_state.json skeleton to <CONVERSION>/ (see step 7).
# Then build the combined manifest + notebook_index:
python3 <SKILL_DIRECTORY>/scripts/orchestrate_phases.py \
  --state <CONVERSION>/migration_state.json \
  --build-notebook-index <CONVERSION>/Output

# Plain-Scala sources (skipping native-JSON/exported-text .scala, which the
# notebook_index already covers) and build files:
python3 -c "
import json, os, sys
sys.path.insert(0, '<SKILL_DIRECTORY>/scripts')
import notebook_io as ni

root = '<CONVERSION>/Output'
# Prune VCS/IDE/build-output dirs at every depth (defense-in-depth; the copy
# step already excludes these, but a stray copy must never pollute the manifest).
EXCLUDE = {'.git', '.hg', '.svn', '.idea', '.vscode', '.metals', '.bloop',
           '.bsp', 'target', 'build', 'out', '.gradle', '__pycache__', 'node_modules'}

def walk(root):
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EXCLUDE]  # prune in place
        yield dp, files

scala_files = []
for dp, files in walk(root):
    for f in files:
        if f.endswith('.scala'):
            p = os.path.join(dp, f)
            if not ni.is_notebook(p):
                scala_files.append(os.path.relpath(p, root))

# Collect ONLY real build files. Do NOT glob every .xml — that pulls in IDE
# library descriptors (.idea/libraries/*.xml), data/resource XML, and target
# artifacts. Maven build files are exactly pom.xml; sbt is any *.sbt; Gradle is
# the named build/settings scripts.
BUILD_NAMES = {'pom.xml', 'build.gradle', 'build.gradle.kts',
               'settings.gradle', 'settings.gradle.kts'}
build_files = []
for dp, files in walk(root):
    for f in files:
        if f.endswith('.sbt') or f in BUILD_NAMES:
            build_files.append(os.path.relpath(os.path.join(dp, f), root))

print(json.dumps({
    'scala_files': sorted(scala_files),
    'build_files': sorted(build_files),
}, indent=2))
"
```

The manifest for `migration_state.json` combines `scala_files` and every absolute path in the persisted `notebook_index`, sorted alphabetically. The index carries `format`, `language`, `rel_path`, and `code_cells_by_language` (per-language cell counts) for every notebook, so Phase 2 orchestration can size chunks without re-parsing.

4a. **Unpack .dbc archives** (if present): same unpack step as the Python sub-skill. After unpacking, re-run `orchestrate_phases.py --build-notebook-index` so the index picks up the new notebooks.

5. <!-- SNOW-3383536: Dispatch mode threshold -->
   **Determine dispatch mode**: Check manifest length against `DISPATCH_THRESHOLD` (default: 100).
   - If `len(manifest) <= 100`: set `coordinator_mode = false` — process all phases in the current agent context without sub-agent dispatch. This avoids coordinator overhead for small workloads.
   - If `len(manifest) > 100`: set `coordinator_mode = true` — use chunked sub-agent dispatch for Phase 2 (code fixes). Each chunk is sized by context budget estimation.

6. **Initialize git**:
```bash
cd <CONVERSION> && git init && git add . && git commit -m "Initial commit: source copied for SCOS migration" && git branch -M main && git tag phase-0-source
```
The `phase-0-source` tag is the reference point the Phase 1a assessment render
uses to rebase analyzer line numbers onto the original source and populate the
auto-resolved recipe panel (via `--migration-state-json`).

7. **Write `migration_state.json`** to `<CONVERSION>/`:
```json
{
  "phase": 0,
  "manifest": ["<relative paths for Scala sources AND notebooks, sorted alphabetically>"],
  "file_order": ["<relative paths sorted alphabetically — mirrors manifest order for auditability>"],
  "build_files": ["<list of build files>"],
  "notebook_files": {
    "ipynb":           ["<.ipynb files>"],
    "native_python":   ["<.python Databricks JSON files>"],
    "native_scala":    ["<.scala Databricks JSON files>"],
    "native_sql":      ["<.sql Databricks JSON files>"],
    "exported_python": ["<.py files with '# Databricks notebook source' header>"],
    "exported_scala":  ["<.scala files with '// Databricks notebook source' header>"]
  },
  "conversion_root": "<CONVERSION>",
  "migrated_dir": "<CONVERSION>/Output/",
  "skill_directory": "<SKILL_DIRECTORY>",
  "coordinator_mode": true,
  "dispatch_threshold": 100,
  "context_budget_tokens": 160000,
  "max_parallel_fixers": 4,
  "chunk_size": null,
  "chunks": [],
  "processed_files": [],
  "pending_files": [],
  "dbc_archives": [],
  "phases_completed": {},
  "recipe_edits": {},
  "metadata": {"email": "...", "company": "...", "project": "..."}
}
```

> **Note**: As phases complete, each entry under `phases_completed` will contain `processed_files` (done), `pending_files` (remaining), and `checkpoint_timestamp`. Non-empty `pending_files` after a specialist exits means it was interrupted — the coordinator must spawn a resume agent.
