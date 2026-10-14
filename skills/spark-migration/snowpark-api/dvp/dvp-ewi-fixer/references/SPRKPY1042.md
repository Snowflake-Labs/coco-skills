# SPRKPY1042

pyspark.sql.functions.posexplode

Message: pyspark.sql.functions.posexplode has a workaround

Category: Warning

## Description

This issue appears when the tool detects the usage of [pyspark.sql.functions.posexplode](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.posexplode.html) which has a workaround.

## Scenarios

There are a couple of scenarios that this method can handle depending on the type of column it is passed as a parameter, it can be a `list of values` or a `map/directory (keys/values)`.

### Scenario 1

**Input**

Below is an example of the usage of `posexplode` passing as a parameter of a **list of values**.

```python
df = spark.createDataFrame(
    [Row(a=1,
         intlist=[1, 2, 3])])

df.select(posexplode(df.intlist)).collect()
```

**Output**

The tool adds the EWI `SPRKPY1042` indicating that a workaround can be implemented.

```python
df = spark.createDataFrame(
    [Row(a=1,
         intlist=[100, 200, 300])])
#EWI: SPRKPY1042 => pyspark.sql.functions.posexplode has a workaround, see documentation for more info

df.select(posexplode(df.intlist)).show()
```

**Recommended fix**

For having the same behavior, use the method [functions.flatten](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/api/snowflake.snowpark.functions.flatten), drop extra columns, and rename index and value column names.

```python
df = spark.createDataFrame(
  [Row(a=1,
       intlist=[1, 2, 3])])

df.select(
    flatten(df.intlist))\
    .drop("DATA", "SEQ", "KEY", "PATH", "THIS")\
    .rename({"INDEX": "pos", "VALUE": "col"}).show()
```

### Scenario 2

**Input**

Below is another example of the usage of `posexplode` passing as a parameter a **map/dictionary (keys/values)**

```python
df = spark.createDataFrame([
    [1, [1, 2, 3], {"Ashi Garami": "Single Leg X"}, "Kimura"],
    [2, [11, 22], {"Sankaku": "Triangle"}, "Coffee"]
],
schema=["idx", "lists", "maps", "strs"])

df.select(posexplode(df.maps)).show()
```

**Output**

The tool adds the EWI `SPRKPY1042` indicating that a workaround can be implemented.

```python
df = spark.createDataFrame([
    [1, [1, 2, 3], {"Ashi Garami": "Single Leg X"}, "Kimura"],
    [2, [11, 22], {"Sankaku": "Triangle"}, "Coffee"]
],
schema=["idx", "lists", "maps", "strs"])
#EWI: SPRKPY1042 => pyspark.sql.functions.posexplode has a workaround, see documentation for more info

df.select(posexplode(df.maps)).show()
```

**Recommended fix**

As a workaround, you can use [functions.row_number](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/api/snowflake.snowpark.functions.row_number.html) to get the position and [functions.explode](https://docs.snowflake.com/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.explode) with the name of the field to get the value the key/value for dictionaries.

```python
df = spark.createDataFrame([
    [10, [1, 2, 3], {"Ashi Garami": "Single Leg X"}, "Kimura"],
    [11, [11, 22], {"Sankaku": "Triangle"}, "Coffee"]
],
    schema=["idx", "lists", "maps", "strs"])

window = Window.orderBy(col("idx").asc())

df.select(
    row_number().over(window).alias("pos"),
    explode(df.maps).alias("key", "value")).show()
```

**Note:** using row_number is not fully equivalent, because it starts with 1 (not zero as spark method)

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
