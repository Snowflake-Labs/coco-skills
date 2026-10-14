# SPRKPY1086

pyspark.ml.linalg.VectorUDT

Message: pyspark.ml.linalg.VectorUDT is not supported.

Category: Warning

## Description

The [pyspark.ml.linalg.VectorUDT](https://spark.apache.org/docs/latest/api/python/_modules/pyspark/ml/linalg.html) is not supported.

## Scenario

**Input**

VectorUDT is a data type to represent vector columns in a DataFrame.

```python
data = [
        (1, Vectors.dense([10.0, 20.0])),
        (2, Vectors.dense([25.0, 30.0])),
        (3, Vectors.dense([50.0, 60.0]))
    ]

schema = StructType([
        StructField("Id", IntegerType(), True),
        StructField("VectorCol", VectorUDT(), True),
    ])

df = SparkSession.createDataFrame(data, schema=schema)
```

**Output**

The tool adds this EWI `SPRKPY1086` on the output code to let you know that this function is not supported by Snowpark.

```python
data = [
        (1, Vectors.dense([10.0, 20.0])),
        (2, Vectors.dense([25.0, 30.0])),
        (3, Vectors.dense([50.0, 60.0]))
    ]

#EWI: SPRKPY1086 => The pyspark.ml.linalg.VectorUDT function is not supported.
schema = StructType([
        StructField("Id", IntegerType(), True),
        StructField("VectorCol", VectorUDT(), True),
    ])

df = spark.createDataFrame(data, schema=schema)
```

**Recommended fix**

The pyspark.ml.linalg.VectorUDT does not have a recommended fix.


## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
