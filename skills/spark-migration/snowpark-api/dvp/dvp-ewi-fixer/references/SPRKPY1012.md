# SPRKPY1012

pyspark.sql.dataframe.DataFrameStatFunctions.writeTo

> This Issue Code has been **deprecated** because the element pyspark.sql.dataframe.DataFrameStatFunctions.writeTo is not valid

Message: pyspark.sql.dataframe.DataFrameStatFunctions.writeTo has a workaround

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.sql.dataframe.DataFrameStatFunctions.writeTo](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrame.writeTo.html) which has a workaround.

## Scenario

**Input**

For this example the dataframe df is writed to a Spark table "table".

```python
writer = df.writeTo("table")
```

**Output**

SMA returns the EWI SPRKPY1012 over the line where DataFrameStatFunctions.writeTo is used, so you can use to identify where to fix.

```python
#EWI: SPRKPY1012 => pyspark.sql.dataframe.DataFrameStatFunctions.writeTo has a workaround, see documentation for more info
writer = df.writeTo("table")
```

**Recommended fix**

Use df.write.SaveAsTable() instead.

```python
import df.write as wt
writer = df.write.save_as_table(table)
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
