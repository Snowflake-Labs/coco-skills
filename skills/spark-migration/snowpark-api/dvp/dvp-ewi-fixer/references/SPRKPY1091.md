# SPRKPY1091
pyspark.sql.functions.concat_ws

Message: The pyspark.sql.functions.concat_ws function does not convert aliases to uppercase or enclose them in double quotes.

Category: Warning

## Description

The converted _concat_ws_ignore_nulls function capitalizes the column alias and encloses it in double quotes.

## Scenario

**Input**

This snippet will show the data frame in the console.
```python
# Input Code
from pyspark.sql import SparkSession
from pyspark.sql.functions import *
from pyspark.sql.types import *

spark = SparkSession.builder.appName("SparkByExamples.com").getOrCreate()

concat_wsDf = spark.createDataFrame([("abcd", "123")], ["s", "d"])
concat_wsDf.select(concat_ws("-", concat_wsDf.s, concat_wsDf.d).alias("s")).show()
```

**Output**

When inspecting the output code in the console, the _concat_ws_ignore_nulls function returns the column with its alias
in uppercase and enclosed in double quotes.
```python
from snowflake.snowpark.functions import _concat_ws_ignore_nulls
from snowflake.snowpark import Session
from snowflake.snowpark.functions import *
from snowflake.snowpark.types import *

spark = Session.builder.app_name("SparkByExamples.com", True).getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})

concat_wsDf = spark.createDataFrame([("abcd", "123")], ["s", "d"])
#EWI: SPRKPY1091 => The _concat_ws_ignore_nulls function converts column aliases to uppercase and encloses them in double quotes.
concat_wsDf.select(_concat_ws_ignore_nulls("-", concat_wsDf.s, concat_wsDf.d).alias("s")).show()
```

**Recommended fix**

As of now there is not a direct fix for this difference. However, you can manually change the alias names to upper cases
to avoid any issues with case sensitivity. And be aware that the alias is also wrapped in double quotes.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
