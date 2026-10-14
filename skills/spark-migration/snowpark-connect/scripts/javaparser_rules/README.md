# javaparser_rules — Java AST Rules for SCOS Phase 0.5c

Analog of `scalafix_rules/SCOSRules.scala` for Java Spark source files.

---

## Frozen JSON Facts Contract

`ScosJavaFacts` emits the **exact same JSON shape** as `ScosMigrateFacts.scala` so
`analyze_java.py`'s fact-consumption code works unchanged.

### Top-level

```json
{
  "source":       "<path-passed-to-–-source>",
  "file_count":   42,
  "parse_errors": 0,
  "files":        [ /* per-file objects */ ]
}
```

### Per-file (parse success)

```json
{
  "path":       "/abs/path/to/Foo.java",
  "parse_ok":   true,
  "imports":    [ { "ref": "org.apache.spark.sql.SparkSession", "line": 3 } ],
  "calls":      [ { "method": "getOrCreate", "recv_leaf": "builder",
                    "recv": "SparkSession.builder()", "args": [],
                    "arg_exprs": [], "line": 12 } ],
  "selects":    [ { "member": "sparkContext", "recv_leaf": "spark", "line": 20 } ],
  "new_types":  [ { "type": "SparkContext", "line": 8 } ],
  "spark_sql":  [ { "text": "SELECT * FROM events", "line": 30 } ],
  "infix":      [ { "op": "+", "lhs": "a", "rhs": "b", "line": 5 } ],
  "interpolations": [],
  "session_created": true
}
```

### Per-file (parse failure)

```json
{
  "path":     "/abs/path/to/Bad.java",
  "parse_ok": false,
  "error":    "Encountered unexpected token... (line 7)"
}
```

### Field semantics

| Field | Type | Notes |
|---|---|---|
| `imports[].ref` | string | Fully-qualified name; `.*` suffix for wildcard imports |
| `calls[].recv_leaf` | string | Rightmost identifier in the receiver chain |
| `calls[].recv` | string | Whitespace-collapsed receiver syntax, max 80 chars (tail) |
| `calls[].args` | string[] | String-literal arguments only |
| `calls[].arg_exprs` | string[] | All argument expressions, max 80 chars each (head) |
| `selects[].member` | string | Field or property name |
| `infix[].lhs` | string | Left operand, max 80 chars (tail) |
| `infix[].rhs` | string | Right operand, max 80 chars (head) |
| `interpolations` | array | **Always empty** — Java has no string interpolation |
| `session_created` | bool | `true` if `SparkSession.builder()…getOrCreate()` detected |

---

## Rewrite Rule List

Matches `scos.scalafix.conf` exactly (14 rules):

| Rule | Type | Marker |
|---|---|---|
| `ScosSparkSessionBuilderRewrite` | rewrite | `SCOS-RECIPE-PRESERVED-CONFIG: k=v` |
| `ScosCheckpointToCache` | rewrite | `// SCOS: [SPRKCNTSCL1500] …` |
| `ScosMapSubscriptToElementAt` | rewrite | _(inline replacement)_ |
| `ScosWildcardReadAnnotate` | annotate | `// SCOS: TODO - wildcard pattern…` |
| `ScosSaveAsTableDropStorageOpts` | rewrite | `// SCOS: dropped unsupported…` |
| `ScosExternalCloudReadAnnotate` | annotate | `// SCOS: Performance tip - <scheme> read…` |
| `ScosSelfJoinUnaliasedAnnotate` | annotate | `// SCOS: TODO - self-join requires…` |
| `ScosSparkContextPropertyFallbackAnnotate` | annotate | `// SCOS: [SPRKCNTSCL1500] sc.parallelize…` |
| `ScosUdtfCompatibilityModeAnnotate` | annotate | `// SCOS: TODO - UDTF compatibility mode…` |
| `ScosUnionByNameAllowMissingAnnotate` | annotate | `// SCOS: TODO - schema-align…` |
| `ScosDriverHotPathAnnotate` | annotate | `// SCOS: Performance tip - driver materialization…` |
| `ScosTempViewMultiUseCache` | rewrite (insert) | _(inserts `recv.cache();`)_ |
| `ScosSystemGetenvRewrite` | rewrite | _(inline `System.getenv` → `System.getProperty`)_ |
| `ScosDeltaTableAnnotate` | annotate | `// SCOS: TODO - [SPRKCNTSCL1000] DeltaTable API…` |

### Marker conventions

```
// SCOS-RECIPE-PRESERVED-CONFIG: k=v        SparkSession .config() preserved for Phase 3
// SCOS-WARN: <message>                      Non-extractable config dropped — needs review
// SCOS: [SPRKCNTSCL<code>] <message>        EWI-style error/warning (SPRKCNTSCL family)
// SCOS: TODO - <message>                    Action required before migration completes
// SCOS: Performance tip - <message>         Perf hint (no blocking action required)
```

---

## Build & Run

```bash
# Build fat-jar (run once; subsequent runs skip if jar is fresh)
mvn -q -f scripts/javaparser_maven/pom.xml package

JAR=scripts/javaparser_maven/target/scos-javaparser-runner.jar

# Facts extraction
java -jar $JAR facts --source path/to/spark-project/ --output /tmp/facts.json

# Rewrite — apply one rule, print to stdout
java -jar $JAR rewrite --source path/to/Foo.java --rule ScosCheckpointToCache --stdout

# List all rule names
java -jar $JAR rewrite --list-rules
```

---

## Source layout

```
javaparser_rules/
  README.md                                    this file
  com/snowflake/scos/javaparser/
    ScosJavaRunner.java                        fat-jar entry-point / subcommand dispatcher
    ScosJavaFacts.java                         facts extractor (JSON output)
    ScosJavaRewrite.java                       14 rewrite/annotate rules
  _smoke/
    SparkAppExample.java                       smoke-test fixture (excluded from production jar)
```
