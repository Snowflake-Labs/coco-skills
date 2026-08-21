---
name: scos-harvester
description: "Worker-side harvest agent. Cherry-picks this batch's [MIGRATION-FIX] commits onto the primary deliverable branch after summary completes. Handles the harvest session lock, retries if another worker is currently harvesting, and resolves cherry-pick conflicts by keeping migration-fix logic and dropping test-patch scaffolding. Triggers: harvest batch, consolidate fixes, cherry-pick migration-fix."
---

# Harvester

Dispatched by `batch-runner` after `validate.py summary` exits 0. Your job is to
cherry-pick **this batch's** `[MIGRATION-FIX]` commits onto the primary
deliverable branch, serialising against concurrent workers via `validate.py
consolidate`'s built-in harvest session lock.

Think of it like a developer merging a feature branch to main: you know exactly
what you changed, another developer may be merging at the same time, and you
just wait your turn if the repo is locked.

**Prior learnings:** Before Step 1, read
`$PRIMARY_CONV_ROOT/Validation/shared/batch-learnings.md` into your context.
It contains patch patterns, schema quirks, and dialect issues discovered by
workers that completed before you. Apply any relevant patterns rather than
rediscovering them.

## Inputs

- `PRIMARY_CONV_ROOT` — path to the **primary** conversion repo (not your worktree).
- `VALIDATION_BRANCH` — your validation branch, e.g. `validation/abc12345`.
- `BASE_SHA` — the base SHA all workers branched from.
- `SKILL_DIRECTORY` — path to this skill.
- `WORKTREE_CONV_ROOT` — your own worktree (for the pre-harvest commit step).

```bash
RUN="uv run --project $SKILL_DIRECTORY/.. python $SKILL_DIRECTORY/scripts"
```

## Step 1 — Commit any outstanding fixes

Before harvesting, ensure every genuine code fix in your worktree is committed.
Blueprint patches (`patch-add`) auto-commit as `[TEST-PATCH]`. But if **you or
the migration-fixer made a direct edit to `Output/`**, it may be uncommitted:

```bash
git -C $WORKTREE_CONV_ROOT status -- Output/
```

If anything is staged or modified, commit with the correct kind:

```bash
# Genuine logic fix — cherry-picked at harvest:
$RUN/validate.py commit \
  --conv-root $WORKTREE_CONV_ROOT \
  --kind migration-fix \
  --trial-ids "<trial id(s)>" \
  --message "<what and why>"

# Harness-only change — NOT cherry-picked (do not use migration-fix for these):
$RUN/validate.py commit \
  --conv-root $WORKTREE_CONV_ROOT \
  --kind test-patch \
  --message "<what>"
```

Skip this step if the working tree is already clean.

## Step 2 — Harvest with retry

Call `consolidate` on the **primary repo**, passing only your branch:

```bash
$RUN/validate.py consolidate \
  --conv-root $PRIMARY_CONV_ROOT \
  --base-sha $BASE_SHA \
  --branches $VALIDATION_BRANCH
```

**Do NOT pipe this through `grep`/`tail` — a pipe swallows exit codes and a
conflict looks like success. Run it bare and capture `$?`.**

Exit codes:

| Code | Meaning | Action |
|------|---------|--------|
| **0** | Applied cleanly — or no [MIGRATION-FIX] commits to cherry-pick (also success) | Done — report success to batch-runner |
| **5** | Cherry-pick conflict | Resolve the conflict (Step 3), then `--continue` |
| **6** | Git is busy — another worker's cherry-pick is in progress or a git process holds the index lock | Sleep 30 s, retry from the top of Step 2 |
| **1** | git error | Stop, report the error message |

For exit 6, retry up to 30 times (15 minutes total):

```bash
# Runs in CoCo bash sandbox (Linux) — safe on any host OS
for i in $(seq 1 30); do
  $RUN/validate.py consolidate \
    --conv-root $PRIMARY_CONV_ROOT \
    --base-sha $BASE_SHA \
    --branches $VALIDATION_BRANCH
  EXIT=$?
  [ $EXIT -ne 6 ] && break
  echo "Harvest locked by another worker (attempt $i/30) — waiting 30s..."
  sleep 30
done
```

After 30 retries, report failure to the orchestrator.

## Step 3 — Resolve a conflict (exit 5 only)

A conflict means one of your `[MIGRATION-FIX]` commits touched lines that a
`[TEST-PATCH]` had already rewritten. You know what you changed; use that to
resolve it.

### What to keep vs drop

| Keep in the resolved file | Drop |
|---------------------------|------|
| Your genuine logic fix (renamed join keys, corrected SQL, explicit column refs, etc.) | `SCOS_INPUT_*` / `SCOS_SINK_*` env var reads |
| Production table references (original FQNs) | `SCOS_DATABASE_NAME`, `SCOS_OUTPUT_SCHEMA` env reads |
| Any change that would be correct in production PySpark | `SCOS_TEST_AUX_*` refs, harness fixture imports |

The test-patch had rewritten the file to use `SCOS_*` env vars.  The conflict
shows your migration fix applied on top of that rewrite.  Strip the harness
wiring; keep only the genuine code change.

### Workflow

```bash
# See which files conflicted
git -C $PRIMARY_CONV_ROOT diff --name-only --diff-filter=U

# For each conflicted file: read the conflict markers, read what your
# MIGRATION-FIX commit actually changed (to know its intent):
git -C $PRIMARY_CONV_ROOT show CHERRY_PICK_HEAD -- Output/<file>

# Edit the file to the correct resolved state (no conflict markers,
# no SCOS_* scaffolding, migration fix applied cleanly):
# [use Edit tool]

# Stage the resolved file:
git -C $PRIMARY_CONV_ROOT add Output/<file>
```

Then resume:

```bash
$RUN/validate.py consolidate \
  --conv-root $PRIMARY_CONV_ROOT \
  --base-sha $BASE_SHA \
  --continue
```

Exit codes for `--continue`: same table as Step 2. Repeat until exit 0.

### Hard stop

If you cannot confidently resolve a conflict — the fix and the test-patch touched
completely different semantic regions and you cannot tell which is the genuine
change — abort instead of guessing:

```bash
$RUN/validate.py consolidate \
  --conv-root $PRIMARY_CONV_ROOT \
  --base-sha $BASE_SHA \
  --abort
```

Report the conflicting commit SHA (`CHERRY_PICK_HEAD`), the conflicted file(s),
and a brief explanation. The orchestrator will handle it manually.

## Done

Report back to the batch-runner: "Harvest complete. Branch `$VALIDATION_BRANCH`
cherry-picked onto the deliverable branch." The batch-runner can then return to
the orchestrator.
