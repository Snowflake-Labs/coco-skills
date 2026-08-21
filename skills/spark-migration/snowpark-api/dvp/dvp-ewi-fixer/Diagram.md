# DVP EWI Fixer - Architecture Diagram

> **Last Updated**: 2026-03-17
> **Note**: Keep this diagram updated when making changes to the skill.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           DVP EWI FIXER - FLUJO                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────┐
│   TRIGGER       │  "fix EWIs", "resolve SPRKPY", "resolve SPRKSCL", "resolve PNDSPY", "SMA warnings", "migration issues", "Scala migration"
│   (Cortex)      │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  🚨 RE-EXECUTION POLICY (CRITICAL)                                             │
│                                                                                 │
│  ALWAYS execute the FULL workflow from Step 1 every time this skill is invoked. │
│  NEVER skip steps or decide "work is already complete."                         │
│  The user's answer to Question 3 (which EWIs to process) determines what       │
│  gets reprocessed. Each run gets its own fix_id.                                │
│  DO NOT inspect DB/Git state to preemptively decide there is nothing to do.     │
│                                                                                 │
│  🚨 EXECUTION MODE (CRITICAL)                                                  │
│  NEVER run this skill as a background agent (run_in_background=true).           │
│  NEVER use the Task tool to spawn background agents for processing files.       │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 1: ASK SMA OUTPUT PATH & CONFIGURE OPTIONS                                │
│  1. ask_user_question → "<sma_output_dir>"                                      │
│     Expected structure: Output/, Reports/, Logs/                                │
│                                                                                 │
│  2. ask_user_question → "How should EWI comments be handled?"                   │
│     ┌────────────┐  ┌─────────────────────────────────────────────────────────┐ │
│     │  Mark      │  │ Keep comments, prefix with [FIXED] or [NOT-FIXED]       │ │
│     └────────────┘  └─────────────────────────────────────────────────────────┘ │
│     ┌────────────┐  ┌─────────────────────────────────────────────────────────┐ │
│     │  Remove    │  │ Delete EWI comments after fixing                        │ │
│     └────────────┘  └─────────────────────────────────────────────────────────┘ │
│                                                                                 │
│  3. ask_user_question → "Which EWIs should be processed?"                       │
│     ┌────────────────────────┐  ┌───────────────────────────────────────────────┐ │
│     │ Only pending           │  │ Process only 'pending' EWIs (skip processed) │ │
│     └────────────────────────┘  └───────────────────────────────────────────────┘ │
│     ┌────────────────────────┐  ┌───────────────────────────────────────────────┐ │
│     │ Retry not_auto_resolved│  │ Reset not_auto_resolved → pending and retry  │ │
│     └────────────────────────┘  └───────────────────────────────────────────────┘ │
│     ┌────────────────────────┐  ┌───────────────────────────────────────────────┐ │
│     │ Specific code          │  │ Enter EWI code (e.g., SPRKPY1002, SPRKSCL1100) to process │ │
│     └────────────────────────┘  └───────────────────────────────────────────────┘ │
│     ┌────────────────────────┐  ┌───────────────────────────────────────────────┐ │
│     │ All (reset)            │  │ Reset ALL to 'pending' and reprocess everything│ │
│     └────────────────────────┘  └───────────────────────────────────────────────┘ │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 2: INITIALIZE GIT & CREATE BRANCH (via sma_api)                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  All git operations use deterministic Python functions from sma_api.py          │
│                                                                                 │
│  2.1 Check/Initialize Git Repository                                            │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  status = sma_api.git_init_if_needed(workload_path)                 │    │
│  │     │                                                                   │    │
│  │     ├─► action="already_initialized" → check if clean                   │    │
│  │     │                                                                   │    │
│  │     ├─► action="initialized" → repo created, proceed to 2.2            │    │
│  │     │                                                                   │    │
│  │     └─► If repo dirty (sma_api.git_is_clean = False):                   │    │
│  │            ask user:                                                    │    │
│  │            ┌─────────────────────────────────────┐                     │    │
│  │            │ • sma_api.git_stash(workload_path)  │                     │    │
│  │            │ • Commit changes first               │                     │    │
│  │            │ • Abort (handle manually)            │                     │    │
│  │            └─────────────────────────────────────┘                     │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  2.2 Create Feature Branch                                                      │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  result = sma_api.git_ensure_branch(workload_path)                     │    │
│  │  → action: "created" | "switched" | "already_on_branch"                │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  Shortcut: sma_api.git_ensure_ready(workload_path) = 2.1 + 2.2 combined       │
│                                                                                 │
│  All changes on `sma/migration-process` branch                                  │
│  Original code preserved in `main` branch                                       │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 3-4: INITIALIZE DATABASE                                                  │
│  initialize_database(sma_output_dir)                                  │
│  ⚠️ CRITICAL: Call EXACTLY ONCE at the start — NEVER again (not on retry/error) │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │  Function: initialize_database(workload_path)                           │   │
│  │  Module: sma_api.py                                                      │   │
│  ├──────────────────────────────────────────────────────────────────────────┤   │
│  │                                                                          │   │
│  │  ┌─────────────────────┐     ┌─────────────────────────────────────────┐ │   │
│  │  │ Check sma_storage.  │ NO  │ Create from Reports/Issues.csv          │ │   │
│  │  │ sqlite3 exists?     │────►│ or Reports/IssuesConnect.csv            │ │   │
│  │  └─────────┬───────────┘     └─────────────────────────────────────────┘ │   │
│  │            │ YES                                                         │   │
│  │            ▼                                                             │   │
│  │  ┌─────────────────────┐                                                 │   │
│  │  │ ensure_tracking_    │ Add status, notes columns if missing            │   │
│  │  │ columns()           │                                                 │   │
│  │  └─────────┬───────────┘                                                 │   │
│  │            │                                                             │   │
│  │            ▼                                                             │   │
│  │  ┌─────────────────────┐                                                 │   │
│  │  │ create_results_     │ CREATE TABLE ewi_fixer_results                  │   │
│  │  │ table()             │                                                 │   │
│  │  └─────────┬───────────┘                                                 │   │
│  │            │                                                             │   │
│  │            ▼                                                             │   │
│  │  ┌─────────────────────┐                                                 │   │
│  │  │ create_summary_     │ CREATE TABLE ewi_fixer_summary                  │   │
│  │  │ table()             │                                                 │   │
│  │  └─────────┬───────────┘                                                 │   │
│  │            │                                                             │   │
│  │            ▼                                                             │   │
│  │  ┌─────────────────────┐                                                 │   │
│  │  │ ORPHAN CLEANUP      │ UPDATE ewi_fixer_summary                        │   │
│  │  │                     │ SET end_time=NOW, counters=0                     │   │
│  │  │                     │ WHERE end_time IS NULL                           │   │
│  │  └─────────┬───────────┘                                                 │   │
│  │            │                                                             │   │
│  │            ▼                                                             │   │
│  │  ┌─────────────────────┐                                                 │   │
│  │  │ generate_fix_id()   │ UUID v4 (NEW for each run)                      │   │
│  │  │ insert_summary_     │ INSERT (fix_id, start_time)                     │   │
│  │  │ start()             │ ← start_time recorded NOW                       │   │
│  │  └─────────────────────┘                                                 │   │
│  │                                                                          │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 5-6: LOAD EWI CONTEXT                                                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────┐     ┌─────────────────────────────────────────┐    │
│  │ get_pending_  │────►│ List of unique EWI codes                │    │
│  │ ewi_codes(workload_path)│     │ [{Code, Description, Category}, ...]    │    │
│  └─────────────────────────┘     │ ⚠️ Column names are CAPITALIZED         │    │
│                                  └─────────────────────────────────────────┘    │
│                                                                                 │
│  ┌─────────────────────────┐     ┌─────────────────────────────────────────┐    │
│  │ For each EWI code:      │────►│ Try: references/<EWI_CODE>.md          │    │
│  │ Load reference markdown │     │ Note: exists? specific? generic?        │    │
│  └─────────────────────────┘     │ ⚠️ All 8 prefixes supported (474 files)│    │
│                                  └─────────────────────────────────────────┘    │
│                                                                                 │
│  ┌─────────────────────────┐     ┌─────────────────────────────────────────┐    │
│  │ get_ewis_by_  │────►│ File paths + line numbers per EWI      │    │
│  │ code(workload_path,code)│     │ Keys: FileId, Line, Code, CellId       │    │
│  └─────────────────────────┘     │ ⚠️ Normalize FileId: \ → /             │    │
│                                  └─────────────────────────────────────────┘    │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  FILE PATH RESOLUTION:                                                  │    │
│  │                                                                         │    │
│  │  Construct path: Output/<FileId> (normalize Windows \ → Unix /)        │    │
│  │     │                                                                   │    │
│  │     ├─► File EXISTS → use directly                                      │    │
│  │     │                                                                   │    │
│  │     └─► File NOT FOUND:                                                 │    │
│  │            ALWAYS try replacing extension with .ipynb                   │    │
│  │            (most common case in Databricks workloads)                   │    │
│  │            e.g. sample.py → sample.ipynb                                │    │
│  │            │                                                            │    │
│  │            ├─► .ipynb EXISTS → use notebook                             │    │
│  │            └─► NOT FOUND → log error, continue                          │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  ⚠️ MUST process ALL files - NEVER skip or stop early                           │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 7: PRESENT FINDINGS                                                       │
│  Show user: EWI codes, affected files, reference status, proposed fixes         │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 8: APPLY FIXES (PER FILE LOOP)                                            │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ⚠️ CRITICAL: NOTEBOOK CELL SOURCE FORMAT                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  SMA output stores cell['source'] as a SINGLE STRING, not an array.    │    │
│  │                                                                         │    │
│  │  Reading:  src = cell['source']  # already a string                    │    │
│  │            if isinstance(src, list): src = ''.join(src)  # fallback    │    │
│  │            lines = src.split('\n')                                      │    │
│  │                                                                         │    │
│  │  Writing:  cell['source'] = new_src  # ALWAYS string                   │    │
│  │                                                                         │    │
│  │  ⛔ NEVER use list(new_src) — creates char array, CORRUPTS notebook    │    │
│  │  ⛔ NEVER use notebook_edit_cell tool — use Python script instead      │    │
│  │     import json; nb = json.load(f); ...; json.dump(nb, f)              │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  ⚠️ BATCH STRATEGY FOR LARGE WORKLOADS (>20 files)                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  1. Classify EWI codes into:                                            │    │
│  │     - Auto-fixable → bulk mark [FIXED]                                  │    │
│  │     - Not-fixable  → bulk mark [NOT-FIXED]                              │    │
│  │     - Needs analysis → process individually                             │    │
│  │  2. Use inline scripts for bulk marks (NOT background agents)           │    │
│  │  3. After each batch: verify EWI count matches DB expectations          │    │
│  │     ⚠️ 0 EWIs found in files with known EWIs = parsing bug!            │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  FOR EACH FILE with pending EWIs:                                       │    │
│  │  ═══════════════════════════════════════════════════════════════════════│    │
│  │                                                                         │    │
│  │  ⚠️ LINE SHIFT PREVENTION: Process EWIs BOTTOM→TOP (highest line first)│    │
│  │  This prevents earlier edits from shifting line numbers of later EWIs.  │    │
│  │  Alternative: re-read file after each edit to recalculate positions.    │    │
│  │                                                                         │    │
│  │  8.1 APPLY ALL EWI FIXES                                                │    │
│  │  ⚠️ MUST attempt fix for EVERY EWI (Case A or B). NEVER bulk-mark all  │    │
│  │     as [NOT-FIXED]. Case C is ONLY after individual analysis fails.     │    │
│  │  ┌─────────────────────────────────────────────────────────────────┐    │    │
│  │  │  Decision Tree per EWI:                                         │    │    │
│  │  │                                                                 │    │    │
│  │  │  Reference markdown exists & complete?                          │    │    │
│  │  │     │                                                           │    │    │
│  │  │     ├─► YES: Apply fix from <EWI_CODE>.md                      │    │    │
│  │  │     │        Handle EWI comment (per user choice)               │    │    │
│  │  │     │        Track: "Applied using <EWI_CODE>.md guidance"     │    │    │
│  │  │     │                                                           │    │    │
│  │  │     └─► NO:  Analyze code context + EWI description             │    │    │
│  │  │              Apply AI-determined fix                            │    │    │
│  │  │              Handle EWI comment (per user choice)               │    │    │
│  │  │              Track: "EWI context not complete. Fix: <details>"  │    │    │
│  │  │                                                                 │    │    │
│  │  │  ┌─────────────────────────────────────────────────────────┐    │    │    │
│  │  │  │  EWI COMMENT HANDLING (based on Step 1 choice):         │    │    │    │
│  │  │  │                                                         │    │    │    │
│  │  │  │  User chose "Remove":                                   │    │    │    │
│  │  │  │    - Success → Delete comment                           │    │    │    │
│  │  │  │    - Failed  → Keep comment as-is                       │    │    │    │
│  │  │  │                                                         │    │    │    │
│  │  │  │  User chose "Mark":                                     │    │    │    │
│  │  │  │    - Success → #EWI: [FIXED] <PREFIX>...                  │    │    │    │
│  │  │  │    - Failed  → #EWI: [NOT-FIXED] <PREFIX>...              │    │    │    │
│  │  │  └─────────────────────────────────────────────────────────┘    │    │    │
│  │  │                                                                 │    │    │
│  │  │  File type:                                                     │    │    │
│  │  │  - .py/.scala → edit/multi_edit tool                                 │    │    │
│  │  │  - .ipynb → Python script (json.load/dump) ⛔ NOT notebook_edit │    │    │
│  │  └─────────────────────────────────────────────────────────────────┘    │    │
│  │                         │                                               │    │
│  │                         ▼                                               │    │
│  │  8.2 VERIFY COMPILATION                                                 │    │
│  │  ┌─────────────────────────────────────────────────────────────────┐    │    │
│  │  │  .py files:    python3 -m py_compile <file_path>                │    │    │
│  │  │  .scala files: scalac -Xfatal-warnings <file_path> (if avail.) │    │    │
│  │  │  .ipynb:       notebook_run_cell mode="all"                    │    │    │
│  │  │                                                                 │    │    │
│  │  │  If FAILS → Fix syntax errors → Re-verify (loop)                │    │    │
│  │  │  Track: compilation_errors_fixed++                              │    │    │
│  │  └─────────────────────────────────────────────────────────────────┘    │    │
│  │                         │                                               │    │
│  │                         ▼                                               │    │
│  │  8.3 RECORD RESULTS (Batch per file)                                   │    │
│  │  ┌─────────────────────────────────────────────────────────────────┐   │    │
│  │  │  # Accumulate results during 8.1-8.2:                           │   │    │
│  │  │  file_results = []                                              │   │    │
│  │  │  file_results.append({ewi_code, fix_description, ...})          │   │    │
│  │  │                                                                 │   │    │
│  │  │  # After compilation verified, batch insert:                    │   │    │
│  │  │  batch_insert_fix_results(workload_path, file_results)│   │    │
│  │  └─────────────────────────────────────────────────────────────────┘   │    │
│  │                         │                                               │    │
│  │                         ▼                                               │    │
│  │  8.4 REPEAT → Next file                                                 │    │
│  │                                                                         │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 9: UPDATE ISSUES TABLE                                                    │
│  update_ewi_status_single(workload_path, code, file_id, line,         │
│    'auto_resolved', notes)                                                      │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 10: UPDATE REFERENCE DOCS (Optional)                                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Generic Template?  ─► DO NOT MODIFY (contains <placeholders>)                  │
│                                                                                 │
│  Specific Markdown? ─► CAN ENHANCE with new examples/approaches                 │
│                                                                                 │
│  Missing Markdown?  ─► CREATE references/<EWI_CODE>.md                         │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 11: FINALIZE & DISPLAY SUMMARY                                            │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  update_summary_end(                                                  │
│      workload_path,                                                             │
│      total_ewis=...,                                                            │
│      auto_resolved_ewis=...,                                                    │
│      not_auto_resolved_ewis=...,                                                │
│      total_files_fixed=...,                                                     │
│      total_not_auto_resolved_files=...,                                         │
│      compilation_errors_fixed=...                                               │
│  )                                                                              │
│                                                                                 │
│  Display: Fix Session ID, Success/Failed counts, Files fixed, etc.              │
│                                                                                 │
│  (No db.close() needed — sma_api.py functions auto-close connections)             │
│                                                                                 │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 12: COMMIT CHANGES TO GIT (via sma_api)                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  result = sma_api.git_commit(workload_path, message)                           │
│  → {"success": True, "action": "committed", "commit_hash": "abc1234"}          │
│                                                                                 │
│  Commit message includes:                                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  EWI Fixer Session: <fix_id>                                            │    │
│  │                                                                         │    │
│  │  Summary:                                                               │    │
│  │  - Total EWIs processed: X                                              │    │
│  │  - Auto-resolved: Y                                                          │    │
│  │  - Not auto-resolved: Z                                                         │    │
│  │  - Files modified: N                                                    │    │
│  │  - Compilation errors fixed: M                                          │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  ✅ Changes preserved in `sma/migration-process` branch                          │
│  ✅ Original code recoverable via `git checkout main`                           │
│                                                                                 │
│  ⚠️ CRITICAL: Verify with sma_api.git_verify_branches(workload_path):          │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  main                    ← Original SMA output, ZERO modifications      │    │
│  │  sma/migration-process   ← All EWI fixes applied on top of main         │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│  If result["has_main"] is False → process FAILED                               │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Database Schema

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  sma_storage.sqlite3                                                            │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────┐    ┌────────────────────────────────────────┐  │
│  │ Issues                      │    │ ewi_fixer_results                      │  │
│  │ (from SMA Issues.csv)       │    │ (per-EWI fix tracking)                 │  │
│  ├─────────────────────────────┤    ├────────────────────────────────────────┤  │
│  │ Code          (CAPITALIZED) │    │ id (PK, autoincrement)                 │  │
│  │ Category      (CAPITALIZED) │    │ fix_id (FK → summary)                  │  │
│  │ Description   (CAPITALIZED) │    │ ewi_code                               │  │
│  │ FileId        (CAPITALIZED) │    │ fix_description                        │  │
│  │ Line          (CAPITALIZED) │    │ affected_file (full path)              │  │
│  │ CellId        (CAPITALIZED) │    │ affected_lines                         │  │
│  │ status (added, lowercase)   │    │ status ('success'/'failed')            │  │
│  │ notes (added, lowercase)    │    │ created_at                             │  │
│  │ ⚠️ CSV headers = col names   │    │                                        │  │
│  └─────────────────────────────┘    └────────────────────────────────────────┘  │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │ ewi_fixer_summary (per-session tracking)                                │    │
│  ├─────────────────────────────────────────────────────────────────────────┤    │
│  │ fix_id (PK, UUID)                                                       │    │
│  │ total_ewis                                                              │    │
│  │ auto_resolved_ewis                                                           │    │
│  │ not_auto_resolved_ewis                                                         │    │
│  │ total_files_fixed                                                       │    │
│  │ total_not_auto_resolved_files                                                  │    │
│  │ compilation_errors_fixed                                                │    │
│  │ start_time                                                              │    │
│  │ end_time                                                                │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Output Structure

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  OUTPUT STRUCTURE (Git-based)                                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  <sma_output_dir>/                        ← Git repository (root)               │
│  ├── .git/                               ← Git metadata                        │
│  ├── Output/                                                                   │
│  │   └── *.py, *.ipynb                   ← Source files with EWI fixes         │
│  │                                                                              │
│  ├── Git Branches:                                                              │
│  │   ├── main                            ← Original SMA output (preserved)     │
│  │   └── sma/migration-process           ← Branch with EWI fixes applied       │
│  │                                                                              │
│  ├── Reports/                                                                   │
│  │   ├── Issues.csv                      ← Source for Issues table             │
│  │   └── IssuesConnect.csv               ← Alternative source                  │
│  │                                                                              │
│  └── sma_storage.sqlite3                 ← Unified database                    │
│      ├── Issues                          ← EWI tracking with status/notes      │
│      ├── ewi_fixer_results               ← Per-EWI fix attempts               │
│      └── ewi_fixer_summary               ← Per-session summary                │
│                                                                                 │
│  Recovery: git checkout main          ← Restores original SMA output            │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## EWI Fix Decision Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  EWI FIX DECISION TREE                                                          │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│                    ┌─────────────────────┐                                      │
│                    │ EWI: #EWI: <PREFIX>   │                                      │
│                    │ <NUMBER> => <INFO>  │                                      │
│                    └──────────┬──────────┘                                      │
│                               │                                                 │
│                               ▼                                                 │
│                    ┌─────────────────────┐                                      │
│                    │ references/<PREFIX>  │                                      │
│                    │ <NUMBER>.md exists? │                                      │
│                    └──────────┬──────────┘                                      │
│                               │                                                 │
│               ┌───────────────┼───────────────┐                                 │
│               │ YES           │               │ NO                              │
│               ▼               │               ▼                                 │
│    ┌──────────────────┐       │    ┌──────────────────┐                         │
│    │ Is it SPECIFIC?  │       │    │ AI Analysis:     │                         │
│    │ (no <placeholders>)      │    │ - Code context   │                         │
│    └────────┬─────────┘       │    │ - EWI description│                         │
│             │                 │    │ - PySpark/Scala→ │                         │
│      ┌──────┴──────┐          │    │   Snowpark       │                         │
│      │ YES    │ NO │          │    │   patterns       │                         │
│      ▼        ▼    │          │    └────────┬─────────┘                         │
│  ┌────────┐ ┌──────┴──────┐   │             │                                   │
│  │ Apply  │ │ Generic     │   │             │                                   │
│  │ from   │ │ template    │───┘             │                                   │
│  │ .md    │ │ → AI fix    │◄────────────────┘                                   │
│  └────┬───┘ └──────┬──────┘                                                     │
│       │            │                                                            │
│       └─────┬──────┘                                                            │
│             ▼                                                                   │
│    ┌─────────────────────┐                                                      │
│    │ Remove EWI comment  │                                                      │
│    │ Verify compilation  │                                                      │
│    │ Record to database  │                                                      │
│    └─────────────────────┘                                                      │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Key Functions Reference


