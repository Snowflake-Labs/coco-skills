# SPRKPY1041

pyspark.sql.functions.explode_outer

> This issue code has been **deprecated** since [Spark Conversion Core Version 2.9.0](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-2.9.0)

Message: pyspark.sql.functions.explode_outer has a workaround

Category: Warning

## Description

This issue appears when the tool detects the usage of [pyspark.sql.functions.explode_outer](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.explode_outer.html) which has a workaround.

## Scenario

**Input**

The example shows the use of the method `explode_outer` in a select call.

```python
df = spark.createDataFrame(
    [(1, ["foo", "bar"], {"x": 1.0}),
     (2, [], {}),
     (3, None, None)],
    ("id", "an_array", "a_map")
)

df.select("id", "an_array", explode_outer("a_map")).show()
```

**Output**

The tool adds the EWI `SPRKPY1041` indicating that a workaround can be implemented.

```python
df = spark.createDataFrame(
    [(1, ["foo", "bar"], {"x": 1.0}),
     (2, [], {}),
     (3, None, None)],
    ("id", "an_array", "a_map")
)

#EWI: SPRKPY1041 => pyspark.sql.functions.explode_outer has a workaround, see documentation for more info
df.select("id", "an_array", explode_outer("a_map")).show()
```

**Recommended fix**

As a workaround, you can import the snowpark_extensions package, which contains a helper for the `explode_outer` function.

```python
import snowpark_extensions

df = spark.createDataFrame(
    [(1, ["foo", "bar"], {"x": 1.0}),
     (2, [], {}),
     (3, None, None)],
    ("id", "an_array", "a_map")
)

df.select("id", "an_array", explode_outer("a_map")).show()
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
