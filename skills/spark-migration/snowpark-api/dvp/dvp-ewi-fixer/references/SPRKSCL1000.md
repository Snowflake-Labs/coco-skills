# SPRKSCL1000

The Spark-core version is not supported version.

Message: Source project spark-core version is ***version number***, the spark-core version supported by snowpark is 2.12:3.1.2 so there may be functional differences between the existing mappings

Category: Warning

## Description

This issue appears when the SMA detects a version of the `spark-core` that is not supported by SMA.
Therefore, there may be functional differences between the existing mappings and the output might have unexpected behaviors.

## Additional recommendations

- The spark-core version supported by SMA is 2.12:3.1.2. Consider changing the version of your source code.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
