# SPRKPY1068

pyspark.sql.DataFrame.toPandas

Message: pyspark.sql.DataFrame.toPandas is not supported if there are columns of type ArrayType, but it has a workaround.

Category: Warning

## Description

This issue appears when the tool detects the usage of [pyspark.sql.DataFrame.toPandas](https://spark.apache.org/docs/3.5.3/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrame.toPandas.html), a mapping that is not supported if there are columns of type ArrayType.

## Scenario

**Input**

`pyspark.sql.DataFrame.toPandas` returns the data of the original DataFrame as a Pandas DataFrame.

```python
sparkDF = spark.createDataFrame([
Row(a=1, b=2., c='string1', d=date(2000, 1, 1), e=datetime(2000, 1, 1, 12, 0)),
Row(a=2, b=3., c='string2', d=date(2000, 2, 1), e=datetime(2000, 1, 2, 12, 0))
])

pandasDF = sparkDF.toPandas()
```

**Output**

The tool adds this EWI to let you know that `toPandas` is not supported if there are columns of type ArrayType, but it has a workaround.

```python
sparkDF = spark.createDataFrame([
Row(a=1, b=2., c='string1', d=date(2000, 1, 1), e=datetime(2000, 1, 1, 12, 0)),
Row(a=2, b=3., c='string2', d=date(2000, 2, 1), e=datetime(2000, 1, 2, 12, 0))
])
#EWI: SPRKPY1068 => pyspark.sql.DataFrame.toPandas is not supported if there are columns of type ArrayType, but it has a workaround. See documentation for more info.
pandasDF = sparkDF.toPandas()
```

**Recommended fix**

The workaround for these cases is converting those columns into a Python Dictionary by using `json.loads` method.

The cycle will check and convert all resulting fields from calling toPandas when they are of type ArrayType, they will be reassigned by converting them into a Python Dictionary using the `json.loads` method.
```python
pandas_df = sparkDF.toPandas()

for field in pandas_df.schema.fields:
  if isinstance(field.datatype, ArrayType):
    pandas_df[field.name] = pandas_df[field.name].apply(lambda x: json.loads(x) if x is not None else x)
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
