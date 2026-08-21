# Phase 4: Generate Reports

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

**Run mode (size-aware)**: if `coordinator_mode == false`, run this **inline** by reading `../agents/reporter.md` and following **Section B (Dashboard CSVs) only** yourself — it is purely a deterministic script invocation plus existence checks, with no judgment; if `coordinator_mode == true`, **spawn a `task()` sub-agent** with `../agents/reporter.md` to run Section B (consistent with the other phases' multi-file handling, keeping the generator output and any `Issues.csv` inspection out of your window). Run `generate_scos_reports.py`, which (a) ensures each `# SCOS:` comment carries its EWI code inline (`# SCOS: [SPRKCNT...] <message>`) — reusing the code the fixer embedded, injecting a generic one only when absent, and removing legacy `#EWI:` lines that sit directly above a `# SCOS:` comment — then (b) produces `Reports/Issues.csv`, `Reports/InputFilesInventory.csv`, `Reports/ArtifactDependencyInventory.csv` from the final files. `Issues.csv` reads the same inline codes — and also surfaces the recipe-emitted `# SCOS-WARN:` / `# SCOS-TODO:` markers — so the report and the in-file comments agree on count, code, and line. (`MigrationReadinessReport.html` + `AssessmentIR.json` are **not** rendered here — they were already produced in Phase 1.2.)

**Quality gate**: run the dashboard-CSV gate (a deterministic script):

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/scos_gates.py reports --section csvs \
  --state <CONVERSION>/migration_state.json --json
```

The gate confirms `Reports/Issues.csv`, `Reports/InputFilesInventory.csv`, and `Reports/ArtifactDependencyInventory.csv` exist, that `Issues.csv` has data rows carrying `SPRKCNTPY` codes, and that `InputFilesInventory.csv` is non-empty. Read the verdict from stdout.

**Gate (bounded retry, then hard fail)**:
- Exit `0` → update `migration_state.json` phase to 4 and record:
  ```json
  "phases_completed": {"4_reports": {"status": "passed", "gate": "scos_gates.reports:csvs", "attempts": <n>}}
  ```
- Exit `2` (`FAIL`) → re-run Section B the same way you ran it (inline, or by re-dispatching the `../agents/reporter.md` Section B sub-agent in multi-file mode) with the gate's `gaps` as feedback, then re-run the gate. Retry at most **3 times total**. If it still fails, **STOP and escalate to the user**. Record:
  ```json
  "phases_completed": {"4_reports": {"status": "skipped", "attempts": 3, "skip_reason": "<one-line reason>"}}
  ```
- Exit `3` (IO / usage error) → STOP and escalate.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 4: reports generated"`

# Phase 4a: Post-Run State Validation (MUST RUN)

**This phase MUST run as the last deterministic step of every migration.** It
asserts that every required phase (1, 1a, 2, 2a, 2b, 2c, 3, 4) recorded evidence in
`migration_state.json` — either via the canonical `phases_completed[<key>]`
block or via the documented legacy top-level field. Silent skips become loud
failures here, before the user is offered validation.

The validator script is pure stdlib (no third-party deps), so invoke it
directly with `python3` — no `uv run` needed:

```bash
python3 <SKILL_DIRECTORY>/scripts/validate_migration_state.py \
  --strict \
  --state <CONVERSION>/migration_state.json
echo "validator_exit=$?"
```

**Hard gate (all must be true):**

1. The script exits 0 (no required phase missing or skipped without reason).
2. The printed report shows `PASS: all required phases present.`.

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
cannot confirm Phase 4a actually executed without parsing the transcript. (The
validator still accepts the older `4b_validation` key for back-compat.)

For machine-readable output (e.g. when wrapping in CI), pass `--json` instead
of the default human report.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 4a: validation passed"`

# Phase 4b: Generate Migration Feedback File (Non-Fatal)

Run the migration feedback generator to produce the file the FDE attaches to a
Jira ticket for Casper to triage:

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/generate_migrate_feedback.py \
  --conv-root <CONVERSION>
```

Output: `<CONVERSION>/Feedback/migrate_gaps.md`

**Non-fatal**: if the script fails or `Reports/Issues.csv` is absent, log a
warning and continue.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 4b: migration feedback file generated"`
