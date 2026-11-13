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

### Resolving the Workspace Root (the effective vault)

The workspace root — the **effective vault** — is where `tasks/`, `notes/`, and `diary/` live. It is **NOT** the xo plugin source directory or the current project directory. Resolution is done for you by the hooks and the `xo-ops` MCP (shared `_common.js`), by this precedence:

1. **Registry entry** for the resolved repo-root (`~/.snowflake/cortex/memory/xo/vaults.json`)
2. **Committed `.xocortex` marker** name at the repo root
3. **`XOCORTEX_HOME`** environment variable (default-vault override)
4. **Fallback** — `~/.snowflake/cortex/memory/xocortex/`

You do not resolve this by hand. The SessionStart directive prints the effective vault ("your private memory vault is at …", with its source); the `xo-ops` `resolve_paths` tool returns it (and the diary/notes/tasks/scratch subpaths) for any `cwd`. Two tokens are used throughout the references — substitute the concrete paths from `resolve_paths` / the SessionStart line (they are not real shell env vars):

- **`$XO_VAULT`** — the effective vault root (`resolve_paths.vault`). For the default vault this equals `$XOCORTEX_HOME`.
- **`$XO_SCRATCH`** — the per-WI scratch dir `$XO_VAULT/tmp/wi{N}/` (`resolve_paths({ wi_number }).scratch`, recorded as the task file `Scratch:` field).

To point the current repo at its own project vault (write the machine-local registry entry) use the `xo-ops` `register_vault` tool, or `setup.sh vault-init` to also create + git-back it; to opt in every contributor, commit a `.xocortex` marker at the repo root. See `SETUP.md`.

**Verification**: the resolved `$XO_VAULT` MUST contain a `tasks/` directory. If it does not, something is wrong — do not write files; check the resolution (`resolve_paths`) first.

**Common mistakes to avoid**:
- Do NOT write to the xo plugin source repo (where SKILL.md lives)
- Do NOT write to the operator's current working directory or project directory
- Do NOT create tasks/notes/diary directories in a new location — use `$XO_VAULT` as resolved. A **registered project vault is not a "new location"**: it is the correctly resolved `$XO_VAULT` for that repo — write there.

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

A savepoint triggered by a workflow gate or a stage boundary also runs the milestone awareness check (`references/core-guidelines.md`, Freshness and concurrency); hook-reminder savepoints do not.

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
- `Last checked` — today's date (UTC), only when this savepoint ran the milestone awareness check

Keep compact. A cold-start agent reads this to know **what to do next**, not the full history.

**Load** `references/save-task-conventions.md` if creating a new task file or unsure of field ordering.

### Step 3: Append Diary Entry (always — including WI-less sessions)

**Path**: `diary/{YYYY-MM}/{YYYY-MM-DD}.md` — the tool resolves this; do not construct it.

⚠️ **APPEND ONLY** — write the entry with the `xo-ops` `append_diary` tool (pass `cwd` plus the authored `entry`). It resolves the effective vault, creates the month dir if absent, and appends without reading. Never read-edit-write the diary, never use `edit`/`multi_edit`, and never shell-append.

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

### Step 4: Commit session files

After the diary append completes, immediately call the `xo-ops` `commit_wi` tool (pass `cwd`, `wi_number` if tracked, and `session_id`). **Issue it as the final tool call of the savepoint, on its own — never in the same parallel tool batch as the Step 1–3 writes (`write` / `edit` / `append_diary`).** Parallel tool calls are unordered, so a batched `commit_wi` can run before those writes land: it then commits a partial state and the leftover is swept into the next savepoint as a second, split commit. Wait for the Step 1–3 calls to return, then call `commit_wi` once. This commits **only** the session's own vault journal files — the diary entry, task file, and notes file written in Steps 1–3 — via a scoped pathspec (plus, for an archived WI, the deletion of its old `tasks/current` path). It is commit-only (never pushes) and will not sweep in other sessions' files. Commit-count inflation is accepted; the priority is that every savepoint is durably recorded. For a WI-less session, call `commit_wi` without `wi_number` — it commits today's diary only.

**Non-git-backed vault (a valid setup).** If the effective vault is not git-backed — the fallback vault, or a project vault registered before `vault-init` — `commit_wi` returns `{ error: 'vault_not_git' }`. Treat that as a **benign no-op**: skip it silently, do not retry it, and do not surface it as an error. Likewise `{ reason: 'nothing_to_commit' }` (no changed journal files this savepoint) is a normal, silent result. The SessionStart directive already tells you at startup whether the vault is git-backed, so on a non-git vault you can simply omit the commit step; the `vault_not_git` return is the self-contained fallback signal if you call it anyway.

