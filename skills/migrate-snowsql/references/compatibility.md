# Script compatibility rules

This page is the rule list for SnowSQL 1.4 and 1.5. Those lines use the same rules. Read every file in the SnowSQL project, match each SnowSQL construct to a rule below, and write one row per match in the plan. Translate only equivalents documented here or in [Migrating from SnowSQL to Snowflake CLI](https://docs.snowflake.com/en/user-guide/snowsql-migrate). Snowflake CLI is a separate client. Do not describe it as a drop-in replacement.

## Contents

- How to apply the rules
- Commands
- Connection flags
- Environment variables in scripts
- Variables
- Client directives
- Output (CSV quoting, large extracts)
- Stop on error
- SnowSQL config files in the project
- Fallback

## How to apply the rules

Three dispositions only:

| Disposition | Meaning | When implemented in place |
| --- | --- | --- |
| `compatible` | Snowflake CLI already does this. | Keep it. Change `snowsql` to `snow sql`. |
| `rewrite required` | A documented equivalent exists. | Apply the replacement in the rule. |
| `manual review` | A person decides, or there is no equivalent. | Leave the file unchanged and list it under Manual review. |

- One line can match several rules. Record each one.
- A file takes the worst disposition of its rows: `manual review`, then `rewrite required`, then `compatible`.
- A `manual review` file is listed only in that plan section. Do not repeat its constructs under Findings.
- When two replacements disagree, the more specific rule wins: `variable-undefined` (no `--enhanced-exit-codes`) over `exit-on-error-true`; a `SELECT` or `GENERATOR` extract stays `snow sql --format` and a redirect, never `COPY INTO`.
- A construct that no rule names is `manual review` under the rule `unlisted`. Say there is no documented equivalent. Do not invent a translation.
- Never write a secret value into the plan or a rewritten file. Keep the key or flag name and write `<redacted>` for the value. Secrets are passwords, passphrases, tokens, API and access keys, `private_key_file_pwd`, `SNOWSQL_PWD`, `SNOWSQL_PRIVATE_KEY_PASSPHRASE`, and `SNOWSQL_PROXY_PWD`.

Files to read: shell and batch scripts (`.sh`, `.bash`, `.zsh`, `.ps1`, `.bat`, `.cmd`), `.sql`, `.py`, SnowSQL config files (`config`, `.cnf`, `.ini`), and any CI, cron, Makefile, or Dockerfile that calls `snowsql`. Also read every SQL file a script passes with `-f`. Skip `.git`, virtual environments, `node_modules`, and earlier migration output.

## Commands

| Rule | Recognize | Disposition | Replacement or action |
| --- | --- | --- | --- |
| `invocation` | The `snowsql` command | compatible | `snow sql`. The flags on the line decide the file. |
| `query` | `-q` or `--query` | compatible | Same flag. |
| `file` | `-f` or `--filename` | compatible | Same flag. `-f` is repeatable. |
| `connection` | `-c` or `--connection` | compatible | Same flag. |
| `put-get` | `PUT` and `GET` statements | compatible | Run the SQL with `snow sql`. |
| `single-transaction` | `--single-transaction` | compatible | Same flag. The CLI wraps the file in `BEGIN` and `COMMIT` and prints a status row for each. Those rows are not the statement results. Report the extra rows. Remediation is in the output-gaps reference. |
| `async` | A statement ending in `;>` | compatible | Same syntax. The CLI prints a scheduled query id. SnowSQL 1.4 prints none. The id is not the query result. Report the extra row. Remediation is in the output-gaps reference. |
| `stdin` | SQL reaches `snowsql` on stdin (`cat file.sql \| snowsql`, `snowsql < file.sql`) with no `-q` or `-f` | rewrite required | `snow sql --stdin`. |
| `temporary-connection` | Account flags on the command and no saved connection | rewrite required | `snow sql --temporary-connection` (`-x`) plus the same flags. |
| `probe-connection` | `--probe-connection` | manual review | `snow connection test`. `--enable-diag` writes a report. It is not a TLS chain dump. The two commands do not print the same text. |
| `query-tag` | `--query_tag` | rewrite required | `alter session set query_tag = '...'` before the query. The CLI prints a success status for that statement. Report it. Remediation is in the output-gaps reference. |
| `jwt` | A JWT built from user, account, and private key | manual review | `snow connection generate-jwt`. |

Without `-q`, `-f`, or `--stdin`, `snow sql` reads stdin and then still enters the REPL. The piped SQL is discarded. The alias `snowsql='snow sql'` does not fix that.

Interactive SQL is `snow sql` with no `-q` or `-f`. `;>` schedules a statement. Snowflake CLI prints the query id. SnowSQL 1.4 noninteractive does not. `!queries`, `!result`, and `!abort` work in both. `!exit` and `!quit` do not. `!edit` fails outside the REPL.

## Connection flags

| Rule | Recognize | Disposition | Replacement or action |
| --- | --- | --- | --- |
| `connection-flag` | `--account` or `--accountname`, `--user` or `--username`, `--database` or `--dbname`, `--schema` or `--schemaname`, `--role` or `--rolename`, `--warehouse`, `--host`, `--port`, `--protocol`, `--private-key-file` or `--private-key-path`, `--authenticator` | compatible | Same flag. |
| `config-flag` | `--config` | rewrite required | Drop the flag after the connection import. The CLI reads `~/.snowflake/config.toml`. `--config-file` is only for a CLI config. |
| `client-flag` | `--abort-detached-query`, `--disable-request-pooling`, `-K` / `--client-session-keep-alive` | rewrite required | Drop the flag. It does not change the statement result. |
| `secret-flag` | `--password`, `--passphrase`, `--token`, `--secret` | manual review | Redact the value. Move the secret to a saved connection or an environment variable. |
| `unknown-flag` | Any other long flag | manual review | No documented `snow sql` equivalent. |

Runtime connection flags override the connection for that command. They do not save a connection. `snow connection add` saves one.

## Environment variables in scripts

| Rule | Recognize | Disposition | Replacement or action |
| --- | --- | --- | --- |
| `snowsql-env` | A `SNOWSQL_*` name | from the configuration reference environment table | Suggested `SNOWFLAKE_*` name from that table. A name the table does not list is manual review. |

`SNOWSQL_*` variables are not normal Snowflake CLI runtime variables. `snow helpers check-snowsql-env-vars` only prints suggestions.

## Variables

SnowSQL `&name` and `&{name}` remain accepted and are deprecated. Prefer `<% name %>`. Substitution is on by default.

A name is defined when the project sets it with `-D name=value`, `--variable name=value`, `!define name=...`, or a `[variables]` key in a SnowSQL config.

| Rule | Recognize | Disposition | Replacement or action |
| --- | --- | --- | --- |
| `variable-flag` | `-D name=value` or `--variable name=value` | compatible | Same flag. |
| `variable-defined` | `&name` or `&{name}` for a defined name | rewrite required | `<% name %>`. `-D` still passes the value. |
| `variable-ctx-env` | `&{ctx.env.name}` | rewrite required | `<% ctx.env.name %>`. |
| `variable-config` | `[variables]` in a SnowSQL config | rewrite required | Pass each key with `-D name=value`. Rewrite `&name` to `<% name %>`. |
| `variable-builtin` | A SnowSQL built-in variable (`&__rowcount`, `&__sfqid`, any name starting with `_`) | rewrite required | `count(*)` for the last DML table, and `last_query_id()` for the query id. The id value will differ. |
| `variable-undefined` | `&name` with no definition in the project | rewrite required | `<% name %>`. Both clients error, return no result rows, and exit 1. A template error is this rule, not `exit-on-error-true`. Drop `-o exit_on_error=true` on the same command. Do not add `--enhanced-exit-codes`. |
| `substitution-on` | `-o variable_substitution=true` | rewrite required | Drop the option. The CLI substitutes by default. |
| `substitution-off` | `-o variable_substitution=false` | rewrite required | Drop the option. Do not leave `-o` on `snow sql`; the CLI rejects it and exits 2. Keep a literal `&` inside a string. If the CLI would read `&name` there, write `chr(38)` or split the string. Rewrite bitwise `&` to `bitand()`. |
| `environment-variables` | `-o environment_variables=NAME` | rewrite required | `<% ctx.env.NAME %>`. Drop `-o environment_variables=NAME` and any `-D NAME=...` that only exists to feed that option. The value stays in the shell environment. |

`ctx.env` applies only to project definition `env` values, not to `-D` variables.

## Client directives

| Rule | Recognize | Disposition | Replacement or action |
| --- | --- | --- | --- |
| `source-local` | `!source` or `!load` of a local file | compatible | Same directive. |
| `source-https` | `!source` or `!load` of an `https://` URL | manual review | Same directive. SnowSQL and the CLI do not fail the same way on a bad URL. Rejected when `--local-only`, `SNOWFLAKE_CLI_SQL_LOCAL_ONLY`, or `cli.sql_local_only` is set. A redirect from `https://` to `http://` is rejected. |
| `source-http` | `!source` or `!load` of an `http://` URL | manual review | The CLI never fetches `http://`. Do not fetch it, do not rewrite to a guessed `https://` URL, and do not replace the script with a no-op. A person picks a local file, a known `https://` URL, or deletes the include. |
| `define` | `!define name=value` | rewrite required | `-D name=value`. Redact secret values. |
| `repl-directive` | `!queries`, `!result`, `!abort` | compatible | Same directive. |
| `exit-quit` | `!exit` or `!quit` | rewrite required | Copy only the statements above the directive. Do not run the statements below it. |
| `edit` | `!edit` | rewrite required | Drop `!edit`. Keep the SQL statements after it. |
| `spool` | `!spool <file>` and `!spool off` | compatible | Same directive on Snowflake CLI 3.31.0, including `3.31.0.dev0` from `main`. Results go to the file and still print. On 3.30, split at `!spool off` and `tee`. |
| `set-plain` | `!set output_format=plain` | compatible | Same directive, or `snow sql --format PLAIN`. Spacing may differ. |
| `print-variables` | `!print` or `!variables` | manual review | Drop the directive. Keep the SQL statements. `!variables` prints a table the CLI does not. |
| `set` | Any other `!set` | rewrite required | Drop the directive. Apply a matching `--format` on that command when one exists. Grid and HTML become the default TABLE. |
| `directive` | Any other `!` directive | manual review | No documented equivalent. |

`!spool` is in Snowflake CLI 3.31.0, including `3.31.0.dev0` from `main`. Keep `!spool <file>` and `!spool off`. Results append to the file and still print. Format follows `--format` / `!set output_format`. A `3.30` build does not have it.

<details>
<summary>Below 3.31.0</summary>

Split at `!spool off` and `tee` the spooled query.

</details>

`!set output_format=plain`: same directive, or `snow sql --format PLAIN`. SnowSQL plain for `select 1 as id, 'plain' as note`, with friendly and timing off, is `ID  NOTE` and ` 1  plain`. The CLI spacing may differ.

## Output

Published `--format` values are `TABLE`, `JSON`, `JSON_EXT`, and `CSV`. The v3.30 dev build also accepts `PLAIN`. There is no `-o output_file`. Use shell redirection.

A large extract is a `SELECT` (including `GENERATOR`) written to a file. A name or comment that says large is not enough. Use `COPY INTO` only when the project already unloads to a stage, or a person said the result is too big to stream.

| Rule | Recognize | Disposition | Replacement or action |
| --- | --- | --- | --- |
| `output-json` | `-o output_format=json`, not an existing stage unload | rewrite required | `snow sql --format JSON > file`, then the JSON fix in output-gaps.md. |
| `output-file` | `-o output_file=<path>`, not an existing stage unload | rewrite required | Redirect stdout to that same path: `> <path>`. Do not rename the file or change its extension. |
| `output-plain` | `-o output_format=plain` | rewrite required | `snow sql --format PLAIN`. Layout fix is in output-gaps.md. |
| `output-csv` | `-o output_format=csv`, including a small file | rewrite required | `snow sql --format CSV > file`. Record `QUOTING: CHANGED`. Apply the CSV quoting fix in output-gaps.md. |
| `output-csv-specials` | CSV with an embedded newline, tab, CR, backslash, or NULL | rewrite required | `snow sql --format CSV`. Apply the CSV specials fix in output-gaps.md. |
| `output-tsv` | `-o output_format=tsv` | rewrite required | Apply the TSV fix in output-gaps.md. |
| `output-format` | Any other `-o output_format` value | rewrite required | `--format TABLE`, `JSON`, `JSON_EXT`, `CSV`, or `PLAIN`. Grid and HTML become TABLE. |
| `large-extract` | A `SELECT` or `GENERATOR` streamed to a file | rewrite required | `snow sql --format CSV > file` and the CSV quoting fix. `COPY INTO @stage` only when the project already unloads to a stage, or a person said the result is too big to stream. |
| `option` | Any other `-o` setting (`timing`, `friendly`, `quiet`, `echo`, `log_level`, `header`, `rowset_size`, `sfqid`, and the rest) | rewrite required | Drop the option. Do not leave `-o` on `snow sql`; the CLI rejects it and exits 2. `header=false` uses the header fix in output-gaps.md. Other dropped options stay as residuals there. |

CSV quoting may differ. The check compares parsed fields. Newline, backslash, tab, and CRLF inside a field still differ and fail that comparison. Snowflake CLI CSV is UTF-8 only, uses a newline of `\n`, and has no delimiter or quoting flags. A clean Linux run is not evidence that truncation is fixed. Report the exact bytes. Fixes are in the output-gaps reference.

### CSV quoting

SnowSQL quotes every field. Snowflake CLI quotes a field only when it contains a comma, a quote, or a newline.

<details>
<summary>Measured clients</summary>

SnowSQL 1.4.0 and 1.5.2 quote every field. Snowflake CLI 3.30.0.dev0 quotes a field only when it contains a comma, a quote, or a newline.

</details>

`SELECT 1 AS id, '3,456.00' AS amount, 8.88 AS other` is `"1","3,456.00","8.88"` from SnowSQL and `1,"3,456.00",8.88` from Snowflake CLI.

The same run on a row with a comma, a quote, a newline, a backslash, a tab, a CR LF, and NULL:

- SnowSQL writes the newline, tab, and CR as the two-character escapes `\n`, `\t`, and `\r`, doubles a backslash, and writes NULL as `""`.
- Snowflake CLI writes a real newline and a real tab, leaves one backslash, drops the CR from a CR LF, and writes NULL as an empty field.

`SELECT 'No Comma', 'With, comma'` keeps the SQL quotes in the header. The second name contains a comma, so the CLI header is `'NO COMMA',"'WITH, COMMA'"`. That second field has both the quotes from the SQL and the CSV quotes.

For every `output-csv` row, the plan contains this line:

```text
QUOTING: CHANGED
```

Then one of these lines:

| Line | What it means |
| --- | --- |
| `QUOTING: ACCEPTED` | The consumer will read the CLI file. Fields without a comma, a quote, or a newline will not be quoted. |
| `QUOTING: REWRITE` | You write the CSV with a double quote around every field. A double quote inside a field is written twice. Separate fields with a comma and end each row with a newline. Do not rename a header, fill in a dropped row, or repair a newline inside a field. |
| `QUOTING: FILE_FORMAT` | Keep the extract as `COPY INTO` with an explicit `FILE_FORMAT`. The enclosure is optional. It is not quote-every-field. Apply the same quoting to the downloaded file when those quotes are still required. |

`ID,AMOUNT,OTHER` and `1,"3,456.00",8.88` become `"ID","AMOUNT","OTHER"` and `"1","3,456.00","8.88"`. Which second line to write, and which tool to pipe through, is the CSV quoting fix in output-gaps.md.

### Large extracts

A `SELECT` or `GENERATOR` streamed to a file stays `snow sql --format CSV > file`, plus that same quoting fix. A file name or comment that says large does not make it a stage unload. The stage-unload steps are in output-gaps.md.

## Stop on error

Noninteractive `snow sql` stops at the first failing SQL statement and returns a nonzero exit code. There is no continue-on-error switch.

| Rule | Recognize | Disposition | Replacement or action |
| --- | --- | --- | --- |
| `exit-on-error-true` | `-o exit_on_error=true` on a command whose SQL fails after it renders | rewrite required | Add `--enhanced-exit-codes`. The CLI already stops. SnowSQL exits 5. Without the flag the CLI exits 1. The flag restores 5. It does not continue. Skip this rule when the only error is an undefined `<% name %>` (`variable-undefined`). |
| `exit-on-error-false` | `-o exit_on_error=false` | rewrite required | Split into one `snow sql` per statement. Ignore a failing command so the next still runs. |
| `set-exit-on-error` | Mid-script `!set exit_on_error=...` | rewrite required | Split at each `!set`. After `false`, ignore failures. After `true`, add `--enhanced-exit-codes` and stop. |
| `stop-on-error` | `-o stop_on_error=true` | rewrite required | Run statements until the first failure. Ignore that command's exit so the script exits 0. Do not run the rest. |
| `enhanced-exit-codes` | `--enhanced-exit-codes` or `SNOWFLAKE_ENHANCED_EXIT_CODES=1` | manual review | It changes a SQL failure from exit 1 to exit 5 and still stops at the first failing statement. It does not continue. |

## SnowSQL config files in the project

The connection import handles these files. Record what each section means for the import. Load the configuration reference before import.

| Rule | Recognize | Disposition | Replacement or action |
| --- | --- | --- | --- |
| `config-alias` | An alias key such as `accountname`, `username`, `dbname`, `warehousename`, `rolename`, `pwd`, `private_key_path` | rewrite required | The importer renames it. A blank value is skipped so the next alias is used. |
| `config-secret` | `password`, `pwd`, `passphrase`, `token`, `secret`, `private_key_file_pwd`, `proxy_password` | manual review | Redact the value. Passwords stay strings. |
| `config-connection` | Any other key in `[connections]` or `[connections.<name>]` | manual review | Named connections do not receive keys from the unnamed `[connections]` section. Confirm the key on the connection that should own it. |
| `config-options-copied` | `[options]` `login_timeout`, `client_session_keep_alive`, `ocsp_fail_open`, `insecure_mode`, `client_store_temporary_credential` | compatible | Copied only when a connection omits the key. |
| `config-options` | Any other `[options]` key | rewrite required | Drop the key. It is not a CLI connection setting. |
| `config-connection-options` | `[connection_options]` | rewrite required | Drop the section. It is not imported. |
| `variable-config` | `[variables]` | rewrite required | See Variables. |

## Fallback

| Rule | Recognize | Disposition | Replacement or action |
| --- | --- | --- | --- |
| `unlisted` | A SnowSQL flag, option, directive, or behavior that no rule above names | manual review | Say there is no documented equivalent. Leave it unchanged. |
