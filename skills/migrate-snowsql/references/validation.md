# Validation

Static checks do not open a connection. The plan covers only the files you read. It does not certify scripts that were not in the project.

## Static checks

Before you submit the host plan, or write the fallback file, check the plan against the project:

- The plan has two sections, Findings and Manual review. It has no file inventory, proposed-edits list, counts section, separate output-gaps section, or blocked section.
- Every `snowsql` invocation, `SNOWSQL_` name, `!` directive, and `&name` is a Findings row, or a Manual review row when that file stays unchanged.
- Findings dispositions are only `compatible` and `rewrite required`. Each Findings row names a compatibility rule.
- A file whose worst disposition is `manual review` appears only in Manual review.
- Every JSON, CSV, TSV, HTML, grid, plain, spool, async, single-transaction, query-tag, `header=false`, dropped `-o` option, or large-extract finding has a Manual review row: what a caller sees, and the Suggested fix. The fix names the installed tool, `accept CLI bytes`, or `no installed tool`. Implement applies that fix. CSV adds `QUOTING: CHANGED` and `ACCEPTED`, `REWRITE`, or `FILE_FORMAT` for the fix applied. A pipe left for review has no second `QUOTING:` line.
- Every `-o output_file=<path>` and `!spool <path>` keeps that exact path in the Findings action and in the rewritten script. A format change does not change the path or the extension. `out/result.tsv` stays `out/result.tsv` when the bytes are CSV.
- No secret value appears in the plan, or in the fallback briefing. Search both for each secret you redacted. If one is there, replace it with `<redacted>` before you submit the plan or send the briefing.
- The plan summary's compatible count matches the Findings rows with disposition `compatible`. Rewrites are grouped in plain sentences, at most 6, with no rule ids. Every Manual review row is one sentence under `Left as written`, naming the file and what a caller would see. A redaction is not one of those lines. Compatible locations are not listed. When nothing is left as written, that body is the word `None`. The host plan has no `Reply:` line. The fallback briefing does, and it offers only implement or report-only.

Preflight must have passed before the plan and before an import. Below 1.4, stop and say to upgrade first. `snow helpers check-snowsql-env-vars` is the read-only environment check.

After the in-place rewrite, `rewrite.diff` is the only record of the old bytes. A Manual review file marked `Leave unchanged` must be unchanged. It is not deleted. Compare each original `-o output_file` and `!spool` path with the rewritten script. The path string must match. Search the rewritten scripts for `snow sql` and `-o` on the same command. There must be no match. The CLI rejects `-o` and exits 2.

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

Do not write `snowsql-migration-report.md`. The plan is the record of what will change. After the rewrite, say what changed in chat. The plan keeps `STATUS: STOPPED_BEFORE_MUTATION` until they approve the host plan or choose implement.
