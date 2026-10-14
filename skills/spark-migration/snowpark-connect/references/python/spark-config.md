# Spark Configuration Reference — Python (SCOS)

How Snowpark Connect (SCOS) treats Spark configuration set via
`spark.conf.set(key, value)`, `SparkSession.builder.config(key, value)`, or
`SparkConf().set(...)`. Use this when migrating any workload that tunes Spark
configs so honored knobs are preserved, no-op knobs are flagged, and
SCOS-specific knobs are added where they buy parity.

> **Ground truth.** Every classification below is derived from the SCOS engine's
> `snowflake/snowpark_connect/config.py` — specifically `GlobalConfig`,
> `SessionConfig.default_session_config`, `SESSION_CONFIG_KEY_WHITELIST`, and the
> `set_snowflake_parameters(...)` translation. When SCOS changes, re-derive from
> that file; do not hand-edit drift in.

## How SCOS handles a config set

`set_config_param(session_id, key, value, …)` in the engine:

1. stores the value in `global_config` **for any key** (so an unknown/unsupported
   key does **not** raise — it is accepted silently), then
2. only keys that SCOS actually *reads* have any effect:
   - keys translated by `set_snowflake_parameters(...)` (e.g.
     `spark.sql.session.timeZone` → `ALTER SESSION SET TIMEZONE`),
   - keys in `snowpark_config_mapping` (e.g. `spark.app.name` → query tag),
   - the semantic SQL defaults in `GlobalConfig` the planner consults
     (`spark.sql.ansi.enabled`, `caseSensitive`, …),
   - session keys in `SESSION_CONFIG_KEY_WHITELIST`.

**Consequence:** cluster/runtime-resource configs (`spark.executor.*`,
`spark.dynamicAllocation.*`, `spark.shuffle.*`, …) are *stored but never read* —
they are **silent no-ops**. They don't error, so the LLM/operator can easily
miss that they do nothing. SCOS runs on a Snowflake warehouse; it has no Spark
executors to size.

## Three buckets

### 1. PRESERVE — honored / semantics-affecting (never drop)

These change query results or are read by the engine. Keep them byte-for-byte.

| Key | Notes |
|---|---|
| `spark.sql.session.timeZone` | translated to `ALTER SESSION SET TIMEZONE`; **dropping it shifts every timestamp** (the canonical migration bug — preserved by the builder recipe) |
| `spark.sql.timestampType` | SCOS default `TIMESTAMP_LTZ` |
| `spark.sql.ansi.enabled` | **SCOS default `false`** (see deviations below) |
| `spark.sql.storeAssignmentPolicy` | SCOS default `LEGACY`; allowed `ANSI`/`LEGACY`/`STRICT` |
| `spark.sql.caseSensitive` | SCOS default `false` |
| `spark.sql.crossJoin.enabled` | SCOS default `true` |
| `spark.sql.mapKeyDedupPolicy` | SCOS default `EXCEPTION` |
| `spark.sql.sources.partitionOverwriteMode` | `static`/`dynamic` |
| `spark.sql.parquet.outputTimestampType` | translated to `UNLOAD_PARQUET_*` |
| `spark.sql.legacy.*` | individual legacy-behavior toggles the planner reads |
| `spark.app.name` | becomes the Snowpark `query_tag` |
| `spark.jars` | loaded onto the JPype classpath (JDBC drivers) |
| `spark.hadoop.fs.s3a.*` / Azure SAS/account keys | session-whitelisted storage credentials |

Any other `spark.sql.*` / `snowpark.connect.*` / `snowflake.*` key: treat as
**PRESERVE/DEFER** — leave it untouched (the engine may read it; never assume
no-op).

### 2. NO-OP — silently ignored on SCOS (annotate, safe to remove)

Cluster, runtime-resource, and infra knobs Snowflake's warehouse owns. SCOS
stores but never reads them. The deterministic recipe
**`spark_config_noop_annotate`** flags these automatically with
`# SCOS-WARN: [SPRKCNTPY1000] … no-op on SCOS`; you do not need to hand-annotate
them, but if you write a fix, use the same code/category ("No-Op Config").

Families: `spark.executor.*`, `spark.driver.{memory,cores,maxResultSize,memoryOverhead}`,
`spark.dynamicAllocation.*`, `spark.shuffle.*`, `spark.kryo.*` / `spark.kryoserializer.*`,
`spark.memory.*`, `spark.speculation*`, `spark.task.*`, `spark.scheduler.*`,
`spark.yarn.*`, `spark.kubernetes.*`, `spark.mesos.*`, `spark.network.*`,
`spark.rpc.*`, `spark.broadcast.*`, `spark.eventLog.*`, `spark.history.*`,
`spark.ui.*`, `spark.metrics.*`, `spark.cleaner.*`, `spark.storage.*`,
`spark.reducer.*`, `spark.blockManager.*`, `spark.locality.*`; exact keys
`spark.cores.max`, `spark.default.parallelism`, `spark.local.dir`,
`spark.extraListeners`, `spark.logConf`.

> Note: `spark.sql.shuffle.partitions` is **not** read by SCOS (Snowflake plans
> its own partitioning), but it carries the `spark.sql.` prefix and is left
> **untouched** by the recipe out of caution — flag it manually as advisory if
> the workload depends on a specific partition count.

### 3. SCOS-specific knobs — consider ADDING for parity

Not in source Spark; set these when a workload needs Snowflake-side behavior.
Mostly LLM/operator judgment; a row marked *(recipe)* is injected
deterministically in Phase 0.5 when its trigger is statically decidable.

