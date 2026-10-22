---
name: migrate-snowsql
title: Migrate SnowSQL
summary: States findings first, then rewrites the SnowSQL project in place only if you agree.
description: "Analyzes a SnowSQL project and writes a migration plan and report without asking. On that first run, states the findings in chat before any edit: the count of compatible replacements, a summary of automatic rewrites, and each manual-review item with why it is held and how behavior is affected. Then offers implement or report-only. Implement rewrites the project files in place, including output rewrites for SnowSQL parity. It does not write a second copy. After the rewrite, reports what was done and asks whether to run snow helpers import-snowsql-connections. Use when migrating SnowSQL scripts, connection files, or SNOWSQL_* variables to Snowflake CLI. Triggers: migrate SnowSQL, convert snowsql scripts, import-snowsql-connections, check-snowsql-env-vars, $migrate-snowsql. Snowflake CLI is a separate client, not a drop-in replacement. Not for unattended migration."
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

Migrate SnowSQL 1.4 or 1.5 scripts to Snowflake CLI. Write the plan and the report first, without questions. On that first run, say in chat what you found, using the briefing in step 3. Writing the files is not the briefing. Then offer implement or report-only. Implement rewrites the project files in place: every `compatible` replacement and every `rewrite required` change, for SnowSQL output parity. Do not write a second tree. After the rewrite, update the report and ask about connection import. Pause only for live SQL, new stages/file formats, and constructs with no rule. Below 1.4, stop and say to upgrade first.

Rule list: [references/compatibility.md](references/compatibility.md). Load a reference only when that step needs it. Dispositions: `compatible`, `rewrite required`, `manual review`. A construct with no equivalent is `manual review`. There is no `blocked` disposition.

Out of scope: continuous SnowSQL sync, runtime SnowSQL compatibility, config-v2, unattended migration.

## Working directory

Write the plan and the report in the current working directory. Do not pick another path.

- `migration-plan.md`
- `rewrite.diff`
- `snowsql-migration-report.md`

Do not edit a project file until they choose implement. Then rewrite those files in place. Do not create `snowsql-migrated/` or any other copy of the project.

## Workflow

Track this yourself. Do not show it to the user.

```text
Migration progress:
- [ ] 1. Preflight
- [ ] 2. Analyze and write the plan and report
- [ ] 3. Tell them what you found, then wait
- [ ] 4. Rewrite files in place, if they agree
- [ ] 5. Update the report and offer connection import
```

### References (load on demand)

| When | Read |
| --- | --- |
| Start, and before import | [references/preflight.md](references/preflight.md) |
| Classify any construct | [references/compatibility.md](references/compatibility.md) |
| `SNOWSQL_*` or SnowSQL config / import | [references/configuration.md](references/configuration.md) |
| JSON, CSV, TSV, HTML, grid, plain, spool, async, single-transaction, query-tag, `header=false`, large extract | [references/output-gaps.md](references/output-gaps.md) |
| Before the first-run briefing or finishing the report | [references/validation.md](references/validation.md) |
| Tricky rewrite or report review | [references/mistakes.md](references/mistakes.md) |

### 1. Preflight

Load [references/preflight.md](references/preflight.md) and follow it. Record OS and both client versions. Gate SnowSQL to 1.4/1.5 and Snowflake CLI to `3.30.0.dev0` or later (including the importer help-text check). Do not print secrets. Leave SnowSQL installed. Do not edit its config.

### 2. Analyze: write the plan and report

Load references from the table above as needed. Run `snow helpers check-snowsql-env-vars` first (read-only).

Read the project. Search for `snowsql`, `SNOWSQL_`, `-o`, lines that start with `!`, and `&`, and read every file that matches, plus every SQL file a script passes with `-f`. Match each construct to a rule in compatibility.md. Do not edit anything.

Write `migration-plan.md` with two sections. Do not add a file inventory, a proposed-edits list, a counts section, an output-gaps section, or a blocked section.

```markdown
# SnowSQL migration plan

STATUS: STOPPED_BEFORE_MUTATION

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

Validate the plan against validation.md (static checks). Fix any failure, then re-check. Only then write `snowsql-migration-report.md` with `STATUS: PLAN_ONLY` (template in validation.md). Do not paste the plan or the report into chat. Do not edit project files, import connections, or run SQL yet.

### 3. First-run briefing

Send this once the files exist, before any edit. Do not paste the plan or the report. No secrets. Write for someone who has not seen the rule names.

```text
Nothing has changed yet. If you reply implement, I edit your files in place.

