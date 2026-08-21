# Reporter Agent — Phase 1a & Phase 4 Specialist

Generate the early (pre-fix) stakeholder readiness report (Phase 1a), drive the
LLM data-edge resolution convergence loop, and generate the final SMA-compatible
CSV reports + readiness HTML (Phase 4) for a Scala migration.

## Phase 1a: Render Early Stakeholder Readiness Report (pre-fix)

Run **before** Phase 2 (the fixer) so stakeholders get an early snapshot of the
migration risk surface. This is the same render as Phase 4's HTML, but against
the pre-fix `analysis.json` and source directory.

### Step 1a.0: Create the `phase-0-source` git tag

After Phase 0 copies the source into the conversion folder and initializes git,
tag the source snapshot so the assessment render can rebase analyzer line
numbers onto the original source:

```bash
cd <CONVERSION_ROOT> && git tag phase-0-source
```

This tag is the reference point for `--migration-state-json` line-number
rebasing (see Step 1a.1) and for the auto-resolved recipe panel.

### Step 1a.1: Render the assessment report

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/assessment/render_assessment.py \
  --language scala \
  --project "<project>" \
  --analysis-json <CONVERSION_ROOT>/analysis.json \
  --workload-dir <source_dir> \
  --migration-state-json <CONVERSION_ROOT>/migration_state.json \
  --output-html <CONVERSION_ROOT>/Reports/MigrationReadinessReport.html \
  --dump-ir <CONVERSION_ROOT>/Reports/AssessmentIR.json
```

Key flags:
- `--language scala` — Scala-specific extraction heuristics.
- `--migration-state-json <path>` — reads the `phase-0-source` git tag and the
  `recipe_edits` block from `migration_state.json`, rebases analyzer line numbers
  onto the original source, and populates the auto-resolved recipe panel. When
  omitted, the render uses `--workload-dir` directly (no rebasing, no
  auto-resolved panel).

If `<source_dir>` is unavailable, fall back to `--workload-dir <MIGRATED_DIR>`.
If `analysis.json` does not exist, surface that as a `FAIL` — the migration flow
always produces an analysis; do NOT silently emit an empty report.

### Step 1a.2: LLM data-edge resolution convergence loop

If the IR's `unresolved_data_edges` is non-empty, run the `data_edge_resolver`
agent (`agents/data_edge_resolver.md`) to resolve each edge, then re-render with
the resolved edges injected:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/assessment/render_assessment.py \
  --language scala \
  --project "<project>" \
  --analysis-json <CONVERSION_ROOT>/analysis.json \
  --workload-dir <source_dir> \
  --migration-state-json <CONVERSION_ROOT>/migration_state.json \
  --llm-resolved-edges \
  --narratives-inline-json '<narratives JSON from Step 1a.2a>' \
  --output-html <CONVERSION_ROOT>/Reports/MigrationReadinessReport.html \
  --dump-ir <CONVERSION_ROOT>/Reports/AssessmentIR.json
```

- `--llm-resolved-edges` — reads the `llm_resolved_data_edges` block the resolver
  wrote into the IR and augments the data DAG with the resolved edges (resolved,
  unresolvable, and orchestration edges), moving matched `unresolved_data_edges`
  rows into the audited tables.
- `--narratives-inline-json` — writes the LLM's advisory narratives alongside the
  IR so the gate can audit them.

**Convergence loop:** the resolver writes its output, then the coverage gate
(`scripts/assessment/check_data_edges_gate.py`) validates it. The gate exits:
- `0` — every unresolved edge is accounted for. Convergence achieved; stop.
- `2` — edge gaps: some `unresolved_data_edges` rows were not matched by the
  resolver (a `(file, line, kind)` triple mismatch). Re-invoke the resolver on
  **only** the files/edges the gate names, reusing each item's **exact
  `(file, line, kind)`** (a mismatched line is the usual leak cause). Repeat.
- `3` — schema errors in the resolver's output. Fix the schema violation and
  re-run.

**Stuck-round detection:** if the same set of edges fails to converge for 3
consecutive rounds (the gap set is identical across rounds), stop the loop and
record a checkpoint:

```json
{
  "1a_data_edge_resolution": {
    "status": "stuck",
    "rounds": 3,
    "unresolved_count": <N>,
    "failure_categories": ["<cat1>", "<cat2>"],
    "checkpoint": {
      "round": 3,
      "unresolved_files": ["<file1>", "<file2>"],
      "unresolved_edges": [{"file": "...", "line": N, "kind": "..."}],
      "last_error": "<gate message>",
      "resolved_count": <M>,
      "unresolvable_count": <K>,
      "elapsed_seconds": <T>,
      "model": "<model id>",
      "dispatch_units": <D>,
      "last_gate_exit": 2
    }
  }
}
```

The 5 failure categories to classify stuck edges into:
1. **`line_mismatch`** — the resolver's `(file, line)` does not match the IR's
   `unresolved_data_edges` row (off-by-one or wrong file).
2. **`runtime_only`** — the edge's argument is a pure runtime value
   (`args(0)`, env var without default) that genuinely cannot be traced.
3. **`missing_file`** — the referenced file is not in the workload export
   (caller scripts missing).
4. **`schema_violation`** — the resolver's output does not satisfy
   `llm_resolved_data_edges.schema.json`.
5. **`dead_code`** — the edge is in unreachable code (a function with no call
   sites).

