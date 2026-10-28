---
name: migrate-snowsql
title: Migrate SnowSQL
summary: States the migration in the host plan, then rewrites in place only after that plan is approved. Writes migration-plan.md only when the host has no plan mode.
description: "Analyzes a SnowSQL project without editing it. When the host can submit a plan for approval, that plan is the migration plan: approving it rewrites the project files in place, and leaving it unapproved changes nothing. migration-plan.md and an implement / report-only question are the fallback when the host has no plan mode. The rewrite includes output rewrites for SnowSQL parity and does not write a second copy. After the rewrite, reports what was done and asks whether to run snow helpers import-snowsql-connections. Use when migrating SnowSQL scripts, connection files, or SNOWSQL_* variables to Snowflake CLI. Triggers: migrate SnowSQL, convert snowsql scripts, import-snowsql-connections, check-snowsql-env-vars, $migrate-snowsql. Snowflake CLI is a separate client, not a drop-in replacement. Not for unattended migration."
tools:
  - Bash
  - Read
  - Write
  - Edit
  - Grep
  - Glob
prompt: Migrate my SnowSQL scripts and connections to Snowflake CLI.
language: en
status: Hidden
author: Jan Bilek
type: snowflake
---

# Migrate SnowSQL

## Overview

Migrate SnowSQL 1.4 or 1.5 scripts to Snowflake CLI. Analyze first, without questions and without editing the project. Present the migration plan through the host's plan mode when this session can submit one. Approving that plan rewrites the project files in place. Leaving it unapproved changes nothing. `migration-plan.md` is only the fallback when the host has no plan mode; that path asks implement or report-only. Implement applies every `compatible` replacement and every `rewrite required` change, for SnowSQL output parity. Do not write a second tree. After the rewrite, say what changed and ask about connection import. Do not write `snowsql-migration-report.md`. Pause only for live SQL, new stages/file formats, and constructs with no rule. Below 1.4, stop and say to upgrade first.

Rule list: [references/compatibility.md](references/compatibility.md). Load a reference only when that step needs it. Dispositions: `compatible`, `rewrite required`, `manual review`. A construct with no equivalent is `manual review`. There is no `blocked` disposition.

Out of scope: continuous SnowSQL sync, runtime SnowSQL compatibility, config-v2, unattended migration.

## Working directory

Write `rewrite.diff` in the current working directory. Do not pick another path. Do not write `snowsql-migration-report.md`.

The migration plan lives in the host plan. Write `migration-plan.md` in the working directory only when this session cannot submit a host plan. A chat reply is not a host plan. A host plan is a plan the user approves before any project edit. Cortex Code calls `enter_plan_mode`, then `exit_plan_mode` with this plan. Claude Code calls `EnterPlanMode`, then `ExitPlanMode` with this plan. Cursor switches to Plan mode and puts this plan there; building it is the approval. Any other host uses its own plan approval. If that mode is missing or refused, use the fallback.

Do not edit a project file until that plan is approved, or, on the fallback, until they choose implement. Then rewrite those files in place. Do not create `snowsql-migrated/` or any other copy of the project.

## Workflow

Track this yourself. Do not show it to the user.

```text
Migration progress:
- [ ] 1. Preflight
- [ ] 2. Analyze
- [ ] 3. Submit the host plan, or write the fallback file, then wait
- [ ] 4. Rewrite files in place, if they approve
- [ ] 5. Say what changed and offer connection import
```

### References (load on demand)

| When | Read |
| --- | --- |
| Start, and before import | [references/preflight.md](references/preflight.md) |
| Classify any construct | [references/compatibility.md](references/compatibility.md) |
| `SNOWSQL_*` or SnowSQL config / import | [references/configuration.md](references/configuration.md) |
| JSON, CSV, TSV, HTML, grid, plain, spool, async, single-transaction, query-tag, `header=false`, large extract | [references/output-gaps.md](references/output-gaps.md) |
| Before submitting the plan | [references/validation.md](references/validation.md) |
| Tricky rewrite | [references/mistakes.md](references/mistakes.md) |

### 1. Preflight

Load [references/preflight.md](references/preflight.md) and follow it. Record OS and both client versions. Gate SnowSQL to 1.4/1.5 and Snowflake CLI to `3.30.0.dev0` or later (including the importer help-text check). Do not print secrets. Leave SnowSQL installed. Do not edit its config.

### 2. Analyze

Load references from the table above as needed. Run `snow helpers check-snowsql-env-vars` first (read-only).

Read the project. Search for `snowsql`, `SNOWSQL_`, `-o`, lines that start with `!`, and `&`, and read every file that matches, plus every SQL file a script passes with `-f`. Match each construct to a rule in compatibility.md. Do not edit anything.

