# Output gaps and remediations

Load this page when a finding is JSON, CSV, TSV, HTML, grid, plain, spool, async, single-transaction, query-tag, `header=false`, or a large extract.

## Contents

- Rewrite one-liners
- Plan rows
- JSON types
- TABLE, PLAIN, and spool layout
- HTML and grid
- Scheduled query id
- Success status rows
- header=false
- CSV quoting
- CSV specials
- TSV

CLI result rows can match SnowSQL while the bytes differ. Write that difference in the plan. Suggested fix must name the parity path you will apply on Implement (rewrite one-liner or matching code). Do not leave the fix as "ask the user".

Default on Implement: restore SnowSQL parity. Prefer **Rewrite** (one-liner below). Use **Matching code** when the one-liner cannot restore SnowSQL bytes (embedded newline, tab, CR, NULL as `""`, doubled backslash). **Accept** only when no rewrite or matching-code path exists, or the plan already records that the project tolerates CLI bytes — never as the default.

Do not skip a rewrite because a tool is missing; say so in the report. Do not invent a SnowSQL emulator unless matching code needs those bytes.

## Rewrite one-liners

Quote every CSV field:

```bash
snow sql --format CSV -f sql/quote-contract.sql \
  | python3 -c "import csv,sys; csv.writer(sys.stdout, quoting=csv.QUOTE_ALL, lineterminator='\n').writerows(csv.reader(sys.stdin))" \
  > out/quote.csv
```

CSV to TSV:

```bash
snow sql --format CSV -f sql/csv-contract.sql \
  | python3 -c "import csv,sys; csv.writer(sys.stdout, dialect='excel-tab', lineterminator='\n').writerows(csv.reader(sys.stdin))" \
  > out/result.tsv
```

JSON numbers as strings, one line:

```bash
snow sql --format JSON -f sql/json.sql \
  | jq -c 'walk(if type == "number" then tostring else . end)' \
  > out/result.json
```

Drop a CSV header (`header=false`):

```bash
snow sql --format CSV -q "select 1 as id, 'batch' as note" | tail -n +2 > out/batch.csv
```

The quote and TSV lines do not restore SnowSQL `\n` `\t` `\r` escapes, a doubled backslash, or NULL as `""`. That is matching code, `COPY INTO` with a `FILE_FORMAT`, or keep SnowSQL.

## Plan rows

For each gap:

```markdown
| Location | Difference | Consumer | Suggested fix |
| scripts/10-output-json.sh | CLI pretty-prints JSON; `1` is a number, SnowSQL writes `"1"` | parser | rewrite (`jq` numbers-as-strings) for parity |
```

CSV rows still add `QUOTING: CHANGED`. The second `QUOTING:` line is what Implement applied: `REWRITE`, `FILE_FORMAT`, or `ACCEPTED` only when parity was not available.

## JSON types

SnowSQL writes one line and quotes every value: `[{"ID": "1", "NAME": "a", "AMOUNT": "8.88", "NOTHING": null}]`.

CLI pretty-prints and leaves a small integer as a number: `"ID": 1`. Parsed values match.

Rewrite is the `jq` line above. If they want matching code and no `jq`, write the same walk in Python.

## TABLE, PLAIN, and spool layout

Same cells. Different padding and box drawing. SnowSQL: `|  1 | spooled |`. CLI: `| 1  | spooled |`. PLAIN: ` 1  plain` vs `1   plain`. Grid uses SnowSQL `+====+` rules; the CLI writes default TABLE.

No pipe matches SnowSQL table art. Point the consumer at CSV or JSON, or keep parsing cells.

## HTML and grid

SnowSQL `output_format=html` writes `<table>…`. `output_format=grid` writes a different box style. The CLI has neither. The copy writes TABLE.

No HTML flag. If a page needs a table, build HTML from CSV or JSON. Do not keep `output_format=html` and expect HTML.

## Scheduled query id

`;>` on the CLI prints a `scheduled query ID` row. SnowSQL 1.4 and 1.5 noninteractive print none. The id is not the query result.

Keep the CLI row unless they choose rewrite or matching code. Rewrite: run the schedule in a separate command and capture only the result command. Do not drop `;>` to hide it.

## Success status rows

`--single-transaction` wraps the file in `BEGIN` and `COMMIT`. The CLI prints `Statement executed successfully.` for each. `ALTER SESSION` for a query tag does the same. SnowSQL does not print those wrapper rows.

Those rows are not statement results. Ignore them when comparing. Rewrite: run `BEGIN`/`COMMIT` or `ALTER SESSION` as their own `snow sql` and keep the query command's output.

## header=false

SnowSQL `!set header=false` or `-o header=false` drops the header row. The CLI always prints headers.

Rewrite for CSV is `tail -n +2`. For TABLE, do not strip box-drawing rows. Switch that consumer to CSV, or write matching code.

## CSV quoting

SnowSQL: `"1","3,456.00","8.88"` (every field quoted).

CLI: `1,"3,456.00",8.88` (quote only comma, quote, or newline).

`QUOTING: REWRITE` when Implement applies quote-every-field on clean columns (no embedded newline): use the `python3` line above. `QUOTING: ACCEPTED` only when parity was not applied.

`QUOTING: FILE_FORMAT` keeps `COPY INTO` with an explicit file format. That enclosure is optional and does not quote every field. Apply the same quote-all line to the downloaded file when they still want those quotes.

## CSV specials

A field with a newline, tab, CR, backslash, or NULL:

- SnowSQL writes `\n`, `\t`, `\r` as two characters, doubles a backslash, and writes NULL as `""`.
- CLI writes a raw newline and tab, leaves one backslash, drops the CR from a CR LF, and writes NULL as an empty field.

Parsed fields match after undoing SnowSQL escapes and dropping CR. The quote-all line does not repair those bytes.

## TSV

The CLI has no TSV format. The default copy writes CSV. Rewrite is the TSV one-liner, or `COPY INTO` with `FIELD_DELIMITER='\t'`. Escapes still will not match SnowSQL TSV unless they asked for matching code.
