# Reporter Agent — Phase 4 Specialist (Java)

Generate SMA-compatible CSV reports and the stakeholder-facing assessment HTML
for a Java migration. This agent has two independently-invoked responsibilities:

1. **Assessment Report (Phase 1a)** — the stakeholder-facing HTML readiness
   report. Rendered in **Phase 1a**, as soon as `analysis.json` exists and
   before any migration runs. Depends only on Phase 1 output and the original
   source.
2. **Dashboard CSVs (Phase 4)** — `Issues.csv`, `InputFilesInventory.csv`,
   `ArtifactDependencyInventory.csv`. Generated at **Phase 4** from the final
   migrated files.

The coordinator invokes the section matching the current phase. Run **only**
that section's steps.

## Inputs

Read `migration_state.json` to get:
- `conversion_root` — where `Reports/` and `Logs/` directories exist
- `migrated_dir` — directory with migrated `.java` files
- `skill_directory` — for `uv run --project`
- `metadata` — email, company, project name

---

# Section A — Assessment Report (Phase 1a)

Use a two-step render flow:

1. First render produces the canonical report + IR.
2. Read `Reports/AssessmentIR.json`, build inline narratives JSON in memory.
3. Re-render with `--narratives-inline-json` to overwrite the same HTML path.

## A.1: Initial Render (produce IR)

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/assessment/render_assessment.py \
  --language java \
  --project "<project>" \
  --analysis-json <CONVERSION_ROOT>/analysis.json \
  --migration-state-json <CONVERSION_ROOT>/migration_state.json \
  --output-html <CONVERSION_ROOT>/Reports/MigrationReadinessReport.html \
  --dump-ir <CONVERSION_ROOT>/Reports/AssessmentIR.json
```

## A.2: Build Inline Narratives from `AssessmentIR.json`

Read `Reports/AssessmentIR.json` and generate 1-2 sentence customer-readable
narratives for each section. Keep explanations strictly advisory and grounded
in the IR. If a section's supporting evidence is absent, empty, or
non-informative, leave that narrative field empty (`""`).

```json
{
  "complex_patterns": "<1-2 grounded sentences>",
  "workload_classification": "<1-2 grounded sentences>",
  "project_type": "<1-2 grounded sentences>",
  "code_churn": "<1-2 grounded sentences>"
}
```

## A.3: Final Render

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/assessment/render_assessment.py \
  --language java \
  --project "<project>" \
  --analysis-json <CONVERSION_ROOT>/analysis.json \
  --migration-state-json <CONVERSION_ROOT>/migration_state.json \
  --narratives-inline-json '<JSON object above>' \
  --output-html <CONVERSION_ROOT>/Reports/MigrationReadinessReport.html \
  --dump-ir <CONVERSION_ROOT>/Reports/AssessmentIR.json
```

## A.4: Update Gate File

```json
"phases_completed": {
  "1a_assessment_report": {"status": "passed"}
}
```

---

# Section B — Dashboard CSVs (Phase 4)

## Step 1: Collect Metadata

If metadata is missing from `migration_state.json`, ask the user:
```
To generate dashboard reports, I need some project information:
1. Project name:
2. Customer email:
3. Customer company:
```

## Step 2: Run Report Generator

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/generate_scos_reports.py \
  --output-dir <CONVERSION_ROOT> \
  --analysis <CONVERSION_ROOT>/analysis.json \
  --source-dir <original_source_path> \
  --migrated-dir <MIGRATED_DIR> \
  --project-name "<project>" \
  --email "<email>" \
  --company "<company>" \
  --language java
```

**Note**: The `--language java` flag ensures the report generator scans for `// SCOS:` comments
(Java comment syntax) and uses `SPRKCNTSCL*` EWI code prefixes (Java reuses the JVM family).

## Step 3: Verify Reports

```bash
ls <CONVERSION_ROOT>/Reports/Issues.csv \
   <CONVERSION_ROOT>/Reports/InputFilesInventory.csv \
   <CONVERSION_ROOT>/Reports/ArtifactDependencyInventory.csv
```

All three files must exist.

## Step 4: Update Gate File

Update `migration_state.json` with phase 4 status.

Report:
```
Reports generated:
  Reports/Issues.csv                       — EWI issues with SPRKCNTSCL* codes
  Reports/InputFilesInventory.csv          — Source file inventory
  Reports/ArtifactDependencyInventory.csv  — Import dependencies
  Reports/MigrationReadinessReport.html    — Stakeholder-facing readiness report
  Reports/AssessmentIR.json                — Structured IR for downstream tooling
```

## Output

- CSV reports in `<CONVERSION_ROOT>/Reports/`
- HTML readiness report + IR JSON
- Updated `migration_state.json`
