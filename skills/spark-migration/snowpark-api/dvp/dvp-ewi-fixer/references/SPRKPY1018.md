# SPRKPY1018

pyspark.sql.functions.date_sub

> This Issue Code has been **deprecated** since [Spark Conversion Core Version 4.8.0 ](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-4.8.0)

Message: pyspark.sql.functions.date_sub has a workaround

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.sql.functions.date_sub](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.date_sub.html) which has a workaround.

## Scenario

**Input**

In this example we use date_add to calculate the date 5 days before the current date for the dataframe df.
```python
col = df.select(date_sub(df.colName, 5))
```

**Output**

SMA returns the EWI SPRKPY1018 over the line where date_sub is used, so you can use to identify where to fix.
```python
#EWI: SPRKPY1018 => pyspark.sql.functions.date_sub has a workaround, see documentation for more info
col = df.select(date_sub(df.colName, 5))
```

**Recommended fix**

Import snowflake.snowpark.functions, which contains an implementation for [date_sub](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.date_sub) function.

```python
from pyspark.sql.functions import date_sub
df.withColumn("date", date_sub(df.colName, 5))
```

## Additional recommendation

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
