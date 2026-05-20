# RDD to DataFrame Conversion Reference

RDD operations are **not supported** in SCOS. Convert them to DataFrame equivalents using native functions over UDFs when possible.

## Quick Reference Table

| RDD Operation | DataFrame Equivalent |
|---------------|---------------------|
| `sc.parallelize(data)` | `spark.createDataFrame(data, schema)` |
| `sc.textFile(path)` | `spark.read.text(path)` |
| `df.rdd` | Remove - use DataFrame directly |
| `rdd.map(f)` | `df.select(...)` or `df.withColumn(...)` |
| `rdd.flatMap(f)` | `df.select(explode(...))` |
| `rdd.filter(f)` | `df.filter(condition)` |
| `rdd.mapValues(f)` | `df.withColumn("value", expr)` |
| `rdd.reduce(f)` | `df.agg(sum/max/min(...))` |
| `rdd.reduceByKey(f)` | `df.groupBy("key").agg(...)` |
| `rdd.groupByKey()` | `df.groupBy("key").agg(collect_list(...))` |
| `rdd.sortByKey()` | `df.orderBy("key")` |
| `rdd.join(other)` | `df.join(other_df, "key")` |
| `rdd.union/distinct/collect/count` | Direct DataFrame equivalents |
| `rdd.repartition/coalesce` | Remove (no-op in SCOS) |

## Key Examples

**Word Count (flatMap + reduceByKey):**
```python
# BEFORE: text_rdd.flatMap(lambda x: x.split(" ")).map(lambda w: (w,1)).reduceByKey(lambda a,b: a+b)
# AFTER:
df.select(explode(split(col("value"), " ")).alias("word")).groupBy("word").agg(count("*"))
```

**GroupByKey:**
```python
# BEFORE: rdd.groupByKey()
# AFTER:
df.groupBy("key").agg(collect_list("value").alias("values"))
```

**Map with arithmetic:**
```python
# BEFORE: rdd.map(lambda x: x * 2)
# AFTER:
df.select((col("value") * 2).alias("value"))
```

**Filter:**
```python
# BEFORE: rdd.filter(lambda x: x > 10)
# AFTER:
df.filter(col("value") > 10)
```

**Join:**
```python
# BEFORE: rdd1.join(rdd2)
# AFTER:
df1.join(df2, "key")  # or df1.join(df2, "key", "left") for leftOuterJoin
```

**UDF Fallback** (only when native functions won't work):
```python
from pyspark.sql.functions import udf
from pyspark.sql.types import StringType

@udf(StringType())
def complex_transform(val):
    return val.upper() + "_processed"

df.select(complex_transform(col("name")).alias("result"))
```
