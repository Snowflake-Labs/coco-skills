---
name: save-task-conventions
description: "Task file schema for work items. Covers mandatory fields, contextual fields, category patterns, and field ordering conventions."
---

# Task File Conventions

Work item files live in `tasks/current/{project}-wi{N}-{kebab-slug}.md`. The `{project}` prefix is the leaf name of the project directory (kebab-cased), providing alphabetical grouping by project. Each file is the single source of truth for one unit of work — readable by both human supervisors and agent operators.

## Design Principles

1. **Human scanning**: A supervisor dispatching parallel agents needs to assess priority, status, and next action within seconds.
2. **Agent recovery**: An agent resuming after context reset (compaction or summarisation) needs enough context to continue without re-reading the full conversation history.

The file should be **compact but complete** — a recovering agent reads this to know what to do next, not to understand the full investigation history (that lives in the notes file).

## File Structure

### Heading

```
# WI-{N}: {Descriptive Title}
```

Specific enough that a human scanning the index understands the purpose without opening the file.

### Mandatory Fields

Every work item, in this order:

```markdown
- Priority: {Now | Next | Later | Archived}
- Status: {Ready | InProgress | Shipped | Blocked | Done}
- StatusNote: {brief progress commentary or empty}
- Blocker: {None | description}
```

**Priority** — scheduling signal: when should an agent work on this?
**Status** — lifecycle maturity: `Ready → InProgress → Shipped → Done`. `Blocked` is orthogonal — it can interrupt any active state. A reviewer requesting changes moves `Shipped → InProgress`; re-delivery returns it to `Shipped`.
**StatusNote** — at-a-glance context that surfaces in the index table
**Blocker** — explicit dependency naming

### `Shipped` vs `Blocked` vs `Done`

Easy to conflate, but distinct:

- **`Shipped`** — our deliverable is out (PR opened, doc circulated, design staged) and is awaiting others' review, merge, or acceptance. Our part is *done*; there is no further work from us until they respond. Use `StatusNote` to name the awaited party and artifact (e.g. "awaiting reviewer X on PR #N"). This is the status to grep for a daily "what am I waiting on others to accept?" check.
- **`Blocked`** — our work is *stalled mid-flight*: we need someone's input, a fix, or access to continue building. The ball is in someone else's court to unblock **us**.
- **`Done`** — accepted and closed.

Key test: if our part is finished and we only await sign-off → `Shipped`. If we still have work queued (even if also waiting on someone) → `InProgress` or `Blocked`. Because `Status` is orthogonal to `Priority` (urgency), a `Shipped` item still carries a Priority signalling how hard to chase it.

### Contextual Fields

Include whichever fields serve the work's category and complexity:

| Field | When to Use | Purpose |
|-------|-------------|---------|
| `Category` | Always helpful | Work type (Investigation, Bug fix, Process, Idea, etc.) |
| `Deadline` | Time-sensitive items | Target date — agent should remind operator as it approaches |
| `Observed` | Ideas and evidence-driven items | Date of first capture |
| `Evidence` | Items accumulating observations | Timestamped list of findings |
| `Context` | Items needing background | Bullet list of relevant background |
| `Decision` | Items where a choice was made | What was decided and rationale |
| `Scope` | Items with defined deliverables | Numbered steps. ~~Strikethrough~~ marks completed. |
| `Progress` | Multi-session items | Bullet list of accomplishments |
| `Remaining` | Items with known outstanding work | Bullet list of what's left |
| `Root Cause` | Bug investigations | Structured analysis of underlying problem |
| `Fixes Applied` | Bug fixes | Named fixes with file locations |
| `Fix Confirmation Status` | Bug fixes under test | Which fixes are confirmed vs. awaiting |
| `Artifacts changed` | Broad-impact changes | List of modified files/systems |
| `Artifacts` | Items producing references | Links to PRs, notes, memory files |
| `NextAction` | Almost always | One concrete next step |
| `Provisional Pathway` | Substantial multi-stage work | The projected arc of stages, current one marked `(here)` — e.g. `Spec → Build (here) → Prove → Ship → Capture → Cleanup`. Revisable, not a commitment; advanced at each stage boundary; read it to know the natural next step. |
| `ValidationRequired` | Items with quality gates | `Yes` or `No` |
| `ValidationAction` | When ValidationRequired is Yes | Specific acceptance criteria |
| `Related` | Items with connections | Cross-references to related WIs |
| `Notes` | Items with deep records | Path to notes file in `notes/{YYYY-MM}/` |
| `Scratch` | Set at allocation | Authoritative per-WI scratch dir (`$XOCORTEX_HOME/tmp/wi{N}/`) — the transient handoff surface stages use for subagent outputs, drafts, and intermediates. Recorded so downstream stages use it directly without checking or inventing a path. |
| `Promotion trigger` | Idea-stage items | What would promote this to a real WI |

### Provisional Pathway

The arc of stages we currently expect this work to move through, with the current stage marked explicitly by `(here)`:

```
- Provisional Pathway: Survey → Spec → Build (here) → Prove → Ship → Capture → Cleanup
```

**Projected, not a contract.** It records what we currently think the path is — revise it freely as facts change; it is not necessarily agreed with the operator and carries no obligation to follow. Stages left of `(here)` are done, stages right are upcoming (explicit `(here)`, no symbols/strikethrough). Its purpose is to make "what's the natural next step?" a *lookup* rather than something to re-derive under load: the agent advances `(here)` as part of the recording it does at each stage boundary, and consults it when a stage looks complete — e.g. to offer close-out (Capture, then Cleanup) after shipping. Only on substantial multi-stage work; small or reactive tasks omit it.

## Field Ordering Convention

Top-down reading order:

1. **Identity**: Priority, Status, StatusNote, Blocker
2. **Scheduling**: Category, Deadline
3. **Background**: Observed, Evidence, Context
3. **Decisions**: Decision
4. **Work definition**: Scope, Progress, Remaining, Root Cause, Fixes
5. **Pointers**: NextAction, Provisional Pathway, Artifacts, Notes, Related
6. **Quality**: ValidationRequired, ValidationAction

## Category Patterns

### Idea (lightweight idea capture)

```markdown
- Priority: Later
- Status: Ready
- StatusNote: {one-line assessment}
- Blocker: None
- Category: Idea
- Observed: {date}
- Evidence:
  - {date}: {observation}
- Promotion trigger: {what would make this worth investigating}
```

### Investigation

Emphasises Evidence, Context, and often evolves a Root Cause section. Notes file is essential.

### Bug Fix

Emphasises Context (symptoms), Root Cause (analysis), Fixes Applied (implementation), and ValidationAction (acceptance criteria). Most structured category.

### Process / Documentation

Emphasises Decision (what was chosen), Scope (deliverables), and Artifacts changed (impact).

### Reactive Work (email, Slack, PR review)

Minimal — often just mandatory fields plus NextAction.

## Size Convention

A task file that grows beyond ~50 lines signals that content should migrate to a notes file. The task file then points to it via the `Notes` field.

## Relationship to Notes Files

- Task file answers **"what should I do?"**
- Notes file answers **"what do we know and how did we get here?"**

Task files stay compact. When investigation depth, design reasoning, or detailed analysis accumulates, it belongs in the notes file at `notes/{YYYY-MM}/{project}-wi{N}-{description}.md`.
