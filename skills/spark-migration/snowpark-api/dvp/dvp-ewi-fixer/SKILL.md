---
name: dvp-ewi-fixer
description: "Scan .py, .ipynb, and .scala files for SMA (Snowpark Migration Accelerator) EWI codes and resolve them. Triggers: fix EWIs, resolve SPRKPY, resolve SPRKSCL, resolve PNDSPY, SMA warnings, migration issues, Scala migration."
---

# EWI Resolution (Snowpark Migration Accelerator)

## EWI Pattern
```
#EWI: <PREFIX><NUMBER> => <INFO>
```

**Supported EWI Prefixes:**

| Prefix | Source | Description |
|--------|--------|-------------|
| `SPRKPY` | PySpark/Scala → Snowpark Python | Spark Python conversion issues |
| `SPRKSCL` | Spark Scala → Snowpark Scala | Spark Scala conversion issues |
| `PNDSPY` | Pandas → Snowpark Pandas | Pandas conversion issues |
| `SPRKDBX` | Databricks → Snowsight | Databricks-specific issues |
| `SPRKCNTPY` | PySpark/Scala → Snowpark Connect Python | Snowpark Connect Python issues |
| `SPRKCNTSCL` | Spark Scala → Snowpark Connect Scala | Snowpark Connect Scala issues |
| `SPRKHVSQL` | HiveSQL → Snowflake SQL | Hive SQL conversion issues |
| `SPRKSPSQL` | SparkSQL → Snowflake SQL | Spark SQL conversion issues |

## Output Format

Every time you begin a step, sub-step, or significant action, prefix the message with a timestamp in the format `[YYYY-MM-DD HH:MM:SS]`. Obtain the current time by running `date '+%Y-%m-%d %H:%M:%S'` in bash.

Example:
```
[2026-03-24 14:05:32] Starting Step 1: Scan for EWI markers...
[2026-03-24 14:05:45] Found 12 EWI markers in 3 files
[2026-03-24 14:05:46] Step 1 complete.
```

## Workflow

**🚨 CRITICAL — RE-EXECUTION POLICY:**
Every time this skill is invoked, you MUST execute the **full workflow from Step 1** — ask ALL questions, initialize the database, process EWIs, commit, etc. **NEVER** skip steps or decide that "work is already complete" based on the state of a previous run. The workflow is designed to handle re-execution:
- Step 1, Question 3 asks the user which EWIs to process (`Only pending`, `Specific EWI code`, or `All (reset)`).
- If a previous run resolved everything, `Only pending` will find 0 pending EWIs — that is fine, present that to the user and let THEM decide.
- If the user selects `All (reset)`, all EWIs are reset to `pending` and reprocessed from scratch.
- The database, Git, and summary tables all support multiple runs. Each run gets its own `fix_id`.
- **DO NOT** inspect the database or Git state to preemptively decide there is nothing to do. Always let the user make that decision through the questions.

**⚠️ DO NOT create additional files** (like `fix_session.json`, `session.txt`, etc.) for tracking session state. All persistence is handled by the SQLite database (`sma_storage.sqlite3`) via the database module at `skills/spark-migration/snowpark-api/scripts/sma_api.py`. The `fix_id` is managed automatically by `initialize_database()`.

### Step 1: Locate SMA Output Directory and Configure Options

**Pre-configured options:** If this skill was invoked from `spark-migration` and the caller provided pre-configured answers (SMA output directory, EWI comment handling, and EWI scope), **skip all questions in this step** and use the provided values directly. Proceed to Step 2.

If running standalone (no pre-configured options), ask the following questions:

1. **Ask** user for the path to the SMA output directory:
   - Use `ask_user_question` tool with type "text"
   - Question: "Path to SMA output directory (containing Output, Reports, Logs folders)"
   - This directory contains:
     - `Output/` - Converted .py/.ipynb/.scala files
     - `Reports/` - Issues.csv or IssuesConnect.csv
     - `Logs/` - Conversion logs

2. **Ask** user about EWI comment handling:
   - Use `ask_user_question` tool
   - Question: "How should EWI comments be handled after fixing?"
   - Options:
     - **Mark**: Keep EWI comments but prefix with `[FIXED]` or `[NOT-FIXED]`
     - **Remove**: Delete EWI comments after applying fixes
   - Store the user's choice for use in Step 8