Build the plan below. Do not add a file inventory, a proposed-edits list, a counts section, an output-gaps section, or a blocked section. The plain summary is part of the plan. The two tables follow it.

```markdown
# SnowSQL migration plan

STATUS: STOPPED_BEFORE_MUTATION

Nothing has changed yet. Approving this plan edits the project files in place.

<n> lines only need snowsql renamed to snow sql.

<n> lines need a different command. The main kinds:
- <plain group>: <one sentence saying what changes for the person reading the script, and whether the result stays the same>

Left as written:
- <file>:<line> <what is there>: <what a caller would see>

## Findings

| Location | Found | Rule | Disposition | Action |
| --- | --- | --- | --- | --- |
| run.sh:4 | `snowsql` | invocation | compatible | `snow sql` |
| run.sh:4 | `-o exit_on_error=false` | exit-on-error-false | rewrite required | One `snow sql` per statement. Ignore a failing command. |

## Manual review

| Location | What a caller sees | Implement |
| --- | --- | --- |
| 20-probe.sh:4 | `--probe-connection` prints different text | Leave unchanged. |
| 10-output-json.sh:5 | CLI pretty-prints JSON; `1` is a number, SnowSQL writes `"1"` | Accept CLI bytes. `jq` is not installed. |
| 34-tsv.sh:5 | `out/result.tsv` contains CSV | Write CSV to `out/result.tsv`. Do not rename it. |
```

Findings lists only what Implement changes: `compatible` and `rewrite required`. One row per construct. `Found` is the construct as written, with secret values replaced by `<redacted>`. A file whose worst disposition is `manual review` does not appear here. Its other constructs are not repeated.

Manual review is the other section. It holds a construct with no equivalent, a redacted secret, and every output gap. One row per item. An output gap names the byte difference and the Suggested fix from output-gaps.md. Implement applies that fix. `Leave unchanged.` means the file is not edited. Put the `QUOTING:` words on every CSV row in Manual review. Do not write "ask the user". An output gap stays in this section and also stays a Findings row: the file is still rewritten, and the original output path stays the same.

Validate the plan against validation.md (static checks). Fix any failure, then re-check. Do not edit project files, import connections, or run SQL yet.

The first count is the number of Findings rows with disposition `compatible`. Do not list those lines. Group every rewrite by what it does to the script, not by rule name. At most 6 group lines. No rule ids, no flag soup, no arrows. A group with no rows is omitted. Say where output bytes will still differ, in that group's sentence.

Every Manual review row is one sentence under `Left as written`: the file, what is there, and what a caller would see. A redaction is not one of those lines. When that count is zero, the body is the single word `None`. Approving the plan accepts those rows as written. Do not ask the user to decide them one by one.

### 3. Submit the plan

No secrets. Write for someone who has not seen the rule names.

**Host plan.** If this session can submit a plan for approval before edits, enter that mode if you are not already in it, then submit this plan. Cortex Code calls `enter_plan_mode`, then `exit_plan_mode` with the plan text. Claude Code calls `EnterPlanMode`, then `ExitPlanMode` with the plan text. Cursor switches to Plan mode and puts this plan there; building it is the approval. Any other host uses its own plan approval. Do not write `migration-plan.md` or `snowsql-migration-report.md`. Do not ask implement or report-only. In chat, say only that nothing has changed and that approving the plan edits the files in place. Do not paste the plan into chat. Stop until they approve. Approval is the decision to edit. Leaving the plan unapproved, or refusing plan mode, stops with no project changes. Refusing plan mode uses the fallback.

**Fallback.** If this session cannot submit a host plan, write the same plan to `migration-plan.md`. Do not write `snowsql-migration-report.md`. Send the briefing below. Do not paste the plan into chat.

```text
Nothing has changed yet. If you reply implement, I edit your files in place.

<n> lines only need snowsql renamed to snow sql.

<n> lines need a different command. The main kinds:
- <plain group>: <one sentence saying what changes for the person reading the script, and whether the result stays the same>
Full list: migration-plan.md

Left as written:
- <file>:<line> <what is there>: <what a caller would see>

Reply: implement / report-only
```

Implement edits every Findings file in place. Manual review files marked `Leave unchanged` stay as they are. If they already approved the plan, or already chose implement, print `Rewriting your files in place.`, and go to Implement. Report-only stops. Do not ask about output gaps or import yet.

### 4. Implement