### Step 1a.2a: Build inline narratives from current `AssessmentIR.json`

Read the IR **as it stands now** — post-LLM if resolution ran, pre-LLM
otherwise. This ensures counts (e.g. resolved edge count, unresolved count)
in the narrative text reflect the actual state of the report.

Keep each explanation to 1-2 sentences, customer-readable, and strictly
advisory. Never invent facts not supported by the IR.

If a section's supporting evidence is absent, empty, or non-informative
(for example: `complex_patterns` is empty, `project_type.label` is blank, or
`workload_classification.classification` is `Unknown`), leave that narrative
field empty (`""`) or omit the key. The renderer will apply a deterministic
fallback for that section.

```json
{
  "executive_summary": "<2-3 sentences for a non-technical stakeholder synthesising workload scale, migration path, file readiness, and any critical risk signals. Omit if classification or churn data is absent.>",
  "complex_patterns": "<1-2 grounded sentences>",
  "workload_classification": "<1-2 grounded sentences>",
  "project_type": "<1-2 grounded sentences>",
  "code_churn": "<1-2 grounded sentences>"
}
```

---

### Step 1a.3: Update gate file

Update `migration_state.json` with the Phase 1a result so
`validate_migration_state.py` recognizes the early report:

```json
{
  "phases_completed": {
    "1a_assessment_report": {
      "status": "passed",
      "html": "Reports/MigrationReadinessReport.html",
      "ir": "Reports/AssessmentIR.json",
      "data_edges_resolved": <true|false>,
      "convergence_rounds": <N>
    }
  }
}
```

`1a_assessment_report` is a **required** phase key for Scala
(see `scripts/validate_migration_state.py` → `REQUIRED_PHASES_SCALA`). The
Phase 4a strict gate fails if it is missing. This is the early (pre-fix)
attestation; Phase 4 re-renders the HTML after fixes with the final data.

---

## Phase 4: Final Reports

## Inputs

Read `migration_state.json` to get:
- `conversion_root` — where `Reports/` and `Logs/` directories exist
- `migrated_dir` — directory with migrated files (for scanning `// SCOS:` comments)
- `skill_directory` — for `uv run --project`
- `metadata` — email, company, project name

The `analysis.json` file is in the conversion root.

## Step 1: Collect Metadata

If metadata (project, email, company) is missing from `migration_state.json`, ask the user:
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
  --language scala
```

**Note**: The `--language scala` flag ensures the report generator scans for `// SCOS:` comments (Scala comment syntax) and uses `SPRKCNTSCL*` EWI code prefixes.

## Step 3: Verify Reports

```bash
ls <CONVERSION_ROOT>/Reports/Issues.csv \
   <CONVERSION_ROOT>/Reports/InputFilesInventory.csv \
   <CONVERSION_ROOT>/Reports/ArtifactDependencyInventory.csv
```

All three files must exist.

## Step 4: Render Migration Readiness HTML

Phase 1 already produced `<CONVERSION_ROOT>/analysis.json`, and the migrate
skill has the original source directory available as `<source_dir>` from
`migration_state.json`. Both feed the readiness report:

* The deterministic codebase scanner walks `<source_dir>` to populate file
  types, library imports, complex patterns, data sources, dependency graph,
  and migration waves.
* The analyzer transformer reads `<analysis.json>` for risk-scored findings,
  the EWI Issue Summary rollup, and per-file readiness statuses.

The two are merged into a single IR (`AssessmentIR.json`) and rendered to
HTML matching the five-tab prototype layout. This step is **always run** —
do not prompt the user, and do not skip even when the analysis is empty.

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/assessment/render_assessment.py \
  --language scala \
  --project "<project>" \
  --analysis-json <CONVERSION_ROOT>/analysis.json \
  --workload-dir <source_dir> \
  --migration-state-json <CONVERSION_ROOT>/migration_state.json \
  --output-html <CONVERSION_ROOT>/Reports/MigrationReadinessReport.html \
  --dump-ir <CONVERSION_ROOT>/Reports/AssessmentIR.json
```

`--migration-state-json` rebases analyzer line numbers onto the original source
(via the `phase-0-source` git tag) and populates the auto-resolved recipe panel
from `recipe_edits` — the same mechanism Phase 1a uses, but now over the
post-fix `analysis.json`. When omitted, the render uses `--workload-dir`
directly (no rebasing, no auto-resolved panel).

If `<CONVERSION_ROOT>/analysis.json` does not exist (which would mean Phase 1
was skipped), surface that as a `FAIL` to the coordinator — the migration
flow always produces an analysis. Do NOT silently emit an empty report.

If `<source_dir>` is somehow unavailable, fall back to `--workload-dir
<MIGRATED_DIR>` (still better than nothing — the migrated output reflects the
same code shape). The HTML's empty-state placeholders cover the missing-scan
case gracefully, but the prototype's full structure (Overview tiles, File
Type Summary, Migration Waves, etc.) needs the scan to populate.

The HTML is self-contained (CSS + JS inlined) and renders directly in any
browser. The IR JSON is the stable contract — downstream tooling that wants
structured access to the analyzer + codebase data should consume it instead
of scraping the HTML.

## Step 5: Update Gate File

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
- HTML readiness report + IR JSON in `<CONVERSION_ROOT>/Reports/`
- Log file in `<CONVERSION_ROOT>/Logs/`
- Updated `migration_state.json`
