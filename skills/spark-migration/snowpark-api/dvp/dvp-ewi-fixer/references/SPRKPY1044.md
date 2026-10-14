# SPRKPY1044

pyspark.sql.functions.split

> This issue code has been **deprecated** since [Spark Conversion Core Version 2.4.0](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-2.4.0)

Message: pyspark.sql.functions.split has a workaround

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.sql.functions.split](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.split.html) which has a workaround.

## Scenarios

There are a couple of scenarios depending on the amount of parameters passed to the method.

### Scenario 1

**Input**

Below is an example when the function `split` has just the *str* and *pattern* parameters

```python
F.split('col', '\\|')
```

**Output**

The tool shows the EWI `SPRKPY1044` indicating there is a workaround.

```python
#EWI: SPRKPY1044 => pyspark.sql.functions.split has a workaround, see the documentation for more info
F.split('col', '\\|')
```
**Recommended fix**

As a workaround, you can call the function [snowflake.snowpark.functions.lit](https://docs.snowflake.com/ko/developer-guide/snowpark/reference/python/api/snowflake.snowpark.functions.lit.html) with the pattern parameter and send it into the split.

```python
F.split('col', lit('\\|'))
# the result of lit will be sent to the split function
```

### Scenario 2

**Input**

Below is another example when the function `split` has the *str, pattern, and limit* parameters.

```python
F.split('col', '\\|', 2)
```

**Output**

The tool shows the EWI `SPRKPY1044` indicating there is a workaround.

```python
#EWI: SPRKPY1044 => pyspark.sql.functions.split has a workaround, see the documentation for more info
F.split('col', '\\|', 2)
```

**Recommended fix**

This specific scenario is not supported.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
