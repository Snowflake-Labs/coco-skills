# SPRKPY1016

pyspark.sql.functions.collect_set

> This Issue Code has been **deprecated** since [Spark Conversion Core Version 0.11.7](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-0.11.7)


Message: pyspark.sql.functions.collect_set has a workaround

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.sql.functions.collect_set](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.collect_set.html) which has a workaround.

## Scenario
**Input**

Using collect_set to get the elements of _colname_ without duplicates:
```python
col = collect_set(colName)
```

**Output**

SMA returns the EWI SPRKPY1016 over the line where collect_set is used, so you can use to identify where to fix.
```python
#EWI: SPRKPY1016 => pyspark.sql.functions.collect_set has a workaround, see documentation for more info
col = collect_set(colName)
```

**Recommended fix**

Use function array_agg, and add a second argument with the value True.

```python
col = array_agg(col, True)
```

## Additional recommendation

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
