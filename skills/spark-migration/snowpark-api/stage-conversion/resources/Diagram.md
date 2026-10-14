# DVP Stage Conversion - Architecture Diagram

> **Last Updated**: 2026-03-17
> **Note**: Keep this diagram updated when making changes to the skill.

## Workflow Checklist

```
Path Replacement Progress:
- [ ] Step 1: Identify target files
- [ ] Step 2: Scan for embedded paths
- [ ] Step 3: Present findings and get prefix from user
- [ ] Step 4: Preview changes (dry-run)
- [ ] Step 5: Get user approval
- [ ] Step 6: Check git repository status
- [ ] Step 7: Apply replacements
- [ ] Step 8: Generate report and summary
- [ ] Step 9: Verify results
```

---

## Main Workflow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                      DVP STAGE CONVERSION - WORKFLOW                            │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────┐
│   TRIGGER       │  "replace embedded file paths", "SMA file paths", "stage prefix"
│   (Cortex)      │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 1: IDENTIFY TARGET FILES                                        [ ]      │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Ask user: "Which files should I scan?"                                         │
│    - Specific file(s)                                                           │
│    - Directory (glob **/*.py, **/*.ipynb)                                       │
│    - Current file                                                               │
│                                                                                 │
│  Completion: User has confirmed list of files to scan                           │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 2: SCAN FOR EMBEDDED PATHS                                      [ ]      │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Command: python3 scripts/embedded_path_replacer.py --scan-only [files...]      │
│                                                                                 │
│  Detection Methods:                                                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │ PRIMARY: AST Parsing                                                    │    │
│  │   - Snowpark read: session.read.csv(), session.read.parquet()           │    │
│  │   - Snowpark write: df.write.csv(), df.write.parquet()                  │    │
│  │   - File ops: pd.read_csv(), open(), session.file.get()                 │    │
│  │   - Variables: input_path = "s3://..."                                  │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │ FALLBACK: Regex Patterns                                                │    │
│  │   - Stage paths: @STAGE/protocol/path                                   │    │
│  │   - SQL statements: CREATE STAGE, COPY INTO                             │    │
│  │   - Protocol URLs: s3://, hdfs://, gs://                                │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  Output: List[PathOccurrence] with path, file, line, method                     │
│  Completion: Script has returned list of detected paths                         │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 3: PRESENT FINDINGS & GET PREFIX                                [ ]      │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Show user:                                                                     │
│    Found N paths in M files:                                                    │
│      - s3://bucket/file.csv (line 45, Snowpark read)                            │
│      - hdfs://cluster/data (line 67, String variable)                           │
│                                                                                 │
│  Explain format:                                                                │
│    s3://bucket/path -> @PREFIX/s3/bucket/path                                   │
│                                                                                 │
│  Ask: "What prefix should be used? (e.g., MY_STAGE)"                            │
│                                                                                 │
│  ⚠️ STOP: Wait for user to provide prefix                                       │
│  Completion: User has provided stage prefix                                     │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 4: PREVIEW CHANGES (DRY RUN)                                    [ ]      │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Command: python3 scripts/embedded_path_replacer.py --dry-run --prefix X [...]  │
│                                                                                 │
│  Show preview:                                                                  │
│    s3://bucket/file.csv -> @X/s3/bucket/file.csv (3 occurrences)                │
│    hdfs://cluster/data -> @X/hdfs/cluster/data (1 occurrence)                   │
│    Total: N replacements across M files                                         │
│                                                                                 │
│  Completion: Preview has been shown to user                                     │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 5: GET USER APPROVAL                                            [ ]      │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Ask: "Apply these changes? (Yes / No / Modify prefix)"                         │
│                                                                                 │
│  ⚠️ STOP: Do NOT proceed without explicit "Yes"                                 │
│                                                                                 │
│  ┌─────────────┐    ┌─────────────────┐    ┌─────────────────┐                  │
│  │    "No"     │    │ "Modify prefix" │    │     "Yes"       │                  │
│  └──────┬──────┘    └────────┬────────┘    └────────┬────────┘                  │
│         │                    │                      │                           │
│         ▼                    ▼                      ▼                           │
│      END (no             Return to              Continue to                     │
│      changes)            Step 3                 Step 6                          │
│                                                                                 │
│  Completion: User has explicitly approved changes                               │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 6: CHECK GIT REPOSITORY STATUS                                  [ ]      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Uses: sma_api.git_is_repo(workload_path)                                       │
│                                                                                 │
│  ┌──────────────────────────────┐                                               │
│  │ sma_api.git_is_repo(path)?  │                                               │
│  └────────────┬─────────────────┘                                               │
│               │                                                                 │
│         ┌─────┴─────┐                                                           │
│         ▼           ▼                                                           │
│        YES          NO                                                          │
│         │            │                                                          │
│         │     ┌──────┴──────────────────────────────────────────┐               │
│         │     │  ⚠️  WARNING: Not a git repository              │               │
│         │     │  "Proceed anyway? (yes/no)"                     │               │
│         │     └──────┬──────────────────────────────────────────┘               │
│         │            │                                                          │
│         │      ┌─────┴─────┐                                                    │
│         │      ▼           ▼                                                    │
│         │    "yes"       "no" ───────► END (recommend git init)                 │
│         │      │                                                                │
│         └──────┴───────────────────────────────────────────────────────────┐    │
│                                                                            │    │
│                                  PROCEED                                   │    │
│                                                                                 │
│  Bypass: --skip-git-check flag                                                  │
│  Completion: Git check passed or user confirmed without git                     │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 7: APPLY REPLACEMENTS                                           [ ]      │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Command: python3 scripts/embedded_path_replacer.py --prefix X [files...]       │
│                                                                                 │
│  Process:                                                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  For each file:                                                         │    │
│  │    1. Read file content                                                 │    │
│  │    2. Replace paths (preserve quote style)                              │    │
│  │    3. Inject WARNING comments for needs_revision paths                  │    │
│  │    4. Write modified content                                            │    │
│  │    5. Report: "filename.py: N replacements"                             │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  File Types:                                                                    │
│    .py  -> Full AST analysis, direct replacement                                │
│    .ipynb -> Parse JSON, modify code cells only, preserve structure             │
│                                                                                 │
│  Completion: Script completed without errors                                    │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 8: GENERATE REPORT & SUMMARY                                    [ ]      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Reports Generated:                                                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  CSV: sma_path_replacement_report_YYYYMMDD_HHMMSS.csv                   │    │
│  │                                                                         │    │
│  │  Columns: Original Path | Transformed | Status | Method | File | Line   │    │
│  │  Use: Excel review, team sharing, filtering by status                   │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  JSON: sma_path_replacement_report_YYYYMMDD_HHMMSS.json                 │    │
│  │                                                                         │    │
│  │  Structure: metadata, replaced_paths, needs_revision, skipped, by_file  │    │
│  │  Use: Programmatic analysis, automation, detailed audit                 │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  Summary to user:                                                               │
│    Replaced: N paths in M files                                                 │
│    Needs revision: X paths                                                      │
│    Skipped: Y paths                                                             │
│                                                                                 │
│  Completion: Reports generated and summary presented                            │
└────────┬────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  STEP 9: VERIFY RESULTS                                               [ ]      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Verification:                                                                  │
│    1. Check replaced count matches preview                                      │
│    2. Review needs_revision paths                                               │
│    3. Explain any skipped paths                                                 │
│                                                                                 │
│  Next steps for user:                                                           │
│    1. Review needs_revision paths (dynamic elements)                            │
│    2. Check CSV report for full details                                         │
│    3. Test modified code                                                        │
│    4. Commit if everything works                                                │
│                                                                                 │
│  Completion: User informed of results and next steps                            │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Path Transformation Matrix

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  PROTOCOL TRANSFORMATION                                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Protocol         │ Input Example                    │ Output                   │
│  ─────────────────┼──────────────────────────────────┼──────────────────────────│
│  s3://            │ s3://bucket/path/file.csv        │ @PREFIX/s3/bucket/...    │
│  s3a://           │ s3a://bucket/path/file.parquet   │ @PREFIX/s3a/bucket/...   │
│  hdfs://          │ hdfs://cluster/warehouse/data    │ @PREFIX/hdfs/cluster/... │
│  abfs://          │ abfs://container@account/path    │ @PREFIX/abfs/container/..│
│  abfss://         │ abfss://container@account/path   │ @PREFIX/abfss/container/.│
│  gs://            │ gs://bucket/path/file.csv        │ @PREFIX/gs/bucket/...    │
│  file://          │ file:///local/path/file.csv      │ @PREFIX/local/path/...   │
│  /path (local)    │ /tmp/data/file.csv               │ @PREFIX/local/tmp/data/..│
│  ./path (rel)     │ ./config/settings.json           │ @PREFIX/relative/config/.│
│  ../path (rel)    │ ../shared/data.parquet           │ @PREFIX/relative/parent/.│
│  ../../ (rel)     │ ../../level2/file.csv            │ @PREFIX/relative/parent2/│
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Status Values

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  REPLACEMENT STATUS                                                             │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Status             │ Meaning                         │ Action Required          │
│  ───────────────────┼─────────────────────────────────┼──────────────────────────│
│  replaced           │ Successfully transformed        │ None                     │
│  needs_revision     │ Transformed, has dynamic parts  │ Manual review            │
│  skipped_relative   │ Relative path, couldn't parse   │ Manual fix               │
│  skipped_dynamic    │ Too many variables              │ Manual fix               │
│  skipped_unsupported│ Unrecognized format             │ Manual fix               │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Class Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  embedded_path_replacer.py - CLASS STRUCTURE                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Enums:                                                                         │
│  ┌─────────────────────────┐    ┌─────────────────────────────────────────────┐ │
│  │ DetectionMethod         │    │ ReplacementStatus                           │ │
│  │ - SNOWPARK_READ         │    │ - REPLACED                                  │ │
│  │ - SNOWPARK_WRITE        │    │ - NEEDS_REVISION                            │ │
│  │ - FILE_OPERATION        │    │ - SKIPPED_RELATIVE                          │ │
│  │ - STRING_VARIABLE       │    │ - SKIPPED_DYNAMIC                           │ │
│  │ - SNOWFLAKE_STAGE       │    │ - SKIPPED_UNSUPPORTED                       │ │
│  │ - REGEX_FALLBACK        │    │ - FAILED                                    │ │
│  └─────────────────────────┘    └─────────────────────────────────────────────┘ │
│                                                                                 │
│  Data Classes:                                                                  │
│  ┌─────────────────────────┐    ┌─────────────────────────────────────────────┐ │
│  │ PathOccurrence          │    │ PathReplacement                             │ │
│  │ - path: str             │    │ - original: str                             │ │
│  │ - file: str             │    │ - transformed: Optional[str]                │ │
│  │ - line_number: int      │    │ - status: ReplacementStatus                 │ │
│  │ - detection_method      │    │ - reason: str                               │ │
│  │ - cell_index: Optional  │    │ - occurrences: List[PathOccurrence]         │ │
│  │ - quote_char: str       │    └─────────────────────────────────────────────┘ │
│  └─────────────────────────┘                                                    │
│                                                                                 │
│  Core Classes:                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │ ASTPathVisitor (ast.NodeVisitor)                                            ││
│  │ - visit_Call()    -> Extract paths from function arguments                  ││
│  │ - visit_Assign()  -> Detect path variable assignments                       ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │ EmbeddedPathDetector                                                        ││
│  │ - detect_with_ast()  -> Primary AST-based detection                         ││
│  │ - detect_in_line()   -> Regex fallback for edge cases                       ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │ FileScanner                                                                 ││
│  │ - scan_python_file()      -> Scan .py files                                 ││
│  │ - scan_jupyter_notebook() -> Scan .ipynb code cells                         ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │ PathTransformer                                                             ││
│  │ - transform()         -> Convert path to @PREFIX/protocol/path              ││
│  │ - is_dynamic_path()   -> Check for f-string interpolation                   ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │ ReplacementApplier                                                          ││
│  │ - apply_replacements_to_file() -> Apply changes preserving format           ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │ ReportGenerator                                                             ││
│  │ - generate_csv_report()  -> Create CSV report                               ││
│  │ - generate_json_report() -> Create JSON report                              ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## CLI Quick Reference

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  COMMAND LINE OPTIONS                                                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  # Step 2: Scan only                                                            │
│  python3 scripts/embedded_path_replacer.py --scan-only [files...]               │
│                                                                                 │
│  # Step 4: Dry run (preview)                                                    │
│  python3 scripts/embedded_path_replacer.py --dry-run --prefix PREFIX [files...] │
│                                                                                 │
│  # Step 7: Apply changes                                                        │
│  python3 scripts/embedded_path_replacer.py --prefix PREFIX [files...]           │
│                                                                                 │
│  # Optional flags:                                                              │
│  --no-warnings      # Don't inject WARNING comments                             │
│  --skip-git-check   # Bypass git repository check                               │
│  --report FILE      # Custom report output path                                 │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Key Functions Reference

| Step | Function | Class | Purpose |
|------|----------|-------|---------|
| 2 | `scan_python_file()` | FileScanner | Scan .py files |
| 2 | `scan_jupyter_notebook()` | FileScanner | Scan .ipynb code cells |
| 2 | `detect_with_ast()` | EmbeddedPathDetector | Primary AST detection |
| 2 | `detect_in_line()` | EmbeddedPathDetector | Regex fallback |
| 6 | `sma_api.git_is_repo()` | sma_api | Check if workload is a git repo |
| 4,7 | `transform()` | PathTransformer | Convert path format |
| 7 | `apply_replacements_to_file()` | ReplacementApplier | Apply changes |
| 8 | `generate_csv_report()` | ReportGenerator | Create CSV |
| 8 | `generate_json_report()` | ReportGenerator | Create JSON |
