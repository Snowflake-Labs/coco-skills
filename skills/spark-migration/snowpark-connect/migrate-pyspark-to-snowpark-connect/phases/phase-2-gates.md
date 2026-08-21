# Phase 2a: Coverage Verification Gate (MUST RUN)

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

<!-- SNOW-3375304: Ensure 100% file coverage -->

Every run of `orchestrate_phases.py` prints a `COVERAGE VERIFICATION` report and sets `migration_state.json` field `orchestrator_coverage_verified` (recorded under the `2a_coverage` phase). Before advancing, read the report from the final orchestrator run:

- `Coverage: 100%` (every manifest file is present in `Output/`, copied in Phase 0) → advance to the compilation gate.
- `MISSING` files listed → escalate to the user; a manifest file is absent from `Output/`. Do not advance.

# Phase 2b: Compilation Verification Gate (MUST RUN)

<!-- SNOW-3379886: Hard gate ensuring 100% compilation after code fixes -->

**This phase MUST run after Phase 2, on every workload, with no exceptions.**
Skipping it lets broken syntax ship to the customer's `Output/` directory. Even
single-file workloads must run the gate.

This is the **same fixer gate** from Phase 2, re-run with `--revert-failing` —
its final safety-net mode. During Phase 2 the gate runs read-only and drives the
re-fix loop; here it gets one authority to *repair*. Any `.py` that **still**
does not compile is reverted to its pre-Phase-2 baseline (`phase-1-complete`) — a
working original beats broken half-migrated syntax — and reported as an advisory
`fix_reverted`. Files that cannot be reverted (missing baseline, empty, or still
broken after revert) remain blocking `CRITICAL` findings. (There is no separate
`revert_failing_files.py` step anymore; that logic is folded into the gate, so
there is a single post-loop compilation gate.)

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/scos_gates.py fixer \
  --state <CONVERSION>/migration_state.json \
  --revert-failing --phase-tag phase-1-complete --json
```

The JSON payload reports `verdict`, `exit_code`, `gaps`, `reverted`, and
`reverted_count`. Use `reverted_count` for the bookkeeping below.

**Checklist** (do every step in order; do not skip steps):

- [ ] Run the gate above. If `exit_code == 2` (`FAIL`), some files could neither
      be compiled **nor** reverted — gap-scoped re-dispatch `../agents/fixer.md`,
      passing the gate's `gaps` as `TARGET_ISSUES` (fix only those, leave resolved
      code untouched — see `../agents/fixer.md` "Targeted re-fix mode"), then re-run.
      Repeat until `exit_code == 0` or you have iterated 3 times.
- [ ] Write to `migration_state.json`:
  ```json
  "phases_completed": {
    "2b_compilation": {
      "status": "passed",
      "reverted_count": <N>,
      "iterations": <K>
    }
  }
  ```
  AND write the legacy top-level field for backward compat:
  ```json
  "compilation_reverted_count": <N>
  ```
- [ ] If you cannot run this phase for any reason (e.g. `<MIGRATED>` is empty,
      `phase-1-complete` tag missing), set:
  ```json
  "phases_completed": {
    "2b_compilation": {
      "status": "skipped",
      "skip_reason": "<one-line reason>"
    }
  }
  ```
  and **STOP** — do not advance to Phase 3. Escalate to the user.

**Hard gate (all of the following MUST be true to advance to Phase 3):**

1. Final `exit_code == 0` (verdict `PASS` / `PASS_WITH_GAPS`) — no remaining `CRITICAL` syntax/compile findings.
2. `migration_state.json["phases_completed"]["2b_compilation"]["status"] == "passed"`.
3. The legacy field `migration_state.json["compilation_reverted_count"]` is set to `<N>`.

If any of these is false, do NOT advance. Either re-iterate or mark `skipped`
with a reason and escalate.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 2b: compilation gate passed (reverted_count=<N>)"`

# Phase 2c: Evidence-Based Verification Gate (MUST RUN)

<!-- SNOW-3383532: single, evidence-based writer of Partial Migration findings -->
**This phase MUST run exactly ONCE, after Phase 2b and after all fixer
re-dispatching is complete.** Do NOT run it inside the per-chunk dispatch loop
— doing so persists partial labels into `analysis.json` before the async fixer
has finished, producing stale/false partials.

The self-reported completion in `migration_state.json` (`processed_files` /
`files_done`) is not proof a file was migrated — only that the agent attempted
it. This gate cross-checks the state against on-disk evidence and reconciles
both artifacts to the truth. It is the **sole writer** of Partial Migration
findings. A file is marked done only by the genuine fixer, so its recorded
completion state is itself trustworthy evidence.

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/orchestrate_phases.py \
  --state <CONVERSION>/migration_state.json \
  --run-verification --language <python|scala>
```

This runs `verify_migration.py --write`, which:
- Classifies every file from evidence: `migrated` (a real `# SCOS:` fixer marker is present, OR the file is recorded done with Spark surface), `partial` (has Spark surface / real findings but no genuine fixer edit and not recorded done), `trivial` (no Spark surface), `not_attempted` (file missing from `Output/` and therefore not produced by the migration flow).
- Writes ONE verified `SPRKCNTPY0099`/`SPRKCNTSCL0099` finding per genuinely-partial file into `analysis.json` and records it in `needs_human_action`; clears any stale Partial-Migration noise and falsely-flagged migrations.
- Re-verifies and prints `disagreements = 0` on success.

If any file appears as `not_attempted`, Phase 2's coverage gate should already
have caught it. Treat that as a hard failure and escalate to the user — do NOT
advance to Phase 3 or Phase 4.

After the gate passes, record the Phase 2c milestone in `migration_state.json`
so downstream validation can detect if this phase was silently skipped:

```json
"phases_completed": {
  "2c_verification": {
    "status": "passed",
    "disagreements": 0,
    "not_attempted": 0,
    "needs_human_action": ["<relative path>", "..."],
    "verified_human_action_count": <N>,
    "recorded_migrated_count": <M>
  }
}
```

**Gate**: the command must print `Re-verify after reconcile: disagreements = 0`
and must NOT print a `Not attempted` section. The files listed in
`needs_human_action` are the genuine human-action items for the report.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 2c: evidence-based verification reconciled"`
