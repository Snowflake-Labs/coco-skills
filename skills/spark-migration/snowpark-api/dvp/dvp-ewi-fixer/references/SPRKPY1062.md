# SPRKPY1062

pyspark.sql.group.GroupedData.pivot

> This issue code has been **deprecated**.
> 
> The `pyspark.sql.group.GroupedData.pivot` function is now fully supported by Snowpark. If you are still encountering this EWI, you are likely using an older version of the SMA. We recommend updating to the latest version to take advantage of this improved support and eliminate the warning.

Message: Snowpark does not support GroupedData.pivot without parameter "values".

Category: Warning

## Description

This issue appears when the SMA detects the usage of the [pyspark.sql.group.GroupedData.pivot](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.GroupedData.pivot.html) function without the "values" parameter *(the list of values to pivot on)*.

At the moment, the Snowpark Python pivot function requires you to explicitly specify the list of distinct values to pivot on.

## Scenarios

### Scenario 1

**Input**

The SMA detects an expression that matches the pattern `dataFrame.groupBy("columnX").pivot("columnY")`
and the pivot does not have the **values** parameter.

```python
df.groupBy("date").pivot("category").sum("amount")
```

**Output**

The SMA adds an EWI message indicating that the pivot function without the "values" parameter is not supported.

In addition, it will add as a second parameter of the pivot function a list comprehension that calculates the list of values that will be translated into columns.
Keep in mind that this operation is not efficient for large datasets, and it is advisable to indicate the values explicitly.

```python
#EWI: SPRKPY1062 => pyspark.sql.group.GroupedData.pivot without parameter 'values' is not supported. See documentation for more info.
df.groupBy("date").pivot("category", [v[0] for v in df.select("category").distinct().limit(10000).collect()]]).sum("amount")
```

**Recommended fix**

For this scenario the SMA add a second parameter of the pivot function a list comprehension that calculates the list of
values that will be translated into columns, but you can a list of distinct values to pivot on, as follows:

```python
df = spark.createDataFrame([
      Row(category="Client_ID", date=2012, amount=10000),
      Row(category="Client_name",   date=2012, amount=20000)
  ])

df.groupBy("date").pivot("category", ["Client_ID", "Client_name"]).sum("amount")
```

### Scenario 2

**Input**

The SMA couldn't detect an expression that matches the pattern `dataFrame.groupBy("columnX").pivot("columnY")` and the pivot does not have the **values** parameter.

```python
df1.union(df2).groupBy("date").pivot("category").sum("amount")
```

**Output**

The SMA adds an EWI message indicating that the pivot function without the "values" parameter is not supported.

```python
#EWI: SPRKPY1062 => pyspark.sql.group.GroupedData.pivot without parameter 'values' is not supported. See documentation for more info.
df1.union(df2).groupBy("date").pivot("category").sum("amount")
```

**Recommended fix**

Add a list of distinct values to pivot on, as follows:

```python
df = spark.createDataFrame([
      Row(course="dotNET", year=2012, earnings=10000),
      Row(course="Java",   year=2012, earnings=20000)
  ])

df.groupBy("year").pivot("course", ["dotNET", "Java"]).sum("earnings").show()
```

## Additional recommendations

- Calculating the list of distinct values to pivot on is not an efficient operation on large datasets and could become a blocking call. Please consider indicating the list of distinct values to pivot on explicitly.

- If you don't want to specify the list of distinct values to pivot on explicitly (not advisable), you can add the following code as the second argument of the pivot function to infer the values at runtime*

```python
[v[0] for v in <df>.select(<column>).distinct().limit(<count>).collect()]]
```

****Replace** `<df>` with the corresponding DataFrame, <column> with the column to pivot and <count> with the number of rows to select.*

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
