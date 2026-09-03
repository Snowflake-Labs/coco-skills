---
name: persist-memory
title: Persist CoCo Memory
summary: Back up CoCo memory files and session summaries to a Snowflake table with SHA-256 dedup.
description: |
  Persists CoCo's local /memories/*.md files to a Snowflake table so project context,
  user preferences, and session history survive across machines and are queryable via SQL.
  Optionally summarizes recent CoCo sessions and stores the summaries alongside memory files.

  On first run, auto-creates the target database/schema/table if they don't exist.
  Subsequent runs use SHA-256 content hashing to skip unchanged files. Supports
  project-scoped sync (from a repo directory) or full sync (from playground or with "all").

  Triggers: persist memory, back up memory, save memory to snowflake, sync memory to table,
  persist coco memory, memory backup, memory to snowflake, persist-memory, back up my memories,
  save memories, persist sessions.

  Do NOT use for: reading or searching persisted memories (query the table directly),
  managing CoCo's local memory files (use the built-in memory tool), or restoring memories
  from Snowflake back to local files.
prompt: "Persist my CoCo memory files to Snowflake"
language: en
status: Published
author: Dash Desai
type: snowflake
tools:
  - memory
  - snowflake_sql_execute
  - bash
  - ask_user_question
---

## Overview

CoCo Desktop maintains local memory files under `/memories/` that capture project context,
user preferences, feedback rules, and reference links. These files are invaluable for
continuity across sessions — but they live only on the local machine. If you switch
machines, reset your environment, or want to query your memory history over time, the
local files aren't enough.

This skill persists every memory file to a Snowflake table with SHA-256 content hashing
for dedup. Optionally, it also summarizes recent CoCo sessions and stores those summaries
in the same table — giving you a queryable log of what you worked on and when.

## When to Use

- **Back up memory files** before a machine migration or environment reset
- **Keep a history** of how memory files evolved over time (every change creates a new row)
- **Query your project context** via SQL — find which projects you worked on, when decisions were made
- **Summarize sessions** you forgot to document — the skill reads transcripts and extracts key actions
- **Share context** with teammates by pointing them at the Snowflake table

## When NOT to Use

| Topic | Use instead |
|-------|-------------|
| Reading or editing local memory files | CoCo's built-in `memory` tool |
| Searching persisted memories by keyword | `SELECT * FROM <table> WHERE CONTENT ILIKE '%keyword%'` |
| Restoring memories from Snowflake to local | Manual: read from table, write with `memory` tool |
| General Snowflake backup/DR | Snowflake Time Travel and Fail-Safe |

## Workflow

### Step 0: Determine scope and resolve target table

1. **Ask the user** (if not already specified): "Which database and schema should I use for the memory table? Default: `COCO_MEMORY.PUBLIC.MEMORY_LOG`." Accept their answer or use the default.

2. **Determine sync scope** based on how the skill was invoked:
   - If the user said **"all"** or **"--sessions"**: full sync of all memory files. If `--sessions` was included, also run session summarization (Part B).
   - If invoked from the **playground** (`~/.snowflake/cortex/playground/workspace`): full sync of all memory files. Session summarization only if the user explicitly requested it.
   - If invoked from a **project directory**: identify the matching memory file for this project (by folder name → filename match in `/memories/`). Sync only that file.

### Step 1: First-run setup

Check if the target table exists:

```sql
SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_CATALOG = '{database}'
  AND TABLE_SCHEMA = '{schema}'
  AND TABLE_NAME = '{table}';
```

If it doesn't exist, create it:

```sql
CREATE DATABASE IF NOT EXISTS {database};
CREATE SCHEMA IF NOT EXISTS {database}.{schema};
CREATE TABLE IF NOT EXISTS {database}.{schema}.{table} (
    PROJECT_NAME    VARCHAR NOT NULL,
    MEMORY_FILE     VARCHAR NOT NULL,
    CONTENT         VARCHAR,
    CONTENT_HASH    VARCHAR NOT NULL,
    METADATA        VARIANT NOT NULL,
    SESSION_ID      VARCHAR NOT NULL,
    SAVE_TRIGGER    VARCHAR NOT NULL DEFAULT 'manual',
    NOTES           VARCHAR,
    SAVED_AT        TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);
```

Confirm to the user: "Table `{database}.{schema}.{table}` is ready."

### Step 2: Read memory files

