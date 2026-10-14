# SPRKPY1045

pyspark.sql.functions.map_values

Message: pyspark.sql.functions.map_values has a workaround

Category: Warning.

## Description

This function is used to extract the list of values from a column that contains a **map/dictionary (keys/values)**.

The issue appears when the tool detects the usage of [pyspark.sql.functions.map_values](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.map_values.html) which has a workaround.

## Scenario

**Input**

Below is an example of the usage of the method `map_values`.

```python
df = spark.createDataFrame(
    [(1, {'Apple': 'Fruit', 'Potato': 'Vegetable'})],
    ("id", "a_map"))

df.select(map_values("a_map")).show()
```

**Output**

The tool adds the EWI `SPRKPY1045` indicating that a workaround can be implemented.

```python
df = spark.createDataFrame(
    [(1, {'Apple': 'Fruit', 'Potato': 'Vegetable'})],
    ("id", "a_map"))
#EWI: SPRKPY1045 => pyspark.sql.functions.map_values has a workaround, see documentation for more info

df.select(map_values("a_map")).show()
```

**Recommended fix**

As a workaround, you can create an [udf](https://docs.snowflake.com/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.udf) to get the values for a column. The below example shows how to create the udf, then assign it to `F.map_values`, and then make use of it.

```python
from snowflake.snowpark import functions as F
from snowflake.snowpark.types import ArrayType, MapType

map_values_udf=None

def map_values(map):
    global map_values_udf
    if not map_values_udf:
        def _map_values(map: dict)->list:
            return list(map.values())
        map_values_udf = F.udf(_map_values,return_type=ArrayType(),input_types=[MapType()],name="map_values",is_permanent=False,replace=True)
    return map_values_udf(map)

F.map_values = map_values

df.select(map_values(colDict))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
