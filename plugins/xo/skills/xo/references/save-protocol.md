---
name: save-protocol
description: "**[REQUIRED]** Recording discipline for multi-session agent work. Handles savepoints, notes, diary, task updates, and session recovery. Load this FIRST in any work session. Triggers: savepoint, save, record, resume, recover, pick up, continue WI."
---

# Save Protocol (Recording)

**Why:** Context resets (compaction, summarisation) destroy working memory. Without disciplined recording, you lose investigation state, hypotheses, and accumulated understanding — and start over each time. This protocol gives a three-layer system (diary, task, notes) that lets you recover quickly and continue where you left off, even across multiple sessions.

**Not for:** User-facing documentation (use Document — `references/act-document.md`) or durable learnings capture (use Capture — `references/decide-capture.md`). This is operational recording for agent continuity.

---

Recording discipline that enables agent work to span multiple context windows. Without this, context resets (compaction or summarisation) destroy working memory and multi-phase workflows degrade to "start over."

## Session Prerequisites

Before recording operations, you need workspace context. However, do NOT read these files automatically on session start — only read what you need, when you need it:

- `tasks/index.md` — read when you need to orient on the portfolio or find a WI number
- `diary/{YYYY-MM}/{YYYY-MM-DD}.md` — read when resuming work or writing a diary entry
- Your session ID — use in diary headings: `## [{session-id}] WI-N: description`

Paths are relative to the xocortex workspace root.

### Resolving the Workspace Root

The xocortex workspace root is where `tasks/`, `notes/`, and `diary/` directories live. It is **NOT** the xo plugin source directory or the current project directory. Resolve it using this precedence:

1. **`XOCORTEX_HOME` environment variable** — if set, use this path directly
2. **Fallback** — `~/.snowflake/cortex/memory/xocortex/`

To discover the value at runtime:
```bash
echo "${XOCORTEX_HOME:-$HOME/.snowflake/cortex/memory/xocortex}"
```

**Verification**: The resolved path MUST contain a `tasks/` directory. If it does not, the path is wrong — search for the correct xocortex repo before writing any files.

**Common mistakes to avoid**:
- Do NOT write to the xo plugin source repo (where SKILL.md lives)
- Do NOT write to the operator's current working directory or project directory
- Do NOT create tasks/notes/diary directories in a new location — find the existing ones

---

## Routing Table

