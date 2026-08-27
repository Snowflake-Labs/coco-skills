---
name: save-context-urgency
description: "Hook system internals for context monitoring and savepoint reminders. Covers urgency thresholds, state machine, configuration, and debugging."
---

# Context Urgency Protocol

The context urgency system uses Cortex Code plugin hooks to monitor context fullness and inject escalating savepoint reminders. Two hooks work together: `UserPromptSubmit` (fires on user turns) and `PostToolUse` (fires during autonomous tool-use cycles). Hooks are registered automatically when the xocortex plugin is loaded via `settings.json plugins[]`.

## Architecture

### Files

| File | Location | Role |
|------|----------|------|
| `settings.json` env block | `~/.snowflake/cortex/settings.json` | `XOCORTEX_HOME` env var: memory repo path |
| `_common.js` | `xo/plugin/hooks/_common.js` | Input parsing, config, logging, path derivation |
| `_context-monitor.js` | `xo/plugin/hooks/_context-monitor.js` | Token extraction, context state machine, urgency thresholds |
| `reindex-tasks.js` | `xo/plugin/hooks/reindex-tasks.js` | Task reindexer (also callable directly) |
| `sessionstart.js` | `xo/plugin/hooks/sessionstart.js` | Injects context, reindexes tasks, handles compact/resume |
| `userpromptsubmit.js` | `xo/plugin/hooks/userpromptsubmit.js` | User-turn savepoint with verbose urgency reminders |
| `posttooluse.js` | `xo/plugin/hooks/posttooluse.js` | Autonomous-turn savepoint with concise reminders |
| `hooks.json` | `xo/plugin/hooks/hooks.json` | Hook registration — loaded automatically by plugin system |

### Data Flow

1. **Session starts** — `sessionstart.js` fires, reindexes work items, injects context sources. On `compact`, recovery instructions are injected.
2. **Each user prompt** — `userpromptsubmit.js` computes context %, injects full savepoint instructions if urgency threshold met.
3. **Each tool response** — `posttooluse.js` uses the same state file and thresholds, injects concise context annotations.
4. **Agent acts** — writes diary/notes/task per the recording model.
5. **Post-reset** — `sessionstart.js` fires with `source=compact` or `source=summarize`, injects recovery instructions.

### Shared Context Engine

`_context-monitor.js` is the core engine. Values are passed via environment variables before requiring:

| Variable | userpromptsubmit | posttooluse |
|----------|-----------------|-------------|
| `PROMPT_TOKENS_EST` | `prompt_length / 4` | `0` |
| `RESPONSE_BUFFER` | `2000` | `0` |
| `HOOK_SOURCE` | `"userprompt"` | `"posttool"` |

The engine exports `MONITOR_RESULT` (`"fire"` or `"skip"`), `LEVEL`, `REPORTED_PCT`, `PROJECTED_PCT`.

## Urgency Thresholds

| Level | Context % | Condition | Behaviour |
|-------|-----------|-----------|-----------|
| (silent) | < 50% | any | No reminder injected |
| `[routine]` | 50-69% | delta ≥ 10% | Record progress when convenient |
| `[urgent]` | 70-79% | any | Write savepoint before continuing |
| `[critical]` | 80-84% | any | Write immediately — all three layers |
| `[final]` | ≥ 85% | any | Last chance — context reset at ~90% |

Deduplication at routine level uses delta from last savepoint. Higher levels (70%+) always fire. Both hooks share the same context state file.

## Context State Machine

### State File

Each session stores a single integer in `/memories/context-state/{session-id}`:

| Value | Meaning |
|-------|---------|
| `0` | No baseline yet — will be set from next `response_metadata` |
| `N` | Last baseline was N% context fullness |

### Delta: The Decision Metric

```
delta = current_pct − last_baseline_pct
```

- **Positive delta (+15%)**: Context growing. Fire if large enough.
- **Small positive (+3%)**: Suppress to avoid noise.
- **Negative delta (−40%)**: Context dropped (compaction/summarisation). Reset baseline.

### State Transitions

```
startup       → (no context state file yet)
prompt 1      → no response_metadata → no action
prompt 2      → baseline_set N% → file contains N
prompt 3+     → cycle_check → delta comparison → fire or skip
tool use      → same cycle_check logic → shared state file
compact       → context drops, metadata stale for 1 cycle
next event    → current < baseline → baseline_reset → file contains M%
summarisation → context drops mid-session (no lifecycle event)
next event    → current < baseline → baseline_reset
```

### Negative Delta: Detecting Context Drops

Negative delta reliably detects context drops from any cause: compaction, summarisation, stale metadata. When `current_pct < baseline`, the system resets the baseline to the new (lower) percentage and resumes normal tracking.

This is strictly better than sentinel values because summarisation drops context mid-session without triggering any lifecycle event. Only delta comparison catches it.

## Configuration

The `XOCORTEX_HOME` environment variable (set in `settings.json` env block) provides the memory repo path. All other paths are derived:

```
XOCORTEX_HOME=~/Projects/xocortex
```

Derived paths:
- `diary`: `${XOCORTEX_HOME}/diary/${YYYY-MM}/${YYYY-MM-DD}.md`
- `tasks_dir`: `${XOCORTEX_HOME}/tasks`

Context state files: `${SNOWFLAKE_HOME:-~/.snowflake}/cortex/memory/context-state/`


## Hook Input Format

All hooks receive JSON on stdin from Desktop:

```json
{
  "session_id": "uuid",
  "cwd": "/path/to/workspace",
  "hook_event_name": "PostToolUse",
  "response_metadata": {
    "usage": {
      "tokens_consumed": [{
        "input_tokens": {"total": 145000},
        "output_tokens": {"total": 8500},
        "context_window": 200000
      }]
    }
  }
}
```

`response_metadata` is a **trailing indicator** — reflects the previous turn, not the current one. Without it (first prompt, CLI/SDK), hooks exit silently.

## Debugging

### Log Location

`~/.snowflake/cortex/logs/YYYY-MM-DD-hooks.log`

### Useful Filters

```bash
grep "SESSION_ID" ~/.snowflake/cortex/logs/YYYY-MM-DD-hooks.log
grep "→ fire" ~/.snowflake/cortex/logs/YYYY-MM-DD-hooks.log
grep "posttool:.*→ fire" ~/.snowflake/cortex/logs/YYYY-MM-DD-hooks.log
grep "baseline_reset" ~/.snowflake/cortex/logs/YYYY-MM-DD-hooks.log
```

## Limitations

- Context % is `(input_tokens.total + output_tokens.total) / context_window` — may not exactly match Desktop UI ring
- First prompt has no `response_metadata` — no decisions until at least one response
- `response_metadata` fields are not always fully populated — best-effort
- CLI/SDK does not expose `response_metadata` to hooks — Desktop only

## Dependencies

- Node.js built-ins (`fs`, `os`, `path`) — used by JS hooks for JSON parsing and file I/O
- Cortex Code Desktop (minimum build with hooks `response_metadata` passthrough)
