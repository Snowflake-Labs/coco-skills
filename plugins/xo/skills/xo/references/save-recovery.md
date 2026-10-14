---
name: save-recovery
description: "Detailed recovery procedures for resuming work after context reset (compaction or summarisation), new session, or cold start. Covers reset detection, stale metadata, and session lifecycle."
---

# Recovery Protocol

Procedures for resuming agent work after context loss. The recording model (diary/task/notes) provides the recovery surface — this reference covers how to use it.

## Recovery Scenarios

### 1. Cold Start — New Session, Assigned Work Item

The operator has told you which WI to work on.

**Steps:**

1. **Read task file** — glob for `tasks/current/*wi{N}-*.md` (the project prefix varies; match on the WI number)
   - Identify: Status, NextAction, Blockers
   - This tells you **what to do next**

2. **Read notes file** — glob for `notes/**/*wi{N}-*.md` (may be in any month directory)
   - This carries the deep understanding: decisions, evidence, architecture context
   - Read the most recent session section for the latest state
   - This tells you **what you know**

3. **Read recent diary entries** for momentum
   - Session IDs in diary headings won't match yours (this is a new session)
   - Look for recent activity on this WI

4. **Resume** from the `NextAction` in the task file
   - The notes file provides the context needed to execute that action intelligently

### 2. Cold Start — New Session, No Assigned Work Item

No specific WI assigned. Orient on the portfolio.

**Steps:**

1. **Read task index** at `tasks/index.md`
   - Scan for `Now` priority items — these are the current focus
   - Scan for `Next` priority items — these are queued
   - Note any `InProgress` status items — these may need continuation

2. **Read today's diary** at `diary/{YYYY-MM}/{YYYY-MM-DD}.md`
   - Understand what's been happening today
   - Identify momentum and recent decisions

3. **Present** active work items to operator:
   ```
   Active work items:
   - Feature-X [Now, InProgress]: API Refactor — NextAction: write tests
   - Bug-123 [Now, Ready]: Cache Fix — NextAction: reproduce issue
   
   Which would you like to work on?
   ```

### 3. Post-Reset — Same Session, Context Lost

Context window was reset (compaction or summarisation) mid-session. The conversation summary provides a thin approximation of what was happening.

**Steps:**

1. **Read the conversation summary** — understand what the system thinks you were doing
2. **Read task file** — ground truth for current state (more reliable than summary)
3. **Read notes file** — the deep record that the summary compressed away
4. **Read today's diary** — find entries with your session ID `[{session-id}]`
   - You know your own session ID
   - Diary entries with matching session ID are from this session
5. **Reconcile** — if the summary and notes disagree, trust the notes file
6. **Resume** from NextAction, informed by notes context

### 4. Resume — Session Restored from Disk

Desktop restored a prior session from its local storage. Context is preserved but may be stale.

**Steps:**

1. **Check diary** for any entries since the session was last active
   - Other sessions may have advanced the WI
2. **Re-read task file** — status may have changed
3. **Continue** from where you left off, adjusting for any external changes

## Compact Detection

The hook system detects context resets (compaction or summarisation) via **negative delta** in context monitoring:

| Signal | Meaning |
|--------|---------|
| `sessionstart.sh` fires with `source=compact` or `source=summarize` | Explicit context reset lifecycle event |
| Context % drops significantly between events | Summarisation (no lifecycle event) |
| `response_metadata` reports values > 100% | Stale pre-reset data (transient, 1-2 events) |

After context reset, the first 1-2 `response_metadata` readings may be stale (reporting pre-reset values). The context urgency system handles this automatically via baseline reset.

## Stale Metadata Handling

`response_metadata` is a trailing indicator — it reports the previous turn's state. After context reset:

```
Event 1 post-compact: reported=122% (stale) → baseline_reset
Event 2 post-compact: reported=25% (fresh) → baseline_set
Event 3+: normal tracking resumes
```

The agent should not be alarmed by >100% readings immediately after context reset. They settle within 3-5 events.