3. **Ask** user which EWIs to process:
   - Use `ask_user_question` tool
   - Question: "Which EWIs should be processed?"
   - Options:
     - **Only pending**: Process only EWIs with status 'pending' (default - skips already processed)
     - **Retry not_auto_resolved**: Reset `not_auto_resolved` EWIs back to `pending` and retry them
     - **Specific EWI code**: Process only a specific EWI code (e.g., SPRKPY1002)
     - **All (reset)**: Reset all EWIs to 'pending' and reprocess everything
   - If "Retry not_auto_resolved" selected: `reset_not_resolved_to_pending(workload_path)`
   - If "Specific EWI code" selected: Ask for the code (e.g., "SPRKPY1002") and filter by that code
   - If "All (reset)" selected: `reset_all_to_pending(workload_path)`

### Step 2: Initialize Git Repository and Create Branch

**IMPORTANT**: Use Git to track changes instead of creating a copy. This preserves history and allows easy rollback.

All git operations are handled by deterministic Python functions in `sma_api`. Import:
```python
from sma_api import git_init_if_needed, git_status, git_stash, git_ensure_branch, git_ensure_ready, git_verify_branches, git_commit, MIGRATION_BRANCH
```

#### 2.1 Check/Initialize Git Repository

1. **Ensure** the git repository exists (no-op if it already does):
   ```python
   result = sma_api.git_init_if_needed("<sma_output_dir>")
   ```
   - If **not a repo**: atomically runs `git init` → `git add .` → `git commit` → `git branch -M main`
   - If **already a repo**: returns `{"success": True, "action": "already_initialized"}`
   
   **⚠️ CRITICAL**: `git_init_if_needed()` ensures the default branch is named `main` so the original SMA output is always preserved and accessible. Without this, there is no reference branch to diff against or revert to.

2. **If the repo has uncommitted changes** (check `sma_api.git_is_clean("<sma_output_dir>")`):
   Ask user via `ask_user_question`:
   - **Stash changes**: `sma_api.git_stash("<sma_output_dir>")` (recover later with `git stash pop`)
   - **Commit changes**: Commit pending changes before proceeding
   - **Abort**: Stop and let user handle manually

#### 2.2 Create Feature Branch

```python
result = sma_api.git_ensure_branch("<sma_output_dir>")
```
Returns: `{"success": True, "action": "created"|"switched"|"already_on_branch"}`

This deterministically handles all cases: creates `sma/migration-process` if it doesn't exist, switches to it if it exists but isn't checked out, or confirms if already on it.

**Shortcut** — Steps 2.1 + 2.2 can be replaced with a single call:
```python
result = sma_api.git_ensure_ready("<sma_output_dir>")
```
This runs the full sequence: init if needed → stash if dirty → ensure branch. Use this when the user doesn't need to be asked about stashing.

**All subsequent file operations (reads, edits, fixes) work directly on files in the SMA output directory on the `sma/migration-process` branch.**

The original code is preserved in the `main` (or `master`) branch and can be recovered with `git checkout main`.

### Step 3: Generate Fix ID

The fix_id is automatically generated when initializing the database (Step 4).
You can access it from the response of `initialize_database()` or via `generate_fix_id()`.

### Step 4: Initialize or Load SQLite Database
1. **Check** for `sma_storage.sqlite3` in the SMA output directory
2. **If database does NOT exist**:
   - Create `sma_storage.sqlite3` in SMA output directory
   - Look for CSV file in `Reports/` subdirectory (try in order):
     - `Issues.csv`
     - `IssuesConnect.csv`
   - If neither exists, STOP with error
   - Create `Issues` table and import CSV data
   - Add columns: `status TEXT DEFAULT 'pending'`, `notes TEXT DEFAULT ''`
3. **If database EXISTS**:
   - Connect to existing database
   - Verify `Issues` table exists
   - Ensure `status` and `notes` columns exist (add if missing with defaults)

The `initialize_database` function automatically:
- Creates the `ewi_fixer_results` table if it doesn't exist
- Creates the `ewi_fixer_summary` table if it doesn't exist
- Generates a unique `fix_id` for the session
- Inserts an initial summary record with `fix_id` and `start_time`

**⚠️ CRITICAL: Call `initialize_database()` EXACTLY ONCE, at the very beginning of Step 4.**
- Each call generates a new `fix_id` and creates a new summary record.
- The `start_time` is recorded at this moment. If you call it late, the duration will be wrong.
- Store the returned `fix_id` and reuse it for ALL subsequent operations.
- **NEVER call `initialize_database()` again** — not on retry, not on error, not at the end.
- **Orphan cleanup**: Before creating the new summary row, close any previous summary records that have `end_time IS NULL` by setting their `end_time` to the current timestamp and all numeric counters to `0`. This prevents orphaned rows from accumulating across retries or cancelled runs.

**Use the `initialize_database` function from `sma_api.py`:**
```
# Call ONLY ONCE at the beginning — returns fix_id and database status
result = initialize_database(workload_path=sma_output_dir)
# result contains: {"fix_id": "...", "status": "...", ...}
# Store fix_id for use in subsequent calls

# DO NOT call initialize_database() again during this session
```