| Function                          | Source     | Purpose                                        |
| --------------------------------- | ---------- | ---------------------------------------------- |
| `initialize_database()`           | sma_api.py  | Create/load DB, generate fix_id, create tables |
| `get_pending_ewi_codes()`         | sma_api.py  | Get distinct pending EWI codes                 |
| `get_ewis_by_code()`              | sma_api.py  | Get EWIs for specific code                     |
| `get_ewis_by_file()`              | sma_api.py  | Get EWIs for specific file                     |
| `update_ewi_status_single()`      | sma_api.py  | Update single Issues table row                 |
| `update_ewi_status()`             | sma_api.py  | Update all rows for a code (cascading)         |
| `reset_all_to_pending()`          | sma_api.py  | Reset ALL EWIs to 'pending' for full reprocess |
| `reset_not_auto_resolved_to_pending()` | sma_api.py  | Reset not_auto_resolved to 'pending'           |
| `insert_fix_result()`             | sma_api.py  | Record single fix attempt                      |
| `batch_insert_fix_results()`      | sma_api.py  | Batch insert results per file (recommended)    |
| `update_summary_end()`            | sma_api.py  | Finalize session summary                       |
| `generate_fix_id()`               | sma_api.py  | Generate new session UUID                      |
| `get_fix_results()`               | sma_api.py  | Get all results for session                    |
| `get_fix_results_stats()`         | sma_api.py  | Get success/failed counts                      |
| `get_summary_stats()`             | sma_api.py  | Get Issues table statistics                    |
| `git_status()`                    | sma_api.py  | Check repo state: is_repo, is_clean, branch    |
| `git_init_if_needed()`             | sma_api.py  | Init repo if not exists (no-op if exists)      |
| `git_stash()`                     | sma_api.py  | Stash uncommitted changes (incl. untracked)    |
| `git_ensure_branch()`             | sma_api.py  | Create or checkout sma/migration-process       |
| `git_commit()`                    | sma_api.py  | Stage all + commit with message                |
| `git_verify_branches()`           | sma_api.py  | Verify main + sma/migration-process exist      |
| `git_ensure_ready()`              | sma_api.py  | High-level: init + stash + ensure branch       |


