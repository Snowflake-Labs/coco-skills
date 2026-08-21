# SCOS Java Migration — Troubleshooting

Common issues and resolutions for Java Spark → Snowpark Connect migrations.

---

## Phase 0.5c: JavaParser Pre-Processing

### Error: `JDK not found` or `mvn: command not found`

**Cause:** Phase 0.5c requires a JDK (11+) and Maven (or `./mvnw` wrapper).

**Fix:**
```bash
# Check JDK
java -version
# Check Maven
mvn -version
# If missing, install:
# macOS: brew install openjdk@11 maven
# Linux: apt-get install openjdk-11-jdk maven
```

If your project has a Maven wrapper, the driver will use `./mvnw` automatically.

### Error: `fat-jar build failed`

**Cause:** Maven could not download JavaParser dependencies (network issue or proxy).

**Fix:** Run the fat-jar build manually:
```bash
cd <SKILL_DIRECTORY>/scripts/javaparser_maven
mvn -q package -DskipTests
```
Then re-run `preprocess_javaparser.py`.

### Error: `JavaParser parse error on <file>.java`

**Cause:** A `.java` file uses preview features or a language level unsupported by the
pinñed JavaParser version (javaparser-symbol-solver-core 3.25.x supports Java 17 grammar).

**Fix:** The driver skips parse-failing files and logs them in `failures`. Inspect the
file manually and fix syntax issues before re-running.

---

## Phase 1: Analysis

### Error: `CORTEX_LLM_PREFLIGHT=FAIL`

Same as Scala: the configured Snowflake connection lacks `SNOWFLAKE.CORTEX.COMPLETE` access.
Contact your Snowflake admin to grant it before running Phase 1.

### Warning: `0 issues found but source contains Spark imports`

**Cause:** `analyze_java.py` may have fallen back to the regex path and the patterns
did not match. Try with `--require-ast-facts` to force the JavaParser facts path.

---

## Phase 2: Fixes

### Error: `high-risk issue(s) with no nearby // SCOS marker`

**Cause:** The fixer processed a file but left a high-risk issue (risk ≥ 0.7) without
an annotation within ±3 lines.

**Fix:** Re-dispatch the fixer on that file with the specific issue as feedback:
```
Fix the high-risk issue at <file>:<lines> — the analyzer found: <root_cause>.
Add a // SCOS: [SPRKCNTSCL1000] annotation or fix it.
```

### Error: `fixer dropped recipe preserve-config markers`

**Cause:** The LLM fixer collapsed the `SparkSession.builder()` chain and silently
dropped `SCOS-RECIPE-PRESERVED-CONFIG: spark.sql.session.timeZone=UTC` markers placed
by Phase 0.5c.

**Fix:** Re-dispatch the fixer on that file with the Phase 0.5c markers as explicit context:
```
Do NOT modify any line containing SCOS-RECIPE-PRESERVED-CONFIG or
SCOS-RECIPE-INSERT-AFTER-BUILDER. These are AST-managed by Phase 0.5c javaparser.
```

---

## Phase 2b: Compilation Gate

### Error: `unbalanced braces (net +1)` from verify_phase

**Cause:** The fixer introduced a syntax error (missing closing brace) in a `.java` file.

**Fix:** The file is flagged by the Phase 2b tokenizer check. Re-dispatch the fixer on
that file with the compiler error as feedback. If `javac` is available:
```bash
javac -proc:none -d /tmp/scos_check <file>.java
```

### Error: `missing snowpark-connect-java-client`

**Cause:** The `pom.xml` or `build.gradle` does not yet have the SCOS client dependency.

**Fix:** Re-run `update_imports_java.py`:
```bash
uv run --project <SKILL_DIRECTORY> \
  python <SKILL_DIRECTORY>/scripts/update_imports_java.py \
  --state <CONVERSION>/migration_state.json
```

---

## Phase 3: Imports, Session, Build

### Error: `no non-test file initializes SnowparkConnectSession`

**Cause:** `update_imports_java.py` could not identify the entry-point class (no `main()`
or `@SpringBootApplication` found, or the file is nested).

**Fix:** Manually add the session init to your entry point, then re-run the verifier.

### Error: `PIN_CONCRETE_VERSION` in pom.xml

**Cause:** Maven has no safe dynamic-version keyword. `update_imports_java.py` left a
`PIN_CONCRETE_VERSION` sentinel because the exact `snowpark-connect-java-client` version
was unknown at transform time.

**Fix:** Replace `PIN_CONCRETE_VERSION` with a concrete version (e.g. `1.0.0`):
```xml
<version>1.0.0</version>
```
Then re-run `verify_phase.py --phase 3`.

---

## General

### `import org.apache.hadoop.*` still present

**Cause:** Phase 3 import removal missed a Hadoop import.

**Fix:** Remove the import and replace the file operation with a Snowflake stage/table
operation. See `references/fix-rules.md` Rule 16.

### `spark.sql("USE DATABASE …")` has no effect

Same as Scala. Use `SnowflakeSession` instead:
```java
import com.snowflake.snowpark_connect.client.SnowflakeSession;
SnowflakeSession sf = new SnowflakeSession(spark);
sf.useDatabase("mydb");
sf.useSchema("myschema");
```