### Step 5: Load EWI Context and Reference Documentation
1. **Query** Issues table to get pending EWIs:
   ```
   ewi_codes = get_pending_ewi_codes(workload_path=sma_output_dir)
   # Returns: [{'Code': 'SPRKPY1002', 'Description': '...', 'Category': 'ConversionError'}, ...]
   #            ^^^^       ^^^^^^^^^^^       ^^^^^^^^
   # Column names are CAPITALIZED (imported from CSV headers)
   ```
2. **For each unique EWI code**, attempt to load reference markdown:
   - Try to read `references/<EWI_CODE>.md`
   - Note whether reference exists, is specific, or is generic template
   - **CACHE**: Read each reference markdown **only once** per EWI code. Store the content in memory and reuse it for all occurrences of the same code across different files and lines. Do NOT re-read the same `.md` file multiple times.
   - **Note:** `SPRKDBX` codes (Databricks-specific) now have reference files. These typically refer to magic commands (`%run`, `%sql`) and widgets that SMA already converted or removed. Mark as `[NOT-FIXED]` if the code persists in files, or update directly in the DB as `not_auto_resolved` if they did not generate visible `#EWI:` comments in the code.
3. **Query** file and line details for each code:
   ```
   ewis = get_ewis_by_code(workload_path=sma_output_dir, code='SPRKPY1002', status='pending')
   # Returns: [{'FileId': 'databricks\\notebooks\\sample.py', 'Line': 32, 
   #            'Code': 'SPRKPY1002', 'Description': '...', 'Category': '...', 'CellId': '0'}, ...]
   #            ^^^^^^     ^^^^                                                   ^^^^^^
   # Column names are CAPITALIZED. Key columns: FileId, Line, Code, Description, Category, CellId
   ```

⚠️ **CRITICAL - PROCESS ALL FILES:**
- **MUST** process ALL files returned by the queries, not just a subset
- **MUST** iterate through EVERY file with pending EWIs
- **NEVER** skip files or stop early
- In most Databricks workloads, the Issues.csv registers files with `.py` extension but the actual files in `Output/` are `.ipynb`. **ALWAYS** try the `.ipynb` extension if the `.py` file does not exist, regardless of the `CellId` value. The condition `CellId > 0` is NOT reliable — some notebooks have EWIs at `CellId = 0`.

⚠️ **CRITICAL - PATH NORMALIZATION:**
- `FileId` values in the database may use Windows backslashes (e.g., `databricks\notebooks\file.py`). **ALWAYS** normalize paths by replacing `\` with `/` before constructing filesystem paths.

### Step 6: Build File Paths and Validate
For each EWI in query results:
1. **Construct** full path: `<sma_output_dir>/Output/<FileId>` (after normalizing path separators)
   - Handle path separators (Windows `\` → Unix `/`)
   - Example: `FileId='databricks\\notebooks\\sample.py'` → `Output/databricks/notebooks/sample.py`
2. **Resolve file extension** (notebook detection):
   - If the file exists at the constructed path → use it directly
   - If the file does NOT exist:
     - **ALWAYS** try replacing the extension with `.ipynb` (do NOT rely on `CellId > 0` as the only trigger)
     - Example: `sample.py` not found → try `sample.ipynb`
     - This is the MOST COMMON case in Databricks workloads — SMA registers files as `.py` but the actual output is `.ipynb`
   - If neither path exists → log as error and continue with next file
3. **Read** the file to analyze:
   - Verify EWI comments exist on reported line numbers
   - Understand code context around each EWI
   - Identify the issue based on EWI description
4. **Identify** file type:
   - **Python files (.py)**: Use `edit` or `multi_edit` tool
   - **Scala files (.scala)**: Use `edit` or `multi_edit` tool (same approach as .py files)
   - **Notebooks (.ipynb)**: Use a **Python script** to read/modify/write the JSON directly (see Step 8 format warning). Use `CellId` to locate the correct cell

### Step 7: Present Findings
Present summary to user:
- EWI codes to fix with descriptions and categories
- Files affected per EWI with specific line numbers
- Reference documentation status (exists/missing/incomplete/generic template)
- Proposed fixes based on reference documentation OR AI analysis

### Step 8: Apply Fixes (Per File)

⚠️ **CRITICAL: NOTEBOOK CELL SOURCE FORMAT**

SMA output notebooks store `cell['source']` as a **single string**, NOT as an array. Example:
```json
{"source": "#EWI: SPRKPY1055 => ...\nsource_data = spark.read.parquet(...)"}
```

**When reading:** The source is already a string. Just split by `\n` to get lines:
```python
src = cell['source']  # Already a string
if isinstance(src, list):
    src = ''.join(src)  # Safety fallback if somehow it's a list
