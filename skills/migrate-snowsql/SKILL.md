---
name: migrate-snowsql
title: Migrate SnowSQL
summary: Guided SnowSQL to Snowflake CLI migration. The first run states findings in chat, then optional auto-implement for 1:1 parity.
description: "Analyzes a SnowSQL project and writes a migration plan and report without asking. On that first run, states the findings in chat before any edit: the count of compatible replacements, a summary of automatic rewrites, and each manual-review item with why it is held and how behavior is affected. Then offers implement or report-only. Implement copies every compatible and rewrite file automatically, including output rewrites for SnowSQL parity. After copies, reports what was done and asks whether to run snow helpers import-snowsql-connections. Use when migrating SnowSQL scripts, connection files, or SNOWSQL_* variables to Snowflake CLI, or when the user mentions snow helpers import-snowsql-connections, check-snowsql-env-vars, convert snowsql scripts, or $migrate-snowsql. Snowflake CLI is a separate client, not a drop-in replacement. Not for unattended migration."
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

Migrate SnowSQL 1.4 or 1.5 scripts to Snowflake CLI. Write the plan and the report first, without questions. On that first run, say in chat what you found, using the briefing in step 3. Writing the files is not the briefing. Then offer implement or report-only. Implement auto-applies every `compatible` replacement and every `rewrite required` copy for SnowSQL output parity. After copies, update the report and ask about connection import. Pause only for live SQL, new stages/file formats, and constructs with no rule. Below 1.4, stop and say to upgrade first.

Rule list: [references/compatibility.md](references/compatibility.md). Load a reference only when that step needs it. Dispositions: `compatible`, `rewrite required`, `manual review`, `blocked`. Unlisted constructs are `manual review`.

Out of scope: continuous SnowSQL sync, runtime SnowSQL compatibility, config-v2, unattended migration.

## Working directory

Write the plan, the script copies, and the report in the current working directory. Do not pick another path.

- `migration-plan.md`
- `snowsql-migrated/` (same relative paths as the SnowSQL project)
- `copies.diff`
- `snowsql-migration-report.md`

Never edit, move, or overwrite a file that is already in the SnowSQL project.

## Workflow

Copy this checklist and check off as you go:

```text
Migration progress:
- [ ] 1. Preflight
- [ ] 2. Analyze (plan + PLAN_ONLY report)
- [ ] 3. First-run briefing, then implement / report-only
- [ ] 4. Implement (if chosen)
- [ ] 5. Update report; offer import
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

## Proposed copies

<each file without a blocked row, and the rewrites its copy would get>

## Blocked

<each blocked file and the reason>

## Output gaps

| Location | Difference | Consumer | Suggested fix |
| --- | --- | --- | --- |
| scripts/10-output-json.sh | CLI pretty-prints JSON; `1` is a number, SnowSQL writes `"1"` | parser | rewrite (`jq` numbers-as-strings) for parity |

## Redactions

<each secret you replaced with <redacted>, by key or flag name only>
```

One row per construct. `Found` is the construct as written, with secret values replaced by `<redacted>`. `Disposition` is exactly one of the four words. Add the `QUOTING:` lines for every CSV row. For each output gap, write the exact byte difference, the consumer when known, and the Suggested fix that preserves SnowSQL parity (rewrite or matching-code from output-gaps.md). Do not leave "ask the user" as the Suggested fix.

Validate the plan against validation.md (static checks). Fix any failure, then re-check. Only then write `snowsql-migration-report.md` with `STATUS: PLAN_ONLY` (template in validation.md). Do not paste the plan or the report into chat. Do not copy files, import connections, or run SQL yet.

### 3. First-run briefing

Send this once the files exist, before any copy. Do not paste the plan or the report. No secrets. The standing lines stay this short. Lists add one line each.

```text
Nothing changed. Files: migration-plan.md, snowsql-migration-report.md

Replace <n> compatible (not listed).
Rewrite <n>:
- <rule> (<n>): <what the copy will do>; <what stays the same, or what still differs>
Review <n>:
- <location> <found>: <why it is not automatic>; <what changes or breaks>
Blocked <n>:
- <file>: <why>. Stays on SnowSQL.

Reply: implement / report-only
```

Counts match the plan. One rewrite line per rule; those counts sum to the rewrite count. Do not list compatible locations. A compatible output gap is one extra clause on the Replace line. One review line per plan row: why it is held, then the behavior hit. One blocked line per file. A zero count has no bullets.

Implement applies every Replace and Rewrite line. Review and Blocked stay untranslated. If they already approved implement, print this, then `Writing copies.`, and go to Implement. Report-only stops. Do not ask about output gaps or import yet.

### 4. Implement

