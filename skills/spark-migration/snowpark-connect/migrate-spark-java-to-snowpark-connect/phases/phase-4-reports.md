# Phase 4: Generate Reports

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

### Phase 4: Generate Reports

**Generate the dashboard CSVs directly (no specialist agent)**:
```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/generate_scos_reports.py \
  --output-dir <CONVERSION> --analysis <CONVERSION>/analysis.json \
  --source-dir <original_source_path> --migrated-dir <MIGRATED> \
  --project-name "<project>" --email "<email>" --company "<company>" \
  --language java
```

Produces `Reports/{Issues,InputFilesInventory,ArtifactDependencyInventory}.csv`.

Record: `"phases_completed": { "4_reports": {"status": "passed"} }`.

> `agents/reporter.md` is retained as human-readable reference.

**Verify (deterministic)**:
```bash
python3 <SKILL_DIRECTORY>/scripts/verify_phase.py \
  --phase 4 --language java --strict \
  --state <CONVERSION>/migration_state.json
echo "verify_phase4_exit=$?\""
```

**Gate**: exit 0 ⇒ `PASS` or `PASS_WITH_GAPS`. On exit 1 (`FAIL`), re-run the reporter, then re-run the verifier.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 4: reports generated"`

### Phase 4a: Post-Run State Validation (MUST RUN)

**This phase MUST run as the last deterministic step of every migration:**

```bash
python3 <SKILL_DIRECTORY>/scripts/validate_migration_state.py \
  --strict \
  --language java \
  --state <CONVERSION>/migration_state.json
echo "validator_exit=$?"
```

**Hard gate (all must be true):**
1. The script exits 0.
2. The printed report shows `PASS: all required phases present.`

The required phase set for Java is:
`{0_5c_javaparser, 1_analysis, 1a_assessment_report, 2_fixes, 2a_fallback, 2b_compilation, 2c_verification, 3_imports, 4_reports}`.

If the script exits non-zero, do NOT advance to Phase 5. Re-run the corresponding phase, then re-invoke the validator.

**Then record the self-attestation:**
```json
"phases_completed": {
  "4a_validation": {
    "status": "passed",
    "validator_exit_code": 0,
    "validator_run_at": "<ISO-8601 UTC timestamp>"
  }
}
```

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
`Information`/`Warning` for legacy), extracts a redacted code snippet per issue
from `Output/`, and writes a redacted Markdown summary safe to attach to a Jira
ticket. It is language-agnostic — no `--language` flag is needed.

**Non-fatal**: if the script fails or `Reports/Issues.csv` is absent, log a
warning and continue. Do not gate Phase 5 on this step.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 4b: migration feedback file generated"`
