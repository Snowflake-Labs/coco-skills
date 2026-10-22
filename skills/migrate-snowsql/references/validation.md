# Validation and the report

Static checks do not open a connection. The plan covers only the files you read. It does not certify scripts that were not in the project.

## Static checks

Before the first-run briefing, check the plan against the project:

- Every file that mentions `snowsql`, `SNOWSQL_`, a `!` directive, or `&name` is under "Files inspected".
- Every finding row names a compatibility rule, or `unlisted`.
- Every JSON, CSV, TSV, HTML, grid, plain, spool, async, single-transaction, query-tag, `header=false`, dropped `-o` option, or large-extract finding has an Output gaps row: byte difference, consumer, and a Suggested fix. The fix names the installed tool, `accept CLI bytes`, or `manual review: no installed tool`. Implement applies that fix. CSV adds `QUOTING: CHANGED` and `ACCEPTED`, `REWRITE`, or `FILE_FORMAT` for the fix applied. A pipe left for review has no second `QUOTING:` line.
- No secret value appears in the plan or the first-run briefing. Search both for each secret you redacted. If one is there, replace it with `<redacted>` before you send the briefing.
- The compatible number matches the plan count. The briefing groups rewrites in plain sentences, at most 6, with no rule ids. Every `manual review` row is one sentence naming the file and the behavior hit. A redaction is not a decision line. Compatible locations are not listed. A zero decision or blocked count is the word `None`.

Preflight must have passed before the plan and before an import. Below 1.4, stop and say to upgrade first. `snow helpers check-snowsql-env-vars` is the read-only environment check.

After the in-place rewrite, `rewrite.diff` is the only record of the old bytes. Manual-review and blocked files must be unchanged. A blocked file is not deleted.

## Optional live SQL

Do not run SQL until the user gives a separate confirmation and names a non-production connection. Do not invent credentials. Do not change a network policy. Do not print secret values. Generate any temporary config as mode `0600` and delete it before you finish.

Use synthetic SQL only. Record hashes, row counts, the exact `snow --version` and `snowsql --version`, timings, and redacted diagnostics. A match on one statement does not certify the rest of the project.

| Check | What you are showing |
| --- | --- |
| Stop on error | The CLI stops at the first failing statement. SnowSQL with `exit_on_error=true` exits 5. The CLI exits 1 unless the copy adds `--enhanced-exit-codes`, which restores 5 and still stops. |
| Continue on error | `-o exit_on_error=false` has no CLI equivalent. `--enhanced-exit-codes` still stops. |
| CSV edges | Newline, backslash, tab, and CRLF bytes differ, and so does NULL. UTF-8 only. No delimiter or quoting flags. SnowSQL quotes every field, writes `\n`, `\t`, and `\r` as two-character escapes, and doubles a backslash. Snowflake CLI quotes a field only when it contains a comma, a quote, or a newline, writes the raw newline and tab, drops a CR, and writes NULL as an empty field. Record `QUOTING: CHANGED`, then `QUOTING: ACCEPTED`, `QUOTING: REWRITE`, or `QUOTING: FILE_FORMAT`. |
| Substitution | A defined `&name` rewrites to `<% name %>`. |
| Undefined variable | The CLI error and exit code are not the SnowSQL undefined-variable exit code. |
| Local `!source` | A local include runs. `http://` does not. |
| Ordered narrow results | Compare counts and hashes for an ordered 10k result and an ordered 1M narrow result when the user approves the cost. |
| Wide CSV | One wide multi-minute CSV is a timing sample. It does not prove truncation is fixed. |

A `SELECT` or `GENERATOR` extract stays `snow sql --format CSV > file`, plus the CSV quoting fix from output-gaps.md. `COPY INTO @stage` is for a project that already unloads to a stage, or when a person said the result is too big to stream. A faster TABLE render does not show that the CSV kept every row.

If any of `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PRIVATE_KEY`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE`, or `SNOWFLAKE_SCHEMA` is missing, skip live SQL and say which names are missing. Do not print values.

## Report

Write `snowsql-migration-report.md`. Keep it short. Do not paste it into chat. The first-run chat is the briefing in SKILL.md.

```markdown
# SnowSQL migration report

STATUS: PLAN_ONLY | REWRITTEN

<exact snow --version one line>

Rewrote: <n> files in place, or none yet
Output parity: <location, difference, fix applied, or planned fix>
Manual review / blocked: <file and reason, or none>
Residual gaps: <what still cannot match SnowSQL bytes, or none>

The plan does not certify untested scripts.
SnowSQL remains installed. CLI config backup: <path or none>.
```

Write the report in Analyze with `STATUS: PLAN_ONLY` (Rewrote: none yet; Output parity lists planned fixes). After Implement, set `STATUS: REWRITTEN` and record what was applied. Keep `STATUS: STOPPED_BEFORE_MUTATION` in `migration-plan.md` until they choose implement.

A CSV row still adds `QUOTING: CHANGED` and the second `QUOTING:` line for what Implement applied. Blocked files stay listed even after the other files are rewritten.
