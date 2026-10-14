---
name: stage-conversion
description: "Replace embedded file paths in SMA-converted Snowpark code. Use when: migrating Spark code via SMA, fixing file path references, updating storage locations, changing stage prefix. Triggers: replace embedded file paths, embedded file paths, file paths, SMA file paths, Snowpark paths, change stage prefix."
---

# Replace Embedded File Paths in Snowpark Code

Scans **SMA-converted Snowpark code** for embedded file paths and updates them. Handles two scenarios:

1. **Already converted paths** (`@STAGE/protocol/path`) - Change stage prefix
2. **Unconverted paths** (original protocols like `s3://`, `hdfs://`, relative paths) - Convert to stage format

**Uses script:** `scripts/embedded_path_replacer.py` for AST-based path detection and transformation.

---

## Output Format

Every time you begin a step, sub-step, or significant action, prefix the message with a timestamp in the format `[YYYY-MM-DD HH:MM:SS]`. Obtain the current time by running `date '+%Y-%m-%d %H:%M:%S'` in bash.

Example:
```
[2026-03-24 14:05:32] Starting Step 1: Identify target files...
[2026-03-24 14:05:45] Found 8 files with embedded paths
[2026-03-24 14:05:46] Step 1 complete.
```

## Workflow Checklist

Copy this checklist and track your progress:

```
Path Replacement Progress:
- [ ] Step 0: Initialize Git
- [ ] Step 1: Identify target files
- [ ] Step 2: Scan for embedded paths
- [ ] Step 3: Present findings and get prefix from user
- [ ] Step 4: Preview changes (dry-run)
- [ ] Step 5: Get user approval
- [ ] Step 6: Apply replacements
- [ ] Step 7: Generate report and summary
- [ ] Step 8: Verify results
- [ ] Step 9: Commit changes to Git
```

---

## Step 0: Initialize Git

Ensure the workload directory has a git repository on the `sma/migration-process` branch. This is idempotent — if the orchestrator already initialized git, this is a no-op.

```python
result = sma_api.git_ensure_ready("<workload_path>")
```

---

## Step 1: Identify Target Files

**Goal:** Determine which files to scan for embedded paths.

**Ask user:**
```
Which files should I scan for embedded file paths?

Options:
1. Specific file(s) - provide path(s)
2. Directory - scan all Python files recursively
3. Current file - if already working on one
```

**Actions:**
1. Get file/directory path from user
2. If directory, glob for `**/*.py` and `**/*.ipynb` files
3. List files that will be scanned and confirm with user

**Completion criteria:** User has confirmed the list of files to scan.

---

## Step 2: Scan for Embedded Paths

**Goal:** Detect all embedded file paths in the target files.

**Action:** Run the scanner script in scan-only mode:

```bash
python3 scripts/embedded_path_replacer.py --scan-only [files...]
```

> **Note:** If script is not executable, run: `chmod +x scripts/embedded_path_replacer.py`

**What the script detects:**

| Detection Method | What It Finds |
|------------------|---------------|
| AST: Snowpark read | `session.read.csv()`, `session.read.parquet()` |
| AST: Snowpark write | `df.write.csv()`, `df.write.parquet()` |
| AST: File operations | `pd.read_csv()`, `open()`, `session.file.get()` |
| AST: String variables | `input_path = "s3://..."` |
| Regex: Stage paths | `@STAGE/protocol/path` (already converted) |
| Regex: SQL statements | `CREATE STAGE`, `COPY INTO` with paths |

**Completion criteria:** Script has returned list of detected paths with locations.

---

## Step 3: Present Findings and Get Prefix

**Goal:** Show user what was found and get the stage prefix for transformation.

**Present findings to user:**
```
Found N embedded file paths in M files:

File: data_processor.py
  - s3://company-data/sales.csv (line 45, Snowpark read)
  - hdfs://cluster/input.parquet (line 67, Snowpark read)

File: analysis.ipynb
  - ./config/settings.json (cell[3]:line 12, String variable)
```

**Explain transformation format:**
```
Paths will be transformed to: @{PREFIX}/{protocol}/{path}

Examples:
  s3://bucket/file.csv      -> @PREFIX/s3/bucket/file.csv
  hdfs://cluster/data       -> @PREFIX/hdfs/cluster/data
  /local/path/file.csv      -> @PREFIX/local/path/file.csv
  ./relative/path           -> @PREFIX/relative/path
  ../parent/path            -> @PREFIX/relative/parent/path
```

