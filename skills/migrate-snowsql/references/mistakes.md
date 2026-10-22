# Common mistakes

Load when classifying a tricky construct, writing a rewrite, or reviewing the report. The short list in SKILL.md covers workflow only.

## Clients and versions

- Treating Snowflake CLI as a drop-in replacement.
- Migrating SnowSQL below 1.4. Upgrade to 1.4 or 1.5 first. 1.5 is the recommended line.
- Migrating with a CLI below `3.30.0.dev0`, or importing with help text that still says custom config files are used instead of the default list.

## Exit codes and errors

- Using `--enhanced-exit-codes` to continue after a SQL error. It changes exit 1 to exit 5 and still stops.
- Adding `--enhanced-exit-codes` on an undefined `&name`, including when the same command also has `-o exit_on_error=true`. A template error exits 1. `variable-undefined` wins.
- Dropping `-o exit_on_error=true` and expecting exit code 5. The CLI exits 1 unless the copy adds `--enhanced-exit-codes`.

## Directives and variables

- Rewriting `!source http://...` to a guessed `https://` URL, or replacing the script with `true`. The CLI never fetches `http://`. Leave the file for the person.
- Passing `-D NAME="${NAME}"` for `-o environment_variables=NAME`. The copy is `<% ctx.env.NAME %>`.
- Replacing `!spool` with a shell redirect on Snowflake CLI 3.31.0, including `3.31.0.dev0` from `main`. On 3.30, split at `!spool off` and `tee`. One redirect captures results SnowSQL wrote only to the screen after `!spool off`.

## Output parity

- Rewriting a CSV extract to `snow sql --format CSV` and calling the bytes equivalent without recording `QUOTING: CHANGED`.
- Recording `QUOTING: ACCEPTED` because no parity tool was obvious. That gap is manual review.
- Using `python3` for a JSON, CSV, or TSV rewrite. Use `jq` when it can do the transform. Otherwise use a tool already installed. If you are not sure, mark the gap for review.
- Inventing a `COPY INTO` for a `SELECT` or `GENERATOR` because the file name says large. That stays `snow sql --format CSV` and the quote-all line.
- Putting `HEADER` inside `FILE_FORMAT`. It is a `COPY INTO` option.
- Using the quote-all one-liner to repair embedded newline, tab, CR, or NULL bytes. That is matching code.

## Safety

- Printing passwords, keys, tokens, or connection values.
- Editing a project file before they choose implement, or editing the SnowSQL config.
- Writing the plan or the report anywhere other than the working directory.
- Writing a second project tree instead of rewriting the files in place.
- Guessing a translation for a construct no rule names. It is `manual review`.
