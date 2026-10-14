---
name: assess-pyspark-workload
description: |
  Standalone assessment of PySpark and Databricks workloads for Snowflake SCOS compatibility.
  Produces a MigrationReadinessReport.html and AssessmentIR.json without committing to migration.
  Use when: analyzing Spark workload complexity, generating a migration readiness report,
  understanding migration effort before deciding, evaluating PySpark compatibility with SCOS,
  assessing a Databricks workload for Snowflake, or producing a pre-migration feasibility report.
parent_skill: snowpark-connect
allowed-tools: Read, Write, Bash, Task
---

# Assess PySpark Workload — Coordinator

Scan a PySpark or Databricks workload, run SCOS compatibility analysis, and produce a
stakeholder-facing readiness report — without starting a migration. The report can be used as a
standalone deliverable or as the launchpad to continue into the full SCOS migration.

This coordinator runs phases 0 through 1.2 of the SCOS migration pipeline (workspace setup,
deterministic pre-processing, compatibility analysis, and report rendering) using the same phase
playbooks and scripts as the full migration coordinator. There is no separate assessment
implementation — the two coordinators share every phase file under `phases/` and every agent
under `agents/`.

## When to Load

[snowpark-connect] Intent Detection: After user indicates assessment or pre-migration analysis
intent (assess, readiness report, analyze workload, understand migration effort, evaluate
compatibility, check before migrating, how hard is migration, migration feasibility).

## Arguments

- `$ARGUMENTS` — Path to the PySpark file or directory to assess

## Placeholders

| Placeholder | Set by | Description |
|-------------|--------|-------------|
| `<SKILL_DIRECTORY>` | step 1 | Absolute path to `snowpark-connect/` (contains `pyproject.toml` and `scripts/`) |
| `<MIGRATION_DIR>` | step 1 | `<SKILL_DIRECTORY>/migrate-pyspark-to-snowpark-connect` — where phase playbooks and agents live |
| `<CONVERSION>` | Phase 0 | Timestamped workspace root, e.g. `<output_root>/Conversion-SCOS-<TS>` |
| `coordinator_mode` | Phase 0 | `false` for single-file; `true` for multi-file |

When a phase playbook references `../agents/<name>.md`, resolve it as `<MIGRATION_DIR>/agents/<name>.md`.
When it references `../SKILL.md`, resolve it as `<MIGRATION_DIR>/SKILL.md` (the migration coordinator).

## Prerequisites

### Skill Directory

`<SKILL_DIRECTORY>` is the `snowpark-connect/` directory that contains `pyproject.toml` and
`scripts/`. It is the parent of both this file's directory and `migrate-pyspark-to-snowpark-connect/`.
Resolve it as the grandparent of this `SKILL.md`. All script invocations use
`uv run --project <SKILL_DIRECTORY>`.

`<MIGRATION_DIR>` = `<SKILL_DIRECTORY>/migrate-pyspark-to-snowpark-connect`

### uv Package Manager

Install `uv` if not already available. Show both OS variants:

```bash
# macOS / Linux
uv --version || curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows (PowerShell)
uv --version; if ($LASTEXITCODE -ne 0) { irm https://astral.sh/uv/install.ps1 | iex }
```

## State Model

State lives in `<CONVERSION>/migration_state.json` — the same file and schema used by the
migration coordinator. The phase keys written here (`0_5_preprocess`, `1_analysis`,
`1a_assessment_report`, etc.) are the same keys the migration coordinator reads when it resumes
from an existing assessment. Do not invent alternative keys.

## Workflow

### Step 1: Collect Info

Ask for the two fields upfront in one message:

```
To run the assessment I need:

  1. Source path:   (PySpark file or directory to assess)
  2. Project name:  (used to label the report — defaults to the last path component)
```

Output path defaults to `<source_path>_scos`. Email and company are not needed for the
assessment report; leave them blank in `migration_state.json :: metadata` for now.

### Step 2: Phases 0 through 1.2

Run each phase in order by reading its playbook and following the instructions exactly.

> **Gate contract:** the same universal gate contract from the migration coordinator applies.
> Exit `0` = advance. Exit `2` = gap-scoped retry (3× for Phase 1.2, 2× for Phase 1).
> Exit `3` = stop and escalate.