## Session Lifecycle Events

| Event | Hook | Action |
|-------|------|--------|
| `startup` | `sessionstart.sh` | Fresh session. Inject context sources, reindex tasks. |
| `compact` | `sessionstart.sh` | Context was compacted. Inject recovery instructions + session extract. |
| `resume` | `sessionstart.sh` | Session restored from disk. Preserve context state. |

### Session Start Injection

On every session start, `sessionstart.sh` injects:
- Path to today's diary
- Path to tasks directory
- Task index content (if under size threshold)
- Session ID for diary headings

On `compact`, additionally injects:
- Recovery instructions directing the agent to read task + notes files
- Pre-reset session extract (if available)

## Recovery Verification

After recovering, verify your understanding before proceeding:

1. **State the current WI and status** — confirm with operator if uncertain
2. **State your understanding of next action** — "I believe the next step is X. Correct?"
3. **Identify any gaps** — "The notes mention Y but I'm unclear on Z. Can you clarify?"

This prevents the failure mode where a recovering agent confidently proceeds in the wrong direction based on an incomplete summary.

## Recovery Fallback

When the three-layer model is insufficient — files disagree, are missing, or the operator corrects you:

### Fallback Hierarchy

| Priority | Source | When to Use | Characteristics |
|----------|--------|-------------|-----------------|
| 1 (Primary) | Task → Notes → Diary | Always try first | Structured, curated, authoritative |
| 2 (Fallback) | `/memories/` (memory tool) | If primary files incomplete | May have scratch files, working state |
| 3 (Last Resort) | Conversation history | If files disagree or operator corrects | Structured JSON, but verbose log exhaust |

The agent knows its own session ID. After reset, diary entries with matching `[{session-id}]` headings are from this session — use these to understand what happened before the reset.

To inspect conversation history, load `$session-history` skill which provides Python tools for conversation.json analysis.

### When to Escalate to Conversation History

- Task and notes files contradict each other
- Operator says "no, we weren't working on that"
- Recovery files are missing or clearly stale
- Agent cannot make sense of the state from primary sources

### How to Use Conversation History

1. **Surface confusion** — don't silently fall back. Tell the operator:
   > "The recovery state is unclear — the task file says X but the notes suggest Y. Would you like me to inspect the conversation history?"

2. **Wait for approval** — conversation history is verbose; the operator may prefer to clarify verbally

3. **With approval**, load `$session-history` skill:
   - Provides Python tools for conversation.json analysis
   - Search for recent WI references
   - Look for the last savepoint message
   - Identify what was actually being worked on

4. **Reconcile** — once you understand the true state:
   - Update task file with correct status/NextAction
   - Append clarification to notes file
   - Inform operator of corrections made

### Why This Is Last Resort

The conversation history is:
- **Verbose** — full turns, tool calls, exploration paths
- **Log exhaust** — captures everything, not curated reasoning
- **Low signal-to-noise** — includes false starts, abandoned approaches, raw tool output

The notes file is intentionally written to preserve understanding. The conversation history is what happened, not what matters. The three-layer model exists precisely to avoid re-processing log exhaust. Use this fallback sparingly, and when you do, update the primary files so future recovery doesn't need it.

## Anti-Patterns

| Anti-Pattern | Why It Fails | Correct Approach |
|-------------|-------------|-----------------|
| Trust conversation summary over notes | Summary is a lossy compression | Read notes file — it's the deep record |
| Skip reading notes for "simple" tasks | Depth of understanding is invisible | Always read notes — you can't assess what you've lost |
| Re-derive understanding from code | Burns context on rediscovery | Read notes first — prior analysis is preserved |
| Savepoint only to diary | Diary is too thin for recovery | All three layers: notes (deep), task (status), diary (index) |
| Read-edit-write the diary | Race condition with concurrent agents | Append-only operations |
| Silently fall back to conversation history | Operator unaware of confusion | Surface confusion, ask for approval |
