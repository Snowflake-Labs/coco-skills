# SPRKCNTPY4000

SparkContext element is not supported by Snowpark Connect

Message: The element ***\<element full name\>*** of the library SparkContext is not supported by Snowpark Connect

Category: Conversion error

## Description

This issue appears when the SMA detects an usage of a Python SparkContext element that is not supported by Snowpark Connect and does not have its own specific error code associated with it. This is a generic error code used by the SMA for unsupported SparkContext elements.

## Scenario

**Input**

Below is an example of an usage of a SparkContext element that triggers this EWI:

```python
from pyspark import SparkContext

sc = SparkContext()
sc.not_supported_element()
```

**Output**

The SMA adds the EWI `SPRKCNTPY4000` indicating that the SparkContext element is not supported by Snowpark Connect.

```python
from pyspark import SparkContext

sc = SparkContext()
#EWI: SPRKCNTPY4000 => The element 'pyspark.context.SparkContext.not_supported_element' of the library SparkContext is not supported by Snowpark Connect
sc.not_supported_element()
```

**Recommended fix**

Snowpark Connect uses a DataFrame-based architecture and doesn't support SparkContext or RDD operations. As a workaround, you could refactor your code to use Snowpark Connect Session and DataFrame APIs instead.

## Additional recommendations

- Consult the [Snowpark Connect documentation](https://docs.snowflake.com/en/developer-guide/snowpark-connect/snowpark-connect-overview) for available alternatives to your specific use case.
- Note that some SparkContext functionality has no direct equivalent and may require application redesign.
- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