#### Phase 0 — Workspace setup

Read `<MIGRATION_DIR>/phases/phase-0-setup.md` and follow it.

Pass: `$ARGUMENTS` = source path, `$OUTPUT` = `<output_root>`, `$PROJECT` = project name.
Email and company may be omitted from `migration_state.json :: metadata` at this stage.

#### Phase 0.5 + 0.6 — Deterministic pre-processing

Read `<MIGRATION_DIR>/phases/phase-0.5-preprocess.md` and follow it.
(This file covers both Phase 0.5 recipe pre-processing and the Phase 0.6 SQL rewrite.)

#### Phase 1 + 1.1 — Compatibility analysis and adjudication

Read `<MIGRATION_DIR>/phases/phase-1-analysis.md` and follow it.
(This file covers Phase 1 analysis and the conditional Phase 1.1 adjudication pass.)

#### Phase 1.2 — Assessment report

Read `<MIGRATION_DIR>/phases/phase-1.2-assessment.md` and follow it.
(This file covers Phase 1.2 report rendering and the optional Phase 1.3 data-edge enrichment.)

**Stop here.** Do not read or load Phase 2 playbooks.

### Step 3: Present the Report

After Phase 1.2 gate passes, present the result in chat:

```
✅ Assessment complete.

  Report:  <CONVERSION>/Reports/MigrationReadinessReport.html
  IR:      <CONVERSION>/Reports/AssessmentIR.json

Open the HTML report in a browser for the full five-tab view:
  Overview · Detailed Compatibility · Migration Plan · API Compatibility · Discovery
```

Describe the migration effort using the code-churn **categories** — Ready / Light Refactor /
Active Refactor — and the per-bucket file counts. **Never quote a numeric readiness score or
percentage** — the assessment is deliberately category-based.

### Step 4: Offer Migration Upgrade

Ask in one message:

```
Would you like to continue with the full SCOS migration from this assessment?
The workspace at <CONVERSION> is already set up — migration picks up at Phase 2
(code fixes, import updates, final reports). No re-scanning or re-processing.

Continue? (Y/n)
```

**Yes** — Read `<MIGRATION_DIR>/SKILL.md` and follow it, passing `$ARGUMENTS`, `$OUTPUT`,
  and `$PROJECT`. The migration coordinator's startup probe will discover the completed
  assessment at `<CONVERSION>` from disk and resume from Phase 2 automatically — no explicit
  handoff parameter needed. Email and company were not collected during assessment; the
  migration coordinator will prompt for them in Phase 4.

**No** — Exit cleanly:

```
The assessment is saved at <CONVERSION>.

To continue the migration later, re-invoke the SCOS migration skill
and point it at <source_path> — it will detect the existing assessment
and resume from Phase 2.

To re-run LLM data-edge enrichment:
  uv run --project <SKILL_DIRECTORY> \
    python <SKILL_DIRECTORY>/scripts/assessment/render_assessment.py \
    --llm-resolved-edges \
    --dump-ir <CONVERSION>/Reports/AssessmentIR.json \
    --output-html <CONVERSION>/Reports/MigrationReadinessReport.html
  (--dump-ir is required; without it the flag loads nothing and the report is static-only)
```

## Stopping Points

- After Step 1: confirm all settings with the user before Phase 0 executes (unconditional)
- After Step 1: if source path contains > 100 Python files, additionally confirm scale before proceeding
- After Phase 1 gate FAIL × 2: escalate to user before attempting a third retry
- After Phase 1.2 gate FAIL × 3: STOP and escalate — do not present a result without a valid report

## Output

```
<output_root>/
  Conversion-SCOS-<timestamp>/          ← <CONVERSION>
    Output/                             ← source copy (pre-recipe state in git tag phase-0-source)
    Reports/
      MigrationReadinessReport.html     ← stakeholder-facing five-tab readiness report
      AssessmentIR.json                 ← structured IR (stable contract for downstream tooling)
    migration_state.json                ← phase gate tracking (reusable if migration continues)
    analysis.json                       ← compatibility findings
```
