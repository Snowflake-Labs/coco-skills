# SPRKPY1046

pyspark.sql.functions.monotonically_increasing_id

> This issue code has been **deprecated** since [Spark Conversion Core Version 2.1.22](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-2.1.22)

Message: pyspark.sql.functions.monotonically_increasing_id has a workaround

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.sql.functions.monotonically_increasing_id](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.monotonically_increasing_id.html) which has a workaround.

## Scenario

**Input**

Below is an example of the usage of the method `monotonically_increasing_id`.

```python
from pyspark.sql import functions as F

spark.range(0, 10, 1, 2).select(F.monotonically_increasing_id()).show()
```

**Output**

The tool adds the EWI `SPRKPY1046` indicating that a workaround can be implemented.

```python
from pyspark.sql import functions as F
#EWI: SPRKPY1046 => pyspark.sql.functions.monotonically_increasing_id has a workaround, see documentation for more info
spark.range(0, 10, 1, 2).select(F.monotonically_increasing_id()).show()
```

**Recommended fix**

Update the tool version.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
