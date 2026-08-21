# Reporter Agent — Reporting Specialist

> **Execution model:** the reporting phases (1a and 4) are run **inline by the
> coordinator** on single-file / small workloads (`coordinator_mode == false`) —
> it reads this file and follows the relevant section directly rather than
> spawning a `task()` sub-agent, since both sections are deterministic generator
> scripts wrapped in at most a little bounded judgment (four advisory narrative
> sentences in Section A; none in Section B). On multi-file workloads
> (`coordinator_mode == true`) each section **is** spawned as a `task()`
> sub-agent so the `AssessmentIR.json` read and report output stay out of the
> coordinator's context window. The procedures below are identical either way;
> "agent" names the role, whoever performs it.

This agent has two independently-invoked responsibilities:

1. **Assessment Report (Phase 1.2)** — the stakeholder-facing HTML readiness
   report. Rendered in **Phase 1.2**, as soon as `analysis.json` exists and
   before any migration runs. Depends only on Phase 1 output and the original
   source; has **no** dependency on the migrated files.
2. **Dashboard CSVs (Phase 4)** — `Issues.csv`, `InputFilesInventory.csv`,
   `ArtifactDependencyInventory.csv`. Generated at **Phase 4** from the final
   migrated files (they read the inline `# SCOS:` markers the fixer embedded).

The coordinator invokes the section matching the current phase. Run **only**
that section's steps.

## Inputs

Read `migration_state.json` to get:
- `conversion_root` — where `Reports/`, `Logs/`, `migration_state.json`, and
  `analysis.json` live. The `phase-0-source` git tag created in Phase 0
  also lives in this repo.
- `migrated_dir` — `<conversion_root>/Output/`. **Only relevant to Phase 4
  CSVs** (Section B). **Phase 1.2 does NOT scan this directory**.
- `skill_directory` — for `uv run --project`
- `metadata` — email, company, project name (only required for the Phase 4 CSVs)

---

# Section A — Assessment Report (Phase 1.2)

Render the stakeholder-facing migration-readiness HTML. This runs in Phase 1.2.

## A.1: Inputs check

Phase 1 already produced `<CONVERSION_ROOT>/analysis.json`. Phase 0 tagged the
customer's UNMODIFIED source as `phase-0-source` before Phase 0.5 recipes
rewrote `<CONVERSION_ROOT>/Output/`. The Phase 1.2 report describes the
PRE-Phase-0.5 source — pass `--migration-state-json
<CONVERSION_ROOT>/migration_state.json` and the renderer materializes the
`phase-0-source` tag.

Both feed the readiness report:

* The deterministic codebase scanner walks the materialized pre-Phase-0.5
  source to populate file types, library imports, complex patterns, data
  sources, the dependency graph, and migration waves.
* The analyzer transformer reads `analysis.json` for risk-scored findings, the
  EWI Issue Summary rollup, and per-file readiness statuses.

The two are merged into a single IR (`AssessmentIR.json`) and rendered to HTML
matching the five-tab prototype layout. This step is **always run** — do not
prompt the user, and do not skip even when the analysis is empty. Only the
`--project` name is needed here (from `migration_state.json :: metadata`, or
`unknown-project` if absent); email/company are not used by the HTML.

If `<CONVERSION_ROOT>/analysis.json` does not exist (Phase 1 was skipped),
surface that as a `FAIL` to the coordinator. Do NOT silently emit an empty
report.

## A.2: Render

**At most 2 render calls total** — one to seed the IR, one final render that
folds in narratives (and LLM data if the user opted in).  The LLM step, if
run, sits between the two renders and updates the IR in place; the final render
picks up the post-LLM state automatically via `--llm-resolved-edges`.

### Step 1: Seed render (always)

Produces the IR and an initial HTML for the coordinator to review.

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/assessment/render_assessment.py \
  --project "<project>" \
  --analysis-json <CONVERSION_ROOT>/analysis.json \
  --migration-state-json <CONVERSION_ROOT>/migration_state.json \
  --output-html <CONVERSION_ROOT>/Reports/MigrationReadinessReport.html \
  --dump-ir <CONVERSION_ROOT>/Reports/AssessmentIR.json