---

## Session Naming

Name every session with the `rename_session` tool. Build the title from these segments, in this order, separated by single spaces; omit absent segments:

```
WI-{N} {JIRA ID} {customer name} {repo}#{PR number} {topic}
```

- `WI-{N}` — the tracked work item, if any.
- `{JIRA ID}` — the ticket under investigation (e.g. `SNOW-1234567`), if any.
- `{customer name}` — the customer the session concerns, if any.
- `{repo}#{PR number}` — the pull request under review or change (repository name only, no org), if any.
- `{topic}` — always present; at most six words describing the subject.

A session with no identifier is named by `{topic}` alone. Examples: `WI-42 connector retry fix`, `SNOW-1234567 Example Corp ingestion stall`, `nifi#10234 validation review`.

Apply the name after Triage once the subject is known (immediately on WI pickup), and again whenever the subject changes: a WI is created or attached, or a JIRA ID, customer, or PR is identified. When a hook reports the current title, compare it to this convention and rename if inaccurate. Rename silently — do not announce the rename unless the operator asks.

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
3. **With operator approval**, load a session-analysis skill if one is installed (e.g. `$session-history`) to analyze the conversation log; if none is available, inspect the local conversation files directly (`~/.snowflake/cortex/conversations/`)

The conversation history is structured JSON but verbose — it's log exhaust, not curated reasoning. The notes file is intentionally written to preserve understanding; the conversation history captures everything including exploration paths and false starts. Use it as a fallback, not a primary source.

**Load** `references/save-recovery.md` for detailed procedures including reset detection and stale metadata handling.

---

## Create Work Item

### Step 1: Assign Number

Allocate the next WI number with the `xo-ops` `allocate_wi` tool — pass your `cwd`. It resolves the effective vault and scans that vault's `tasks/current` + `tasks/archive`, returning the next **per-vault** number (a brand-new vault starts at 1). There is **no counter file**: the scan is the source of truth.

**Multi-session race guard.** Allocation is a snapshot, not a lock. Call `allocate_wi` in the same turn you create the task file, and write the file before yielding. Never carry a number across turns or a user reply. If turns have elapsed, call `allocate_wi` again and use the new number — do not hand-roll a filename scan. On a late collision, the unstarted WI yields.

### Step 2: Create Task File

**Path**: `tasks/current/{project}-wi{N}-{kebab-slug}.md`

Where `{project}` is the leaf name of the current project directory (kebab-cased). This groups work items alphabetically by project when working across multiple repos.

Example: working in `~/Projects/my-cool-app/` → prefix is `my-cool-app`. Working in `~/Projects/my-org/xo/` → prefix is `xo`.

The `{project}` prefix and the canonical filename stem come from `resolve_paths({ cwd, wi_number })` (fields `project`, `task_path_stem` / `notes_path_stem` — append your `{kebab-slug}.md`); do not hand-derive them. (For reference, `project` is the sanitised repo-root basename, lowercased, spaces/underscores to hyphens.)

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
- Scratch: $XO_SCRATCH/
```

The `Scratch:` field is the **authoritative per-WI scratch path** — the transient handoff surface every stage uses for subagent outputs, drafts, and intermediate artifacts. Recording it here means downstream stages read it and use it directly, without checking or guessing. See `references/save-task-conventions.md`.

### Step 3: Allocate Scratch Space

Ensure the scratch dir exists in one call:

```
resolve_paths({ cwd, wi_number, create: true })
```

It returns the scratch path (the effective vault's `tmp/wi{N}/`) and creates it — contained under the vault, no manual `ls`/`mkdir`, no approval prompt. After this, stages read the `Scratch:` path from the task file and use it directly. Stages must never invent an alternative location (e.g. `/.xo/`, repo-local dirs).

### Step 4: Diary Entry

Append a creation entry to today's diary.

If the new WI is carved from or added to another WI, cite the origin in the literal form `WI-N` in both the task's `Related` field and this creation entry (e.g. "carved from WI-624"); `WI N` and `#N` are not matched. Then run the milestone awareness light check (`references/core-guidelines.md`, Freshness and concurrency) for the new WI.

