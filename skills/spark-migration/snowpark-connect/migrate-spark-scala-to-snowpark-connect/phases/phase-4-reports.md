# Phase 4: Generate Reports

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

### Phase 4: Generate Reports

**Generate the dashboard CSVs directly (no specialist agent)** — the CSV generator is deterministic and always emitted, so the coordinator runs it itself. The readiness HTML + IR were already rendered pre-fix in **Phase 1a** and are **not** regenerated here.

1. **Metadata:** `project`/`email`/`company` were collected in Phase 0 and live in `migration_state.json :: metadata`. Only if they are missing, prompt the user for them now (the coordinator is the right place for user interaction).

2. **CSV reports:**
   ```bash
   uv run --project <SKILL_DIRECTORY> \
     python <SKILL_DIRECTORY>/scripts/generate_scos_reports.py \
     --output-dir <CONVERSION> --analysis <CONVERSION>/analysis.json \
     --source-dir <original_source_path> --migrated-dir <MIGRATED> \
     --project-name "<project>" --email "<email>" --company "<company>" \
     --language scala
   ```
   Produces `Reports/{Issues,InputFilesInventory,ArtifactDependencyInventory}.csv`.
   Also annotates migrated source files inline: every `// SCOS:` comment gets its
   EWI code embedded (`// SCOS: [SPRKCNTSCL…] …`) via `annotate_scos_markers` —
   no separate bridge step is required.
   In `InputFilesInventory.csv`, only source code and build files are conversion
   units (`Ignored == "False"`); data/resource files (CSV, JSON, Parquet, txt, …)
   are inventoried but marked `Ignored == "True"` so they are not counted as
   migration work (code-vs-data split).

3. **Record the phase:** `"phases_completed": { "4_reports": {"status": "passed"} }`.

> `agents/reporter.md` is retained as human-readable reference for the report flow; the coordinator now runs the generator directly. The `MigrationReadinessReport.html` + `AssessmentIR.json` are produced in **Phase 1a**, not here.

**Verify (deterministic)**: run `verify_phase.py --phase 4` — covers all three CSVs present, InputFilesInventory row count, Issues.csv columns, and `SPRKCNTSCL` prefix:

```bash
python3 <SKILL_DIRECTORY>/scripts/verify_phase.py \
  --phase 4 --language scala --strict \
  --state <CONVERSION>/migration_state.json
echo "verify_phase4_exit=$?"
```

**Gate**: exit 0 ⇒ `PASS` or `PASS_WITH_GAPS`. On exit 1 (`FAIL`), re-run the reporter, then re-run the verifier. Update `migration_state.json` phase to 4.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 4: reports generated"`

### Phase 4a: Post-Run State Validation (MUST RUN)

**This phase MUST run as the last deterministic step of every migration.** It
asserts that every required phase (0.5, 1, 2, 2a, 2b, 3, 4) recorded evidence in
`migration_state.json` — either via the canonical `phases_completed[<key>]`
block or via the documented legacy top-level field. Silent skips become loud
failures here, before the user is offered validation.

The validator script is pure stdlib (no third-party deps), so invoke it
directly with `python3` — no `uv run` needed:

```bash
python3 <SKILL_DIRECTORY>/scripts/validate_migration_state.py \
  --strict \
  --language scala \
  --state <CONVERSION>/migration_state.json
echo "validator_exit=$?"
```

**Hard gate (all must be true):**

1. The script exits 0 (no required phase missing or skipped without reason).
2. The printed report shows `PASS: all required phases present.`.

The required phase set for Scala is:
`{0_5b_scalafix, 1_analysis, 1a_assessment_report, 2_fixes, 2a_fallback, 2b_compilation, 3_imports, 4_reports}`.

If the script exits non-zero, do NOT advance to Phase 5. Read the listed
missing phase(s), re-run the corresponding phase, and re-invoke the validator
until it passes. If a phase genuinely cannot run, edit `migration_state.json`
to set:

```json
"phases_completed": {
  "<phase_key>": {
    "status": "skipped",
    "skip_reason": "<one-line reason>"
  }
}
```

and re-run the validator. Skipped-with-reason is the only acceptable form of
non-completion. Skipping without a `skip_reason` always fails the gate.

**Then record the self-attestation** — after the validator exits 0, append a
`phases_completed["4a_validation"]` entry to `migration_state.json` so future
readers can tell from the state file alone that Phase 4a ran:

```json
"phases_completed": {
  "4a_validation": {
    "status": "passed",
    "validator_exit_code": 0,
    "validator_run_at": "<ISO-8601 UTC timestamp>"
  }
}
```

This entry is **optional** to the validator (it does not fail strict mode if
absent), but **required** by this SKILL — without it, downstream tooling
cannot confirm Phase 4a actually executed without parsing the transcript.

For machine-readable output (e.g. when wrapping in CI), pass `--json` instead
of the default human report.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 4a: validation passed"`

### Phase 4b: Generate Migration Feedback File (Non-Fatal)

Run the migration feedback generator to produce the file the FDE attaches to a
Jira ticket for triage:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/generate_migrate_feedback.py \
  --conv-root <CONVERSION>
```

Output: `<CONVERSION>/Feedback/migrate_gaps.md`

The script reads `Reports/Issues.csv`, filters to rows requiring human
intervention (Status = `Error` or `IO` for the new CSV format; Category ≠
`Information`/`Warning` for legacy), extracts a redacted code snippet per
issue from `Output/`, and writes a redacted Markdown summary safe to attach to
a Jira ticket.

**Non-fatal**: if the script fails or `Reports/Issues.csv` is absent, log a
warning and continue. Do not gate Phase 5 on this step.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 4b: migration feedback file generated"`