**Full sync**: Use the `memory` tool to view the `/memories` directory. For each `.md` file (skip `MEMORY.md` — that's the index, and skip directories), read the full file content. Read files in parallel batches for efficiency.

**Project-scoped sync**: Read only the identified memory file from `/memories/`. Also check if a project-specific `MEMORY.md` exists under `/memories/projects/<slugified-path>/` — if it does, include it.

### Step 3: Parse each file

For every file in scope, extract these fields:

- **`PROJECT_NAME`**: The filename without `.md`. Example: `git-setup.md` → `git-setup`. Always derived from the filename, never from frontmatter.
- **`MEMORY_FILE`**: The filename as-is, including `.md`.
- **`CONTENT`**: The entire file content, unmodified.
- **`CONTENT_HASH`**: Will be computed via `SHA2()` in SQL.
- **`METADATA`**: A JSON object with three required fields parsed from the YAML frontmatter (the `---`-fenced block at the top):
  - `name`: from the `name:` field. If no frontmatter, use the filename without `.md`.
  - `type`: from the `type:` field (nested under `metadata:`). If missing, infer: files about a specific repo/app → `project`, tools/setup → `reference`, user info → `user`.
  - `description`: from the `description:` field. If missing, write a one-line description from the file's title and first paragraph.
  - All three must be non-null and non-empty. Never insert a row with incomplete metadata.
- **`SESSION_ID`**: Discover by running `ls -t ~/.snowflake/cortex/conversations/global/*.json 2>/dev/null | head -1` and extracting the UUID from the filename. If not found, use `manual-{today's date}`.
- **`SAVE_TRIGGER`**: `automation`.
- **`NOTES`**: `Memory sync — {today's date}`.

### Step 4: Dedup check and insert

For each file, check if the latest row already has the same content hash:

```sql
SELECT CONTENT_HASH FROM {target_table}
WHERE MEMORY_FILE = '{file}'
ORDER BY SAVED_AT DESC LIMIT 1;
```

If the hash matches, skip — content hasn't changed.

If new or changed, insert:

```sql
INSERT INTO {target_table}
  (PROJECT_NAME, MEMORY_FILE, CONTENT, CONTENT_HASH, METADATA, SESSION_ID, SAVE_TRIGGER, NOTES)
SELECT
  '{project_name}',
  '{memory_file}',
  $${content}$$,
  SHA2($${content}$$),
  PARSE_JSON($${metadata_json}$$),
  '{session_id}',
  'automation',
  'Memory sync — {today''s date}';
```

Use `$$` dollar-sign quoting for CONTENT to avoid single-quote escaping issues. If the content itself contains `$$`, use `$tag$content$tag$` with a unique tag.

**Important**: If `snowflake_sql_execute` fails with dollar-sign quoting (some execution paths don't support it), fall back to writing the content to a temporary file and using `snowflake-connector-python` with parameterized queries via `bash`:

```python
import snowflake.connector
# Use connection from ~/.snowflake/connections.toml
# Parameterized INSERT avoids all quoting issues
cur.execute("INSERT INTO ... SELECT %s, %s, %s, SHA2(%s), ...", (project_name, file, content, content, ...))
```

### Step 5: (Opt-in) List sessions and check captured

Only run this step if the user explicitly requested session summarization (`--sessions`, `all`, or "persist sessions").

Run via bash:

```bash
cortex conversations list --output csv --limit 1000 --origin coco:desktop
```

Check which sessions are already captured:

```sql
SELECT SESSION_ID FROM {target_table}
WHERE SAVE_TRIGGER = 'session-summary';
```

Skip any session ID that already has a row.

### Step 6: (Opt-in) Summarize and insert sessions

For each uncaptured session:

1. Fetch the transcript:
   ```bash
   cortex conversations transcript {session_id} --output json > /tmp/transcript_{session_id}.jsonl
   ```

2. Read the JSONL file. Extract user messages (where `role` is `user`). The `content` field may be a list of content blocks — extract `text` from blocks where `type` is `text` and `internalOnly` is not true. Strip `<system-reminder>` and `<system_context_from_session_hook>` tags.

3. From cleaned messages, write a summary:
   - **What was done**: 2-3 sentences summarizing the main tasks
   - **Key decisions**: Notable choices or configurations made
   - **Files/objects changed**: Files edited, tables created, deployments made
   - **Open items**: Anything left unfinished
   Keep under 1500 characters.

4. Infer the project name from session content:
   - Look for repo names/paths (e.g. `/Users/.../my-project` → `my-project`)
   - Look for database/table references
   - Use the conversation title if descriptive
   - If no clear match: `session-misc`. If completely unclear: `session-unclassified`.

5. Insert:
   ```sql
   INSERT INTO {target_table}
     (PROJECT_NAME, MEMORY_FILE, CONTENT, CONTENT_HASH, METADATA, SESSION_ID, SAVE_TRIGGER, NOTES)
   SELECT
     '{inferred_project_name}',
     'session-{session_id}.md',
     $${summary}$$,
     SHA2($${summary}$$),
     PARSE_JSON('{"name":"{project_name}","type":"session","description":"{title}"}'),
     '{session_id}',
     'session-summary',
     'Auto-summarized from CoCo session {session_id}';
   ```

### Step 7: Validate and report

Run validation:

```sql
SELECT PROJECT_NAME, MEMORY_FILE,
       PROJECT_NAME = REPLACE(MEMORY_FILE, '.md', '') AS name_ok,
       METADATA:name::VARCHAR IS NOT NULL AND METADATA:name::VARCHAR != '' AS has_name,
       METADATA:type::VARCHAR IS NOT NULL AND METADATA:type::VARCHAR != '' AS has_type,
       METADATA:description::VARCHAR IS NOT NULL AND METADATA:description::VARCHAR != '' AS has_desc
FROM {target_table}
QUALIFY ROW_NUMBER() OVER (PARTITION BY MEMORY_FILE ORDER BY SAVED_AT DESC) = 1
ORDER BY MEMORY_FILE;
```

Session summary rows will have `name_ok = FALSE` (expected — different naming pattern). Only flag non-session rows where `name_ok` is FALSE.

Present a summary in chat:

```
Memory Sync Complete — {today's date}

Files: {total} scanned, {inserted} inserted, {skipped} unchanged
Sessions: {checked} checked, {new} summarized, {already} already captured
Validation: PASSED (or FAILED with details)
Target: {database}.{schema}.{table}
```

## Common Mistakes

- **Dollar-sign quoting conflicts**: If memory file content contains `$$`, the INSERT will fail. Always check and use a unique tag like `$m1$...$m1$` instead.
- **Hash mismatches across runs**: The SHA2 hash is computed by Snowflake on the exact string inserted. If the `memory` tool returns slightly different whitespace between sessions, hashes will differ and a "duplicate" row is inserted. This is by design — it captures the state at each sync point.
- **Missing frontmatter**: Some memory files don't have YAML frontmatter. Always infer the three metadata fields from context rather than inserting NULL values.
- **Session transcript format**: The `content` field in JSONL transcripts is a **list of content blocks**, not a plain string. Each block has `type`, `text`, and optionally `internalOnly`. Always handle the list format.
- **Permissions**: The skill needs CREATE DATABASE/SCHEMA/TABLE privileges on first run. If the user's role can't create these, ask them to create the table manually and provide the fully-qualified name.

## Examples

**Full sync (default):**
> "Persist my CoCo memory files to Snowflake"

Creates `COCO_MEMORY.PUBLIC.MEMORY_LOG` if needed, syncs all `/memories/*.md` files, reports results.

**Custom target table:**
> "Persist memory to MY_DB.ANALYTICS.COCO_MEMORY_LOG"

Uses the specified table (creates if needed).

**Include session summaries:**
> "Persist all my memories and sessions to Snowflake"

Syncs memory files AND summarizes all uncaptured CoCo Desktop sessions.

**Project-scoped sync (from a repo directory):**
> "Persist memory"

Syncs only the memory file matching the current project directory.

## Stopping Points

- **After Step 0**: Confirm target table and scope with the user before proceeding.
- **Before Step 5**: If session summarization was requested, confirm: "I'll now summarize {N} uncaptured sessions. This reads transcripts and stores summaries in Snowflake. Proceed?"

## Consistency Rules

- `PROJECT_NAME` is ALWAYS the filename without `.md` for memory files — never use the frontmatter `name` field.
- For session summaries, `PROJECT_NAME` is the inferred project name; `MEMORY_FILE` is `session-{session_id}.md`.
- `METADATA` must always have all three fields: `name`, `type`, `description`. For sessions, `type` is always `session`.
- `SESSION_ID` must always be populated — never NULL.
- Never insert a duplicate: if the latest row for a file has the same `CONTENT_HASH`, skip.
