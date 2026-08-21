# SPRKPY1085

pyspark.ml.feature.VectorAssembler

Message: pyspark.ml.feature.VectorAssembler is not supported.

Category: Warning

## Description

The [pyspark.ml.feature.VectorAssembler](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.ml.feature.VectorAssembler.html) is not supported.

## Scenario

**Input**

VectorAssembler is used to combine several columns into a single vector.

```python
data = [
        (1, 10.0, 20.0),
        (2, 25.0, 30.0),
        (3, 50.0, 60.0)
    ]

df = SparkSession.createDataFrame(data, schema=["Id", "col1", "col2"])
vector = VectorAssembler(inputCols=["col1", "col2"], output="cols")
```

**Output**

The tool adds this EWI `SPRKPY1085` on the output code to let you know that this class is not supported by Snowpark.

```python
data = [
        (1, 10.0, 20.0),
        (2, 25.0, 30.0),
        (3, 50.0, 60.0)
    ]

df = spark.createDataFrame(data, schema=["Id", "col1", "col2"])
#EWI: SPRKPY1085 => The pyspark.ml.feature.VectorAssembler function is not supported.

vector = VectorAssembler(inputCols=["col1", "col2"], output="cols")
```

**Recommended fix**

The pyspark.ml.feature.VectorAssembler does not have a recommended fix.


## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
