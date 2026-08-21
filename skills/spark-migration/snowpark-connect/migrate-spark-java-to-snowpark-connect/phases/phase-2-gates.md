# Phase 2a: Coverage Verification and Deterministic Fallback (MUST RUN)

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

### Phase 2a: Coverage Verification and Deterministic Fallback

**Run the fallback hard gate** — only now that the fixers have completed:
```bash
python3 <SKILL_DIRECTORY>/scripts/orchestrate_phases.py \
  --state <CONVERSION>/migration_state.json \
  --phase 2 --run-fallback \
  --language java
```

- `Coverage: 100%` → proceed to compilation gate.
- `MISSING` files → escalate to user.

**Gate**: All manifest files must exist in `<MIGRATED>`.

### Phase 2b: Compilation Verification Gate (MUST RUN)

**This phase MUST run after Phase 2a, on every workload.**

Run the portable compile gate script. It performs a batch-first `javac -proc:none` check over all `.java` files in `<MIGRATED>`, reverts any that fail to the `phase-1-complete` baseline, and removes leftover Maven `target/` directories.

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/revert_failing_java_files.py \
  --migrated <MIGRATED> \
  --phase-tag phase-1-complete \
  --json
```

**Compile tiers (automatic, no user action required):**
1. `javac` on PATH → `javac -proc:none -d <tmpdir>` (batch-first, then per-file on errors).
2. `javac` absent → tokenizer brace/paren/bracket balance fallback.

**Use `--no-revert` first to diagnose without reverting:**
```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/revert_failing_java_files.py \
  --migrated <MIGRATED> \
  --phase-tag phase-1-complete \
  --no-revert --json
```
Re-dispatch `agents/fixer.md` on `failures` files using the `diagnostics` output as feedback (one bounded pass), then re-run without `--no-revert` to revert what still fails.

**Quarantine rule**: files containing `// SCOS: [SPRKCNTSCL1500]` that fail due to unresolved RDD references are quarantined (not reverted, not counted in `fail_count`) — the pre-migration code is equally broken under SCOS.

**CI / production**: add `--require-javac` to fail (exit 3) when javac is absent rather than silently degrading to tokenizer mode.

Record:
```json
"phases_completed": {
  "2b_compilation": {
    "status": "passed",
    "fail_count_initial": <M>,
    "reverted_count": <N>,
    "iterations": <K>,
    "compile_mode": "<javac|tokenizer>",
    "compile_strategy": "<batch|per_file|none>"
  }
}
```
AND write the legacy field: `"compilation_reverted_count": <N>`.

**Hard gate (all must be true):**
1. Final `fail_count == 0` after the last iteration.
2. `migration_state.json["phases_completed"]["2b_compilation"]["status"] == "passed"`.
3. The legacy field `compilation_reverted_count` is also set.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 2b: compilation gate passed (reverted_count=<N>)"`

### Phase 2c: Evidence-Based Verification Gate (MUST RUN)

<!-- Evidence-based verification: sole writer of Partial Migration findings for Java -->
**This phase MUST run exactly ONCE, after Phase 2b and after all fixer
re-dispatching is complete.** Do NOT run it inside the per-chunk dispatch
loop — doing so persists partial labels into `analysis.json` before the async
fixer has finished, producing stale/false partials.

The self-reported completion in `migration_state.json` (`processed_files`) is
not proof a file was migrated — only that the agent attempted it. This gate
cross-checks the state against on-disk evidence and reconciles both artifacts
to the truth. It is the **sole writer** of Partial Migration findings.

```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/orchestrate_phases.py \
  --state <CONVERSION>/migration_state.json \
  --run-verification --language java
```

This runs `verify_migration.py --write --language java`, which:
- Classifies every file from evidence: `migrated` (a real `// SCOS:` fixer marker is present, OR the file is recorded done with Spark surface), `partial` (has Spark surface / real findings but no genuine fixer edit and not recorded done), `trivial` (no Spark surface), `not_attempted` (file missing from `Output/`).
- Writes ONE verified `SPRKCNTSCL0099` finding per genuinely-partial file into `analysis.json` and records it in `needs_human_action`; clears any stale Partial-Migration noise.
- Re-verifies and prints `disagreements = 0` on success.

If any file appears as `not_attempted`, treat that as a hard failure and escalate — do NOT advance to Phase 3.

After the gate passes, record:
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

**Gate**: the command must print `Re-verify after reconcile: disagreements = 0` and must NOT print a `Not attempted` section.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 2c: evidence-based verification reconciled"`