```

`--migration-state-json` drives the pre-Phase-0.5 behaviour: the renderer
materializes the `phase-0-source` tag into a temp dir for the deterministic
scan + per-finding rebase, and reads `recipe_edits` for the auto-resolved panel.

### Step 1b: LLM data-edge resolution (runs only when the user opted in)

The static scanner produces a partial data graph: it finds many I/O call sites
but cannot always resolve dynamic path arguments, and it does not see patterns
it was not built to recognise (boto3, SQL template files, config-driven paths,
etc.).  The LLM resolution step fills the gap by reading **every** source file
in the workload and tracing all data dependencies to external dead-ends.

**The decision to run this is owned by the coordinator, not this procedure.**
SKILL.md **Phase 1.3** stops after the seed render, reports the incompleteness
counts (`N` unresolved read/write edges + `M` unresolved dynamic imports) with a
"~5+ minutes, scales with workload size" disclaimer, and asks the user Y/n — it
must, because a spawned reporter sub-agent cannot prompt the user.  Do **not**
re-prompt here.  This section is the **execution procedure** the coordinator
follows once the user has said yes.

**LLM resolution loop (runs until convergence):**

**Round 1 — full workload scan:**

  Follow `agents/data_edge_resolver.md` with:
  - `assessmentir_path`: `<CONVERSION_ROOT>/Reports/AssessmentIR.json`
  - `workload_dir`: `<CONVERSION_ROOT>/Output`

  The resolver reads every file in `workload_dir` and writes back
  `llm_resolved_data_edges` (including `analyzed_files` and `excluded_files`).

  Run the gate immediately after the agent writes back:

  ```bash
  uv run --project <SKILL_DIRECTORY> \
    python <SKILL_DIRECTORY>/scripts/assessment/check_data_edges_gate.py \
    <CONVERSION_ROOT>/Reports/AssessmentIR.json \
    <CONVERSION_ROOT>/Output
  ```

  Gate outcomes:
  - **Exit 0** — every file covered and reconciled; write checkpoint (see
    below), proceed to Step 2.
  - **Exit 2** — one or more failure categories is present in the JSON. Collect
    **whichever appear** (a single exit 2 can carry several):
    - `schema_errors` — entries that violate the JSON Schema (missing required
      field, wrong type, bad enum). The gate reports these first and alone, so
      fix them before it re-checks coverage/reconciliation.
    - `gaps` — files in `workload_dir` neither analyzed nor excluded.
    - `edge_gaps` — files in `analyzed_files` with zero edges/unresolvable entries.
    - `data_leaks` — `unresolved_data_edges` sites neither resolved nor confirmed unresolvable.
    - `import_leaks` — `unresolved_dynamic_imports` sites not accounted for.
    Proceed to the next round. Do **not** look only at `gaps` — an exit 2 with
    empty `gaps` but non-empty `schema_errors` / `edge_gaps` / `data_leaks` /
    `import_leaks` is a real failure that still needs another resolver round.
  - **Exit 3** — IO/parse error; STOP and escalate to the user immediately.
    Do not retry.

**Subsequent rounds (exit 2 only) — focused re-analysis:**

  Re-follow `agents/data_edge_resolver.md`, passing **every** non-empty failure
  category from the gate output as focused input, each with the gate's own
  message for that category (`schema_error_message`, `gap_message`,
  `edge_gap_message`, `data_leak_message`, `import_leak_message`). For example:

  > "The gate is not yet satisfied. Fix exactly these, reusing each item's exact
  > `(file, line, kind)`, and merge into the existing `llm_resolved_data_edges`:
  > - Schema violations (fix field/type/enum): `<schema_errors JSON>`
  > - Uncovered files (add to analyzed_files/excluded_files): `<gaps JSON>`
  > - Analyzed files with no edges (SQL files executed by Python must have their
  >   OWN edges — `file` = the .sql path): `<edge_gaps JSON>`
  > - Unresolved data edges to resolve or confirm-unresolvable: `<data_leaks JSON>`
  > - Unresolved dynamic imports to resolve or confirm-unresolvable: `<import_leaks JSON>`"

  (Omit any category the gate did not report.) Run the gate again after the
  agent writes back.

  Gate outcomes:
  - **Exit 0** — full coverage + reconciliation; write checkpoint, proceed to Step 2.
  - **Exit 2** — items still outstanding. Track progress by the **total
    outstanding count** across all categories combined (`schema_error_count +
    gap_count + edge_gap_count + data_leak_count + import_leak_count`), not by
    `gaps` alone:
    - **Decreased** (progress was made) → continue with another round.
    - **Unchanged** (stuck) → after 2 consecutive stuck rounds, write the
      `warned` checkpoint (see below) and **continue** to Step 2 — render with
      the partial data. Do not warn the user here; the coordinator reports the
      outcome after the final render (SKILL.md Phase 1.3).
  - **Exit 3** — STOP and escalate immediately.

  There is no fixed maximum round count.  The loop terminates when the gate
  exits 0 (all files covered and reconciled) or when the total outstanding count
  does not shrink for 2 consecutive rounds.

**Checkpoint — write after every LLM resolution attempt** (success or warning):

Read `llm_resolved_data_edges` from `Reports/AssessmentIR.json` and merge this
block into `migration_state.json :: phases_completed`:

```json
"1b_data_edge_resolution": {
  "status": "passed",
  "ran_at": "<ISO-8601 UTC timestamp>",
  "rounds": <total rounds run>,
  "resolved_count": <edges[source=resolved_unresolved] count>,
  "unresolvable_count": <unresolvable_edges count>,
  "newly_discovered_count": <edges[source=newly_discovered] count>,
  "imports_resolved_count": <resolved_imports with targets count>,
  "imports_unresolvable_count": <resolved_imports[resolution_type=unresolvable] count>,
  "data_edges_accounted": "<from gate summary, e.g. 21/21>",
  "imports_accounted": "<from gate summary, e.g. 11/11>",
  "gap_count": 0
}
```

When stuck after 2 consecutive rounds, set `"status": "warned"` and
`"gap_count"` to the number of uncovered files.  (The coordinator records
`"skipped"` / `"not_needed"` when the user declined or there was nothing to
enrich — see SKILL.md Phase 1.3; you only reach this procedure on "yes".)  After
the final render, the coordinator reports the enrichment outcome to the user
(SKILL.md Phase 1.3) — do not duplicate it here.

### Step 2: Build inline narratives from current `AssessmentIR.json`

Read the IR **as it stands now** — post-LLM if resolution ran, pre-LLM
otherwise.  This ensures counts (e.g. resolved edge count, unresolved count)
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

### Step 3: Final render (always — replaces Step 1 HTML)

Include `--llm-resolved-edges` when LLM resolution was run, regardless of gate
outcome (`passed` or `warned`).  Partial LLM data is better than none — the
HTML clearly shows what was resolved, what was confirmed unresolvable, and what
remains unclassified.

**With LLM resolution:**

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/assessment/render_assessment.py \
  --project "<project>" \
  --analysis-json <CONVERSION_ROOT>/analysis.json \
  --migration-state-json <CONVERSION_ROOT>/migration_state.json \
  --llm-resolved-edges \
  --narratives-inline-json '<narratives JSON from Step 2>' \
  --output-html <CONVERSION_ROOT>/Reports/MigrationReadinessReport.html \
  --dump-ir <CONVERSION_ROOT>/Reports/AssessmentIR.json
```

