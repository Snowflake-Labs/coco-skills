# SPRKPY1000

Not supported spark version

Message: Source project spark-core version is x.xx:xx.xx, the spark-core version supported by snowpark is 2.12:3.1.2 so there may be functional differences between the existing mappings.

Category: Warning.

## Description

This issue appears when the pyspark version of your source code is not supported. This means, there may be functional differences between the existing mappings.

## Additional recommendations

- The pyspark version scanned by the SMA for compatibility to Snowpark is from 2.12 to 3.1.2. If you are using a version outside this range, the tool may produce inconsistent results. You could alter the version of the source code you are scanning.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
