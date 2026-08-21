# SPRKPY1063

pyspark.sql.pandas.functions.pandas_udf

Message: pyspark.sql.pandas.functions.pandas_udf has workaround.

Category: Warning

## Description

This issue appears when the tool detects the usage of [pyspark.sql.pandas.functions.pandas_udf](https://spark.apache.org/docs/3.1.2/api/python/reference/api/pyspark.sql.functions.pandas_udf.html) which has a workaround.

## Scenario

**Input**

The pandas_udf function is used to create a user defined functions that works with large amounts of data.

```python
@pandas_udf(schema, PandasUDFType.GROUPED_MAP)
def modify_df(pdf):
    return pd.DataFrame({'result': pdf['col1'] + pdf['col2'] + 1})
df = spark.createDataFrame([(1, 2), (3, 4), (1, 1)], ["col1", "col2"])
new_df = df.groupby().apply(modify_df)
```

**Output**

The SMA adds an EWI message indicating that the pandas_udf has a workaround.

```python
#EWI: SPRKPY1062 => pyspark.sql.pandas.functions.pandas_udf has a workaround, see documentation for more info
@pandas_udf(schema, PandasUDFType.GROUPED_MAP)

def modify_df(pdf):
    return pd.DataFrame({'result': pdf['col1'] + pdf['col2'] + 1})

df = spark.createDataFrame([(1, 2), (3, 4), (1, 1)], ["col1", "col2"])

new_df = df.groupby().apply(modify_df)
```

**Recommended fix**

Specify explicitly the parameters types as a new parameter `input_types`, and remove `functionType` parameter if applies. Created function must be called inside a select statement.

```python
@pandas_udf(
    return_type = schema,
    input_types = [PandasDataFrameType([IntegerType(), IntegerType()])]
)

def modify_df(pdf):
    return pd.DataFrame({'result': pdf['col1'] + pdf['col2'] + 1})

df = spark.createDataFrame([(1, 2), (3, 4), (1, 1)], ["col1", "col2"])

new_df = df.groupby().apply(modify_df) # You must modify function call to be a select and not an apply
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
