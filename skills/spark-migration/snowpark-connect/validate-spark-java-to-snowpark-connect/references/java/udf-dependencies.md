# Java UDF / Lambda Serialization for Snowpark Connect

> See also `references/java/udf-dependencies.md` (the migrate skill's copy) for
> the broader set of dependency-upload options (`REPLClassDirMonitor`,
> `spark.addArtifact`, staged JAR imports, self-contained UDFs, broadcast-variable
> refactoring). This doc focuses specifically on diagnosing and fixing
> `KryoException` / `NotSerializableException` failures surfaced during Phase B
> validation trials.

## Why `KryoException` / `NotSerializableException` occurs

Spark serializes UDF closures and lambda bodies across the network. A Java UDF
(`functions.udf(...)` wrapping a lambda or anonymous class) fails if the enclosing
class or any captured field is not `java.io.Serializable`. Under Snowpark Connect
the serialization path is identical to OSS Spark — the same closure rules apply.

## Staged-JAR strategy (preferred)

Snowpark Connect can load user code from a Snowflake stage. Compile the UDF class
into a separate JAR, upload it, and register it by name so the closure captures
only a `String` (the stage-relative path):

```java
// Upload once:
//   PUT file:///path/to/udfs.jar @MY_STAGE AUTO_COMPRESS=FALSE;

// Register from stage:
session.addArtifact("@MY_STAGE/udfs.jar");
UserDefinedFunction myUdf = functions.udf(
    (String s) -> s.toUpperCase(),
    DataTypes.StringType
);
```

The lambda is a simple `Serializable` instance — no captured non-serializable
fields. The `addArtifact` call replaces the `REPLClassDirMonitor` / `spark.addArtifact`
pattern used in the Scala equivalent.

## Lambda serialization strategy (minimal change)

If refactoring to a staged JAR is too disruptive, make the UDF lambda
explicitly `Serializable` by casting it:

```java
import org.apache.spark.sql.api.java.UDF1;

UDF1<String, String> myUdf = (UDF1<String, String> & java.io.Serializable)
    s -> s.toUpperCase();

spark.udf().register("MY_UDF", myUdf, DataTypes.StringType);
```

`UDF1`–`UDF22` in `org.apache.spark.sql.api.java` all extend `Serializable`.

## Anonymous-class UDFs

If the UDF is an anonymous class, ensure:
1. The enclosing class is `Serializable` (or the UDF is `static`).
2. No captured fields reference non-serializable singletons (database
   connections, loggers with non-serializable appenders, etc.).

Mark problematic captured fields `transient` and reinitialize in `readResolve()`:

```java
private transient Connection conn;

protected Object readResolve() {
    this.conn = openConnection();
    return this;
}
```

## Verification

After applying the fix, run the affected Phase B trial and confirm the
`KryoException` / `NotSerializableException` no longer appears in the SCOS
server log before dispatching further fixer iterations.
