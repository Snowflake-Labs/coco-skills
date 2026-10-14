# Snowflake Notebook Migration - Architecture Diagram

> **Last Updated**: 2026-06-25
> **Note**: Keep this diagram updated when making changes to the skill.

## Skill Structure

```
┌───────────────────────────────────────────────────────────────────────────┐
│ snowflake-notebook-migration/                                             │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  SKILL.md (orchestrator)                                                  │
│    Frontmatter, core principle, file naming, mode detection,              │
│    PySpark setup cell, migration note format, success criteria,           │
│    validation loop.                                                       │
│                                                                           │
│    Routes to:                                                             │
│      ├── references/standalone-mode.md    (user-invoked flows)            │
│      └── references/orchestrator-mode.md  (spark-migration flows)     │
│                                                                           │
│    Both modes consume:                                                    │
│      ├── references/transformation-rules.md                               │
│      │   (structured registry: pattern | category | action |              │
│      │    preserves | notes)                                              │
│      └── references/tools.md                                              │
│          (scripts reference: args, usage, when-to-use)                    │
│                                                                           │
│  scripts/                                                                 │
│    ├── detect_and_parse_notebook.py  (format-agnostic parser)             │
│    ├── scan_dependencies.py          (%run + import graph)                │
│    ├── validate_notebook.py          (per-notebook validation)            │
│    └── validate_directory.py         (post-conversion directory check)    │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

## Top-Level Flow

```
┌───────────────────────────────────────────────────────────────────────────┐
│                    DATABRICKS NOTEBOOK MIGRATION - FLOW                   │
│ Migrate DBX notebooks to Snowflake Workspace notebooks (.ipynb)           │
│ Core principle: Preserve everything, fix only incompatible patterns       │
└───────────────────────────────────────────────────────────────────────────┘

┌─────────────────┐
│   TRIGGER       │  "migrate databricks", "convert dbx",
│   (Cortex)      │  "dbx notebook", "migrate notebook"
└─────────────────┘
         │
         ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ PREREQUISITE: LOAD snowflake-notebooks SKILL                              │
│ This migration skill defines WHAT to change;                              │
│ snowflake-notebooks defines HOW to format the output notebook             │
│ (nbformat 4.5, SQL cell structure, Jinja templating, upload)              │
└────────┬──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ MODE DETECTION (SKILL.md): Orchestrator context present?                  │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  YES --> Load references/orchestrator-mode.md                             │
│           - (d) Orchestrator Context: pre-filled paths + conversion type  │
│                                                                           │
│  NO  --> Load references/standalone-mode.md                               │
│           - (a) GitHub URL:   clone, navigate, ask dest, create branch    │
│           - (b) Local Dir:    scan directly                               │
│           - (c) Single File:  Single Notebook Conversion                  │
│                                                                           │
│ Both modes apply rules from references/transformation-rules.md.           │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

## Directory Workflow