lines = src.split('\n')
```

**When writing back: ALWAYS write as a string, NEVER as a list.**
```python
cell['source'] = new_src  # String, NOT list(new_src)
```

⛔ **NEVER use `list(new_src)`** — this creates a character array (`["#", "E", "W", ...]`) which **corrupts the notebook**. The notebook will appear to render correctly in some viewers but the JSON structure is broken.

⛔ **Do NOT use `notebook_edit_cell` or `notebook_actions` tools for .ipynb files.** These tools may alter the `source` format. Instead, **always use a Python script** to edit notebooks:
```python
import json
with open(path) as f:
    nb = json.load(f)
# ... modify cell['source'] as string ...
with open(path, 'w') as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)
```

⚠️ **BATCH PROCESSING STRATEGY FOR LARGE WORKLOADS**

When the workload has many files (>20), use this strategy for efficiency:

1. **Classify first, fix second.** Group all EWI codes into three categories:
   - **Auto-fixable** (already handled by SMA or informational): Mark as `[FIXED]` in bulk
   - **Definitively not-fixable** (no Snowpark equivalent): Mark as `[NOT-FIXED]` in bulk
   - **Needs case-by-case analysis**: Process individually
2. **Apply bulk marks using inline scripts**, not individual file edits. Process all auto-fixable and not-fixable EWIs across all files in a single pass per file type (.py, .ipynb, and .scala separately).
3. **Only do individual file analysis** for the EWIs that genuinely need code-level decisions.
4. **Do NOT launch background agents** for parallel file processing — the overhead of coordination and lack of progress visibility makes them unreliable for this task. Use inline scripts instead.

⚠️ **PROGRESS VALIDATION**

After processing each batch of files, **verify the count of EWIs found matches expectations**. If a processing pass finds 0 EWIs in files that the database says should have EWIs, **STOP and investigate** — this is almost certainly a bug in the parsing logic (e.g., the notebook cell format issue above). A count of 0 EWIs found across multiple files is a red flag, not a success.

**CRITICAL: DO NOT SIMPLIFY OR RESTRUCTURE FILES**
- Only fix the specific issues EWIs identify
- Preserve all original code structure, logic, test blocks, and functionality
- Do NOT refactor, consolidate, or "clean up" code unless explicitly part of the EWI fix
- Files may contain repetitive or test code that appears redundant - leave it intact

**Process files one at a time. For each file:**

⚠️ **CRITICAL - LINE SHIFT PREVENTION:**
When a file has multiple EWIs, apply fixes **from bottom to top** (highest line number first). This ensures that line number changes from edits (adding/removing lines) do not shift the positions of EWIs that haven't been processed yet. If you process top-to-bottom, earlier edits will invalidate the line numbers of later EWIs.

Alternative: Re-read the file after each edit to recalculate line positions before applying the next fix.

#### 8.1 Apply All EWI Fixes for the Current File

⚠️ **CRITICAL - MUST ATTEMPT TO FIX EVERY EWI:**
- You MUST read the reference markdown AND analyze the code context for EACH EWI before deciding if it can be fixed.
- You MUST attempt to apply a code fix (Case A or Case B) for every EWI. Case C (`[NOT-FIXED]`) is ONLY for EWIs where you genuinely cannot determine a fix after reading the reference and analyzing the code.
- **NEVER** bulk-mark all EWIs as `[NOT-FIXED]` using sed/regex. Each EWI must be individually analyzed and fixed or explicitly determined to be unfixable.
- **NEVER** skip the fix attempt. Even without a reference markdown, use AI analysis (Case B) to determine and apply a fix.
- A typical workload should have the MAJORITY of EWIs resolved as `auto_resolved`, not `not_auto_resolved`.

**Decision Tree for Each EWI in the file:**

##### Case A: Reference markdown exists and provides complete fix guidance
1. **Apply fix** from reference markdown recommendations
2. **Handle EWI comment** based on user's choice from Step 1:
   - **If "Remove"**: Delete the EWI comment line
   - **If "Mark"**: Change `#EWI: SPRKPY...` to `#EWI: [FIXED] SPRKPY...`
3. **Track** fix source: "Applied using SPRKPY<NUMBER>.md guidance"

##### Case B: Reference markdown missing OR incomplete/vague OR is generic template
1. **Analyze** code context and EWI description
2. **Determine fix** using AI understanding of:
   - PySpark/Scala → Snowpark conversion patterns
   - Python/Snowflake best practices
   - The specific error/warning from EWI description
