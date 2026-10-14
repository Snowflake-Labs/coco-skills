# SPRKPY1008

pyspark.sql.context.HiveContext

Message: pyspark.sql.context.HiveContext is not required

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.sql.context.HiveContext](https://downloads.apache.org/spark/docs/1.6.1/api/python/pyspark.sql.html#pyspark.sql.HiveContext), which is not required.

## Scenario

**Input**

In this example creates a connection to an Hive store.

```python
from pyspark.sql import HiveContext
hive_context = HiveContext(sc)
df = hive_context.table("myTable")
df.show()
```

**Output**

In Snowflake there are not Hive stores, so the Hive Context is not required, You can still use parquet files on Snowflake please check this [tutorial](https://docs.snowflake.com/en/user-guide/tutorials/script-data-load-transform-parquet) to learn how.

```python
#EWI: SPRKPY1008 => pyspark.sql.context.HiveContext is not required
hive_context = sc
df = hive_context.table("myTable")
df.show()
```
the sc variable refers to a [Snow Park Session Object](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.Session)

**Recommended fix**

For the output code in the example you should add the [Snow Park Session Object](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.Session) similar to this code:
```python
# Here manually we can add the Snowpark Session object via a json config file called connection.json
import json
from snowflake.snowpark import Session
jsonFile = open("connection.json")
connection_parameter = json.load(jsonFile)
jsonFile.close()
sc = Session.builder.configs(connection_parameter).getOrCreate()

hive_context = sc
df = hive_context.table("myTable")
df.show()
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
