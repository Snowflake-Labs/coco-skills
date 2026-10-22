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

Rule list: [references/compatibility.md](references/compatibility.md). Load a reference only when that step needs it. Dispositions: `compatible`, `rewrite required`, `manual review`, `blocked`. Unlisted constructs are `manual review`.

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

Read the project. List its files, search for `snowsql`, `SNOWSQL_`, `-o`, lines that start with `!`, and `&`, and read every file that matches, plus every SQL file a script passes with `-f`. Match each construct to a rule in compatibility.md. Do not edit anything.

Write `migration-plan.md`:

```markdown
# SnowSQL migration plan

STATUS: STOPPED_BEFORE_MUTATION

## Files inspected

<every file you read, relative to the SnowSQL project>

## Findings

| Location | Found | Rule | Disposition | Action |
| --- | --- | --- | --- | --- |
| run.sh:2 | `-o exit_on_error=false` | exit-on-error-false | rewrite required | One `snow sql` per statement. Ignore a failing command. |

## Counts

compatible: <n>, rewrite required: <n>, manual review: <n>, blocked: <n>

## Proposed edits

<each file that implement would rewrite in place>

## Blocked

<each blocked file and the reason>

## Output gaps

| Location | Difference | Consumer | Suggested fix |
| --- | --- | --- | --- |
| 10-output-json.sh | CLI pretty-prints JSON; `1` is a number, SnowSQL writes `"1"` | parser | rewrite (`jq` numbers-as-strings) for parity |

## Redactions

<each secret you replaced with <redacted>, by key or flag name only>
```

One row per construct. `Found` is the construct as written, with secret values replaced by `<redacted>`. `Disposition` is exactly one of the four words. Add the `QUOTING:` lines for every CSV row. For each output gap, write the exact byte difference, the consumer when known, and the Suggested fix that preserves SnowSQL parity (rewrite or matching-code from output-gaps.md). Do not leave "ask the user" as the Suggested fix.

Validate the plan against validation.md (static checks). Fix any failure, then re-check. Only then write `snowsql-migration-report.md` with `STATUS: PLAN_ONLY` (template in validation.md). Do not paste the plan or the report into chat. Do not edit project files, import connections, or run SQL yet.

### 3. First-run briefing

Send this once the files exist, before any edit. Do not paste the plan or the report. No secrets. Write for someone who has not seen the rule names.

```text
Nothing has changed yet. If you reply implement, I edit your files in place.

<n> lines only need snowsql renamed to snow sql.

<n> lines need a different command. The main kinds:
- <plain group>: <one sentence saying what changes for the person reading the script, and whether the result stays the same>
Full list: migration-plan.md

<n> lines need your decision. I will leave them as they are:
- <file>:<line> <what is there>: <what breaks, or what a caller would see, if this is rewritten without you>

<n> files stay on SnowSQL:
- <file>: <why a Snowflake CLI version of this file does not exist>

Reply: implement / report-only
```

The first count is the plan's compatible count. Do not list those lines. Group every rewrite by what it does to the script, not by rule name. At most 6 group lines. No rule ids, no flag soup, no arrows. A group with no rows is omitted. Say where output bytes will still differ, in that group's sentence.

Every manual-review row is one sentence: the file, what is there, and the behavior hit. A redaction is not a decision. When a heading has no rows, its body is the single word `None`. Do not print `None` under a heading that has rows. One blocked file per line.

Implement edits every compatible and rewrite file in place. Manual review and blocked files stay as they are. If they already approved implement, print this, then `Rewriting your files in place.`, and go to Implement. Report-only stops. Do not ask about output gaps or import yet.

### 4. Implement

Edit the project files in place. Do not write a second tree. Apply every compatible and rewrite row without asking again. Leave manual-review and blocked files unchanged. Rewrite a defined `&name` to `<% name %>`. Change `snowsql` to `snow sql`. Before the first edit, snapshot only the files you will change, write `rewrite.diff` (`diff -u` against that snapshot), then delete the snapshot.

Replacement detail stays in compatibility.md. A CSV row becomes `snow sql --format CSV > file` and records the `QUOTING:` lines from that page. Pipe through the quote-all line only for parity. A `SELECT` or `GENERATOR` extract is that same `snow sql` command. Use `COPY INTO @stage` only when the project already unloads to a stage, or a person said the result is too big to stream.

**Output parity.** For every Output gaps row, keep `snow sql --format … > file`. Use the `jq` numbers-as-strings line in output-gaps.md when `jq` is installed. If `jq` is missing, or it cannot quote CSV or write TSV, use a tool already on the machine that does that exact transform. If you are not sure, do not add a pipe and do not use `python3`. Mark that gap for review. The `snow sql` command still replaces `snowsql`. When the tool cannot restore SnowSQL bytes (embedded newline, tab, CR, NULL as `""`, doubled backslash), write matching code or mark the gap for review. Do not invent a `COPY INTO` for JSON or CSV. Record `QUOTING: REWRITE` only when you applied a quote-all pipe.

**Manual review and blocked.** Do not invent a translation. Leave those files unchanged. List them in the report. Walk through a manual or blocked row only if they ask after the report; one file at a time.

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

0 lines need your decision.
None.

0 files stay on SnowSQL.
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
