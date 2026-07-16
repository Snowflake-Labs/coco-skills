# FactorPrism SQL reference

## Bind a source

Substitute the actual installed application database name for `FACTORPRISM`.

```sql
CALL FACTORPRISM.API.SET_SOURCE(
  'SOURCE_VIEW',
  SYSTEM$REFERENCE('VIEW', 'MY_DB.MY_SCHEMA.MY_VIEW', 'PERSISTENT', 'SELECT')
);
```

Use `SOURCE_TABLE` and `TABLE` for a table. A persistent binding survives upgrades.
To remove it:

```sql
CALL FACTORPRISM.API.CLEAR_SOURCE('SOURCE_VIEW');
```

## Run an analysis

```sql
CALL FACTORPRISM.API.RUN_DECOMPOSITION(
  source_ref    => 'SOURCE_VIEW',
  date_field    => 'ORDER_DATE',
  metric_field  => 'REVENUE',
  use_row_count => FALSE,
  rollup        => 'WEEK',
  hierarchies   => PARSE_JSON('[["REGION"],["PRODUCT"]]'),
  period_start  => '2026-04-01',
  period_end    => '2026-06-30',
  persist       => FALSE
);
```

### Parameters

- `source_ref`: `SOURCE_TABLE` or `SOURCE_VIEW`.
- `date_field`: date or timestamp column.
- `metric_field`: numeric column to sum. Pass `NULL` with `use_row_count => TRUE`.
- `rollup`: `DAY`, `WEEK`, `MONTH`, or `YEAR`.
- `hierarchies`: JSON array of arrays. Columns inside one array are broad-to-narrow
  levels. Separate arrays are independent dimensions.
- `period_start`, `period_end`: inclusive analysis window.
- `baseline_period`: optional date, comma-separated dates, or JSON array. Every
  baseline date must be before `period_start`.
- `persist`: optional; defaults to `TRUE`.

Examples:

- Nested geography: `[["REGION","DIVISION","STATE"]]`
- Independent dimensions: `[["REGION"],["PRODUCT"],["CHANNEL"]]`
- Mixed: `[["REGION","STATE"],["CATEGORY","PRODUCT"],["CHANNEL"]]`

## Read the result

Important fields include:

- `RANK`: importance order.
- `FACTOR`: business location of the driver.
- `ORIGIN`: broad-based or localized.
- `PATTERN`: timing and shape.
- `AVG_MOVEMENT_PER_PERIOD`: average absolute movement.
- `NET_CONTRIBUTION_PER_PERIOD`: signed units explained per period.
- `SHARE_OF_NET_CHANGE_PCT`: share of net change; may be `NULL` when offsetting
  movement makes the net change too small for a useful percentage.

If `SHARE_OF_NET_CHANGE_PCT` is `NULL`, explain that the metric was roughly flat
while its composition shifted and rank by `AVG_MOVEMENT_PER_PERIOD` instead.

## Safety and interpretation

- The app reads only the bound object.
- Keep `persist => FALSE` for an ad-hoc agent answer.
- Results locate and quantify where movement originated; they are not experimental
  proof that an intervention caused the change.
