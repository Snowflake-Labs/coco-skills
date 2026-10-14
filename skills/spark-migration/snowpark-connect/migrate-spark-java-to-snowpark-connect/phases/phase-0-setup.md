# Phase 0: Collect Info and Create Conversion Folder

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

### Phase 0: Collect Info and Create Conversion Folder

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

4. **Build the file manifest** — enumerate `.java` source files and build files:

```bash
# First, write migration_state.json skeleton (see step 7).
python3 -c "
import json, os, sys
root = '<CONVERSION>/Output'
EXCLUDE = {'.git', '.hg', '.svn', '.idea', '.vscode', '.metals', '.bloop',
           '.bsp', 'target', 'build', 'out', '.gradle', '__pycache__', 'node_modules'}

def walk(root):
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EXCLUDE]
        yield dp, files

java_files = []
for dp, files in walk(root):
    for f in files:
        if f.endswith('.java'):
            java_files.append(os.path.relpath(os.path.join(dp, f), root))

BUILD_NAMES = {'pom.xml', 'build.gradle', 'build.gradle.kts',
               'settings.gradle', 'settings.gradle.kts'}
# Collect ONLY real build files. Do NOT glob every .xml — that pulls in IDE
# library descriptors (.idea/libraries/*.xml), data/resource XML, and target
# artifacts. Maven build files are exactly pom.xml; Gradle is the named
# build/settings scripts.
build_files = []
for dp, files in walk(root):
    for f in files:
        if f in BUILD_NAMES:
            build_files.append(os.path.relpath(os.path.join(dp, f), root))

print(json.dumps({'java_files': sorted(java_files), 'build_files': sorted(build_files)}, indent=2))
"
```

The manifest for `migration_state.json` is `java_files` sorted alphabetically.

4. **Determine dispatch mode**: Check manifest length against `DISPATCH_THRESHOLD` (default: 100).
   - `len(manifest) <= 100`: `coordinator_mode = false` — process all phases in current context.
   - `len(manifest) > 100`: `coordinator_mode = true` — use chunked sub-agent dispatch for Phase 2.

5. **Initialize git**:
```bash
cd <CONVERSION> && git init && git add . && git commit -m "Initial commit: source copied for SCOS migration" && git branch -M main
```

6. **Write `migration_state.json`** to `<CONVERSION>/`:
```json
{
  "phase": 0,
  "manifest": ["<relative paths for .java sources, sorted alphabetically>"],
  "file_order": ["<relative paths sorted alphabetically>"],
  "build_files": ["<list of build files>"],
  "conversion_root": "<CONVERSION>",
  "migrated_dir": "<CONVERSION>/Output/",
  "skill_directory": "<SKILL_DIRECTORY>",
  "coordinator_mode": true,
  "dispatch_threshold": 100,
  "max_parallel_fixers": 4,
  "context_budget_tokens": 160000,
  "processed_files": [],
  "pending_files": [],
  "phases_completed": {},
  "recipe_edits": {},
  "metadata": {"email": "...", "company": "...", "project": "..."}
}
```
