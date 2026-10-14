# Phase 2: Apply Fixes

> **Phase playbook** — loaded on demand by `../SKILL.md` (the coordinator).
> Placeholders (`<SKILL_DIRECTORY>`, `<CONVERSION>`, `<MIGRATED>`, `coordinator_mode`)
> are defined in the coordinator; the universal gate contract lives there too.

### Phase 2: Apply Fixes

**Pre-dispatch: Run External Orchestrator (PLAN ONLY)**:
```bash
python3 <SKILL_DIRECTORY>/scripts/orchestrate_phases.py \
  --state <CONVERSION>/migration_state.json \
  --phase 2 \
  --budget 80000 \
  --max-parallel 6 \
  --language java
```

Read the printed `PHASE 2 DISPATCH PLAN` and act on it wave-by-wave. Dispatch `agents/fixer.md` sub-agents concurrently per wave.

> **You (the coordinator) are the single writer of `migration_state.json`.** In `PARALLEL_MODE=true`, workers return `CHUNK_RESULT` lines; update state once per wave.

**Verify (deterministic)**:
```bash
python3 <SKILL_DIRECTORY>/scripts/verify_phase.py \
  --phase 2 --language java --strict \
  --state <CONVERSION>/migration_state.json
echo "verify_phase2_exit=$?"
```

**Gate**: exit 0 ⇒ `PASS` or `PASS_WITH_GAPS`. On exit 1 (`FAIL`), re-run the fixer on the listed files, then re-run the verifier.

**Git checkpoint**: `cd <CONVERSION> && git add -A && git commit -m "Phase 2: fixes applied"`
