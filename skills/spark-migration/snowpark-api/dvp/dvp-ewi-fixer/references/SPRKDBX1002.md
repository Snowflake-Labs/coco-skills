# SPRKDBX1002

Scala cells are not supported.

Message: Scala cells are not supported in Snowsight.

Category: Conversion Error.

## Description

This issue appears when the SMA detects a cell with Scala code in a DBX notebook, Snowsight does not support Scala cells.
Only SQL, Python and Markdown are available in Snowsight.

## Scenarios

There are two scenarios and both of them are not supported.

### Scenario 1

Scala cell in a DBX notebook.

**Input**

Below is an example of a Scala cell in a DBX notebook.

```scala
val df = spark.read.format("csv").load("path/to/file.csv")
df.show()
```

**Output**

The SMA adds the EWI `SPRKDBX1002` on the output code to let you know that this cell is not supported.

```python
# EWI: SPRKDBX1002 => Scala cells are not supported in Snowpark. It is necessary to rewrite the Scala code in Python.
#val df = spark.read.format("csv").load("path/to/file.csv")
#df.show()
```

**Recommended fix**

There is no direct fix for this issue. It is necessary to rewrite the Scala code in Python.

### Scenario 2

The %scala command in a DBX notebook.

**Input**

Below is an example of a %scala command cell in a DBX notebook.

```scala
%scala
val df = spark.read.format("csv").load("path/to/file.csv")
df.show()
```

**Output**

The SMA adds the EWI `SPRKDBX1002` on the output code to let you know that this cell is not supported.

```python
# EWI: SPRKDBX1002 => Scala cells are not supported in Snowpark. It is necessary to rewrite the Scala code in Python.
#val df = spark.read.format("csv").load("path/to/file.csv")
#df.show()
```

**Recommended fix**

There is no direct fix for this issue. It is necessary to rewrite the Scala code in Python.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
