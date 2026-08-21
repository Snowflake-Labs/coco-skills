# SPRKPY1084

pyspark.sql.readwriter.DataFrameWriter.option

> This issue code has been **deprecated** since [Spark Conversion Core 4.12.0](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-4.12.0)

Message: pyspark.sql.readwriter.DataFrameWriter.option is not supported.

Category: Warning

## Description

The [pyspark.sql.readwriter.DataFrameWriter.option](https://spark.apache.org/docs/3.5.3/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameWriter.option.html) function is not supported.

## Scenario

**Input**

Below is an example using the `option` method, this method is used to add additional configurations when writing the data of a DataFrame.

```python
path_csv_file = "/path/to/file.csv"
data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]

df = spark.createDataFrame(data, schema=["Name", "Age", "City"])

df.write.option("header", True).csv(csv_file_path)
df.write.option("sep", ";").option("lineSep","-").csv(csv_file_path)
```

**Output**

The tool adds this EWI `SPRKPY1084` on the output code to let you know that this function is not supported by Snowpark.

```python
path_csv_file = "/path/to/file.csv"
data = [
        ("John", 30, "New York"),
        ("Jane", 25, "San Francisco")
    ]

df = spark.createDataFrame(data, schema=["Name", "Age", "City"])

#EWI: SPRKPY1084 => The pyspark.sql.readwriter.DataFrameWriter.option function is not supported.

df.write.option("header", True).csv(csv_file_path)
#EWI: SPRKPY1084 => The pyspark.sql.readwriter.DataFrameWriter.option function is not supported.
df.write.option("sep", ";").option("lineSep","-").csv(csv_file_path)
```

**Recommended fix**

The pyspark.sql.readwriter.DataFrameWriter.option method does not have a recommended fix.


## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
