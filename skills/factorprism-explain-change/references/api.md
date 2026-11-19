# FactorPrism SQL reference

Replace `FACTORPRISM` with the application name returned by
`SHOW APPLICATIONS LIKE '%FACTORPRISM%';`.

## Bind a source (once per table or view)

```sql
CALL FACTORPRISM.API.SET_SOURCE(
  'SOURCE_VIEW',
  SYSTEM$REFERENCE('VIEW', 'MY_DB.MY_SCHEMA.MY_VIEW', 'PERSISTENT', 'SELECT')
);
```

Use `'SOURCE_TABLE'` and `'TABLE'` for a table. The binding is read-only, covers
only that object, and survives app upgrades. To remove it:

```sql
CALL FACTORPRISM.API.CLEAR_SOURCE('SOURCE_VIEW');
```

## Explain a change: API.RUN_DECOMPOSITION

```sql
CALL FACTORPRISM.API.RUN_DECOMPOSITION(
  source_ref      => 'SOURCE_VIEW',
  date_field      => 'ORDER_DATE',
  metric_field    => 'REVENUE',
  use_row_count   => FALSE,
  rollup          => 'WEEK',
  hierarchies     => PARSE_JSON('[["REGION","STATE"],["PRODUCT"]]'),
  period_start    => '2026-04-01',
  period_end      => '2026-06-30',
  baseline_period => NULL,
  persist         => FALSE
);
```

Parameters:

- `source_ref`: `'SOURCE_TABLE'` or `'SOURCE_VIEW'`.
- `date_field`: date or timestamp column.
- `metric_field`: numeric column to sum. Pass `NULL` with `use_row_count => TRUE`
  to count rows instead.
- `rollup`: `'DAY'`, `'WEEK'`, `'MONTH'`, or `'YEAR'`.
- `hierarchies`: JSON array of arrays. Columns inside one array are broad-to-narrow
  levels; separate arrays are independent dimensions.
  - Nested: `[["REGION","DIVISION","STATE"]]`
  - Independent: `[["REGION"],["PRODUCT"],["CHANNEL"]]`
  - Mixed: `[["REGION","STATE"],["CATEGORY","PRODUCT"],["CHANNEL"]]`
- `period_start`, `period_end`: inclusive window to explain.
- `baseline_period`: optional. `NULL` uses the window's first period as the
  baseline. Otherwise a date, comma-separated dates, or a JSON array of dates to
  average; every baseline date must be before `period_start`.
- `persist`: optional, default `TRUE`. Saved runs land in
  `FACTORPRISM.APP_RESULTS.SAVED_SESSIONS`.

Result columns, one row per factor:

| Column | Meaning |
|---|---|
| `RANK` | Importance order. |
| `FACTOR` | Where the factor acts: a segment, a segment within another, or the whole business. |
| `TYPE` | `Factor`; `Launch` (a segment that started from zero in the window); `Group` (several small sibling segments moving together, rolled into one row); or `Diffuse` (the "All other" residual, movement not localized to one segment). |
| `ORIGIN` | `Broad-based` or `Localized`. |
| `PATTERN` | When it acted and its shape over the window. |
| `BASELINE` | Baseline volume the factor acts on. |
| `AVG_FACTOR_EFFECT_PCT` | Average multiplicative effect, in percent. |
| `AVG_MOVEMENT_PER_PERIOD` | Average size of the factor's movement per period, ignoring sign across periods. |
| `NET_CONTRIBUTION_PER_PERIOD` | Signed units the factor adds or removes per period. |
| `SHARE_OF_NET_CHANGE_PCT` | Share of the net change. `NULL` when the net change is too small for a meaningful share. |
| `COLLINEAR_FACTORS` | Other locations that explain this movement equally well; empty on most data. |

## Check for unusual moves: API.RUN_VERDICT

One row on the latest period versus the one before. Columns are detected
automatically when the arguments are omitted. It does not need Cortex.

```sql
CALL FACTORPRISM.API.RUN_VERDICT();

SELECT MODE, SENTENCE, PCT_CHANGE, TYPICAL_MOVE_PCT, LEAD_FACTORS
FROM   TABLE(RESULT_SCAN(LAST_QUERY_ID()))
WHERE  MODE NOT IN ('steady', 'flat');
```

Optional named arguments: `source_ref` (default `'SOURCE_TABLE'`), `date_field`,
`metric_field`, `use_row_count`, `hierarchies`.

- `MODE`: `steady` (within its normal swing) or `flat` (no material change), when
  nothing is unusual; otherwise `dominant` (one factor clearly leads), `split` (two or
  three factors lead together), or `dispersed` (spread across many small locations).
- `SENTENCE`: a plain-language verdict.
- `PCT_CHANGE` vs `TYPICAL_MOVE_PCT`: this period's change against the usual swing.
- `GRAIN`, `WINDOW_START`, `WINDOW_END`: the periods compared.
- `LEAD_FACTORS`: JSON array of `{"label", "share", "direction"}`.

## Read a saved run

```sql
SELECT d.value:FACTOR::string                     AS factor,
       d.value:NET_CONTRIBUTION_PER_PERIOD::float AS net_contribution,
       d.value:SHARE_OF_NET_CHANGE_PCT::float     AS share_pct
FROM   FACTORPRISM.APP_RESULTS.SAVED_SESSIONS s,
       LATERAL FLATTEN(input => s.session_blob:headless_drivers) d
WHERE  s.session_id = '<session id>'
ORDER  BY d.value:RANK::int;
```