**Without LLM resolution:**

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/assessment/render_assessment.py \
  --project "<project>" \
  --analysis-json <CONVERSION_ROOT>/analysis.json \
  --migration-state-json <CONVERSION_ROOT>/migration_state.json \
  --narratives-inline-json '<narratives JSON from Step 2>' \
  --output-html <CONVERSION_ROOT>/Reports/MigrationReadinessReport.html \
  --dump-ir <CONVERSION_ROOT>/Reports/AssessmentIR.json
```

## A.3: Verify Report

Confirm the render produced both artifacts (mirrors Section B.3 for the CSVs):

```bash
ls <CONVERSION_ROOT>/Reports/MigrationReadinessReport.html \
   <CONVERSION_ROOT>/Reports/AssessmentIR.json
```

Both files must exist. **Deep validation — clean render, no unsubstituted Jinja
placeholders — is owned by the deterministic `scos_gates.py reports --section
assessment` gate (run by the coordinator in Phase 1.2). Do not duplicate it
here.**

The HTML is self-contained (CSS + JS inlined) and renders in any browser. The
IR JSON is the stable contract — downstream tooling that wants structured
access to the analyzer + codebase data should consume it instead of scraping
the HTML.

## A.4: Update Gate File

Record the Phase 1.2 milestone in `migration_state.json` so the Phase 4a
validator (`validate_migration_state.py`, which lists `1a_assessment_report`
as a required phase) can confirm it ran:

```json
"phases_completed": {
  "1a_assessment_report": {"status": "passed"}
}
```

## A.5: Output

```
Reports/MigrationReadinessReport.html    — Stakeholder-facing readiness report
Reports/AssessmentIR.json                — Structured IR for downstream tooling
```

---

# Section B — Dashboard CSVs (Phase 4)

Generate SCOS-compatible CSV reports for the dashboard from the final migrated
files.

## B.1: Collect Metadata

If metadata (project, email, company) is missing from `migration_state.json`, ask the user:
```
To generate dashboard reports, I need some project information:
1. Project name:
2. Customer email:
3. Customer company:
```

## B.2: Run Report Generator

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
  --language python
```

## B.3: Verify Reports

```bash
ls <CONVERSION_ROOT>/Reports/Issues.csv \
   <CONVERSION_ROOT>/Reports/InputFilesInventory.csv \
   <CONVERSION_ROOT>/Reports/ArtifactDependencyInventory.csv
```

All three files must exist.

## B.4: Update Gate File

Update `migration_state.json` with phase 4 status.

Report:
```
Reports generated:
  Reports/Issues.csv                       — EWI issues with SCOS codes
  Reports/InputFilesInventory.csv          — Source file inventory
  Reports/ArtifactDependencyInventory.csv  — Import dependencies
```

(`MigrationReadinessReport.html` and `AssessmentIR.json` were already produced
in Phase 1.2 — see Section A.)

## Output

- CSV reports in `<CONVERSION_ROOT>/Reports/`
- Log file in `<CONVERSION_ROOT>/Logs/`
- Updated `migration_state.json`

## Notebook File Handling

When generating Issues.csv entries for notebook files:
- `FileId` = relative path to the .ipynb file (e.g., `notebooks/etl.ipynb`)
- `Line` = `cell:<cell_index>:<line_within_cell>` format (e.g., `cell:2:5`)
- Include cell context in the issue description for clarity