Edit the project files in place. Do not write a second tree. Apply every Findings row without asking again. Leave a Manual review file marked `Leave unchanged` as written. Rewrite a defined `&name` to `<% name %>`. Change `snowsql` to `snow sql`. Before the first edit, snapshot only the files you will change, write `rewrite.diff` (`diff -u` against that snapshot), then delete the snapshot.

Replacement detail stays in compatibility.md. Apply each Suggested fix in Manual review when that row is an output gap on a Findings file. output-gaps.md chooses it from the consumer and from tools already installed. The command stays `snow sql --format … > <original path>`. Keep every `-o output_file` path and extension. A TSV file with no TSV tool still uses its original path, and the bytes are CSV. Record `QUOTING: REWRITE` only when a quote-all pipe was applied.

**Manual review.** Do not invent a translation for a `Leave unchanged` row. Name those files in the closing note. Walk through one only if they ask after that note.

Do not import connections in this step.

### 5. Closing note and import offer

After Implement, send the note below. Do not write `snowsql-migration-report.md`. A plan covers only the files you read. Live SQL needs another yes and a named non-production connection.

After the files are rewritten, send this once. Name the command. Do not run it until they say yes. Preflight must still pass.

```text
I edited your files in place.

<n> lines: snowsql is now snow sql.
<n> lines needed a different command. Same groups as the plan summary.
Left as they were:
- <file>:<line> <what is still there, and why>

Import connections? snow helpers import-snowsql-connections
Backs up the CLI config first. May prompt on overwrite. Not a sync.
Reply: yes / no
```

Same wording rules as the plan summary. `Left as they were` is `None` when nothing was held back.

If they say yes and Preflight passed: load configuration.md, back up the Snowflake CLI config file, run `snow helpers import-snowsql-connections` once, show a redacted before/after of connection names and key names. Import does not approve script edits or SQL.

## Stopping points

- After the host plan is submitted, until they approve it. On the fallback, after the briefing, for implement vs report-only.
- After the in-place rewrite, for connection import (with the command named).
- Before creating a stage or file format the project does not already have.
- Before each live SQL session, with a named non-production connection.

## Examples

SnowSQL file `30-continue-on-error.sh`:

```bash
snowsql -c example -o exit_on_error=false -f sql/exit-on-error.sql
```

`sql/exit-on-error.sql` has three statements. Plan rows:

| Location | Found | Rule | Disposition | Action |
| --- | --- | --- | --- | --- |
| 30-continue-on-error.sh:4 | `snowsql` | invocation | compatible | `snow sql` |
| 30-continue-on-error.sh:4 | `-c example` | connection | compatible | Same flag. |
| 30-continue-on-error.sh:4 | `-o exit_on_error=false` | exit-on-error-false | rewrite required | One `snow sql` per statement. Ignore a failing command. |
| 30-continue-on-error.sh:4 | `-f sql/exit-on-error.sql` | file | compatible | Same flag. |

Rewritten in place:

```bash
snow sql -c example -q "select 1 as first_ok;" || true
snow sql -c example -q "select * from snowsql_example_missing_relation;" || true
snow sql -c example -q "select 'STILL_RAN' as after_error;"
```

Fallback briefing when that file is the whole project and the host has no plan mode:

```text
Nothing has changed yet. If you reply implement, I edit your files in place.

3 lines only need snowsql renamed to snow sql.

1 line needs a different command. The main kinds:
- Errors: this script keeps going after a failed statement, same as SnowSQL with exit on error turned off.
Full list: migration-plan.md

Left as written:
None.

Reply: implement / report-only
```

A line under `Left as written`, when the project has one: `20-probe.sh:4 --probe-connection: Snowflake CLI prints different text, so anything that reads this output gets a different result.`

When the host has a plan mode, that same text is the body of the submitted plan. There is no `Reply:` line.

## Common mistakes (workflow)

Technical traps live in [references/mistakes.md](references/mistakes.md).

- Asking implement or report-only when this session can submit a host plan, or writing `migration-plan.md` on that path.
- Treating a chat reply as a host plan. A host plan is one the user approves before any project edit.
- Pasting the plan into chat, or writing `snowsql-migration-report.md`.
- Showing rule ids, or one summary line per rule. Group rewrites by what they do to the script, in plain sentences.
- Asking before the plan exists, or asking per output gap or per `Left as written` row instead of applying the parity Suggested fix.
- Editing a project file before the host plan is approved, or before they choose implement on the fallback, or writing a `snowsql-migrated/` tree instead of editing in place.
- Importing connections during Analyze or Implement, or running import without naming `snow helpers import-snowsql-connections`.
- Printing secrets, editing the SnowSQL config, or writing `migration-plan.md` outside the working directory.
- Guessing a translation for a construct no rule names (`manual review`).
