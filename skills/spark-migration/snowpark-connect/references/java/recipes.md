# Java Deterministic Pre-Processing (Phase 0.5c — JavaParser AST rules)

Java migrations have **one** deterministic pre-processing tier: AST-grade JavaParser rules.
This is the direct analogue of Scalafix for the Java migration path.

- **Rules**: `scripts/javaparser_rules/ScosJavaRewrite.java` (JavaParser visitor-based rules).
- **Facts extractor**: `scripts/javaparser_rules/ScosJavaFacts.java` (JSON facts for analyzer).
- **Maven wrapper**: `scripts/javaparser_maven/pom.xml` (builds a fat-jar on first run).
- **Driver**: `scripts/preprocess_javaparser.py`. Records its summary under
  `migration_state.json["phases_completed"]["0_5c_javaparser"]`.

Because they operate on the JavaParser CST (`LexicalPreservingPrinter`), the rules handle
multi-line chains, string expressions, enclosing-scope context, and chained-receiver
forms that line-anchored regexes cannot match — with no comment/string false positives.

## Hard requirement (JDK + Maven)

Java migration projects are Maven/Gradle/JDK projects, so the AST runner is
**mandatory, not best-effort**. You need `uv` (always) plus a **JDK** and **Maven**
(or Maven wrapper `./mvnw`). The fat-jar is built on first run via
`scripts/javaparser_maven/pom.xml` and cached at
`scripts/javaparser_maven/target/scos-javaparser-*.jar`.

If no JDK/Maven is available, the driver exits **1** and records `status: "failed"` —
the migration MUST NOT advance to Phase 1. Install a JDK (11+ required) and Maven.

## The 12 rules

| Rule | Purpose | Emits |
|---|---|---|
| `ScosSparkSessionBuilderRewrite` | `SparkSession.builder()` → `SnowparkConnectSession.builder()`, drops `.master()`, `.enableHiveSupport()`, `.remote()`; emits `SCOS-RECIPE-PRESERVED-CONFIG: k=v` for every `.config(k, v)` call | rewrite + markers |
| `ScosCheckpointToCache` | `.checkpoint(false)` / `.checkpoint()` → `.cache()` | rewrite + `// SCOS: [SPRKCNTSCL1000]` comment |
| `ScosMapSubscriptToElementAt` | Java `col.getItem(key)` map subscript → `functions.element_at(col, key)` | rewrite |
| `ScosWildcardReadAnnotate` | wildcard/glob file-read path (`*` in a read path) | `// SCOS: TODO -` annotation |
| `ScosSaveAsTableDropStorageOpts` | drops storage-only options on `saveAsTable` (format, path, partitionBy) | rewrite + `// SCOS:` comment |
| `ScosExternalCloudReadAnnotate` | read from a cloud scheme (`s3://`, `gs://`, `wasbs://`, …) | `// SCOS: Performance tip -` annotation |
| `ScosSelfJoinUnaliasedAnnotate` | unaliased self-join `df.join(df, …)` | `// SCOS: TODO -` annotation |
| `ScosSparkContextPropertyFallbackAnnotate` | `new JavaSparkContext(...)`, `sc.parallelize(...)`, `sc.broadcast(...)` | `// SCOS: [SPRKCNTSCL…]` annotation |
| `ScosUdtfCompatibilityModeAnnotate` | class implementing `UserDefinedTableFunction` | `// SCOS: TODO -` annotation |
| `ScosUnionByNameAllowMissingAnnotate` | `unionByName(…, true)` (allowMissingColumns) | `// SCOS: TODO -` annotation |
| `ScosDriverHotPathAnnotate` | `collectAsList()` / `toLocalIterator()` inside a loop (enclosing-scope analysis) | `// SCOS: Performance tip -` annotation |
| `ScosTempViewMultiUseCache` | temp view referenced ≥2× in SQL strings and not already cached | `// SCOS: Performance tip -` comment + `recv.cache();` rewrite |

The exact comment/marker strings are defined in `ScosJavaRewrite.java`.

## `recipe_edits` contract

The driver merges per-file edits into the top-level `recipe_edits` block of
`migration_state.json`, keyed by relative path:

```json
"recipe_edits": {
  "<rel_path>.java": [
    {
      "recipe_id": "javaparser:<RuleName>",
      "src_line": <int>,
      "output_line_anchor": "javaparser:<RuleName>:<src_line>:<8-hex>"
    }
  ]
}
```

`recipe_id` is always in the `javaparser:<RuleName>` namespace (e.g.
`javaparser:ScosSparkSessionBuilderRewrite`). The analyzer (Phase 1) and fixer (Phase 2)
**MUST** read `recipe_edits` and treat those lines as already-handled.

## Adding a rule

1. Add a `JavaParser` `VoidVisitorAdapter` or `ModifierVisitorAdapter` subclass to
   `ScosJavaRewrite.java` that implements the transform using `LexicalPreservingPrinter`.
2. Register it in the rule registry inside `ScosJavaRewrite.main()`.
3. Add a golden-file fixture in `scripts/tests/fixtures/javaparser/` (input `.java` +
   expected output `.java`).
4. Add an integration test in `scripts/tests/test_preprocess_javaparser.py`.

## Idempotency

Every rule is idempotent: after a rewrite the result no longer matches the trigger
pattern, and annotations are guarded so re-running the driver on already-processed
files is a safe no-op.
