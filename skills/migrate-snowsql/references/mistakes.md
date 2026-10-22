# Common mistakes

Load when classifying a tricky construct, writing a rewrite, or reviewing the report. The short list in SKILL.md covers workflow only.

## Clients and versions

- Treating Snowflake CLI as a drop-in replacement.
- Migrating SnowSQL below 1.4. Upgrade to 1.4 or 1.5 first. 1.5 is the recommended line.
- Migrating with a CLI below `3.30.0.dev0`, or importing with help text that still says custom config files are used instead of the default list.

## Exit codes and errors

- Using `--enhanced-exit-codes` to continue after a SQL error. It changes exit 1 to exit 5 and still stops.
- Adding `--enhanced-exit-codes` on an undefined `&name`. Both clients already exit 1.
- Dropping `-o exit_on_error=true` and expecting exit code 5. The CLI exits 1 unless the copy adds `--enhanced-exit-codes`.

## Directives and variables

- Rewriting `!source http://...` to a guessed `https://` URL. The CLI fails the fetch. Use a local file the project already has, or a no-op (`true`).
- Passing `-D NAME="${NAME}"` for `-o environment_variables=NAME`. The copy is `<% ctx.env.NAME %>`.
- Replacing `!spool` with a shell redirect when the CLI already implements `!spool`. One redirect captures results SnowSQL wrote only to the screen after `!spool off`.

## Output parity

- Rewriting a CSV extract to `snow sql --format CSV` and calling the bytes equivalent without the parity rewrite. Record the gap and `QUOTING: CHANGED`, then apply `QUOTING: REWRITE` or matching code from the plan.
- Accepting CLI output by default when a rewrite or matching-code path exists for parity.
- Skipping a rewrite because `jq` or Python is missing without saying so in the report.
- Using the quote-all one-liner to repair embedded newline, tab, CR, or NULL bytes. That is matching code.

## Safety

- Printing passwords, keys, tokens, or connection values.
- Editing the original script or the SnowSQL config.
- Writing the plan, copies, or report anywhere other than the working directory.
- Guessing a translation for a construct no rule names. It is `manual review`.
