# SPRKPY1080
pyspark.SparkContext

Message: The value of SparkContext is replaced with 'session' variable.

Category: Warning

## Description

Spark context is stored into a variable called session that creates a Snowpark Session.

## Scenario

**Input**

This snippet describes a SparkContext
```python
# Input Code
from pyspark import SparkContext
from pyspark.sql import SparkSession

def example1():

    sc = SparkContext("local[*]", "TestApp")

    sc.setLogLevel("ALL")
    sc.setLogLevel("DEBUG")
```

**Output**

In this output code SMA has replaced the PySpark.SparkContext by a SparkSession , Note that SMA also add a template to replace the connection in the "connection.json" file and then load this configuration on the connection_parameter variable.
```python
# Output Code
import logging
import sys
import json
from snowflake.snowpark import Session
from snowflake.snowpark import Session

def example1():
    jsonFile = open("connection.json")
    connection_parameter = json.load(jsonFile)
    jsonFile.close()
    #EWI: SPRKPY1080 => The value of SparkContext is replaced with 'session' variable.
    sc = Session.builder.configs(connection_parameter).getOrCreate()
    sc.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})
    logging.basicConfig(stream = sys.stdout, level = logging.NOTSET)
    logging.basicConfig(stream = sys.stdout, level = logging.DEBUG)
```

**Recommended fix**

The configuration file "connection.json" must be updated with the required connection information:

```json
{
  "user": "my_user",
  "password": "my_password",
  "account": "my_account",
  "role": "my_role",
  "warehouse": "my_warehouse",
  "database": "my_database",
  "schema": "my_schema"
}
```


## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