Copy the project tree into `snowsql-migrated/` with `cp -a` (scripts, sql, data). Then edit those copies. Never edit the original. Apply every compatible and rewrite row without asking again. Rewrite a defined `&name` to `<% name %>`. Change `snowsql` to `snow sql`. Write `copies.diff` (`diff -u` of the originals and the copies). Do not finish until `ls snowsql-migrated/scripts` lists every original script that is not blocked.

Replacement detail stays in compatibility.md. A CSV row becomes `snow sql --format CSV` and records the `QUOTING:` lines from that page. A synthetic or modest extract can be `snow sql --format CSV > file`. A production-scale extract stays `COPY INTO @stage` followed by `snow stage copy`.

**Output parity.** For every Output gaps row, apply the Suggested fix from the plan so CLI output matches SnowSQL as closely as the references allow. Prefer the rewrite one-liner in output-gaps.md. When the one-liner cannot restore SnowSQL bytes (embedded newline, tab, CR, NULL as `""`, doubled backslash), write matching code or keep the gap listed as residual. Do not default to accept. Record `QUOTING: REWRITE` or `QUOTING: FILE_FORMAT` when that is what you applied. Python or `jq` is fine. Do not skip a rewrite because a tool is missing; say so in the report if you cannot run it.

**Manual review and blocked.** Do not invent a translation. Leave those files untranslated or copy the original unchanged only when the plan says so. List them in the report. Walk through a manual or blocked row only if they ask after the report; one file at a time.

Do not import connections in this step.

### 5. Short report and import offer

After Implement, update `snowsql-migration-report.md` to `STATUS: COPIES_WRITTEN` using the template in validation.md, and re-check static rules that apply to copies. Do not paste the report. A plan covers only the files you read. Live SQL needs another yes and a named non-production connection.

After copies are written, send this once. Name the command. Do not run it until they say yes. Preflight must still pass.

```text
Copied to snowsql-migrated/. Originals untouched.

Replace <n> compatible (not listed).
Rewrite <n>:
- <rule> (<n>): <what the copy does>; <what stayed the same, or what still differs>
Review <n>:
- <location> <found>: <why>; <what was left>
Blocked <n>:
- <file>: <why>. Stays on SnowSQL.

Import connections? snow helpers import-snowsql-connections
Backs up the CLI config first. May prompt on overwrite. Not a sync.
Reply: yes / no
```

Same counting rules as the briefing.

If they say yes and Preflight passed: load configuration.md, back up the Snowflake CLI config file, run `snow helpers import-snowsql-connections` once, show a redacted before/after of connection names and key names. Import does not approve script edits or SQL.

## Stopping points

- After the first-run briefing, for implement vs report-only.
- After copies, for connection import (with the command named).
- Before creating a stage or file format the project does not already have.
- Before each live SQL session, with a named non-production connection.

## Example

SnowSQL file `scripts/30-continue-on-error.sh`:

```bash
snowsql -c example -o exit_on_error=false -f sql/exit-on-error.sql
```

`sql/exit-on-error.sql` has three statements. Plan rows:

| Location | Found | Rule | Disposition | Action |
| --- | --- | --- | --- | --- |
| scripts/30-continue-on-error.sh:4 | `snowsql` | invocation | compatible | `snow sql` |
| scripts/30-continue-on-error.sh:4 | `-c example` | connection | compatible | Same flag. |
| scripts/30-continue-on-error.sh:4 | `-o exit_on_error=false` | exit-on-error-false | rewrite required | One `snow sql` per statement. Ignore a failing command. |
| scripts/30-continue-on-error.sh:4 | `-f sql/exit-on-error.sql` | file | compatible | Same flag. |

Implemented copy:

```bash
snow sql -c example -q "select 1 as first_ok;" || true
snow sql -c example -q "select * from snowsql_example_missing_relation;" || true
snow sql -c example -q "select 'STILL_RAN' as after_error;"
```

First-run briefing when that file is the whole project:

```text
Nothing changed. Files: migration-plan.md, snowsql-migration-report.md

Replace 3 compatible (not listed).
Rewrite 1:
- exit-on-error-false (1): one snow sql per statement; later statements still run
Review 0:
Blocked 0:

Reply: implement / report-only
```

A review line, when the project has one: `scripts/20-probe.sh:4 --probe-connection: CLI text differs; a caller would parse a different result.`

## Common mistakes (workflow)

Technical traps live in [references/mistakes.md](references/mistakes.md).

- Ending the first run after writing the files, or pasting the plan or the report instead of the briefing.
- Listing every compatible change, or a review line with no reason and no behavior hit.
- Asking before the plan and report exist, or asking per output gap instead of applying the parity Suggested fix.
- Copying into `snowsql-migrated/` before they choose implement (unless they already said to implement).
- Importing connections during Analyze or Implement, or running import without naming `snow helpers import-snowsql-connections`.
- Printing secrets, editing originals, or writing outputs outside the working directory.
- Guessing a translation for a construct no rule names (`manual review`).
