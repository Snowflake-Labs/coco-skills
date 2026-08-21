# SPRKSCL1105

org.apache.spark.sql.DataframeWriter.format

> This issue code has been **deprecated**

Message: Writer format value is not supported.

Category: Conversion error.

## Description

This issue appears when the [org.apache.spark.sql.DataFrameWriter.format](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/DataFrameWriter.html#format(source:String):org.apache.spark.sql.DataFrameWriter[T]) has an argument that is not supported by Snowpark.

## Scenarios

There are some scenarios depending on the type of format you are trying to save. It can be a `supported`, or `non-supported` format.

### Scenario 1

**Input**

The tool analyzes the type of format that is trying to save, the supported formats are:

- `csv`
- `json`
- `orc`
- `parquet`
- `text`

```scala
    dfWrite.write.format("csv").save(path)
```
**Output**

The tool transforms the `format` method into a `csv` method call when save function has one parameter.

```scala
    dfWrite.write.csv(path)
```

**Recommended fix**

In this case, the tool does not show the EWI, meaning there is no fix necessary.

### Scenario 2

**Input**

The below example shows how the tool transforms the `format` method when passing a `net.snowflake.spark.snowflake` value.

```scala
dfWrite.write.format("net.snowflake.spark.snowflake").save(path)
```

**Output**

The tool shows the EWI `SPRKSCL1105` indicating that the value `net.snowflake.spark.snowflake` is not supported.

```scala
/*EWI: SPRKSCL1105 => Writer format value is not supported .format("net.snowflake.spark.snowflake")*/
dfWrite.write.format("net.snowflake.spark.snowflake").save(path)
```

**Recommended fix**

For the `not supported` scenarios there is no specific fix since it depends on the files that are trying to be read.

### Scenario 3

**Input**

The below example shows how the tool transforms the `format` method when passing a `csv`, but using a variable instead.

```scala
val myFormat = "csv"
dfWrite.write.format(myFormat).save(path)
```

**Output**

Since the tool can not determine the value of the variable in runtime, shows the EWI `SPRKSCL1163` indicating that the value is not supported.

```scala
val myFormat = "csv"
/*EWI: SPRKSCL1163 => format_type is not a literal and can't be evaluated*/
dfWrite.write.format(myFormat).load(path)
```

**Recommended fix**

As a workaround, you can check the value of the variable and add it as a string to the `format` call.

## Additional recommendations

- The Snowpark location only accepts cloud locations using a [snowflake stage](https://docs.snowflake.com/en/user-guide/data-load-local-file-system-create-stage).

- The documentation of methods supported by Snowpark can be found in the [documentation](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/DataFrameWriter.html)

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
