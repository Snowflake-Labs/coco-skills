---
name: snowpark-connect
description: |
  Snowpark Connect (SCOS) skills for migrating and validating PySpark, Spark Scala, and Spark Java workloads on Snowflake.
  Generates SMA-compatible reports (Issues.csv, InputFilesInventory.csv, ArtifactDependencyInventory.csv)
  using EWI codes (SPRKCNTPY* for Python, SPRKCNTSCL* for Scala/Java) for use with dvp-sma-dashboard-generator.
  Use when: migrating PySpark or Spark Scala/Java to Snowpark Connect, validating SCOS migrations,
  analyzing Spark compatibility, assessing a Spark workload before migration, producing a
  migration readiness report, or working with Snowpark Connect for Spark.
  Triggers: snowpark connect, scos, pyspark migration, spark connect, scala spark migration,
  java spark migration, spark java, validate migration, pyspark compatibility, scala compatibility,
  java compatibility, assess pyspark, assess spark, assess databricks, spark assessment,
  databricks assessment, migration readiness, spark workload assessment, pyspark readiness,
  spark compatibility report, analyze spark, analyze pyspark, analyze databricks,
  check spark compatibility, check pyspark, scan spark, spark audit, workload analysis,
  databricks to snowflake, pyspark to snowflake, should I migrate, migration scope.
---

# Snowpark Connect

> **Bundled sub-skill of `spark-migration`.** This SKILL.md is loaded
> on-demand by the parent `spark-migration` skill via the Read tool
> (see its "Sub-skill Loading Convention" section) — it is **not**
> registered as a standalone top-level skill in the Cortex Code skill
> registry, by design, to avoid trigger collisions with its parent.
> Do not call `skill("snowpark-connect")`; if you reached this file
> outside of a `spark-migration` flow, start at `spark-migration` instead.

Skills for working with Snowpark Connect for Spark (SCOS) on Snowflake — supports Python, Scala, and Java workloads.

## When to Use

- User wants to assess a PySpark or Databricks workload for SCOS compatibility (without committing to migration)
- User wants to migrate PySpark, Databricks, Spark Scala, or Spark Java code to Snowflake
- User asks about SCOS or Snowpark Connect compatibility
- User wants to validate a completed SCOS migration
- User mentions "spark connect", "scos", "snowpark connect", "assess spark", or "migration readiness"

## Intent Detection

Determine the **intent** first, then the **language**, then route:

```
Start
  ↓
Analyze User Request
  ↓
Detect Intent
  ├─→ Assessment intent
  │     ↓
  │   Detect Language
  │     ├─→ Python (.py, PySpark, Databricks) → Load assess-pyspark-workload/SKILL.md
  │     ├─→ Scala (.scala, Spark Scala, build.sbt)
  │     │     → Standalone Scala assessment is not yet available. Inform the user, then
  │     │       load migrate-spark-scala-to-snowpark-connect/SKILL.md — it produces a
  │     │       readiness report at Phase 1a before making any code changes; ask whether
  │     │       to continue past Phase 1a or stop after the report.
  │     └─→ Java (.java, pom.xml, build.gradle)
  │           → Standalone Java assessment is not yet available. Inform the user, then
  │             load migrate-spark-java-to-snowpark-connect/SKILL.md — it produces a
  │             readiness report at Phase 1a before making any code changes; ask whether
  │             to continue past Phase 1a or stop after the report.
  │
  ├─→ Migration / Validation intent
  │     ↓
  │   Detect Language
  │     ├─→ Python (.py, PySpark, Databricks, pyspark)
  │     │     ├─→ Migration   → Load migrate-pyspark-to-snowpark-connect/SKILL.md
  │     │     └─→ Validation  → Load validate-pyspark-to-snowpark-connect/SKILL.md
  │     ├─→ Scala (.scala, Spark Scala, build.sbt)
  │     │     ├─→ Migration   → Load migrate-spark-scala-to-snowpark-connect/SKILL.md
  │     │     └─→ Validation  → Load validate-spark-scala-to-snowpark-connect/SKILL.md
  │     ├─→ Java (.java, pom.xml, build.gradle)
  │     │     ├─→ Migration   → Load migrate-spark-java-to-snowpark-connect/SKILL.md
  │     │     └─→ Validation  → Load validate-spark-java-to-snowpark-connect/SKILL.md
  │     └─→ Ambiguous → Ask the user which language the workload uses
  │
  └─→ Ambiguous intent → Ask: "Are you looking for a readiness assessment, or ready to start the migration?"
```