3. **Apply the fix** determined by AI
4. **Handle EWI comment** based on user's choice from Step 1:
   - **If "Remove"**: Delete the EWI comment line
   - **If "Mark"**: Change `#EWI: SPRKPY...` to `#EWI: [FIXED] SPRKPY...`
5. **Track** fix source: "EWI context not complete. Fix: <details>"

##### Case C: Fix could not be applied (unsupported pattern, etc.)
1. **Handle EWI comment** based on user's choice from Step 1:
   - **If "Remove"**: Leave the EWI comment as-is (do not remove unfixed EWIs)
   - **If "Mark"**: Change `#EWI: SPRKPY...` to `#EWI: [NOT-FIXED] SPRKPY...`
2. **Track** status as 'failed'

**EWI Comment Transformation Examples:**

```python
# Original EWI comment:
#EWI: SPRKPY1002 => Element spark.read.format is not supported

# If user chose "Remove" and fix succeeded:
# (comment deleted entirely)

# If user chose "Mark" and fix succeeded:
#EWI: [FIXED] SPRKPY1002 => Element spark.read.format is not supported

# If user chose "Mark" and fix failed:
#EWI: [NOT-FIXED] SPRKPY1002 => Element spark.read.format is not supported
```

**For Python/Scala files (.py, .scala):**
- Use `edit` or `multi_edit` tool to:
  - Apply fix based on reference markdown OR AI analysis
  - Handle EWI comment per user's choice (remove or mark)
- Track which lines were fixed

**For Notebooks (.ipynb):**
- Use a **Python script** to edit notebook JSON directly (NOT `notebook_edit_cell`):
  - Read JSON, modify `cell['source']` as string, write JSON back
  - Handle EWI comments per user's choice (remove or mark)
  - ALWAYS keep `source` as string — NEVER convert to list

#### 8.2 Verify File Compiles

**After applying ALL fixes to the current file**, verify the code compiles:

**For Python files (.py):**
- Run: `python3 -m py_compile <file_path>` using the Bash tool
- Check exit code and any syntax errors
- **If compilation fails:**
  - Read the error message carefully
  - Identify the exact line and issue
  - Fix the syntax error (e.g., missing brackets, parentheses, quotes)
  - Re-run compilation check until it passes

**For Scala files (.scala):**
- Run: `scalac -Xfatal-warnings <file_path>` if scalac is available, otherwise verify syntax by checking for balanced braces, proper imports, and valid Scala constructs
- **If compilation fails:**
  - Read the error message carefully
  - Fix the syntax error
  - Re-run compilation check until it passes

**For Notebooks (.ipynb):**
- Use `notebook_get_kernel_status` to verify kernel is available
- Use `notebook_run_cell` with mode="all" to execute all cells
- Check for execution errors
- **If execution fails:**
  - Review error output
  - Fix the issues
  - Re-run cells until successful

**Common syntax errors to watch for:**
- Missing closing brackets: `}`, `]`, `)`
- Unmatched quotes: `"`, `'`
- Incomplete string replacements that break existing code
- Accidentally removing necessary code during EWI comment removal
- Corrupting notebook JSON by converting `source` string to character array (NEVER use `list()` on source)

**Principle:**
- EWI fixes should ONLY add or remove specific problematic code
- Never break existing valid syntax
- If unsure whether code should be removed, KEEP IT and only remove the EWI comment

#### 8.3 Record Results in SQLite (Batch per File)

**After verifying compilation**, batch insert all results for the current file.

**IMPORTANT:**
- Accumulate results in a list while processing EWIs in steps 8.1-8.2
- Insert all results for the file in ONE batch call (more efficient)
- Each dict in the list represents ONE EWI (one row per EWI)
- `affected_file` must be the **full absolute path**

**Workflow:**

```python
# 1. Initialize list at start of file processing
file_results = []

# 2. As you fix each EWI, append to the list:
file_results.append({
    'ewi_code': 'SPRKPY1002',
    'fix_description': 'Replaced spark.read.format("parquet") with session.read.parquet()',
    'affected_file': '/full/path/to/Output/data_loader.py',
    'affected_lines': '15',
    'status': 'success'
})

file_results.append({
    'ewi_code': 'SPRKPY1045',
    'fix_description': 'Replaced RDD operation with DataFrame equivalent',
    'affected_file': '/full/path/to/Output/data_loader.py',
    'affected_lines': '23',
    'status': 'success'
})

file_results.append({
    'ewi_code': 'SPRKPY1091',
    'fix_description': 'Unable to fix: Custom UDF pattern not supported',
    'affected_file': '/full/path/to/Output/data_loader.py',
    'affected_lines': '78',
    'status': 'failed'
})

# 3. After verifying compilation, batch insert all results for this file:
batch_insert_fix_results(workload_path=sma_output_dir, results=file_results)
```

