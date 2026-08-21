# SPRKPY1056

pyspark.sql.readwriter.DataFrameReader.option

> This issue code has been **deprecated**

Message: pyspark.sql.readwriter.DataFrameReader.option argument ***<argument_name>*** is not a literal and can't be evaluated

Category: Warning

## Description

This issue appears when the argument's key or value of the [pyspark.sql.readwriter.DataFrameReader.option](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameReader.option.html) function is not a literal value (for example a variable). The SMA does a static analysis of your source code, and therefore it is not possible to evaluate the content of the argument.

## Scenario

**Input**

Below is an example of a use of the `pyspark.sql.readwriter.DataFrameReader.option` function that generates this EWI.

```python
my_value = ...
my_option = ...

df1 = spark.read.option("dateFormat", my_value).format("csv").load('filename.csv')
df2 = spark.read.option(my_option, "false").format("csv").load('filename.csv')
```

**Output**

The SMA adds the EWI `SPRKPY1056` to the output code to let you know that the argument of this function is not a literal value, and therefore it could not be evaluated by the SMA.

```python
my_value = ...
my_option = ...

#EWI: SPRKPY1056 => pyspark.sql.readwriter.DataFrameReader.option argument "dateFormat" is not a literal and can't be evaluated
df1 = spark.read.option("dateFormat", my_value).format("csv").load('filename.csv')
#EWI: SPRKPY1056 => pyspark.sql.readwriter.DataFrameReader.option argument key is not a literal and can't be evaluated
df2 = spark.read.option(my_option, "false").format("csv").load('filename.csv')
```

**Recommended fix**

Even though the SMA was unable to evaluate the argument, it does not mean that it is not supported by Snowpark. Please make sure that the value of the argument is valid and equivalent in Snowpark by checking the [documentation](https://docs.snowflake.com/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.DataFrameReader.option).

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