```
┌───────────────────────────────────────────────────────────────────────────┐
│ DIRECTORY WORKFLOW (GitHub repo or local directory)                       │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│ STEP 1: SCAN                                                              │
│   ┌───────────────────────────────────────────────────────────────────┐   │
│   │ Find all .ipynb and .py files in directory + subdirectories       │   │
│   └───────────────────────────────────────────────────────────────────┘   │
│                                                                           │
│ STEP 2: TRACE DEPENDENCIES                                                │
│   ┌───────────────────────────────────────────────────────────────────┐   │
│   │ %run references: which notebooks chain to which                   │   │
│   │ Python imports: local .py files referenced by notebooks           │   │
│   └───────────────────────────────────────────────────────────────────┘   │
│                                                                           │
│ STEP 3: SHOW MIGRATION PLAN                                               │
│   ┌───────────────────────────────────────────────────────────────────┐   │
│   │ Dependency graph (list or tree)                                   │   │
│   │ Recommended conversion order (leaf dependencies first)            │   │
│   │ List .py files to upload alongside notebooks                      │   │
│   │                                                                   │   │
│   │ MANDATORY: Wait for user approval before converting               │   │
│   └───────────────────────────────────────────────────────────────────┘   │
│                                                                           │
│ STEP 4: ASK CONVERSION MODE                                               │
│   ┌───────────────────────────────────────────────────────────────────┐   │
│   │ "Convert all in recommended order?" / "One by one?"               │   │
│   │                                                                   │   │
│   │ All --> Process without pausing between notebooks                 │   │
│   │ One --> Convert each, ask before proceeding to next               │   │
│   └───────────────────────────────────────────────────────────────────┘   │
│                                                                           │
│ STEP 5: CONVERT EACH NOTEBOOK                                             │
│   (Uses Single Notebook Conversion pipeline below)                        │
│                                                                           │
│ STEP 6: POST-CONVERSION SUMMARY                                           │
│   ┌───────────────────────────────────────────────────────────────────┐   │
│   │ Report cell title coverage across all notebooks                   │   │
│   │ Offer to name unnamed cells for minimap readability               │   │
│   │ Offer to upload all: cortex artifact create notebook              │   │
│   │ Combined migration summary                                        │   │
│   └───────────────────────────────────────────────────────────────────┘   │
│                                                                           │
│ GitHub-only extras:                                                       │
│   ┌───────────────────────────────────────────────────────────────────┐   │
│   │ Commit converted notebooks + copied .py files                     │   │
│   │ Offer to upload to Snowflake Workspace                            │   │
│   │ Offer to create PR via gh pr create                               │   │
│   └───────────────────────────────────────────────────────────────────┘   │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

## Single Notebook Conversion

```
┌───────────────────────────────────────────────────────────────────────────┐
│                    SINGLE NOTEBOOK CONVERSION PIPELINE                    │
└───────────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────────┐
│ STEP 1: READ original DBX notebook                                        │
│ Understand full content and structure.                                    │
│ After writing converted .ipynb, run validate_notebook.py --finalize to   │
│ validate output and delete original non-.ipynb source file.              │
│ Output file naming: append .ipynb to full original filename               │
│ (e.g. config.py → config.py.ipynb). Keep .ipynb names unchanged.          │
└────────┬──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ STEP 2: DETECT PYSPARK USAGE                                              │
│ Scan for: from pyspark..., import pyspark, spark.* calls                  │
│                                                                           │
│ PySpark found?                                                            │
│   YES --> First cell = Snowpark Connect bootstrap cell                    │
│           (MUST run before any pyspark imports — import order matters)    │
│           from snowflake import snowpark_connect                          │
│           from snowflake.snowpark_connect.resources_initializer \         │
│               import wait_for_resource_initialization                     │
│           from snowflake.snowpark.context import get_active_session       │
│           spark = snowpark_connect.init_spark_session()                   │
│           wait_for_resource_initialization()                              │
│           _session = get_active_session()                                 │
│           _session.sql("USE DATABASE <DATABASE>").collect()               │
│           _session.sql("USE SCHEMA <SCHEMA>").collect()                   │
│           _session.sql("USE WAREHOUSE <WAREHOUSE>").collect()             │
│                                                                           │
│   NO  --> First cell = SQL setup cell (commented out)                     │
│           -- USE ROLE / DATABASE / SCHEMA / WAREHOUSE                     │
└────────┬──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ STEP 3: PROCESS EACH CELL                                                 │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│ For every cell in the original notebook:                                  │
│                                                                           │
│   NO incompatible patterns                                                │
│     --> Copy cell as-is. Do not touch.                                    │
│                                                                           │
│   PARTIAL incompatible patterns                                           │
│     --> Fix only incompatible lines.                                      │
│         Keep all other lines exactly as they are.                         │
│                                                                           │
│   ENTIRE cell unsupported                                                 │
│     --> Convert to markdown cell with migration note                      │
│         + original code in fenced block.                                  │
│                                                                           │
│ PySpark code: Keep as-is (Snowpark Connect compatibility)                 │
│ Do NOT rewrite to Snowpark Python API equivalents                         │
│                                                                           │
└────────┬──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ STEP 4: VERIFY %run PATHS                                                 │
│ Ensure paths have .ipynb extension, correct relative to Workspaces        │
└────────┬──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ STEP 5: CARRY OVER CELL TITLES                                            │
│ DBX metadata.application/vnd.databricks.v1+cell.title                     │
│   --> metadata.title in converted cell                                    │
│ Report: N cells with titles, M cells without                              │
│ Offer to name unnamed cells for minimap readability                       │
└────────┬──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ STEP 6: ADD MIGRATION SUMMARY (last markdown cell)                        │
│   Changes made (list every modification with cell reference)              │
│   Remaining gaps requiring the owner's attention                          │
│   Count: cells unchanged vs modified vs flagged                           │
└────────┬──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ STEP 7: OFFER UPLOAD to Snowflake Workspace                               │
│ cortex artifact create notebook <name> <path>                             │
│ Follow snowflake-notebooks skill upload workflow + deeplink               │
└────────┬──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ STEP 8: REPORT summary to user                                            │
└───────────────────────────────────────────────────────────────────────────┘
```

## Orchestrator Mode Workflow

```
┌───────────────────────────────────────────────────────────────────────────┐
│ ORCHESTRATOR MODE (invoked by spark-migration)                        │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│ Context received from orchestrator:                                       │
│   - SMA output directory: <output>                                        │
│   - Conversion type: scos | snowpark_api                                  │
│   - Jupyter conversion: yes | no (snowpark_api only)                      │
│   - Git branch: sma/migration-process                                     │
│                                                                           │
│ STEP 1: SCAN <output>/Output/                                             │
│   ┌───────────────────────────────────────────────────────────────────┐   │
│   │ Find .ipynb, Databricks .py (# Databricks notebook source),       │   │
│   │ Databricks .scala (// Databricks notebook source)                 │   │
│   └───────────────────────────────────────────────────────────────────┘   │
│                                                                           │
│ STEP 2: DETERMINE CONVERSION SUB-MODE                                     │
│   ┌─────────────────────────────────────────────────────────────────┐     │
│   │ scos:                                                           │     │
│   │   Full migration + Snowpark Connect setup cell                  │     │
│   │   PySpark kept as-is                                            │     │
│   ├─────────────────────────────────────────────────────────────────┤     │
│   │ snowpark_api + jupyter=yes:                                     │     │
│   │   Polish SMA-generated .ipynb (no setup cell needed)            │     │
│   ├─────────────────────────────────────────────────────────────────┤     │
│   │ snowpark_api + jupyter=no:                                      │     │
│   │   Convert DBX format → .ipynb (no setup cell needed)            │     │
│   └─────────────────────────────────────────────────────────────────┘     │
│                                                                           │
│ STEP 3: CONVERT EACH NOTEBOOK IN-PLACE                                    │
│   ┌───────────────────────────────────────────────────────────────────┐   │
│   │ Apply incompatible pattern rules (same as standalone)             │   │
│   │ Modify files directly in <output>/Output/                         │   │
│   │ Do NOT create copies — originals preserved on main branch         │   │
│   │ Run validate_notebook.py --finalize after each conversion              │   │
│   └───────────────────────────────────────────────────────────────────┘   │
│                                                                           │
│ STEP 4: GIT COMMIT via sma_api.git_commit()                               │
│   ┌───────────────────────────────────────────────────────────────────┐   │
│   │ sma_api.git_commit(<output>, "Notebook migration: ...")           │   │
│   │ Commits on sma/migration-process branch                           │   │
│   └───────────────────────────────────────────────────────────────────┘   │
│                                                                           │
│ SKIPPED in orchestrator mode:                                             │
│   - Dependency tracing (%run analysis)                                    │
│   - Conversion order selection                                            │
│   - Upload to Snowflake Workspace                                         │
│   - PR creation                                                           │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

## Incompatible Pattern Resolution

```
┌───────────────────────────────────────────────────────────────────────────┐
│ INCOMPATIBLE PATTERN RESOLUTION TABLE                                     │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│ MAGIC COMMANDS                                                            │
│   ┌──────────┬──────────────────────────────────────────────────────────┐ │
│   │ %md      │ Markdown cell (remove %md, keep content)                 │ │
│   │ %sql     │ SQL cell (add resultVariableName metadata)               │ │
│   │ %scala   │ Markdown + migration note (NOT SUPPORTED)                │ │
│   │ %r       │ Markdown + migration note (NOT SUPPORTED)                │ │
│   │ %sh      │ Python cell (prefix each command with !)                 │ │
│   │ %fs      │ Markdown + migration note (no DBFS)                      │ │
│   │ %run     │ Keep as-is, ensure post-conversion .ipynb name           │ │
│   │          │ (e.g. config.py → %run ./config.py.ipynb)                │ │
│   └──────────┴──────────────────────────────────────────────────────────┘ │
│                                                                           │
│ DATABRICKS APIs                                                           │
│   ┌────────────────────────┬────────────────────────────────────────────┐ │
│   │ _sqldf                 │ Replace with resultVariableName; strip     │ │
│   │                        │ .toPandas(); see § SQL Cell Referencing:   │ │
│   │                        │ _sqldf Migration below for runtime-        │ │
│   │                        │ dependent type handling.                   │ │
│   ├────────────────────────┼────────────────────────────────────────────┤ │
│   │ dbutils.widgets.*      │ Hardcoded defaults as Python vars          │ │
│   │                        │ Or notebook parameters if called via       │ │
│   │                        │ EXECUTE NOTEBOOK                           │ │
│   ├────────────────────────┼────────────────────────────────────────────┤ │
│   │ dbutils.notebook.run() │ Consider %run or Task DAG                  │ │
│   ├────────────────────────┼────────────────────────────────────────────┤ │
│   │ dbutils.notebook.exit()│ Markdown + migration note                  │ │
│   │                        │ Do NOT use raise SystemExit()              │ │
│   ├────────────────────────┼────────────────────────────────────────────┤ │
│   │ dbutils.secrets.get()  │ Comment out, assign placeholder            │ │
│   │                        │ Keep variable alive downstream             │ │
│   ├────────────────────────┼────────────────────────────────────────────┤ │
│   │ dbutils.fs.*           │ Markdown + migration note                  │ │
│   │                        │ Suggest Snowflake stage ops                │ │
│   ├────────────────────────┼────────────────────────────────────────────┤ │
│   │ display(df)            │ df.show() for DataFrames                   │ │
│   │                        │ Do NOT use bare df — lazy DataFrames only  │ │
│   │                        │ print schema string, not data              │ │
│   │ display(pdf)           │ bare pdf (last expression)                 │ │
│   │ display(plt.gcf())     │ plt.show()                                 │ │
│   └────────────────────────┴────────────────────────────────────────────┘ │
│                                                                           │
│ UNSUPPORTED (flag as migration notes, never replace silently):            │
│   ┌───────────────────────────────────────────────────────────────────┐   │
│   │ pyspark.RDD, pyspark.ml, pyspark.streaming                        │   │
│   │ DataFrameWriter.jdbc(), .orc() read/write                         │   │
│   │ Iterator type in UDFs                                             │   │
│   │ External database reads/writes (Redshift, RDS/MySQL)              │   │
│   │ Cloud storage writes via custom libraries                         │   │
│   │ Custom utility libraries                                          │   │
│   │ Hardcoded credentials or secret references                        │   │
│   └───────────────────────────────────────────────────────────────────┘   │
│                                                                           │
│ SILENTLY IGNORED (no-op, no action needed):                               │
│   ┌───────────────────────────────────────────────────────────────────┐   │
│   │ DataFrame.hint(), DataFrame.repartition()                         │   │
│   └───────────────────────────────────────────────────────────────────┘   │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

## PySpark Strategy: Keep via Snowpark Connect

```
┌───────────────────────────────────────────────────────────────────────────┐
│ PYSPARK CODE HANDLING                                                     │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│ Snowpark Connect provides PySpark 3.5.3-compatible interface.             │
│ snowpark-connect is pre-installed in Snowflake Workspace notebooks.       │
│                                                                           │
│   ┌─────────────────────────────┐  ┌─────────────────────────────────┐    │
│   │ KEEP AS-IS                  │  │ DO NOT REWRITE TO               │    │
│   ├─────────────────────────────┤  ├─────────────────────────────────┤    │
│   │ .withColumn()               │  │ .with_column()                  │    │
│   │ .groupBy()                  │  │ .group_by()                     │    │
│   │ .toDF()                     │  │ .to_df()                        │    │
│   │ F.collect_list()            │  │ snowflake.snowpark.functions    │    │
│   │ F.explode_outer()           │  │                                 │    │
│   │ F.date_format()             │  │ Any Snowpark Python API         │    │
│   │ import pyspark.sql.*        │  │ equivalents                     │    │
│   │ from pyspark.sql.types *    │  │                                 │    │
│   └─────────────────────────────┘  └─────────────────────────────────┘    │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

## Cell Processing Decision Tree

```
┌───────────────────────────────────────────────────────────────────────────┐
│ CELL PROCESSING DECISION TREE                                             │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│               Read next cell                                              │
│                     |                                                     │
│                     v                                                     │
│          Has magic command? (%md, %sql, %sh, etc)                         │
│            |                        |                                     │
│           YES                       NO                                    │
│            |                        |                                     │
│            v                        v                                     │
│   Apply magic resolution   Has dbutils.* or display()?                    │
│   (see table above)          |                    |                       │
│                             YES                   NO                      │
│                              |                    |                       │
│                              v                    v                       │
│                     Partial: fix only    Has unsupported API?             │
│                     incompatible lines   (RDD, ml, streaming)             │
│                     Entire: markdown       |             |                │
│                     + migration note      YES            NO               │
│                                            |             |                │
│                                            v             v                │
│                                    Flag with note   Copy as-is            │
│                                    preserve code    (no changes)          │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

## Migration Note Format

```
┌───────────────────────────────────────────────────────────────────────────┐
│ MIGRATION NOTE FORMAT (for unsupported cells)                             │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│ > Migration Note: [Brief description]                                     │
│ > [Why it's incompatible]                                                 │
│ >                                                                         │
│ > **Owner action required**: [What the owner needs to decide/do]          │
│ >                                                                         │
│ > Original code:                                                          │
│ > ```[language]                                                           │
│ > [original code preserved exactly]                                       │
│ > ```                                                                     │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

## SQL Cell Referencing: _sqldf Migration

```
┌───────────────────────────────────────────────────────────────────────────┐
│ _sqldf MIGRATION                                                          │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│ BEFORE (Databricks):                                                      │
│   ┌────────────────────────────────┐  ┌────────────────────────────────┐  │
│   │ Cell 1 (SQL):                  │  │ Cell 2 (Python):               │  │
│   │ SELECT * FROM users            │  │ df = _sqldf                    │  │
│   │ WHERE active = true            │  │ df.toPandas().head()           │  │
│   └────────────────────────────────┘  └────────────────────────────────┘  │
│                                                                           │
│ AFTER (Snowflake) -- Spark .toPandas() is REMOVED. rule 8 step 3 scans    │
│ the rest of the cell; .head( is in the ambiguous trigger list (Snowpark   │
│ DataFrame has no .head / .tail at all -- calling either raises            │
│ AttributeError; .first(n) / .take(n) are the action methods that return   │
│ List[Row]), so the defensive ternary IS inserted before the .head() call. │
│ Full rule mechanics:                                                      │
│   references/transformation-rules.md § SQL cell result type               │
│   ┌────────────────────────────────┐  ┌────────────────────────────────┐  │
│   │ Cell 1 (SQL):                  │  │ Cell 2 (Python):               │  │
│   │ resultVariableName:            │  │ import pandas as pd            │  │
│   │   "active_users"               │  │ active_users_pdf = (           │  │
│   │ SELECT * FROM users            │  │   active_users.copy()          │  │
│   │ WHERE active = true            │  │   if isinstance(               │  │
│   │                                │  │     active_users, pd.DataFrame)│  │
│   │                                │  │   else active_users.to_pandas()│  │
│   │                                │  │ )                              │  │
│   │                                │  │ df = active_users_pdf          │  │
│   │                                │  │ df.head()  # auto-displays     │  │
│   └────────────────────────────────┘  └────────────────────────────────┘  │
│                                                                           │
│ SQL-to-SQL cross-reference: use Jinja {{variable_name}}                   │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

## Skill Dependencies

```
┌───────────────────────────────────────────────────────────────────────────┐
│ SKILL DEPENDENCIES                                                        │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│   ┌───────────────────────────────────────────────────────────────────┐   │
│   │ snowflake-notebook-migration (this skill)                         │   │
│   │ Defines: WHAT to change (patterns, rules, workflow)               │   │
│   │ Modes: Standalone (GitHub/local/single) + Orchestrator            │   │
│   │                                                                   │   │
│   │ Internal layout:                                                  │   │
│   │   SKILL.md                                                        │   │
│   │     ├── references/standalone-mode.md                             │   │
│   │     ├── references/orchestrator-mode.md                           │   │
│   │     └── references/transformation-rules.md  (shared by both)      │   │
│   └───────────────────────────────────────────────────────────────────┘   │
│                           |                     |                         │
│                           | depends on          | invoked by              │
│                           v                     v                         │
│   ┌──────────────────────────────┐  ┌────────────────────────────────┐    │
│   │ snowflake-notebooks          │  │ spark-migration            │    │
│   │ Defines: HOW to format       │  │ (orchestrator)                 │    │
│   │ output notebooks             │  │ Provides: <output>, branch,    │    │
│   │ - nbformat 4.5 structure     │  │ conversion_type, jupyter flag  │    │
│   │ - SQL cell format            │  │ Uses: sma_api.git_commit()     │    │
│   │ - Jinja templating           │  │ for git operations             │    │
│   │ - Upload workflow            │  └────────────────────────────────┘    │
│   │ - Deeplink URL generation    │                                        │
│   └──────────────────────────────┘                                        │
│                                                                           │
│ External reference:                                                       │
│   ┌───────────────────────────────────────────────────────────────────┐   │
│   │ Snowpark Connect                                                  │   │
│   │ Pre-installed in Snowflake Workspace notebooks                    │   │
│   │ Provides PySpark 3.5.3-compatible interface                       │   │
│   │ No separate pip install pyspark needed                            │   │
│   └───────────────────────────────────────────────────────────────────┘   │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

## Important Constraints

```
┌───────────────────────────────────────────────────────────────────────────┐
│ CONSTRAINTS & WARNINGS                                                    │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│ This is a MIGRATION, not a REWRITE.                                       │
│ Preserve as much original code as possible.                               │
│                                                                           │
│ Do NOT summarize, rewrite, or rename variables.                           │
│ Other notebooks may %run this file and depend on exact names.             │
│                                                                           │
│ Do NOT add cells that are not in the original.                            │
│ Exception: setup cell and the migration summary cell at the end.          │
│                                                                           │
│ Do NOT add boilerplate (session init, imports) unless                     │
│ the original notebook had them.                                           │
│                                                                           │
│ Do NOT apply vNext best practices to migrated code.                       │
│ Keep variable names, import paths, and function calls as-is               │
│ unless they match an incompatible pattern.                                │
│                                                                           │
│ Do NOT rewrite PySpark to Snowpark Python API.                            │
│ Snowpark Connect handles PySpark compatibility natively.                  │
│                                                                           │
│ Run validate_notebook.py --finalize after each conversion to delete      │
│ original. Run validate_directory.py after all conversions complete.      │
│ Always create a new .ipynb file in the target location.                   │
│ Naming: append .ipynb to full original filename for non-.ipynb            │
│ sources (config.py → config.py.ipynb). Keep .ipynb names as-is.          │
│                                                                           │
│ Show migration plan to user BEFORE starting any conversion                │
│ (directory workflow). Wait for explicit approval.                         │
│                                                                           │
│ dbutils.notebook.exit(): Do NOT replace with SystemExit().                │
│ Convert to markdown cell with migration note.                             │
│                                                                           │
│ dbutils.secrets.get(): Do NOT convert entire cell to markdown.            │
│ Comment out the call, assign placeholder, keep variable alive.            │
│                                                                           │
│ DataFrame.hint() and DataFrame.repartition() are no-ops                   │
│ in Snowpark Connect. No action needed.                                    │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```
