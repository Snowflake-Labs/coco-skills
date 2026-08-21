# harness-java — Java AST Analyzer for SCOS Validation

The `control/` directory contains a JavaParser-based AST facts extractor that
mirrors the Scala `ScosAnalyze.scala` contract for Java source files.

## Build

Requires JDK 11+ and Maven 3.6+.

```bash
cd control
mvn package -q
```

Produces: `control/target/scos-analyze-java.jar` (self-contained fat-jar via
maven-shade-plugin).

## Usage

```bash
java -jar control/target/scos-analyze-java.jar analyze --source <file-or-dir> [--output <path>]
```

- `--source`: path to a single `.java` file or a directory (recursively scanned).
- `--output`: optional path to write JSON output (otherwise prints to stdout).

Exit code is always 0; per-file `parse_ok` flags indicate parse failures.

## Output Contract

The JSON output matches the Scala analyzer (`scos-analyze.jar`) schema:

```json
{
  "source": "<input path>",
  "file_count": 3,
  "parse_errors": 0,
  "files": [
    {
      "path": "/abs/path/MyJob.java",
      "parse_ok": true,
      "classes": ["MyJob"],
      "entrypoints": [{"owner": "MyJob", "method": "main"}],
      "imports": ["import org.apache.spark.sql.SparkSession;"],
      "spark_session_created": true,
      "reads": [{"call": "parquet", "args": ["/data/input"]}],
      "writes": [{"call": "saveAsTable", "args": ["output_table"]}],
      "write_helpers": ["loadData"],
      "table_refs": ["output_table"],
      "column_refs": ["id", "name"]
    }
  ]
}
```

### Per-file fields

| Field | Description |
|-------|-------------|
| `path` | Absolute path of the analyzed file |
| `parse_ok` | `true` if JavaParser parsed successfully |
| `error` | Present only when `parse_ok` is false |
| `classes` | All class/interface declarations |
| `entrypoints` | Classes with `public static void main(String[])` or `run` method |
| `imports` | Import statements |
| `spark_session_created` | Whether `SparkSession...getOrCreate()` was found |
| `reads` | Spark read calls (parquet/csv/json/orc/text/load/table/jdbc) |
| `writes` | Spark write calls (save/saveAsTable/insertInto + format terminals) |
| `write_helpers` | Methods whose body contains write operations (direct + transitive) |
| `table_refs` | Table name arguments from table/saveAsTable/insertInto |
| `column_refs` | Column names from col()/column()/functions.col() + select/groupBy/orderBy/drop/dropDuplicates string args |

## Relationship to the Validation Kit

The validation kit (`../validate-spark-scala-to-snowpark-connect/harness-scala/kit/`)
is reused unchanged for Java workloads. The kit's `ReflectionEntrypoint.load()`
handles Java class names + static `main` identically to Scala objects. This
analyzer (`scos-analyze-java.jar`) is the only Java-specific JVM artifact in the
validation stack.