| User Language | Operation | Action |
|---------------|-----------|--------|
| "savepoint", "save progress", "record what we did" | Write Savepoint | Follow [Savepoint Write](#savepoint-write) |
| "pick up WI-N", "continue WI-N", "resume" | Recovery | Follow [Session Recovery](#session-recovery) |
| "new work item", "create WI" | Create WI | Follow [Create Work Item](#create-work-item) |
| "update status", "mark done", "close WI" | Update Task | Follow [Update Task File](#update-task-file) |
| "what's the recording model?", "how do savepoints work?" | Explain | **Load** `references/save-three-layer.md` |

---

## Savepoint Write

When triggered — by hook reminder, operator request, or workflow gate:

**The diary layer always fires; the notes and task layers are WI-tracked.** Append a diary entry for every session that did substantive work — even a lightweight one with no work item. The entry hangs on the short session id, so it does not need a WI. Update the notes and task files only when the session is tracked under a work item (Steps 1–2); for a WI-less session, do Step 3 alone.

### Step 1: Update Notes File (when tracked under a WI)

**Path**: `notes/{YYYY-MM}/{project}-wi{N}-*.md`

Write or append:
- Current understanding and progress
- Key decisions with rationale
- Evidence, hypotheses, architecture context
- Anything a recovering agent needs to continue at full depth

The notes file is the deep record. **Expand as needed** — there is no length ceiling.

If no notes file exists yet, create one:
```
# WI-{N}: {Title} — {Description}

## Session {session_id} ({YYYY-MM-DD}): {Phase}

{content}
```

### Step 2: Update Task File (when tracked under a WI)

**Path**: `tasks/current/{project}-wi{N}-*.md`

Update these fields in-place:
- `Status` — current lifecycle state
- `StatusNote` — at-a-glance progress commentary
- `NextAction` — one concrete next step for the next session
- `Blocker` — if anything blocks progress

Keep compact. A cold-start agent reads this to know **what to do next**, not the full history.

**Load** `references/save-task-conventions.md` if creating a new task file or unsure of field ordering.

### Step 3: Append Diary Entry (always — including WI-less sessions)

**Path**: `diary/{YYYY-MM}/{YYYY-MM-DD}.md`

⚠️ **APPEND ONLY** — use `echo "..." >>` or append mode. Never read-edit-write.

```markdown
## [{session_id}] WI-{N}: {title}
- {1-2 line summary of what was accomplished}
- {Key decision, if any}
- Status: {current status}
- Notes: `notes/{YYYY-MM}/{project}-wi{N}-*.md`
```

For a lightweight session with no work item, key the entry on the session id alone — omit the `WI-{N}:` prefix and the notes pointer:

```markdown
## [{session_id}] {short description of what the session did}
- {1-2 line summary of what was accomplished}
- {Key decision, if any}
- Status: {e.g. one-off — no WI}
```

**Maximum 5-10 lines.** The diary is a savepoint index, not a design document.

Do NOT put in the diary:
- Files-changed lists
- Architecture discussion
- Detailed fix descriptions
- Duplicated task file content

---

## Session Recovery

When resuming work after context reset (compaction, summarisation), new session, or cold start:

**Post-reset (same session):**
1. Read task file → current state and `NextAction`
2. Read notes file → accumulated understanding (this is the deep record, not the conversation summary)
3. Read today's diary, find `[{session-id}]` entries → what happened this session
4. Resume from `NextAction`

The agent knows its own session ID. After reset, diary entries with matching `[{session-id}]` headings are from this session.

**Cold start (new session):**
1. Operator direction or read `tasks/index.md` → which WI
2. Read task file → status, NextAction
3. Read notes file → deep understanding
4. Read recent diary entries → momentum (session IDs won't match yours)
5. Present active items to operator if no direction given

### Recovery Fallback

If recovery files disagree, are missing, or the operator corrects you ("no, we weren't working on that"):

1. **Check the memory store** (`memory view /memories/`) — may have scratch files or working state from prior sessions
2. **Surface confusion** — tell the operator: "The recovery state is unclear. Would you like me to inspect the conversation history?"
3. **With operator approval**, load `$session-history` skill to analyze the conversation log

The conversation history is structured JSON but verbose — it's log exhaust, not curated reasoning. The notes file is intentionally written to preserve understanding; the conversation history captures everything including exploration paths and false starts. Use it as a fallback, not a primary source.

**Load** `references/save-recovery.md` for detailed procedures including reset detection and stale metadata handling.

---

## Create Work Item

### Step 1: Assign Number

Allocate the next WI number using the `memory` tool.

**Happy path** — memory counter:
1. `memory view /memories/xo/wi-counter.txt` → read value N (the next number to allocate)
2. `memory str_replace` to change `N\n` → `{N+1}\n` in the same file
3. Use N as the new WI number

Because `str_replace` requires an exact match, two concurrent sessions cannot both claim the same N — the loser's replace will fail and it falls through to the fallback.

**Fallback** — authoritative scan of existing tasks (use whenever the counter is missing, out of sync, or the str_replace fails):
1. `glob` for `*wi*.md` across `tasks/current/` and `tasks/archive/**/`
2. Extract the numeric portion (the digits after `wi`) of each filename and sort **numerically** (not alphabetically — `wi99` < `wi100`)
3. `N = max + 1`
4. Seed the counter by writing `{N+1}\n` to `/memories/xo/wi-counter.txt` via `memory create`, so future calls take the happy path

The scan is the source of truth; the counter is just a fast cache. If they ever disagree, the scan wins.

**Multi-session race guard.** Allocation is not a lock — XO is multi-session and other sessions allocate concurrently (the counter `str_replace` only guards the counter path, not two simultaneous scans). Immediately before creating the task file (Step 2), confirm no `*wi{N}-*.md` already exists in `tasks/current/` or `tasks/archive/`; if one does, the number was claimed concurrently — re-run the fallback scan for a fresh `max + 1`. If a collision surfaces later, the unstarted/uncommitted WI yields its number.

### Step 2: Create Task File

**Path**: `tasks/current/{project}-wi{N}-{kebab-slug}.md`

Where `{project}` is the leaf name of the current project directory (kebab-cased). This groups work items alphabetically by project when working across multiple repos.

Example: working in `~/Projects/my-cool-app/` → prefix is `my-cool-app`. Working in `~/Projects/my-org/xo/` → prefix is `xo`.

Derivation: `basename` of the repo root (or working directory if not in a git repo), lowercased, spaces/underscores to hyphens.

**Load** `references/save-task-conventions.md` for required fields and ordering.

Minimum viable task file:
```markdown
# WI-{N}: {Descriptive Title}

- Project: {project}
- Priority: {Now | Next | Later}
- Status: Ready
- StatusNote: {brief description}
- Blocker: None
- Category: {Investigation | Bug fix | Process | Idea | etc.}
- NextAction: {first concrete step}
- Scratch: $XOCORTEX_HOME/tmp/wi{N}/
```

The `Scratch:` field is the **authoritative per-WI scratch path** — the transient handoff surface every stage uses for subagent outputs, drafts, and intermediate artifacts. Recording it here means downstream stages read it and use it directly, without checking or guessing. See `references/save-task-conventions.md`.

### Step 3: Allocate Scratch Space

Create the scratch directory once, now, so the rest of the system can rely on it existing:

```bash
ls "$XOCORTEX_HOME/tmp/wi{N}" 2>/dev/null || mkdir -p "$XOCORTEX_HOME/tmp/wi{N}"
```

This is the **one expected `mkdir` approval moment** for this WI — in context, at allocation. After this, stages read the `Scratch:` path from the task file and use it directly: no `ls`, no `mkdir`, no further approval. The canonical path is always `$XOCORTEX_HOME/tmp/wi{N}/` — stages must never invent an alternative location (e.g. `/.xo/`, repo-local dirs).

### Step 4: Diary Entry

Append a creation entry to today's diary.

Note: The index is updated automatically when the SessionStart hook fires — do not manually update `index.md`.

---

## Update Task File

Read the current task file, then update fields in-place per operator instruction. Common operations:

| Intent | Fields to Update |
|--------|------------------|
| "mark done" | Status → Done, StatusNote → completion summary, then **archive** |
| "blocked on X" | Status → Blocked, Blocker → description |
| "shipped it" (PR opened / doc circulated / design staged, awaiting others) | Status → Shipped, StatusNote → name the awaited party + artifact (e.g. "awaiting reviewer X on PR #N") |
| "reviewer requested changes" | Status → InProgress (re-deliver → back to Shipped) |
| stage completed | advance `Provisional Pathway` (move the `(here)` marker) + refresh NextAction; if the stage looks done, offer the natural next stage once (dismissable) |
| "promote to Now" | Priority → Now |
| "fold into WI-N" | Status → Done, StatusNote → "Folded into WI-{N} — {reason}", then **archive** |

### Archiving Completed Work Items

When a work item reaches terminal state (Status: Done), ask the user if they want to archive it.

If so, first update the Priority to Archived, then move it from `current/` to the month-coded archive:

```bash
mv tasks/current/{project}-wi{N}-*.md tasks/archive/{YYYY-MM}/
```

Use the current month (when the work was completed), not the creation month. Check if the month directory exists first (`ls tasks/archive/`); only create it if missing.

After updating, the index will be refreshed automatically at next session start.

---

## Savepoint Urgency Levels

Hook reminders arrive at escalating urgency. Three independent signals fire checkpoints — whichever reaches the higher level wins, and the hook tells you which one triggered:

| Level | Context % | Output-Δ tokens | Turns since last | Agent Response |
|-------|-----------|-----------------|------------------|----------------|
| `[routine]` | 50-69% (+Δ≥10%) | ≥20K | ≥10 | Record progress at next milestone |
| `[urgent]` | 70-79% | ≥40K | ≥25 | Write savepoint before continuing |
| `[critical]` | 80-84% | ≥70K | ≥50 | Write immediately — all three layers |
| `[final]` | ≥85% | ≥110K | ≥75 | STOP. Savepoint NOW. Compaction imminent. |

The multi-signal design catches long autonomous runs on large context windows (where pure % would stay low for hundreds of turns) and long sustained output sessions (where the agent has written a lot but the window is not yet full).

At `[critical]` and `[final]`, prioritize savepoint over task completion. Incomplete work can be resumed; lost context cannot.

---

## Mandatory Behaviors

1. **Notes before diary, when there are notes.** For WI-tracked work, update the notes file before the diary so the diary entry references what's in notes rather than duplicating it. A lightweight WI-less session has no notes file — just append the diary entry.
2. **Diary is append-only.** Never read-edit-write the diary. Multiple sessions may savepoint concurrently.
3. **Task file stays compact.** If it exceeds ~50 lines, content should migrate to the notes file.
4. **Savepoint on milestones and gates.** Savepoint when completing significant work (a feature, a fix, a decision) and before workflow gates — these are recoverable states.
5. **Savepoint on context warnings.** When hook reminders fire, treat them as interrupts — save state, then continue.
6. **Check before create for directories.** Before writing to any path under `notes/`, `diary/`, `tasks/archive/`, or `tmp/`, first `ls` the parent directory to see if it already exists. Only create the directory if `ls` confirms it is missing. An `ls` is low-friction (auto-approved); a `mkdir` requires user approval — so always prefer checking first.

---

## Reference Index

| Reference | Purpose | When to Load |
|-----------|---------|-------------|
| `references/refs-terminology.md` | Standard terminology across stages: artifacts, actions, roles, workflow terms | When clarifying what a term means or ensuring consistent language |
| `references/save-three-layer.md` | Recording model design: diary/task/notes separation, what goes where, recovery patterns | When explaining the model or training a new user |
| `references/save-context-urgency.md` | Hook system internals: urgency thresholds, context monitoring, state machine, configuration | When debugging savepoint behavior or adjusting thresholds |
| `references/save-recovery.md` | Detailed recovery procedures: context reset detection, stale metadata, session lifecycle | When recovering from compaction/summarisation or debugging recovery failures |
| `references/save-task-conventions.md` | Task file schema: mandatory fields, contextual fields, category patterns, field ordering | When creating or restructuring task files |