Note: The index is updated automatically when the SessionStart hook fires — do not manually update `index.md`.

---

## Update Task File

Read the current task file, then update fields in-place per operator instruction. Common operations:

| Intent | Fields to Update |
|--------|------------------|
| "mark done" | Status → Done, StatusNote → completion summary, then **offer to archive** (see below) |
| "blocked on X" | Status → Blocked, Blocker → description |
| "shipped it" (PR opened / doc circulated / design staged, awaiting others) | Status → Shipped, StatusNote → name the awaited party + artifact (e.g. "awaiting reviewer X on PR #N") |
| "reviewer requested changes" | Status → InProgress (re-deliver → back to Shipped) |
| stage completed | advance `Provisional Pathway` (move the `(here)` marker) + refresh NextAction; if the stage looks done, offer the natural next stage once (dismissable) |
| "promote to Now" | Priority → Now |
| "fold into WI-N" | Status → Done, StatusNote → "Folded into WI-{N} — {reason}", then **offer to archive** (see below) |

### Finds Belonging to Another Work Item

File by ownership, not by which session found it. When work on one WI turns up something that belongs to another, put the decision or design in the owning WI's notes, placed against its open questions, and keep the evidence in the current WI's notes, with a pointer each way. Add a `Related` entry and refresh NextAction in both task files, and append a diary entry noting the cross-link. If another session is running the owning WI, raise it with the operator instead of writing to that WI's files (see Freshness and concurrency in `core-guidelines.md`).

### Archiving Completed Work Items

When a work item reaches terminal state (Status: Done), ask the user if they want to archive it.

If so, first update the task file content (Status → Done, StatusNote → completion summary, Priority → Archived), THEN call `archive_wi({ cwd, wi_number })` — it moves the file to `tasks/archive/{YYYY-MM}/` (current completion month, dir created if absent). `archive_wi` performs the MOVE ONLY; it does not edit task content, so make the field updates first. Then pass the archived WI's number to the next `commit_wi` (in `wi_numbers` alongside the current WI if needed), so the move is committed whole; otherwise the old `tasks/current` copy stays in HEAD.

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
2. **Diary is append-only.** Write it with `append_diary`; never read-edit-write. Multiple sessions may savepoint concurrently.
3. **Task file stays compact.** If it exceeds ~50 lines, content should migrate to the notes file.
4. **Savepoint on milestones and gates.** Savepoint when completing significant work (a feature, a fix, a decision) and before workflow gates — these are recoverable states.
5. **Savepoint on context warnings.** When hook reminders fire, treat them as interrupts — save state, then continue.
6. **Check before create for directories.** Before writing to any path under `notes/`, `diary/`, `tasks/archive/`, or `tmp/`, first `ls` the parent directory to see if it already exists. Only create the directory if `ls` confirms it is missing. An `ls` is low-friction (auto-approved); a `mkdir` requires user approval — so always prefer checking first.
7. **Commit after every savepoint.** After completing a savepoint write (Steps 1–3), call `commit_wi` to commit the session's own diary, task, and notes files. Call it **last and alone** — the final tool call of the turn, after the Step 1–3 writes have returned, never batched in parallel with them (unordered calls let it commit before the writes land → partial, duplicated commit). This is a scoped pathspec commit of vault journal directories (`diary/`, `tasks/`, `notes/`) only — it never commits code or any other repository. It is commit-only (never pushes). On a non-git-backed vault `commit_wi` returns `vault_not_git` — a benign no-op; skip it silently (same for `nothing_to_commit`).

---

## Reference Index

| Reference | Purpose | When to Load |
|-----------|---------|-------------|
| `references/refs-terminology.md` | Standard terminology across stages: artifacts, actions, roles, workflow terms | When clarifying what a term means or ensuring consistent language |
| `references/save-three-layer.md` | Recording model design: diary/task/notes separation, what goes where, recovery patterns | When explaining the model or training a new user |
| `references/save-context-urgency.md` | Hook system internals: urgency thresholds, context monitoring, state machine, configuration | When debugging savepoint behavior or adjusting thresholds |
| `references/save-recovery.md` | Detailed recovery procedures: context reset detection, stale metadata, session lifecycle | When recovering from compaction/summarisation or debugging recovery failures |
| `references/save-task-conventions.md` | Task file schema: mandatory fields, contextual fields, category patterns, field ordering | When creating or restructuring task files |
