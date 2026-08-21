# SPRKPY1072

PySpark.StorageLevel

Message: The use of StorageLevel is not required in Snowpark.

Category: Warning.

## Description

This issue appears when the tool finds the use of the [StorageLevel](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.StorageLevel.html) class, which works like "flags" to set the storage level. Since Snowflake controls the storage, the use of this function is not required.

## Additional recommendations

- Remove all uses of this function.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