**Status Values:**
- `'success'` - Fix was applied and file compiles correctly
- `'failed'` - Fix could not be applied (unsupported pattern, compilation error persists, etc.)

⚠️ **CRITICAL - STATUS CONSISTENCY BETWEEN `ewi_fixer_results` AND `Issues` TABLE:**
The `ewi_fixer_results.status` (`success`/`failed`) and the `Issues.status` (`auto_resolved`/`not_auto_resolved`) MUST be consistent:
- If the edit tool **modified the file** for this EWI → `ewi_fixer_results.status = 'success'` AND `Issues.status = 'auto_resolved'`
- If the file was **NOT modified** for this EWI (unsupported pattern, no fix available) → `ewi_fixer_results.status = 'failed'` AND `Issues.status = 'not_auto_resolved'`
- **NEVER** set `Issues.status = 'not_auto_resolved'` when the fix was actually applied to the file. Even if the fix requires manual review later, if the code was changed, it is `auto_resolved`.

#### 8.4 Repeat for Next File

Move to the next file and repeat steps 8.1 → 8.2 → 8.3 until all files are processed.

### Step 9: Update SQLite Database with Results

For each EWI fixed, update the Issues table:

```
update_ewi_status_single(
    workload_path=sma_output_dir,
    code='SPRKPY1002',
    file_id='databricks/notebooks/sample.py',  # Uses FileId column
    line=32,                                     # Uses Line column
    status='auto_resolved',
    notes='AI Resolved: Fix applied using guidance from SPRKPY1002.md reference documentation.'
)
```

**Notes Format Based on Fix Source:**

1. **With complete specific reference markdown:**
   ```
   AI Resolved: Fix applied using guidance from SPRKPY<NUMBER>.md reference documentation.
   ```

2. **Without complete reference (missing/incomplete/generic template):**
   ```
   AI Resolved: EWI context is not complete or does not exist. The fix found: <describe fix in detail>. Consider updating SPRKPY<NUMBER>.md with this solution.
   ```

3. **With complete reference but requiring additional context:**
   ```
   AI Resolved: Fix applied using SPRKPY<NUMBER>.md guidance. Additional notes: <special considerations>.
   ```

**Important**: Do NOT delete database rows - only UPDATE status and notes columns.

⚠️ **DB vs FILE RECONCILIATION:**
The database may have more EWI entries than the `#EWI:` comments found in files because:
- A single `FileId` can appear multiple times with different `Line` values
- `SPRKDBX` codes may not generate visible `#EWI:` comments in the source code
- Duplicate entries may exist when the same code appears on the same line with different descriptions

At the end of processing, update **ALL** remaining `pending` entries in the database to either `auto_resolved` or `not_auto_resolved`, not only the ones that matched `#EWI:` comments in the files. Use the EWI code classification (auto-fixable vs not-fixable) to determine the correct status.

### Step 10: Update Reference Documentation (When Applicable)

**IMPORTANT**: Distinguish between generic template markdowns and specific markdowns:

**Generic Template Markdowns (DO NOT MODIFY):**
- Contain placeholder text like `<element>`, `<option_name>`, `<function_name>`
- Apply to multiple different scenarios
- Examples: SPRKPY1002 (generic "element is not supported")
- **Action**: Do NOT modify these files - they serve as general templates

**Specific Markdowns (CAN BE ENHANCED):**
- Reference concrete functions, features, or specific conversion patterns
- Examples: SPRKPY1093 (JDBC connection options), SPRKPY1091 (concat_ws function), SPRKPY1092 (backslash removal)
- **Action**: Can be updated with additional examples, clarifications, or fix strategies

**For Specific Markdowns that need enhancement:**
1. **Update** `references/<EWI_CODE>.md` when:
   - You found a fix not documented in the existing markdown
   - You found a **better or alternative approach** to what's already documented
   - You encountered a special case or edge case not covered
   - You have additional examples that would help future fixes
2. **Update by adding** (not replacing):
   - New examples with before/after code
   - Alternative solutions section
   - Clarifications on edge cases
   - Additional fix strategies
3. Use this format for alternatives:
   ```markdown
   ## Resolution
   
   ### Approach 1: [Original approach]
   [existing content]
   
   ### Approach 2: [Your alternative]
   [your fix and explanation]
   ```

**For Missing Markdowns:**
Create `references/<EWI_CODE>.md` with standard structure.

**Always document in database:**
Even if you don't update markdown files, ALWAYS document the fix in the database `notes` column.

### Step 11: Display Summary and Finalize