**Assessment intent** — explore compatibility or effort without committing to migration:
assess, analyze, scan, audit, review for compatibility, check compatibility, check my spark,
understand my spark, evaluate, migration readiness, readiness report, migration effort,
how hard is migration, how complex, migration scope, before I migrate, should I migrate,
databricks to snowflake, pyspark to snowflake, can my spark run on snowflake, pre-migration.

**Migration / Validation intent** — start, continue, or verify a conversion:
migrate, convert, rewrite, update imports, move to SCOS, fix, validate, verify, resume migration.

### Step 1: Detect Language

Determine the source language from:
- **Explicit mention**: "PySpark", "Python Spark", "Scala Spark", "Java Spark", "Spark Java"
- **File extensions**: `.py` → Python; `.scala` → Scala; `.java` → Java
- **Import patterns**: `from pyspark` / `import pyspark` → Python; `import org.apache.spark` with `.scala` → Scala; `import org.apache.spark` in a `.java` file → Java
- **Build files**: `requirements.txt` / `pyproject.toml` → Python; `build.sbt` → Scala; `pom.xml` / `build.gradle` / `build.gradle.kts` (without `.scala` files, or with `.java` files) → Java
- **Notebook primary language + cell distribution** (for notebook workloads):
  - For every notebook found by `notebook_io.scan_notebooks`, combine the
    notebook's primary `language` with a per-cell language count obtained from
    `notebook_io.parse_notebook`.
  - The **dominant language across all code cells** in the workload picks the
    migration sub-skill.

If the language cannot be determined, ask the user:
```
I detected Spark code in your workload. Which language is it written in?
- Python (PySpark / Databricks)
- Scala (Spark Scala)
- Java (Spark Java)
```

## Supported Notebook Formats

Both migration sub-skills process the following notebook formats natively via
the shared `scripts/notebook_io.py` module — no `jupyter nbconvert` required:

| Extension | Format                       | Notes                                         |
|-----------|------------------------------|-----------------------------------------------|
| `.ipynb`  | Standard Jupyter JSON        | Typically pretty-printed; kernel language in metadata |
| `.python` | Databricks native JSON       | Python-primary; `commands[]` array            |
| `.scala`  | Databricks native JSON       | Scala-primary; first byte `{`                 |
| `.scala`  | Databricks exported text     | First line `// Databricks notebook source`    |
| `.sql`    | Databricks native JSON       | SQL-primary; routed to whichever language has more code cells |
| `.py`     | Databricks exported text     | First line `# Databricks notebook source`     |

`.dbc` archives are automatically unpacked in Phase 0 and their contents flow
through the same scanner.

## Cross-Language Delegation

Databricks notebooks routinely mix languages via `%python`, `%scala`, `%sql`
magic lines. When the fixer in one sub-skill encounters a cell whose
`cell_language` differs from the sub-skill's primary language, it delegates
the single cell to the sibling sub-skill's fixer via `task()` in
`CELL_MODE=true` — the delegated agent returns the transformed cell source as
text, and the caller splices it back into the notebook. Markdown, SQL, R,
shell, FS, and `%run` cells are left untouched.

See `migrate-*/agents/fixer.md` — "Cross-Language Delegation" and
"CELL_MODE" sections — for protocol details.

## Phase 6 Handoff (Standalone Mode Only)

After a successful migration in **standalone** invocation (not via the
`snowflake-migration` orchestrator), each sub-skill offers an optional
handoff to `snowflake-notebook-migration` to convert the migrated notebooks
to Snowflake Workspace `.ipynb` format. The offer is skipped entirely when:

- the invocation context carries `snowpark_connect_invoker: orchestrator`, or
- the `snowflake-notebook-migration` skill is not installed (in which case an
  informational note is printed and the sub-skill exits cleanly).

### Step 2: Route by Intent

**Migration intent** — keywords: migrate, convert, rewrite, update imports, move to SCOS
**Validation intent** — keywords: validate, verify, check, test, review migration

### Route: Migrate PySpark to Snowpark Connect

