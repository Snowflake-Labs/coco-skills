# PNDSPY1029

Element has a partial mapping but there are a few scenarios not supported in Snowpark.

Message: Pandas < ***pandas.core.base.IndexOpsMixin.value_counts*** > has a partial mapping with a few scenarios not supported in Snowpark.

Category: Warning.

## Applies to

This EWI applies to the following elements (same implementation):

- `pandas.core.base.IndexOpsMixin.value_counts`
- `pandas.core.indexes.base.Index.value_counts`
- `pandas.core.series.Series.value_counts`

## Description

This issue appears when the SMA detects the use of a Pandas element that has a direct equivalent in Snowpark Pandas, but some scenarios might behave differently than Pandas.


**Missing or Unsupported Parameters:** ``bins``

## Scenario

**Input**

Below is an example of a method with a few scenarios not supported in Snowpark.

```python
import pandas as pd

idx = pd.Index([1, 2, 3, 4, 5])
result = idx.value_counts()
```

**Output**

The SMA adds the EWI `PNDSPY1029` on the output code to let you know that this element has a few scenarios not supported in Snowpark.

```python
import snowflake.snowpark.modin.pandas as pd

#EWI: PNDSPY1029 => pandas.core.base.IndexOpsMixin.value_counts has a partial mapping, with few scenarios not supported. Check Snowpark Pandas documentation for more detail.
idx = pd.Index([1, 2, 3, 4, 5])
result = idx.value_counts()
```

**Recommended fix**

The parameter `bins` is not supported in Snowpark Pandas. If your code uses this parameter, consider one of these approaches:

1. **Remove the parameter**: If the parameter is not essential for your use case, simply remove it from the function call.

2. **Use default behavior**: The function will work with default values for the unsupported parameter.

3. **Post-process with native Pandas**: If the parameter is critical, collect the result using `.to_pandas()` and apply the operation with native Pandas:
   ```python
   # Convert to native pandas for unsupported parameter
   result = df.to_pandas().value_counts(bins=value)
   ```

## Additional recommendations

- Check the [Snowpark Pandas documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/supported/index) to verify which scenarios are not supported for that specific element.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
