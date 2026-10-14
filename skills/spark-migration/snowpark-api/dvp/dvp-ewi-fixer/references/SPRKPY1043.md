# SPRKPY1043

pyspark.sql.functions.posexplode_outer

Message: pyspark.sql.functions.posexplode_outer has a workaround

Category: Warning

## Description

This issue appears when the tool detects the usage of [pyspark.sql.functions.posexplode_outer](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.posexplode_outer.html) which has a workaround.

## Scenarios

There are a couple of scenarios that this method can handle depending on the type of column it is passed as a parameter, it can be a `list of values` or a `map/directory (keys/values)`.

### Scenario 1

**Input**

Below is an example that shows the usage of `posexplode_outer` passing a **list of values**.

```python
df = spark.createDataFrame(
    [
        (1, ["foo", "bar"]),
        (2, []),
        (3, None)],
    ("id", "an_array"))

df.select("id", "an_array", posexplode_outer("an_array")).show()
```

**Output**

The tool adds the EWI `SPRKPY1043` indicating that a workaround can be implemented.

```python
df = spark.createDataFrame(
    [
        (1, ["foo", "bar"]),
        (2, []),
        (3, None)],
    ("id", "an_array"))
#EWI: SPRKPY1043 => pyspark.sql.functions.posexplode_outer has a workaround, see documentation for more info

df.select("id", "an_array", posexplode_outer("an_array")).show()
```

**Recommended fix**

For having the same behavior, use the method [functions.flatten](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/api/snowflake.snowpark.functions.flatten) sending the `outer` parameter in True, drop extra columns, and rename index and value column names.

```python
df = spark.createDataFrame(
    [
        (1, ["foo", "bar"]),
        (2, []),
        (3, None)],
    ("id", "an_array"))

df.select(
    flatten(df.an_array, outer=True))\
    .drop("DATA", "SEQ", "KEY", "PATH", "THIS")\
    .rename({"INDEX": "pos", "VALUE": "col"}).show()
```

### Scenario 2

**Input**

Below is another example of the usage of posexplode_outer passing a **map/dictionary (keys/values)**

```python
df = spark.createDataFrame(
    [
        (1, {"x": 1.0}),
        (2, {}),
        (3, None)],
    ("id", "a_map"))

df.select(posexplode_outer(df.a_map)).show()
```

**Output**

The tool adds the EWI `SPRKPY1043` indicating that a workaround can be implemented.

```python
df = spark.createDataFrame(
    [
        (1, {"x": "Ashi Garami"}),
        (2, {}),
        (3, None)],
    ("id", "a_map"))
#EWI: SPRKPY1043 => pyspark.sql.functions.posexplode_outer has a workaround, see documentation for more info

df.select(posexplode_outer(df.a_map)).show()
```

**Recommended fix**

As a workaround, you can use [functions.row_number](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/api/snowflake.snowpark.functions.row_number.html) to get the position and [functions.explode_outer](https://docs.snowflake.com/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.explode_outer) with the name of the field to get the value of the key/value for dictionaries.

```python
df = spark.createDataFrame(
    [
        (1, {"x": "Ashi Garami"}),
        (2,  {}),
        (3, None)],
    ("id", "a_map"))

window = Window.orderBy(col("id").asc())

df.select(
    row_number().over(window).alias("pos"),
          explode_outer(df.a_map)).show()
```

**Note:** using row_number is not fully equivalent, because it starts with 1 (not zero as spark method)

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
