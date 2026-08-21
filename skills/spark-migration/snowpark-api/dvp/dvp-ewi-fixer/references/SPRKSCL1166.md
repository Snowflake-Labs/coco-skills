# SPRKSCL1166

org.apache.spark.sql.DataFrameReader.format.

> This issue code has been **deprecated**

Message: org.apache.spark.sql.DataFrameReader.format is not supported.

Category: Warning.

## Description

This issue appears when the [org.apache.spark.sql.DataFrameReader.format](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/DataFrameReader.html#format(source:String):org.apache.spark.sql.DataFrameReader) has an argument that is not supported by Snowpark.

## Scenarios

There are some scenarios depending on the type of format you are trying to load. It can be a `supported`, or `non-supported` format.

### Scenario 1

**Input**

The tool analyzes the type of format that is trying to load, the supported formats are:

- `csv`
- `json`
- `orc`
- `parquet`
- `text`

The below example shows how the tool transforms the `format` method when passing a `csv` value.

```scala
spark.read.format("csv").load(path)
```

**Output**

The tool transforms the `format` method into a `csv` method call when load function has one parameter.

```scala
spark.read.csv(path)
```

**Recommended fix**

In this case, the tool does not show the EWI, meaning there is no fix necessary.

### Scenario 2

**Input**

The below example shows how the tool transforms the `format` method when passing a `net.snowflake.spark.snowflake` value.

```scala
spark.read.format("net.snowflake.spark.snowflake").load(path)
```

**Output**

The tool shows the EWI `SPRKSCL1166` indicating that the value `net.snowflake.spark.snowflake` is not supported.

```scala
/*EWI: SPRKSCL1166 => The parameter net.snowflake.spark.snowflake is not supported for org.apache.spark.sql.DataFrameReader.format
  EWI: SPRKSCL1112 => org.apache.spark.sql.DataFrameReader.load(scala.String) is not supported*/
spark.read.format("net.snowflake.spark.snowflake").load(path)
```

**Recommended fix**

For the `not supported` scenarios there is no specific fix since it depends on the files that are trying to be read.

### Scenario 3
**Input**

The below example shows how the tool transforms the `format` method when passing a `csv`, but using a variable instead.

```scala
val myFormat = "csv"
spark.read.format(myFormat).load(path)
```

**Output**

Since the tool can not determine the value of the variable in runtime, shows the EWI `SPRKSCL1163` indicating that the value is not supported.

```scala
/*EWI: SPRKSCL1163 => myFormat is not a literal and can't be evaluated
  EWI: SPRKSCL1112 => org.apache.spark.sql.DataFrameReader.load(scala.String) is not supported*/
spark.read.format(myFormat).load(path)
```

**Recommended fix**

As a workaround, you can check the value of the variable and add it as a string to the `format` call.

## Additional recommendations

- The Snowpark location only accepts cloud locations using a [snowflake stage](https://docs.snowflake.com/en/user-guide/data-load-local-file-system-create-stage).

- The documentation of methods supported by Snowpark can be found in the [documentation](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/DataFrameReader.html)

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