**⚠️ CRITICAL: You MUST call `update_summary_end()` before finishing the workflow.**
This completes the summary record that was started in Step 4.

```
# Get final statistics from Issues table
stats = get_summary_stats(workload_path=sma_output_dir)
code_stats = get_ewi_code_stats(workload_path=sma_output_dir)

# Get fix results statistics for this session
fix_stats = get_fix_results_stats(workload_path=sma_output_dir)
fix_results = get_fix_results(workload_path=sma_output_dir)

# Calculate files statistics
files_with_success = set()
files_with_failure = set()
for result in fix_results:
    if result['status'] == 'success':
        files_with_success.add(result['affected_file'])
    else:
        files_with_failure.add(result['affected_file'])

total_files_fixed = len(files_with_success)
total_not_auto_resolved_files = len(files_with_failure - files_with_success)  # Files with ONLY failures

# ⚠️ REQUIRED: Update summary record with final results
update_summary_end(
    workload_path=sma_output_dir,
    total_ewis=fix_stats['total'],
    auto_resolved_ewis=fix_stats['success'],
    not_auto_resolved_ewis=fix_stats['failed'],
    total_files_fixed=total_files_fixed,
    total_not_auto_resolved_files=total_not_auto_resolved_files,
    compilation_errors_fixed=compilation_errors_fixed  # Track compilation errors fixed
)

# Display summary to user
print(f"Fix Session ID: {fix_id}")
print(f"Fixes attempted: {fix_stats['total']}")
print(f"  - Success: {fix_stats['success']}")
print(f"  - Failed: {fix_stats['failed']}")
print(f"Files fixed: {total_files_fixed}")
print(f"Files with no fixes: {total_not_auto_resolved_files}")
print(f"Compilation errors fixed: {compilation_errors_fixed}")

# No db.close() needed — connections are handled per-call
```

**IMPORTANT:** 
- Always call `update_summary_end()` before finishing the workflow
- This updates the summary record created in Step 4 with the final results
- Without this call, the summary will only have `fix_id` and `start_time`

### Step 12: Commit Changes to Git

**After all fixes are applied and verified**, commit the changes to the Git repository using `sma_api`.

1. **Commit** all changes:
   ```python
   result = sma_api.git_commit("<sma_output_dir>", """DVP EWI Fixer: <summary>

   Fix Session ID: <fix_id>
   Total EWIs processed: <total>
   - Resolved: <success_count>
   - Not fixed: <failed_count>
   Files modified: <files_count>
   Compilation errors fixed: <compilation_errors_fixed>""")
   ```
   Returns: `{"success": True, "action": "committed", "commit_hash": "abc1234"}`

**Example commit message:**
```
DVP EWI Fixer: Resolved 265 EWIs across 41 files

Fix Session ID: c2a3ddad-6981-4769-8341-ce9d77049671
Total EWIs processed: 265
- Resolved: 263
- Not fixed: 2
Files modified: 41
Compilation errors fixed: 3
```

**Note:** The original code remains accessible via:
```bash
git checkout main  # or master
```

2. **Verify** both branches exist and are correct:
   ```python
   result = sma_api.git_verify_branches("<sma_output_dir>")
   ```
   Returns: `{"success": True, "branches": [...], "has_main": True, "has_migration": True}`
   
   **⚠️ CRITICAL: At the end of the process there MUST be exactly two branches:**
   - `main` — contains the original SMA output **with zero modifications** (the initial commit)
   - `sma/migration-process` — contains all EWI fixes applied on top of `main`
   
   If `result["has_main"]` is `False`, the process failed. The user needs `main` as a clean reference to diff against, revert to, or compare with the fixed version.

## Stopping Points
- If SMA output directory not found or invalid
- If neither Issues.csv nor IssuesConnect.csv exists in Reports folder
- If file path cannot be constructed or file doesn't exist
- **DO NOT stop** if reference markdown is missing - analyze and fix anyway
- **DO NOT proceed** to database updates if code compilation fails - fix syntax errors first

## Database Access Module

All database operations use functions from `sma_api.py`:

**Module location:** `skills/spark-migration/snowpark-api/scripts/sma_api.py`

Import and call functions directly. All functions receive `workload_path` as their first parameter.

