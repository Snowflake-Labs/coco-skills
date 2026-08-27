---
name: act-provision
description: "Provision stage — set up an isolated workspace before any production. Type-dispatch: git worktree (repo work), scratch-only (standalone artifacts), local infrastructure (apps/demos), Snowflake (DB objects/pipelines), or cloud deployment."
---

# Provision

Set up an isolated workspace before producing anything. This stage creates the safety net for Build — work happens in a provisioned space; the operator's main environment stays clean.

**Scratch already exists.** The per-WI scratch dir (`$XOCORTEX_HOME/tmp/wi{N}/`, recorded as `Scratch:` in the task file) was allocated at WI creation — Provision does **not** create it. Provision sets up the *workspace* (worktree, local runtime, Snowflake context, cloud target) which lives inside or alongside that scratch dir. If the scratch dir is somehow missing (a legacy WI created before scratch was first-class), `ls` then `mkdir -p` it once — never invent an alternative path.

## When to use this stage

- Before any implementation, document generation, configuration, or artifact production
- When starting work on a new task regardless of output type
- When setting up for a parallel-agent run (each agent needs its own workspace)

---

## Workspace type decision

Before provisioning, determine what kind of workspace the work requires:

| Work type | Workspace mode | What gets provisioned |
|---|---|---|
| Code changes in a repository | **Git worktree** | Isolated branch + worktree in `$XOCORTEX_HOME/tmp/wi{N}/<repo>/` |
| Standalone artifact (document, spec, analysis, guide) | **Scratch-only** | `$XOCORTEX_HOME/tmp/wi{N}/` scratch directory — no repo needed |
| App, demo, or UI that needs local runtime | **Local infrastructure** | Scratch dir + local server (Streamlit in-IDE, Docker, dev server) |
| Snowflake objects (tables, procedures, pipelines, apps) | **Snowflake schema** | Scratch dir + target schema/database context confirmed |
| Cloud deployment (SPCS, external hosting) | **Cloud infrastructure** | Scratch dir + deployment target confirmed with operator |

**Ask the operator** when the work could fit multiple modes (e.g., a Snowflake procedure that also has a local test harness). Default to the simplest mode that covers the work.

**IDE capabilities to remember:**
- Built-in browser for previewing web apps and local servers
- Streamlit execution environment (in-IDE)
- Direct Snowflake account integration (SQL execution, object management)
- Notebook support for exploratory/interactive work

These are available without additional provisioning — note them when relevant.

---

## Mode: Git worktree (repository work)

### Input contract

- Target repository path
- Approved spec or WI reference
- Operator direction on branch/worktree preference

### State assessment

```bash
git -C <repo> branch --show-current
git -C <repo> status --porcelain
git -C <repo> status -sb
```

| State | Implication | Default action |
|---|---|---|
| Clean, on main/master | New work | Create worktree at `$XOCORTEX_HOME/tmp/wi{N}/<repo-name>/` on branch `agent/wi{N}-<slug>` |
| Clean, on feature branch | Resuming | Confirm this is the right branch |
| Dirty, uncommitted changes | Prior work in progress | Clarify: continue, stash, or new worktree |
| Behind remote | Missing upstream | Pull or rebase before starting |
| Detached HEAD | Unusual state | Clarify intent with operator |

### Operator confirmation

> "Target: `<repo>` on branch `<branch>`
> State: [clean/dirty], [ahead/behind/in-sync]
>
> Default: create worktree at `$XOCORTEX_HOME/tmp/wi{N}/<repo-name>/` on branch `agent/wi{N}-<slug>` — keeps main dir clean, isolates this work.
>
> Alternatives:
> (a) Proceed on this branch (if already on the right feature branch)
> (b) Create bare branch instead: `<suggested-name>`
> (c) Stash existing changes first, then worktree
>
> Proceed with default, or specify alternative?"

### Worktree creation

Load `references/refs-workspace-setup.md` for commands and branch naming conventions.

```bash
git -C <repo> worktree add <worktree-path> -b <branch-name>
```

Standard branch name: `agent/wi{N}-<slug>` (see `workspace-setup.md`).

---

## Mode: Scratch-only (standalone artifacts)

