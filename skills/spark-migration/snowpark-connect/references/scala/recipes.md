# Scala Deterministic Pre-Processing (Phase 0.5 — Scalafix AST rules)

Scala migrations have **one** deterministic pre-processing tier: AST-grade
Scalafix rules. The earlier regex recipe engine (`scripts/recipes_scala/`,
`preprocess_recipes_scala.py`) was **removed** — every transform now runs at the
Scalameta AST level, the direct analogue of libcst for PySpark.

- **Rules**: `scripts/scalafix_rules/SCOSRules.scala` (Scalameta `SyntacticRule`s,
  no SemanticDB required).
- **Registration**: `scripts/scalafix_rules/scos.scalafix.conf` (fully-qualified
  `class:` names). Adding a rule there auto-registers it — the driver discovers
  rules from this file.
- **Driver**: `scripts/preprocess_scalafix.py`. Records its summary under
  `migration_state.json["phases_completed"]["0_5b_scalafix"]`.

Because they parse the AST, the rules handle multi-line chains, string
interpolation, computed expressions, enclosing-scope context (loops), and
chained-receiver forms that line-anchored regexes cannot match — with no
comment/string false positives.

## Hard requirement (SBT + JVM)

Scala migration projects are SBT/JVM projects, so the AST runner is
**mandatory, not best-effort**. You need `uv` plus **one of** (resolved in
order): `sbt` + a JVM (preferred), `scalafix-cli` on PATH, or Coursier
(auto-bootstrapped). If no runner can be resolved, the driver exits **1** and
records `status: "failed"` — the migration MUST NOT advance to Phase 1. Pinned
versions: scala 2.12.20, scalafix-cli 0.14.3.

## The rules

