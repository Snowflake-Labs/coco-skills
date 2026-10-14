# SPRKPY1075
pyspark.sql.functions.from_json

Message: The expected result might be different if the schema doesn't match.

Category: Warning.

## Description
The Pyspark's [from_json](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.from_json.html) conversion: [parse_json](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.parse_json) does not apply schema validation, if you need to filter/validate based on schema you might need to introduce some logic.

## Scenarios

**Input**

you can reference a json schema in Pyspark's from_json

In the function _from_json_ the schema is not really passed for inference it is used for validation.

```python
from pyspark.sql import SparkSession, DataFrameStatFunctions
from pyspark.sql.functions import from_json
from pyspark.sql.types import *

spark = SparkSession.builder.getOrCreate()
data = [(1, '''{"a": 1}''')]
schema = StructType([StructField("a", IntegerType())])
df = spark.createDataFrame(data, ("key", "value"))
df.select(from_json(df.value, schema).alias("json")).show()

## PySpark Execution ouput
## +----+
## |json|
## +----+
## | {1}|
## +----+
```

**Output**

Snowpark parse_json does not apply schema validation . SMA returns the EWI SPRKPY1075 over the line where parse_json is used, so you can use to identify where to fix.

```python
from snowflake.snowpark import Session, DataFrameStatFunctions
from snowflake.snowpark.functions import parse_json
from snowflake.snowpark.types import *

spark = Session.builder.getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})
data = [(1, '''{"a": 1}''')]
schema = StructType([StructField("a", IntegerType())])
df = spark.createDataFrame(data, ("key", "value"))
#EWI: SPRKPY1075 => The parse_json does not apply schema validation, if you need to filter/validate based on schema you might need to introduce some logic. Please check the documentation for more detail.
df.select(parse_json(df.value).alias("json")).show()

## Snowpark Execution Output
## ------------
## |"JSON"    |
## ------------
## |{         |
## |  "a": 1  |
## |}         |
## ------------
```

**Recommended fixes**

See this example:
```python
data = [
    ('{"name": "John", "age": 30, "city": "New York"}',),
    ('{"name": "Jane", "age": "25", "city": "San Francisco"}',)
]

df = spark.createDataFrame(data, ["json_str"])
```

- Enforce Data Types and Change Column Names

```python
# Parse JSON column with schema
parsed_df = df.withColumn("parsed_json", from_json(col("json_str"), schema))


parsed_df.show(truncate=False)

# +------------------------------------------------------+---------------------------+
# |json_str                                              |parsed_json                |
# +------------------------------------------------------+---------------------------+
# |{"name": "John", "age": 30, "city": "New York"}       |{John, 30, New York}       |
# |{"name": "Jane", "age": "25", "city": "San Francisco"}|{Jane, null, San Francisco}|
# +------------------------------------------------------+---------------------------+
# notice that values outside of the schema were dropped and columns not matched are returned as null
```

- Select Specific Columns
```python
# Define a schema with only the columns we want to use
partial_schema = StructType([
    StructField("name", StringType(), True),
    StructField("city", StringType(), True)
])

# Parse JSON column with partial schema
partial_df = df.withColumn("parsed_json", from_json(col("json_str"), partial_schema))

partial_df.show(truncate=False)

# +------------------------------------------------------+---------------------+
# |json_str                                              |parsed_json          |
# +------------------------------------------------------+---------------------+
# |{"name": "John", "age": 30, "city": "New York"}       |{John, New York}     |
# |{"name": "Jane", "age": "25", "city": "San Francisco"}|{Jane, San Francisco}|
# +------------------------------------------------------+---------------------+
# there is also an automatic filtering
```
## Additional recommendations

- Useful indent tools [PEP-8](https://peps.python.org/pep-0008/) and [Reindent](https://pypi.org/project/reindent/).

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