For work that doesn't modify a repository — documents, analyses, guides, configs, presentations.

The pre-allocated scratch dir (`Scratch:` in the task file) **is** the workspace — no further setup needed. All subagent outputs, drafts, and intermediate artifacts go here. Final artifacts are promoted to their destination in Ship/Cleanup.

No git state assessment needed. No branch to track.

---

## Mode: Local infrastructure (apps, demos, interactive work)

When the work requires a running local process — a Streamlit app, a web app in the IDE browser, a Docker-based service, or an interactive prototype.

1. Use the pre-allocated scratch dir (`Scratch:` in the task file)
2. Determine runtime:
   - **Streamlit in-IDE** — no additional setup; the IDE runs it directly
   - **Local dev server** — note the command and port; IDE browser can preview
   - **Docker** — confirm image availability and port mapping with operator
   - **Notebook** — create `.ipynb` in scratch dir; IDE kernel handles execution
3. Confirm with operator what runtime they expect and where they'll observe output

---

## Mode: Snowflake schema (database objects)

When the work produces Snowflake objects — tables, views, procedures, tasks, stages, apps.

1. Use the pre-allocated scratch dir (`Scratch:` in the task file)
2. Confirm target context with operator:
   - Database and schema
   - Role (does the active role have CREATE privileges?)
   - Whether to use a dev/staging schema or work directly in target
3. Record the context in WI notes for downstream stages

No worktree needed — the Snowflake account IS the workspace. The scratch dir holds SQL scripts and subagent artifacts.

---

## Mode: Cloud infrastructure (deployment targets)

When the work deploys to a remote platform — SPCS, external cloud, hosted service.

1. Use the pre-allocated scratch dir (`Scratch:` in the task file)
2. Confirm deployment target and credentials with operator
3. Note any cost implications or environment isolation requirements
4. Record connection details and target in WI notes

This mode often combines with another (e.g., git worktree for source + cloud for deployment).

---

## Scratch directory (universal — all modes)

The per-WI scratch dir is the universal handoff surface, **allocated at WI creation** and recorded as the `Scratch:` field in the task file:

```
$XOCORTEX_HOME/tmp/wi{N}/
```

Provision does not create it — it already exists. Read the path from the task file and use it. If it is somehow missing (legacy WI), ensure it once with `ls "$XOCORTEX_HOME/tmp/wi{N}" || mkdir -p "$XOCORTEX_HOME/tmp/wi{N}"` — never invent an alternative location.

Cold and bounded subagents write their handoff artifacts (findings, critiques, briefs) here — never into the codebase, and never creating directories themselves.

**OUTPUT_PATH convention** — the **orchestrator** assigns each dispatched subagent a unique, fully-formed path:

```
$XOCORTEX_HOME/tmp/wi{N}/{stage}-{role}-{nn}.md
```

e.g. `tmp/wi319/workshop-edge-cases-01.md`, `tmp/wi319/review-security-02.md`. The unique `{role}`/`{nn}` suffix prevents collisions across parallel dispatches.

---

## Parallel-agent isolation

When dispatching multiple sub-agents that write files, give each a distinct OUTPUT_PATH under the canonical scratch dir — never the same path — to prevent conflicts. The orchestrator merges their outputs.

For agents that write to the worktree (Build, Prove.author), use separate worktrees per agent if parallelism is needed within a single stage.

---

## Output contract

Confirm the workspace is ready:
- Workspace type and location noted (used by Build, Prove, Ship, Cleanup)
- Branch name noted if applicable (used by Ship in PR mode)
- Runtime/deployment target noted if applicable
- State recorded in WI notes

---

## Cleanup counterpart

Provision creates the workspace structures (worktree, runtime, Snowflake/cloud context) inside the scratch dir; Cleanup removes them. The scratch dir itself is allocated at WI creation and cleared by Cleanup at the end. Always record the workspace location so Cleanup can find it. For Snowflake mode, record what objects were created (for potential rollback).

---

## Chaining (soft — reminders, not gates)

- **Usually follows:** Plan (you know what you're producing) — Provision sets up the isolated workspace before any production.
- **Leads to — primary:** Build.
- **Or:** Prove.author (TDD — author the tests before implementing), Document (if the deliverable IS documentation).