## Status Values


| Table             | Column | Values                            |
| ----------------- | ------ | --------------------------------- |
| Issues            | status | `pending`, `auto_resolved`, `not_auto_resolved` |
| ewi_fixer_results | status | `success`, `failed`               |

**⚠️ STATUS CONSISTENCY:** These two tables MUST stay in sync:
- File was **modified** → `ewi_fixer_results.status = 'success'` AND `Issues.status = 'auto_resolved'`
- File was **NOT modified** → `ewi_fixer_results.status = 'failed'` AND `Issues.status = 'not_auto_resolved'`
- **NEVER** mark `Issues.status = 'not_auto_resolved'` when code was actually changed (even if manual review is needed later)


## Important Constraints

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  CONSTRAINTS & WARNINGS                                                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ⚠️  Call initialize_database() EXACTLY ONCE at the start of Step 4             │
│      NEVER call again on retry/error                                            │
│      start_time is recorded at this moment — calling it late = wrong duration   │
│                                                                                 │
│  ⚠️  DO NOT create additional files (fix_session.json, etc.)                    │
│      All state is in sma_storage.sqlite3                                        │
│                                                                                 │
│  ⚠️  MUST use sma_api.py functions for ALL database operations                  │
│      Tool pattern: sma_api.<function_name>(workload_path, ...)                  │
│      NEVER create tables manually with raw SQL                                  │
│      NEVER improvise table names or schemas                                     │
│      Valid tables: Issues, ewi_fixer_results, ewi_fixer_summary                 │
│      Column names in Issues are CAPITALIZED (Code, FileId, Line, CellId, etc.)  │
│                                                                                 │
│  ⚠️  MUST call update_summary_end() before finishing workflow                    │
│      (No db.close() needed — sma_api.py functions auto-close connections)        │
│                                                                                 │
│  ⚠️  MUST make Git commit at the end (Step 12) via sma_api.git_commit()        │
│                                                                                 │
│  ⚠️  MUST use sma_api.git_init_if_needed() for initial commit (Step 2)         │
│      This ensures 'main' branch exists as reference for the original SMA output │
│                                                                                 │
│  ⚠️  MUST process ALL files with pending EWIs - NEVER skip or stop early        │
│      If .py not found, ALWAYS try .ipynb extension (don't rely on CellId > 0)   │
│      Normalize FileId paths: replace \ with / before constructing file paths    │
│                                                                                 │
│  ⚠️  NOTEBOOK CELLS: SMA stores source as STRING, not array                    │
│      cell['source'] = new_src (string) — NEVER list(new_src)                   │
│      Edit .ipynb via Python json.load/dump — NOT notebook_edit_cell tool       │
│                                                                                 │
│  ⚠️  Do NOT use background agents for parallel file processing                  │
│      Use inline scripts for bulk operations instead                             │
│                                                                                 │
│  ⚠️  NEVER run this skill as a background agent (run_in_background=true)        │
│                                                                                 │
│  ⚠️  VALIDATE progress: if 0 EWIs found in files expected to have them,         │
│      STOP and investigate (likely a parsing/format bug)                          │
│                                                                                 │
│  ⚠️  Update ALL remaining 'pending' DB entries at end (not just file matches)   │
│      SPRKDBX codes may not have visible #EWI: comments in source files          │
│                                                                                 │
│  ⚠️  One row per EWI in ewi_fixer_results (not batched)                         │
│                                                                                 │
│  ⚠️  affected_file must be FULL ABSOLUTE PATH                                   │
│                                                                                 │
│  ⚠️  Verify compilation BEFORE recording results                                │
│                                                                                 │
│  ⚠️  DO NOT simplify/restructure files - only fix EWIs                          │
│                                                                                 │
│  ⚠️  DO NOT modify generic template markdowns (with <placeholders>)             │
│                                                                                 │
│  ⚠️  NEVER create external script files (/tmp/*.py, fix_script.py, etc.)       │
│      All processing must be inline Python via Bash tool                         │
│      NEVER use cat > or write to create standalone batch scripts                │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

