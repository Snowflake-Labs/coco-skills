---
name: refs-workspace-setup
description: "Worktree management, branch creation, and commit conventions for development workflows."
---

# Workspace Setup Reference

Setting up a clean workspace for development work. Load this when provisioning the workspace, not at Ship time.

## Worktree Workflow

Use this guidance for EXTERNAL shared repositories. Do not apply worktree/base-ref procedures to the private `xocortex` vault.

Use git worktrees to create isolated workspaces per work item. This keeps the main working directory clean, avoids branch-switching side effects, and prevents cross-contamination between concurrent work items.

### Placement

Worktrees default to the XO scratch directory — the per-WI scratch dir (`Scratch:` in the task file) already exists, allocated at WI creation; it is write-approved, gitignored, and cleaned up by the Cleanup stage. The operator can override to a different location.

| Pattern | Path | When |
|---------|------|------|
| **XO scratch (default)** | `$XOCORTEX_HOME/tmp/wi{N}/<repo-name>/` | Standard — write-approved, lifecycle-managed |
| **Operator override** | Whatever the operator specifies | Record in notes for future sessions |

**Why XO scratch?** In editor mode (single project open), creating a sibling folder in the project parent may trigger a permissions prompt or appear as a separate project to the IDE. `$XOCORTEX_HOME` is already approved and scoped. Cleanup stage handles removal after merge.

**Constraint:** The worktree must be on the same filesystem as the target repo (git links via absolute paths). This is almost always true for local dev.

When proposing a worktree to the operator:
> "I'll create a worktree at `$XOCORTEX_HOME/tmp/wi{N}/<repo-name>/` — or would you prefer a different location?"

### Commands

```bash
# The scratch parent ($XOCORTEX_HOME/tmp/wi{N}) already exists from WI allocation.
# Insurance for legacy WIs only — guarded so it never prompts when already present:
ls "$XOCORTEX_HOME/tmp/wi{N}" >/dev/null 2>&1 || mkdir -p "$XOCORTEX_HOME/tmp/wi{N}"

# Resolve and fetch the remote default branch before compare/review/PR work.
base_ref=$(git -C <repo> symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null | sed 's#^refs/remotes/origin/##')
if [ -z "$base_ref" ]; then
  base_ref=$(git -C <repo> remote show origin | sed -n 's/.*HEAD branch: //p' | head -n 1)
fi
git -C <repo> fetch origin "$base_ref"

# Create worktree for a work item (default location)
git -C <repo> worktree add "$XOCORTEX_HOME/tmp/wi{N}/<repo-name>" -b agent/wi{N}-<slug>

# Example — WI-235 on myproject:
git -C ~/Projects/myproject worktree add \
  "$XOCORTEX_HOME/tmp/wi235/myproject" -b agent/wi235-worktree-placement

# List active worktrees
git -C <repo> worktree list

# Remove after merge (also handled by Cleanup stage)
git -C <repo> worktree remove "$XOCORTEX_HOME/tmp/wi{N}/<repo-name>"
git -C <repo> branch -d agent/wi{N}-<slug>
```

The worktree is a full working directory with its own checkout. Work in `<worktree-path>` without disrupting the main checkout.

### SWITCH

Switch between the primary checkout and a worktree only after checking both paths with `git -C <path> status --short --branch`.

### HEALTH-CHECK

Before Build, Review, compare, or PR work: verify the worktree exists, `git -C <worktree-path> rev-parse --abbrev-ref HEAD` matches the expected branch, `origin/$base_ref` is freshly fetched, and `git -C <worktree-path> status --short` is empty.

### Primary checkout audit

After worktree work, verify the primary checkout with `git -C <repo> status --short --branch`; if it is dirty or left on a stray feature branch, report it and ask if the user wants to restore the expected branch and clean state before leaving the repo.

---

## Branch Naming

**For xo-tracked work items**, use `agent/wi{N}-<slug>` — the WI number traces back to internal tracking, the slug makes the branch self-documenting for anyone reading the remote.

Examples: `agent/wi235-worktree-placement`, `agent/wi318-local-vector-search`

**For other work**, check prior notes for any recorded branch naming conventions via **Recall** (`references/observe-recall.md`, query the repo name).

**If no standards found**, fall back to conventional patterns:

| Change Type | Pattern | Example |
|-------------|---------|---------|
| XO work item | `agent/wi{N}-<slug>` | `agent/wi235-worktree-placement` |
| Bug fix | `fix/<short-description>` | `fix/agents-md-preview` |
| Feature | `feat/<short-description>` | `feat/context-ring-logging` |
| Refactor | `refactor/<description>` | `refactor/hook-decomposition` |
| Documentation | `docs/<description>` | `docs/hooks-guide` |

---

## Commit Conventions

Before committing, check the repo's conventions:

1. Check prior notes for recorded commit conventions via **Recall** (`references/observe-recall.md`, query the repo name)
2. If none, check `CONTRIBUTING.md`, `AGENTS.md`, or recent `git log`

Common patterns:
- Conventional commits: `feat:`, `fix:`, `docs:`, `chore:`
- Component prefix: `[component] description`
- Issue reference: `description (#123)`

```bash
git add -A
git commit -m "<message per repo conventions>"
```

Do NOT reference internal WI numbers in commits to external repositories.


