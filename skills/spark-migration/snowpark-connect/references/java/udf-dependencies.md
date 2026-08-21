# UDF Dependencies in SCOS — Java

> See also `validate-spark-java-to-snowpark-connect/references/java/udf-dependencies.md`
> for diagnosing/fixing `KryoException` / `NotSerializableException` failures
> surfaced during Phase B validation trials (staged-JAR strategy, lambda
> `Serializable` casting, `readResolve()` patterns for non-serializable captured
> fields).

When SCOS executes Java UDFs, the function closure is serialized and sent to Snowflake's
server-side worker. Java UDFs rely on Java serialization and Spark Connect's class upload
mechanism, same as Scala UDFs.

Two types of errors can occur:
1. **ClassNotFoundException** — The UDF references classes not available on the server
2. **JAR dependency not available** — The UDF imports third-party libraries

---

## Quick Reference

| Config Key / Method | Purpose |
|---|---|
| `spark.registerClassFinder(classFinder)` | Monitor and auto-upload compiled classes |
| `spark.addArtifact("path/to/jar")` | Upload JAR dependencies to the server |
| `snowpark.connect.udf.java.imports` | Stage-based JAR imports: `[@stage/dep.jar]` |

---

## Option 1 (Recommended): REPLClassDirMonitor

```java
import com.snowflake.snowpark_connect.client.SnowparkConnectSession;
import org.apache.spark.sql.connect.client.REPLClassDirMonitor;

SnowparkConnectSession spark = SnowparkConnectSession.builder().appName("MyApp").getOrCreate();
REPLClassDirMonitor classFinder = new REPLClassDirMonitor("/absolute/path/to/target/classes");
spark.registerClassFinder(classFinder);
```

Use during development when UDFs reference multiple classes in your project.

## Option 2: Upload JAR Artifacts

```java
spark.addArtifact("/absolute/path/to/my-app.jar");
spark.addArtifact("/path/to/dependency.jar");
```

Use for production deployments with packaged JARs.

## Option 3: Staged JARs

```java
spark.conf().set("snowpark.connect.udf.java.imports",
    "[@mystage/dependency.jar, @db.schema.stage/other_dependency.jar]");
```

Use when JARs are already in Snowflake stages.

---

## Self-Contained UDFs (Preferred)

For simple UDFs, keep all logic inside the lambda to avoid class upload:

```java
import org.apache.spark.sql.api.java.UDF1;
import org.apache.spark.sql.types.DataTypes;

UDF1<String, String> normalize = value -> (value == null) ? "" : value.trim().toLowerCase();
spark.udf().register("normalize", normalize, DataTypes.StringType);

df.select(functions.callUDF("normalize", functions.col("name")).alias("normalized_name"));
```

---

## Broadcast Variables

Java Spark code often uses `JavaSparkContext.broadcast()`. In Spark Connect there is
no client-side SparkContext, so broadcasts must be refactored:

```java
// BEFORE (fails — no JavaSparkContext):
// Broadcast<Map<String,Integer>> bc = jsc.broadcast(myMap);
// UDF1<String,Integer> udf = key -> bc.value().get(key);

// AFTER: capture data directly in the lambda
final Map<String, Integer> localMap = myMap;
UDF1<String, Integer> udf = key -> localMap.getOrDefault(key, 0);
```

---

## Key Differences from PySpark UDFs

| Aspect | PySpark | Java |
|--------|---------|------|
| Serialization | cloudpickle | Java serialization |
| Custom code upload | `snowpark.connect.udf.python.imports` | `REPLClassDirMonitor` / `addArtifact` |
| Package management | `snowpark.connect.udf.packages` | JAR-based |
| StructType in UDF | Converts to `dict` | Converts to `Row`/`Map` |