**Ask user:**
```
What prefix should be used for all file path replacements?
(e.g., MY_STAGE, DATA_STAGE, INPUT_FILES)
```

**STOP: Wait for user to provide the prefix before continuing.**

**Completion criteria:** User has provided a stage prefix.

---

## Step 4: Preview Changes (Dry Run)

**Goal:** Show exactly what will be changed before making modifications.

**Action:** Run script in dry-run mode:

```bash
python3 scripts/embedded_path_replacer.py --dry-run --prefix {USER_PREFIX} [files...]
```

**Present preview to user:**
```
Planned replacements with prefix "@{USER_PREFIX}":

  s3://company-data/sales.csv
  -> @{USER_PREFIX}/s3/company-data/sales.csv
  Occurrences: 3 (in 2 files)

  hdfs://cluster/input.parquet
  -> @{USER_PREFIX}/hdfs/cluster/input.parquet
  Occurrences: 1

  ./config/settings.json
  -> @{USER_PREFIX}/relative/config/settings.json
  Occurrences: 1

Total: N replacements across M files
```

**Completion criteria:** Preview has been shown to user.

---

## Step 5: Get User Approval

**Goal:** Obtain explicit approval before modifying files.

**Ask user:**
```
Apply these changes? (Yes / No / Modify prefix)
```

**STOP: Do NOT proceed without explicit "Yes" from user.**

**If user says "No":** End workflow, no changes made.
**If user says "Modify prefix":** Return to Step 3.
**If user says "Yes":** Continue to Step 6.

**Completion criteria:** User has explicitly approved the changes.

---

## Step 6: Apply Replacements

**Goal:** Execute the path transformations on all target files.

**Action:** Run script to apply changes:

```bash
python3 scripts/embedded_path_replacer.py --prefix {USER_PREFIX} [files...]
```

