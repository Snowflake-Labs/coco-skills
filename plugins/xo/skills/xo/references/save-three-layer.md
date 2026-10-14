---
name: save-three-layer
description: "Recording model design for multi-session agent work. Explains diary/task/notes separation, what content goes where, and how recovery uses each layer."
---

# Three-Layer Recording Model

Progress is recorded across three artifact types, each with a distinct role. The separation prevents duplication and keeps each artifact lean enough for its purpose.

## Layer Summary

| Layer | Artifact | Role | Lifecycle | Ceiling |
|-------|----------|------|-----------|---------|
| **Diary** | `diary/{YYYY-MM}/{YYYY-MM-DD}.md` | Thin chronological savepoint log | Append-only | 5-10 lines per entry |
| **Task file** | `tasks/current/{project}-wi{N}-*.md` | Living current state of work item | Updated in-place | Compact (~50 lines max) |
| **Notes file** | `notes/{YYYY-MM}/{project}-wi{N}-*.md` | Structured knowledge and reasoning | Append or rewrite per phase | As long as needed |

## Diary: Chronological Savepoint Log

The diary answers **"what happened today, in what order?"** Each entry is a savepoint marker, not a design document.

### Format

```markdown
## [{short_session_id}] Bug-123: Chat Overlap — Fixes F+G
- Applied orphaned DOM cleanup (Fix F) and tool pinning leak fix (Fix G)
- Decision: cleanup at diff-render level, not aggressive node clear
- Status: InProgress — awaiting user testing of multi-monitor scenario
- Notes updated: `notes/2026-03/wi46-chat-overlap-bug-investigation.md`
```

The diary always gets an entry for a session that did substantive work, keyed on the short session id. A WI reference is included when the session is tracked under a work item; a lightweight WI-less session drops the WI prefix and the notes pointer and keys on the session id alone.

### A Diary Entry Contains

- **Session ID + optional WI reference** (the heading — WI included only when the session is tracked under one)
- **1-2 line summary** of what was accomplished
- **Key decision** (if any, one line)
- **Current status** (one line)
- **Pointer to notes file** (if detailed content was written)

### A Diary Entry Does NOT Contain

- Files changed lists (belong in task file or commit)
- Architecture discussion (belongs in notes)
- Duplicated status fields (task file is source of truth)
- Detailed fix descriptions (belongs in notes)
- Reproduction steps, hypotheses, evidence (belongs in notes)

### Concurrency Safety

The diary is a single file per day. Multiple parallel sessions append to it. Short entries minimise conflict surface.

**CRITICAL:** Diary writes must be append-only operations.
- Bash: `echo "..." >> diary.md` or `cat >> diary.md << 'EOF'`
- Never use `edit`, `multi_edit`, or any tool that reads the file first
- This prevents race conditions when multiple agents savepoint concurrently

## Task File: Living Current State

The task file answers **"what is the current state of this work item?"**

It is the source of truth for status, blockers, next action, and validation criteria. A cold-start agent reads this to understand **what to do next**, not the full investigation history.

Key properties:
- Updated in-place by the working session
- Stays compact — migrate content to notes file if it exceeds ~50 lines
- Per-WI, so no concurrency issues
- See `references/save-task-conventions.md` for schema

## Notes File: Reasoning History and Structured Knowledge

The notes file answers **"what do we know about this work item and how did we get there?"**

This is the deep record of investigation, design reasoning, architecture context, hypotheses, evidence, and decisions. It is the primary vehicle for preserving understanding across context windows.

### Why Notes Files Are Critical

- The conversation history is verbose; notes are condensed-to-what-matters
- A recovering agent reads notes to regain the depth of understanding it had before context reset (compaction or summarisation)
- Structured notes are more efficient than re-reading conversation transcripts
- Notes accumulate across sessions — each session appends its section

### Notes Files Should Contain

- Architecture context and code path analysis
- Root cause analysis and hypothesis tracking
- Design decisions with rationale
- Evidence tables and test results
- Fix descriptions with file locations and line numbers
- Chronological progress sections (what was tried, what worked, what didn't)

### Notes File Structure

```markdown
# WI-{N}: {Title} — {Description}

## Session {session_id} ({YYYY-MM-DD}): {Phase}

### Findings
{content}

### Decisions
{content}

### Open Questions
{content}
```

Each session appends a new section header. The file grows over the lifetime of the work item.

## Recovery Patterns

| Recovery Scenario | What Agent Reads | Why |
|-------------------|-----------------|-----|
| Cold start (no assigned WI) | Task index → diary | Orient on portfolio and recent momentum |
| Cold start (assigned WI) | Task file → notes file | Understand current state and accumulated knowledge |
| Post-reset (same session) | Task file → notes file → diary `[{session-id}]` entries | Resume with full context from this session |
| Operator review | Diary | Scan the day's activity chronologically |

## Design Rationale

### Why Three Layers (Not One)

A single file would either be too verbose for scanning (diary use case) or too compressed for recovery (notes use case). The separation allows each artifact to optimize for its reader:

- **Diary** → optimized for human scanning across multiple WIs
- **Task file** → optimized for agent cold-start orientation
- **Notes file** → optimized for agent deep-context recovery

### Why Notes Are Separate From Task Files

Task files stay compact because they serve two masters: human supervisors scanning the index, and agents needing quick orientation. Deep reasoning would bloat the task file and make scanning harder.

Notes files grow freely because they serve one master: a recovering agent that needs to reconstruct its understanding. Length is a feature, not a bug.

### Why Diary Is Append-Only

Multiple agents may savepoint to the same daily diary file concurrently. Read-edit-write creates race conditions where one agent's edits overwrite another's. Append-only operations are atomic at the filesystem level.
