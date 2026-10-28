# Output gaps and remediations

Load this page when a finding is JSON, CSV, TSV, HTML, grid, plain, spool, async, single-transaction, query-tag, `header=false`, a dropped `-o` option, or a large extract.

## Apply the fix

On Implement, apply the Suggested fix. Do not ask. `snow sql` still replaces `snowsql` when the parity pipe cannot be written.

Keep every original output path. `-o output_file=<path>` becomes `> <path>` with the same path and extension. `!spool <path>` keeps that path. Do not invent a new file name because the format changed.

Choose the fix from the consumer and from tools already on the machine:

1. The consumer reads parsed fields and does not need SnowSQL bytes. Keep CLI output and say so. For CSV that is `QUOTING: ACCEPTED`.
2. The consumer needs SnowSQL bytes. Use a tool the repo already uses for that transform.
3. The transform is JSON and `command -v jq` succeeds. Use the `jq` line below.
4. Another installed tool does this exact transform. CSV and TSV need a CSV-aware tool. `jq` does not quote CSV or write TSV.
5. Nothing installed does that transform. Leave the pipe off, record the byte gap, and mark only that pipe for review.

Do not use `python3`, `awk`, or `sed`. Do not install a tool. Do not invent a `COPY INTO` unless the project already unloads to a stage, or a person said the result is too big to stream. Matching code is only for bytes no installed tool can write: embedded newline, tab, CR, NULL as `""`, doubled backslash.

## Plan row

```markdown
| Location | Difference | Consumer | Suggested fix |
| scripts/10-output-json.sh | CLI pretty-prints JSON; `1` is a number, SnowSQL writes `"1"` | parser | `jq` numbers-as-strings |
```

Name the tool, `accept CLI bytes`, or `manual review: no installed tool`. Every CSV row also has `QUOTING: CHANGED`, then `ACCEPTED`, `REWRITE`, or `FILE_FORMAT` for the fix Implement will apply. A pipe marked for review has no second `QUOTING:` line.

## Gaps

### JSON

SnowSQL writes one line and quotes every value: `[{"ID": "1"}]`. CLI pretty-prints and leaves a small integer as a number. Parsed values match.

Suggested fix: the `jq` line when the consumer needs those strings. Accept CLI JSON when it parses values.

```bash
snow sql --format JSON -f sql/json.sql \
  | jq -c 'walk(if type == "number" then tostring else . end)' \
  > out/result.json
```

### TABLE, PLAIN, and spool

Same cells. Padding and box drawing differ. SnowSQL: `|  1 | spooled |`. CLI: `| 1  | spooled |`. PLAIN spacing differs the same way. Grid becomes default TABLE.

Suggested fix: keep TABLE, PLAIN, or `!spool`. No pipe matches the art. On Snowflake CLI 3.31.0, including `3.31.0.dev0` from `main`, keep `!spool`. On 3.30, split at `!spool off` and `tee`. If the consumer splits on padding, point it at CSV or JSON.

### HTML and grid

SnowSQL HTML is `<table>…`. Grid is a different box. The CLI writes TABLE.

Suggested fix: TABLE. When a page needs HTML, build it from CSV or JSON with a tool the project already uses.

### Scheduled query id

`;>` prints a `scheduled query ID` row. SnowSQL 1.4 and 1.5 noninteractive print none. The id is not the result.

Suggested fix: keep the row. When the consumer treats stdout as the result, run the schedule in its own command and capture only the result command. Keep `;>`.

### Success status rows

`--single-transaction` prints `Statement executed successfully.` for `BEGIN` and `COMMIT`. A query-tag `ALTER SESSION` does the same. SnowSQL prints neither.

Suggested fix: ignore those rows when comparing. When the consumer reads the whole stdout, run the wrapper as its own `snow sql` and keep the query command's output.

### header=false

The CLI always prints a header.

Suggested fix for CSV: `tail -n +2`. Leave TABLE box-drawing in place. Switch that consumer to CSV, then strip the header.

```bash
snow sql --format CSV -q "select 1 as id, 'batch' as note" | tail -n +2 > out/batch.csv
```

### CSV quoting

SnowSQL quotes every field: `"1","3,456.00","8.88"`. CLI quotes a field only when it contains a comma, a quote, or a newline: `1,"3,456.00",8.88`.

Record `QUOTING: CHANGED`. Then:

| Context | Fix |
| --- | --- |
| Consumer reads CLI quoting | No pipe. `QUOTING: ACCEPTED`. |
| Consumer needs every field quoted | Pipe through an installed CSV-aware tool. `QUOTING: REWRITE`. |
| Extract already unloads to a stage | Keep `COPY INTO`. `QUOTING: FILE_FORMAT`. Quote-all the download only when those quotes are still required. |
| Quotes are required and no CSV tool is installed | No pipe. Mark the pipe for review. |

`FIELD_OPTIONALLY_ENCLOSED_BY` does not quote every field. A quote-all pipe wraps each field in `"` and doubles a `"` inside a field. It does not rename a header or fill a dropped row.

### CSV specials

A newline, tab, CR, backslash, or NULL. SnowSQL writes `\n`, `\t`, and `\r` as two characters, doubles a backslash, and writes NULL as `""`. CLI writes a raw newline and tab, one backslash, drops the CR, and writes NULL as an empty field.

Parsed fields match after undoing those escapes and dropping CR. A quote-all pipe does not.

Suggested fix: `snow sql --format CSV` and record the byte gap. Matching code only when the consumer needs those exact bytes.

### TSV

The CLI has no TSV format. Keep the original `-o output_file` path, including a `.tsv` extension.

Suggested fix: an installed tool that writes TSV to that same path. Use `COPY INTO` with `FIELD_DELIMITER='\t'` only when the project already unloads to a stage. With no TSV tool, redirect CSV to the original path and mark the pipe for review:

```bash
snow sql -c example --format CSV -f sql/csv-contract.sql > out/result.tsv
```

The file name stays. The bytes are CSV until a TSV tool exists.

### Dropped options

`timing`, `timing_in_output_file`, `friendly`, `quiet`, `echo`, `log_level`, `rowset_size`, and `sfqid`. Drop the option.

The CLI prints its own result to the terminal and, with a redirect or `!spool`, to the file. Nothing mutes the terminal while the file still receives rows, copies the SQL text into the file, or adds SnowSQL's timing and query-id footer. Record that residual. Do not add a pipe to imitate it.

### Large extracts

A `SELECT` or `GENERATOR` streamed to a file stays `snow sql --format CSV > file` plus the CSV quoting fix. A name or comment that says large is not a reason for `COPY INTO`.

Use a stage unload only when the project already has one, or a person said the result is too big to stream:

```sql
COPY INTO @my_stage/extract/
FROM (select ... )
FILE_FORMAT = (TYPE = CSV)
HEADER = TRUE;
```

```bash
snow stage copy @my_stage/extract/ ./extract/ --recursive
```

`HEADER` is a `COPY INTO` option, never a `FILE_FORMAT` parameter. Use a stage and a file format the project already has. Ask before creating either. Do not run the `COPY INTO` until they confirm a named non-production connection.

A faster TABLE render does not show that a CSV extract kept every row.