<n> lines only need snowsql renamed to snow sql.

<n> lines need a different command. The main kinds:
- <plain group>: <one sentence saying what changes for the person reading the script, and whether the result stays the same>
Full list: migration-plan.md

<n> items need your decision:
- <file>:<line> <what is there>: <what a caller would see>

Reply: implement / report-only
```

The first count is the number of Findings rows with disposition `compatible`. Do not list those lines. Group every rewrite by what it does to the script, not by rule name. At most 6 group lines. No rule ids, no flag soup, no arrows. A group with no rows is omitted. Say where output bytes will still differ, in that group's sentence.

Every Manual review row is one sentence: the file, what is there, and what a caller would see. A redaction is not a decision. When that count is zero, the body is the single word `None`.

Implement edits every Findings file in place. Manual review files marked `Leave unchanged` stay as they are. If they already approved implement, print this, then `Rewriting your files in place.`, and go to Implement. Report-only stops. Do not ask about output gaps or import yet.

### 4. Implement

Edit the project files in place. Do not write a second tree. Apply every Findings row without asking again. Leave a Manual review file marked `Leave unchanged` as written. Rewrite a defined `&name` to `<% name %>`. Change `snowsql` to `snow sql`. Before the first edit, snapshot only the files you will change, write `rewrite.diff` (`diff -u` against that snapshot), then delete the snapshot.

Replacement detail stays in compatibility.md. Apply each Suggested fix in Manual review when that row is an output gap on a Findings file. output-gaps.md chooses it from the consumer and from tools already installed. The command stays `snow sql --format … > <original path>`. Keep every `-o output_file` path and extension. A TSV file with no TSV tool still uses its original path, and the bytes are CSV. Record `QUOTING: REWRITE` only when a quote-all pipe was applied.

**Manual review.** Do not invent a translation for a `Leave unchanged` row. List those files in the report. Walk through one only if they ask after the report.

Do not import connections in this step.

### 5. Short report and import offer

After Implement, update `snowsql-migration-report.md` to `STATUS: REWRITTEN` using the template in validation.md. Do not paste the report. A plan covers only the files you read. Live SQL needs another yes and a named non-production connection.

After the files are rewritten, send this once. Name the command. Do not run it until they say yes. Preflight must still pass.

```text
I edited your files in place.

<n> lines: snowsql is now snow sql.
<n> lines needed a different command. Same groups as the briefing.
Left as they were:
- <file>:<line> <what is still there, and why>

Import connections? snow helpers import-snowsql-connections
Backs up the CLI config first. May prompt on overwrite. Not a sync.
Reply: yes / no
```

Same wording rules as the briefing. `Left as they were` is `None` when nothing was held back.

If they say yes and Preflight passed: load configuration.md, back up the Snowflake CLI config file, run `snow helpers import-snowsql-connections` once, show a redacted before/after of connection names and key names. Import does not approve script edits or SQL.

## Stopping points

- After the first-run briefing, for implement vs report-only.
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

First-run briefing when that file is the whole project:

```text
Nothing has changed yet. If you reply implement, I edit your files in place.

3 lines only need snowsql renamed to snow sql.

1 line needs a different command. The main kinds:
- Errors: this script keeps going after a failed statement, same as SnowSQL with exit on error turned off.
Full list: migration-plan.md

0 items need your decision.
None.

Reply: implement / report-only
```

A decision line, when the project has one: `20-probe.sh:4 --probe-connection: Snowflake CLI prints different text, so anything that reads this output gets a different result.`

## Common mistakes (workflow)

Technical traps live in [references/mistakes.md](references/mistakes.md).

- Ending the first run after writing the files, or pasting the plan or the report instead of the briefing.
- Showing rule ids, or one briefing line per rule. Group rewrites by what they do to the script, in plain sentences.
- Asking before the plan and report exist, or asking per output gap instead of applying the parity Suggested fix.
- Editing a project file before they choose implement, or writing a `snowsql-migrated/` tree instead of editing in place.
- Importing connections during Analyze or Implement, or running import without naming `snow helpers import-snowsql-connections`.
- Printing secrets, editing the SnowSQL config, or writing the plan or the report outside the working directory.
- Guessing a translation for a construct no rule names (`manual review`).