| Rule | Purpose | Emits |
|---|---|---|
| `ScosCheckpointToCache` | `.checkpoint(...)` → `.cache()` (truncates lineage equivalently in Snowpark Connect) | rewrite + `// SCOS:` comment |
| `ScosMapSubscriptToElementAt` | `col("m")("k")` / `$"m"("k")` map subscript → `element_at(col("m"), "k")` | rewrite |
| `ScosWildcardReadAnnotate` | wildcard/glob file-read path (`*` in a read path) | `// SCOS: TODO -` annotation |
| `ScosSparkSessionBuilderRewrite` | renames SparkSession→SnowparkConnectSession, drops .master/.enableHiveSupport/.remote, preserves .config(k,v) | `// SCOS-RECIPE-PRESERVED-CONFIG: k=v` markers + `// SCOS-WARN:` for non-extractable configs |
| `ScosSaveAsTableDropStorageOpts` | drops storage-only options on `saveAsTable` | rewrite + `// SCOS:` comment |
| `ScosExternalCloudReadAnnotate` | read from a cloud scheme (`s3://`, `gs://`, `wasb://`, …) | `// SCOS: Performance tip -` annotation |
| `ScosSelfJoinUnaliasedAnnotate` | unaliased self-join `x.join(x, …)` | `// SCOS: TODO -` annotation |
| `ScosSparkContextPropertyFallbackAnnotate` | `sc.parallelize` / `sc.broadcast` / `spark.sparkContext.*` | `// SCOS: [SPRKCNTSCL…]` annotation |
| `ScosUdtfCompatibilityModeAnnotate` | class extending `UserDefinedTableFunction` / `GenericUDTF` | `// SCOS: TODO -` annotation |
| `ScosUnionByNameAllowMissingAnnotate` | `unionByName(..., allowMissingColumns = true)` | `// SCOS: TODO -` annotation |
| `ScosDriverHotPathAnnotate` | `collect` / `toLocalIterator` / `collectAsList` inside a loop (true enclosing-scope analysis) | `// SCOS: Performance tip -` annotation |
| `ScosTempViewMultiUseCache` | temp view referenced ≥2× in SQL strings and not already cached | `// SCOS: Performance tip -` annotation |
| `ScosSystemGetenvRewrite` | `System.getenv("X")` → `System.getProperty("X")` (harness injection) | rewrite |
| `ScosDeltaTableAnnotate` | `DeltaTable.forPath(...)` / `.forName(...)` Delta table API | `// SCOS: TODO -` annotation |
| `ScosPartitionNoopStrip` | `.repartition(N)` / `.coalesce(N)` (partition hints ignored in SCOS) | rewrite (no-op strip) |
| `ScosDeltaWriteToParquet` | `df.write.format("delta")...save(path)` → `df.write.mode(...).saveAsTable(name)` | rewrite + `// SCOS:` comment |
| `ScosDisplayToShow` | bare `display(df)` global (Databricks notebook) → `df.show()` | rewrite + `// SCOS:` comment |
| `ScosDbUtilsWidgetsToProperty` | `dbutils.widgets.get/text/dropdown/...` → `System.getProperty`/`setProperty` | rewrite |
| `ScosDbUtilsSecretsGetStub` | `dbutils.secrets.get/getBytes(...)` → null stub + SCOS-TODO | rewrite |
| `ScosSaveAsTableDropStorageOpts` | `.write.format(...).option("path",...)...saveAsTable(...)` — drops storage opts | rewrite + `// SCOS:` comment |
| **New: 2026-07 parity rules** | | |
| `ScosDisplayMethodToShow` | `df.display()` (zero-arg method form) → `df.show()` | rewrite + `// SCOS:` comment |
| `ScosSnowflakeConnectorIO` | `.format("snowflake")...load()` → `new SnowflakeSession(sess).sql(Q)`; `.format("snowflake")...save()` → `.write.saveAsTable(T)` | rewrite or `// SCOS: TODO` + `SCOS-RECIPE-INSERT-IMPORT:` marker |
| `ScosApproxCountDistinctDropRsd` | `approxCountDistinct(col, rsd)` → `approxCountDistinct(col)` | rewrite + `// SCOS:` comment |
| `ScosHadoopConfCredentialAnnotate` | `sc.hadoopConfiguration().set("fs.s3*",...)` / `spark.conf.set("fs.s3*",...)` | `// SCOS: TODO -` annotation |
| `ScosRddImportAnnotate` | `import org.apache.spark.rdd.*` | `// SCOS: TODO -` annotation |
| `ScosRddExclusiveMethodAnnotate` | `reduceByKey`, `groupByKey`, `sortByKey`, `mapPartitions`, `saveAsTextFile`, … plus the aggregate/§10 additions `treeAggregate`, `treeReduce`, `collectAsMap`, `countApprox`, `countApproxDistinct`, `meanApprox`, `sumApprox`, `repartitionAndSortWithinPartitions`, `toDebugString` (RDD-exclusive names, no DataFrame homonym) | `// SCOS: TODO -` annotation |
| `ScosAccumulatorAnnotate` | Spark accumulator markers — factory calls `longAccumulator`/`doubleAccumulator`/`collectionAccumulator`, type usages `LongAccumulator`/`DoubleAccumulator`/`CollectionAccumulator`, and `extends AccumulatorV2[…]` (never a bare `accumulator` substring — no `sc.accumulator`/`AccumulatorParam` in Scala) | `// SCOS: TODO - [SPRKCNTSCL1500]` annotation routing to the DataFrame-`agg` conversion (see `rdd-conversion.md`), **not** a blanket file-drop |
| `ScosRddPersistToCache` | `df.rdd.persist(...)` / `df.rdd.cache()` → `df.persist(...)` / `df.cache()` | rewrite + `// SCOS:` comment |
| `ScosScRangeToSparkRange` | `sc.range(N)` → `spark.range(N)` | rewrite + `// SCOS:` comment |
| `ScosScTextfileToReadText` | `sc.textFile("path")` → `spark.read.text("path")` (drops numPartitions) | rewrite + `// SCOS:` comment |
| `ScosScWholeTextFilesAnnotate` | `sc.wholeTextFiles("path")` (no direct DataFrame equivalent) | `// SCOS: TODO -` annotation |
| `ScosSparkContextGetOrCreateRewrite` | `SparkContext.getOrCreate()` → `SnowparkConnectSession.builder().getOrCreate()` | rewrite or TODO |
| `ScosSparkContextNoopCommentOut` | `sc.stop()` / `sc.close()` / `sc.setLogLevel()` (SCOS no-ops) | rewrite (comment-out) |
| `ScosSparkConfigNoopAnnotate` | `spark.conf.set("spark.executor.*"/"spark.driver.*"/YARN/K8s keys, …)` | `// SCOS: TODO -` annotation |
| `ScosUnpersistDropBlockingArg` | `df.unpersist(blocking = true)` → `df.unpersist()` | rewrite + `// SCOS:` comment |
| `ScosSqlContextImplicitsRewrite` | `import spark.sqlContext.implicits._` → `import spark.implicits._` (sqlContext not exposed in SCOS) | rewrite + `// SCOS: [SPRKCNTSCL3500]` comment |
| `ScosSparkIoDetectAnnotate` | JDBC chains (`.format("jdbc")`/`.jdbc(...)`) → `[SPRKCNTSCL6000-Error]`; Iceberg chains (`.format("iceberg").load/save`) → `[SPRKCNTSCL3200-IO]`; `.read.table(name)` / `.insertInto(name)` → `[SPRKCNTSCL3200-IO]` (verify table namespace). Cloud URI reads and wildcard paths are excluded (handled by `ScosExternalCloudReadAnnotate` / `ScosWildcardReadAnnotate`). Parity: PySpark `spark_io_detect` recipe (PR #3575). | `// SCOS: [CODE-Status]` annotation |

The exact comment/marker strings are defined verbatim in `SCOSRules.scala`; that
file is the source of truth for the emitted text.

## `recipe_edits` contract

The driver merges per-file edits into the top-level `recipe_edits` block of
`migration_state.json`, keyed by relative path:

```json
"recipe_edits": {
  "<rel_path>.scala": [
    {
      "recipe_id": "scalafix:<RuleName>",
      "src_line": <int>,
      "output_line_anchor": "scalafix:<RuleName>:<src_line>:<8-hex>"
    }
  ]
}
```

`recipe_id` is always in the `scalafix:<RuleName>` namespace (e.g.
`scalafix:ScosSparkSessionBuilderRewrite`). Each rule runs as its own subprocess and
its edits are attributed via a difflib snapshot, so attribution is per-rule.

The analyzer (Phase 1) and fixer (Phase 2) **MUST** read `recipe_edits` and treat
those lines as already-handled: do not re-flag, re-rewrite, collapse, or revert
them. The Phase 2 verifier asserts every `SCOS-RECIPE-PRESERVED-CONFIG` marker
survives the fixer; Phase 3 verifies the preserved config is materialized after
the session rebuild.

## Adding a rule

1. Add a `class Scos… extends SyntacticRule("Scos…")` to `SCOSRules.scala`
   following the `doc.tree.collect { case <pattern> => Patch… }` pattern.
2. Register its fully-qualified name in `scos.scalafix.conf` (the driver
   discovers it from there automatically).
3. Add a gated parity test in
   `scripts/tests/test_scalafix_ported_recipes_scala.py` (static guard always
   runs; behavior test runs under `SCOS_RUN_SCALAFIX_IT=1` with sbt on PATH).

## Idempotency

Every rule is idempotent: after a rewrite the result no longer matches the
trigger pattern, and annotations are guarded so re-running the driver on
already-processed files is a safe no-op.

## Deliberate divergences (PySpark recipes without Scala equivalents)

The following PySpark recipe categories have **no Scalafix rule** by design:

| PySpark recipe | Reason not ported |
|---|---|
| `count_numpy_int_cast_rewrite` | NumPy types are Python-only; no Scala equivalent |
| `dbutils_library_installpypi_stub_rewrite` | Python `%pip install` magic — no Scala equivalent |
| `dbutils_library_restartpython_strip_rewrite` | Python-specific magic — no Scala equivalent |
| `display_matplotlib_to_show_rewrite` | `matplotlib` is Python-only |
| `toLocalIterator_drop_prefetch_arg_rewrite` | `prefetchBufferSize` is a PySpark-only kwarg; Scala `toLocalIterator` takes no args |
| `udf_local_import_add_artifact_rewrite` | Python `pyfile=` / `addArtifact` pattern is Python-specific; Scala UDFs use JAR-based `spark.addArtifact` (see `references/scala/udf-dependencies.md`) |
| `df_rdd_passthrough_rewrite` (full rewrite) | Scala Bucket B passthrough rewrites (`df.rdd.count()` → `df.count()`, etc.) require judgment because some (e.g. `df.rdd.isEmpty()`) are unsafe in Scala SCOS. Handled by the LLM fixer using `fix-rules.md` Rule 2 + `rdd-conversion.md`. The safe `df.rdd.persist/cache()` case IS handled by `ScosRddPersistToCache`. |
| `spark_sql_mechanical_rewrite` | SQL dialect fixes require semantic understanding of the SQL string content — handled by the LLM fixer via `fix-rules.md`. |
| `udf_backed_builtin_perf_annotate` | Requires knowing which Spark functions are built-ins (library knowledge, not AST patterns) — handled by LLM fixer guidance. |
| `implicit_spark_inject_bootstrap` | Scala notebooks with an implicit `spark` session have their bootstrap handled by `ScosSparkSessionBuilderRewrite` (Phase 0.5) + `update_imports_scala.py` (Phase 3). |
| `sc_parallelize_to_createdataframe_rewrite` | Schema inference for `createDataFrame` requires type-level knowledge; `ScosSparkContextPropertyFallbackAnnotate` annotates the call with guidance; the LLM fixer completes the rewrite using `rdd-conversion.md` Bucket C. |