**What the script does:**
1. Reads each file (Python or Jupyter notebook)
2. Replaces paths while preserving quote style (' or ")
3. Injects WARNING comments for paths marked `needs_revision`
4. Writes modified content back to file
5. Reports results per file

**Monitor output:**
```
Applying replacements...

Results:
  data_processor.py: 3 replacements
  analysis.ipynb: 2 replacements
  etl_pipeline.py: 5 replacements

Total: 10 replacements applied
Warnings injected: 2 (dynamic paths)
```

**Completion criteria:** Script has completed without errors.

---

## Step 7: Generate Report and Summary

**Goal:** Create audit trail and provide summary to user.

**Reports generated automatically:**

| Report | Filename | Purpose |
|--------|----------|---------|
| CSV | `sma_path_replacement_report_YYYYMMDD_HHMMSS.csv` | Review in Excel, share with team |
| JSON | `sma_path_replacement_report_YYYYMMDD_HHMMSS.json` | Programmatic analysis, automation |

**CSV columns:**
- Original Path
- Transformed Path
- Status (`replaced`, `needs_revision`, `skipped_*`)
- Reason
- Detection Method
- File
- Location
- Line Content

**Present summary to user:**
```
Replacement complete!

Results:
  Replaced: N paths in M files
  Needs revision: X paths (dynamic/f-string)
  Skipped: Y paths

Files modified:
  - data_processor.py (3 replacements)
  - analysis.ipynb (2 replacements)

Reports generated:
  - sma_path_replacement_report_20260305_103000.csv
  - sma_path_replacement_report_20260305_103000.json
```

**Completion criteria:** Reports generated and summary presented.

---

## Step 8: Verify Results

**Goal:** Ensure transformations were applied correctly and guide next steps.

**Verification actions:**
1. Check that replaced count matches expected count from preview
2. Review any paths marked `needs_revision` in the report
3. Identify any paths that were skipped and explain why

**Present next steps to user:**
```
Next steps:
1. Review paths marked "needs_revision" - these contain dynamic elements
2. Check the CSV report for full details
3. Test the modified code to ensure it works correctly
```

**If there are `needs_revision` paths:**
```
Paths requiring manual review:
  - @PREFIX/s3/{bucket}/data.csv (contains f-string variable)
  - @PREFIX/hdfs/{cluster}/table (multiple variables)

These paths were transformed but contain dynamic elements.
Please verify the transformations are correct for your use case.
```

**Completion criteria:** User has been informed of results and next steps.

---

## Step 9: Commit Changes to Git

After all replacements are applied and verified, commit the changes:

```python
result = sma_api.git_commit("<workload_path>", """DVP Stage Conversion: Replaced N paths in M files

Prefix: @USER_PREFIX
Replaced: N paths
Needs revision: X (dynamic/f-string paths)
Reports: sma_path_replacement_report_YYYYMMDD_HHMMSS.csv/.json""")
```

Verify branches:
```python
result = sma_api.git_verify_branches("<workload_path>")
```

---

## Quick Reference

### Supported Path Patterns

| Type | Pattern | Transformation |
|------|---------|----------------|
| S3 | `s3://bucket/path` | `@PREFIX/s3/bucket/path` |
| S3A | `s3a://bucket/path` | `@PREFIX/s3a/bucket/path` |
| HDFS | `hdfs://cluster/path` | `@PREFIX/hdfs/cluster/path` |
| Azure Blob | `abfs://container/path` | `@PREFIX/abfs/container/path` |
| Azure Secure | `abfss://container/path` | `@PREFIX/abfss/container/path` |
| GCS | `gs://bucket/path` | `@PREFIX/gs/bucket/path` |
| Local | `/path/to/file` | `@PREFIX/local/path/to/file` |
| File Protocol | `file:///path` | `@PREFIX/local/path` |
| Relative | `./path` | `@PREFIX/relative/path` |
| Parent | `../path` | `@PREFIX/relative/parent/path` |
| Multi-Parent | `../../path` | `@PREFIX/relative/parent2/path` |

### CLI Commands

```bash
# Scan only (no changes)
python3 scripts/embedded_path_replacer.py --scan-only [files...]

# Dry run (preview changes)
python3 scripts/embedded_path_replacer.py --dry-run --prefix PREFIX [files...]

# Apply changes
python3 scripts/embedded_path_replacer.py --prefix PREFIX [files...]

# Apply without warnings
python3 scripts/embedded_path_replacer.py --prefix PREFIX --no-warnings [files...]

# Skip git check (automated workflows)
python3 scripts/embedded_path_replacer.py --prefix PREFIX --skip-git-check [files...]
```

### Status Values

| Status | Meaning |
|--------|---------|
| `replaced` | Successfully transformed and applied |
| `needs_revision` | Transformed but contains dynamic elements (f-strings) |
| `skipped_relative` | Relative path that couldn't be transformed |
| `skipped_dynamic` | Path with variables that couldn't be parsed |
| `skipped_unsupported` | Unrecognized path format |

---

## Important Notes

- **Prefix format:** Do NOT include `@` in your prefix - it's added automatically
- **Quote preservation:** The script preserves original quote style (' or ")
- **Jupyter notebooks:** Only Python code cells are processed
- **Dynamic paths:** F-strings like `s3://{bucket}/path` are transformed but flagged for review
- **Git recommended:** Always use version control before bulk modifications
- **Idempotent:** Running the script twice with the same prefix produces the same result

## Final Summary

**MANDATORY**: After completing all steps (whether running standalone or invoked from the orchestrator), ALWAYS present this summary table:

```
Stage Conversion Complete

┌──────────────────────┬──────────┬──────────────────────────────────────────────────┐
│ Step                 │ Status   │ Details                                          │
├──────────────────────┼──────────┼──────────────────────────────────────────────────┤
│ Stage Conversion     │ Done     │ Replaced paths in N files                        │
└──────────────────────┴──────────┴──────────────────────────────────────────────────┘

Output location: <output>/

Git branches:
• main — original code (unmodified)
• sma/migration-process — stage conversion changes applied
```

**Rules:**
- Replace `N` with actual count of files where paths were replaced
- Status is `Done`, `Skipped`, or `Failed`
- If no embedded paths were found, show `Skipped` with reason (e.g., "No embedded paths found")
- If conversion failed, show `Failed` with brief error
- The git branches section uses `sma_api.git_verify_branches()` to confirm both branches exist