**If user wants to migrate Python Spark code:**
- **Load** `migrate-pyspark-to-snowpark-connect/SKILL.md`
- Follow the migration workflow
- Uses EWI codes: `SPRKCNTPY*`
- References: `references/python/`

### Route: Migrate Spark Scala to Snowpark Connect

**If user wants to migrate Scala Spark code:**
- **Load** `migrate-spark-scala-to-snowpark-connect/SKILL.md`
- Follow the migration workflow
- Uses EWI codes: `SPRKCNTSCL*`
- References: `references/scala/`

### Route: Migrate Spark Java to Snowpark Connect

**If user wants to migrate Java Spark code:**
- **Load** `migrate-spark-java-to-snowpark-connect/SKILL.md`
- Follow the migration workflow
- Uses EWI codes: `SPRKCNTSCL*` (Java reuses the JVM/Scala family)
- References: `references/java/`

### Route: Validate a PySpark Migration

**If user wants to validate a completed Python migration:**
- **Load** `validate-pyspark-to-snowpark-connect/SKILL.md`
- Follow the validation workflow

### Route: Validate a Spark Scala Migration

**If user wants to validate a completed Scala migration:**
- **Load** `validate-spark-scala-to-snowpark-connect/SKILL.md`
- Follow the validation workflow

### Route: Validate a Spark Java Migration

**If user wants to validate a completed Java migration:**
- **Load** `validate-spark-java-to-snowpark-connect/SKILL.md`
- Follow the validation workflow

## Cross-Platform Compatibility

Every command this skill surfaces to the user runs on **macOS, Linux, and
Windows**. Follow the authoring rules in
[`skill_development/references/cross-platform.md`](../../../skill_development/references/cross-platform.md)
— specifically:

- **Primary entry point for every script: `uv run --project <SKILL_DIRECTORY> python <SKILL_DIRECTORY>/scripts/<name>.py`.**
  No `chmod`, no `source`, no activation — `uv` handles the venv on every OS.
- **Dual-install snippets for `uv` bootstrap** (see each `migrate-*`
  sub-skill's *uv Package Manager* section): show both the macOS/Linux
  `curl -LsSf … | sh` and the Windows PowerShell `irm … | iex` forms.
- **No user-facing Unix-only constructs**: `date +…`, `mkdir -p`,
  `cp -r`, `find -print0`, `xargs`, `$(…)` command substitution, raw
  `/tmp/` or `~/` paths. Use the portable Python helpers under `scripts/`
  instead:
  - `scripts/prepare_conversion_dirs.py` — timestamped folder + source copy + `.dbc` unpack
  - `scripts/revert_failing_files.py` — Phase-2 compile gate + git-revert + `__pycache__` cleanup
  - `scripts/run_scos_migration.py` — portable wrapper that ensures
    `generate_scos_reports.py` runs even when the migration agent is
    interrupted mid-workflow (replaces the deprecated `.sh`
    equivalent).
- **Sandbox-only bash is explicitly marked** with a comment
  `# Runs in the CoCo bash sandbox (Linux) — not portable` so readers
  know not to copy it into Windows `cmd.exe` or PowerShell.

When authoring new scripts or phases in this skill, pick the Python
helper-script pattern first. Fall back to the in-sandbox bash form only
when the work is genuinely bound to the CoCo Linux sandbox and rewriting
in Python would add disproportionate complexity — and even then, mark
the block with the sandbox comment above.

## Stopping Points

None — this skill routes to sub-skills. Stopping points are defined within each sub-skill.

## Output

Output is determined by the loaded sub-skill:
- **Python Migration**: Migrated `_scos` files with compatibility fixes, migration headers, and SCOS-compatible dashboard reports (`Reports/Issues.csv`, `Reports/InputFilesInventory.csv`, `Reports/ArtifactDependencyInventory.csv`) using `SPRKCNTPY*` codes
- **Scala Migration**: Migrated `_scos` files with compatibility fixes, migration headers, and SCOS-compatible dashboard reports using `SPRKCNTSCL*` codes
- **Java Migration**: Migrated `_scos` files with compatibility fixes, migration headers, and SCOS-compatible dashboard reports using `SPRKCNTSCL*` codes (JVM family)
- **Validation**: Validation report with pass/fail status for each check