| Key | Use |
|---|---|
| `snowpark.connect.use2000AsTwoDigitCenturyStart` | *(recipe)* default `false`. When `"true"`, SCOS issues `ALTER SESSION SET TWO_DIGIT_CENTURY_START = 2000`, so a two-digit year token (`yy`) parses into Spark's `2000-2099` window instead of Snowflake's default 1970-based one (where `70-99` → `1970-1999`). **Parse-side and session-scoped**: it moves every two-digit-year *interpretation* in the session — all `to_timestamp` / `to_timestamp_ltz` / `to_timestamp_ntz` / `try_to_timestamp` / `to_date` / `unix_timestamp` / `to_unix_timestamp` calls, the same functions reached through `spark.sql(...)`, and native SQL run via `SnowflakeSession` — and nothing else (`yyyy`/`yyy`/`y` map to `YYYY` and never read it; rendering with `date_format` does not either). Injected by `two_digit_year_century_window_config_rewrite` when the format is a literal; see fix-rules Rule 31 for dynamic formats. |
| `snowpark.connect.integralTypesEmulation` | `enabled`/`disabled`/`client_default` — decimal↔integral conversion parity |
| `snowpark.connect.handleIntegralOverflow` | surface integral overflow like Spark |
| `snowpark.connect.artifact_repository` | resolve UDF/UDTF packages from a Snowflake artifact repo instead of Anaconda |
| `snowpark.connect.parquet.useLogicalType` | correct Parquet TIMESTAMP/DATE/DECIMAL reads (default `true`) |
| `snowpark.connect.udtf.compatibility_mode` | UDTF compatibility behavior |
| `snowpark.connect.iceberg.external_volume` / `…base_location` | Iceberg table writes |
| `snowpark.connect.temporary.views.create_in_snowflake` | default `false`. When `true`, `createOrReplaceTempView` is materialized as a real Snowflake (temporary) object, so **native SQL through `SnowflakeSession(spark).sql(...)` can see the view** (e.g. a `SELECT COUNT(*)` against a DataFrame's temp view). With the default `false`, a Spark TempView is client-side only and native SQL referencing it fails with `TABLE_OR_VIEW_NOT_FOUND` — set this `true` for the `safe_count` idiom (see troubleshooting). |

> **Do NOT add `snowpark.connect.sql.passthrough`.** It is **legacy / to be
> deprecated** — it historically ran raw SQL through Snowflake unmodified, but new
> migrations should use `SnowflakeSession(spark).sql(...)` for native SQL
> pass-through instead. If you see it in an existing workload, migrate it to
> `SnowflakeSession`.

## Defaults that DEVIATE from open-source Spark (gotchas)

These differ from Spark 3.5 defaults and can silently change results — call them
out even if the workload never sets them:

- **`spark.sql.ansi.enabled = false`** (Spark 3.5 defaults to `true`). SCOS
  matches EMR/Hive customers' effective `LEGACY` behavior. ANSI-mode error
  semantics (overflow, divide-by-zero, invalid cast) will **not** trigger unless
  you set it `true`.
- **`spark.sql.storeAssignmentPolicy = LEGACY`** (Spark defaults `ANSI`).
- **`spark.sql.timestampType = TIMESTAMP_LTZ`**.
- **`spark.sql.session.timeZone` = JVM local** (not UTC). Preserve any explicit
  setting; never drop it.

## Type coercion on a Snowflake table round-trip (gotcha)

Persisting a DataFrame to a Snowflake table and reading it back (`write` /
`saveAsTable`, then `read` / `spark.table(...)`) **widens integral and float
types**. This happens at the **table boundary only** — in-memory
`spark.createDataFrame(...)` preserves the exact declared types.

| Source Spark type | After a Snowflake table round-trip |
|---|---|
| `ByteType` / `ShortType` / `IntegerType` | `LongType` |
| `FloatType` | `DoubleType` |

The `snowpark.connect.integralTypesEmulation` knob does **not** restore the
original narrow integral types: with `enabled`, a round-tripped integral column
comes back as **`Decimal(38, 0)`**, not `Byte`/`Short`/`Int`. Only set it when
downstream code needs decimal (rather than narrow-int) semantics — otherwise
expect `Long`. If a workload asserts exact narrow types after persistence, add an
explicit `cast(...)` after the read instead of relying on this config.

## EWI tagging

- No-op cluster/runtime config → `# SCOS-WARN: [SPRKCNTPY1000] …` ("No-Op
  Config", Warning). Emitted automatically by `spark_config_noop_annotate`.
- Hadoop credential config (`hadoopConfiguration`, `fs.s3a.*` set in code) →
  `SPRKCNTPY3202` (rewrite to a Snowflake storage integration / stage).
- A semantics-affecting deviation worth a reviewer's eye (e.g. workload relies on
  ANSI behavior) → `# SCOS: TODO -` near the site with `SPRKCNTPY1000`.

## Quick decision table

| You see `spark.conf.set("X", …)` where X is… | Action |
|---|---|
| `spark.sql.*`, `snowpark.connect.*`, `snowflake.*`, `spark.app.name`, `spark.jars`, `spark.hadoop.fs.s3a.*` | **keep** (honored / may be read) |
| `spark.executor.*` / `spark.driver.memory|cores` / `spark.dynamicAllocation.*` / `spark.shuffle.*` / other cluster-runtime family | **no-op** — `spark_config_noop_annotate` flags it; safe to delete |
| `sc.hadoopConfiguration.set("fs.s3a.…")` | rewrite to a storage integration / stage (`SPRKCNTPY3202`) |
| anything else / unknown | **leave untouched**, optionally advisory-annotate; do not assume no-op |