⚠️ **CRITICAL — MANDATORY USAGE:**
- **MUST** execute the **full workflow from Step 1** every time the skill is invoked — **NEVER** skip steps or decide "work is already complete"
- **MUST** use functions from `sma_api.py` for ALL database operations
- **NEVER** create tables manually with raw SQL
- **NEVER** improvise table names or schemas
- **MUST** call `update_summary_end()` before finishing the workflow
- **MUST** make Git commit at the end (Step 12) with summary message
- The ONLY valid tables are: `Issues`, `ewi_fixer_results`, `ewi_fixer_summary`
- **NEVER** create external script files (`/tmp/*.py`, `fix_script.py`, `batch_fix.py`, etc.) — write all processing logic as **inline Python** in Bash tool calls
- **NEVER** use `cat >` or `write` to create standalone Python scripts for batch processing
- **NEVER** open separate `sqlite3.connect()` connections — ALL database operations MUST go through `sma_api.py` functions

### Function Reference

**Initialization:**
- `initialize_database(workload_path)` — Create/load database, create tables, generate fix_id, insert initial summary

**Issues Table (EWI tracking):**
- `get_pending_ewi_codes(workload_path)` — Distinct pending EWI codes. Returns dicts: `Code`, `Description`, `Category`
- `get_ewis_by_code(workload_path, code, status)` — EWIs for specific code. Returns dicts: `FileId`, `Line`, `Code`, `Description`, `Category`, `CellId`
- `get_ewis_by_file(workload_path, file_id, status)` — EWIs for specific file
- `update_ewi_status_single(workload_path, code, file_id, line, status, notes)` — Update single row (matches on Code + FileId + Line)
- `update_ewi_status(workload_path, code, status, notes)` — Update all rows for a code (cascading)
- `bulk_update_ewi_status(workload_path, codes, status)` — Update multiple codes at once
- `reset_all_to_pending(workload_path)` — Reset ALL EWIs to 'pending'
- `reset_not_resolved_to_pending(workload_path)` — Reset `not_auto_resolved` to 'pending'
- `get_summary_stats(workload_path)` — Status counts
- `get_ewi_code_stats(workload_path)` — Per-code statistics

**Fix Results Table (per-EWI tracking):**
- `generate_fix_id(workload_path)` — Generate a new fix session ID
- `insert_fix_result(workload_path, ewi_code, fix_description, affected_file, affected_lines, status)` — Record single fix
- `batch_insert_fix_results(workload_path, results)` — Batch insert multiple fix results (recommended)
- `get_fix_results(workload_path, fix_id)` — Get fix results for a session
- `get_fix_results_stats(workload_path, fix_id)` — Get success/failed counts

**Summary Table (per-session tracking):**
- `insert_summary_start(workload_path)` — Called automatically by initialize_database
- `update_summary_end(workload_path, total_ewis, auto_resolved_ewis, not_auto_resolved_ewis, total_files_fixed, total_not_auto_resolved_files, compilation_errors_fixed)` — Complete summary
- `get_fix_summary(workload_path, fix_id)` — Get summary record

See [references/DATABASE_SCHEMA.md](./references/DATABASE_SCHEMA.md) for full database documentation.

## Output
- Modified `.py`, `.ipynb`, and `.scala` files in the SMA output directory (on `sma/migration-process` branch) with EWIs resolved **and verified to compile/execute correctly**
- Git commit with all changes on `sma/migration-process` branch
- Original code preserved in `main`/`master` branch (recoverable via `git checkout main`)
- Updated `sma_storage.sqlite3` database with:
  - `status` column updated to 'auto_resolved' for fixed EWIs
  - `notes` column populated with fix source and description
- Enhanced or created `references/<EWI_CODE>.md` files for specific EWIs (not generic templates)
- Summary of changes:
  - Number of EWIs resolved per code
  - Files modified
  - Lines changed per file
  - Which EWIs were fixed with/without reference documentation
  - Which reference markdowns were updated (if any)
  - **Compilation/execution verification status for each file**
- Database statistics:
  - Total EWIs: pending/resolved/not_auto_resolved counts
  - Per-code statistics breakdown

## Final Summary

**MANDATORY**: After completing all steps (whether running standalone or invoked from the orchestrator), ALWAYS present this summary table:

```
EWI Fixer Complete

┌──────────────┬──────────┬──────────────────────────────────────────────────────────┐
│ Step         │ Status   │ Details                                                  │
├──────────────┼──────────┼──────────────────────────────────────────────────────────┤
│ EWI Fixer    │ Done     │ Fixed N/M EWIs (N resolved, M not auto-resolved)         │
└──────────────┴──────────┴──────────────────────────────────────────────────────────┘

Output location: <output>/

Git branches:
• main — original code (unmodified)
• sma/migration-process — EWI fixes applied
```

**Rules:**
- Replace `N` and `M` with actual counts from the fix session
- Status is `Done`, `Skipped`, or `Failed`
- If no EWIs were found to fix, show `Skipped` with reason
- If fixing failed, show `Failed` with brief error
- The git branches section uses `sma_api.git_verify_branches()` to confirm both branches exist
