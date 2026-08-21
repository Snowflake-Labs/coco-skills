# PNDSPY1121

Element has a partial mapping but there are a few scenarios not supported in Snowpark.

Message: Pandas < ***pandas.core.groupby.generic.DataFrameGroupBy.transform*** > has a partial mapping with a few scenarios not supported in Snowpark.

Category: Warning.

## Description

This issue appears when the SMA detects the use of a Pandas element that has a direct equivalent in Snowpark Pandas, but some scenarios might behave differently than Pandas.


**Missing or Unsupported Parameters:** ``SeriesGroupBy.transform is not implemented.``

**Reason (from Snowpark documentation):** Y when func is a string or callable. A UDTF is created to run transform on every group via apply. transform has the same limitations as apply except for string func also being valid for transform.

## Scenario

**Input**

Below is an example of a method with a few scenarios not supported in Snowpark.

```python
import pandas as pd

df = pd.DataFrame({'A': ['foo', 'bar', 'foo', 'bar'], 'B': [1, 2, 3, 4]})
grouped = df.groupby('A')
result = grouped.transform()
```

**Output**

The SMA adds the EWI `PNDSPY1121` on the output code to let you know that this element has a few scenarios not supported in Snowpark.

```python
import snowflake.snowpark.modin.pandas as pd

#EWI: PNDSPY1121 => pandas.core.groupby.generic.DataFrameGroupBy.transform has a partial mapping, with few scenarios not supported. Check Snowpark Pandas documentation for more detail.
df = pd.DataFrame({'A': ['foo', 'bar', 'foo', 'bar'], 'B': [1, 2, 3, 4]})
grouped = df.groupby('A')
result = grouped.transform()
```

**Recommended fix**

The parameter `SeriesGroupBy.transform is not implemented.` is not supported in Snowpark Pandas. If your code uses this parameter, consider one of these approaches:

1. **Remove the parameter**: If the parameter is not essential for your use case, simply remove it from the function call.

2. **Use default behavior**: The function will work with default values for the unsupported parameter.

3. **Post-process with native Pandas**: If the parameter is critical, collect the result using `.to_pandas()` and apply the operation with native Pandas:
   ```python
   # Convert to native pandas for unsupported parameter
   result = df.to_pandas().transform(SeriesGroupBy.transform is not implemented.=value)
   ```

**Behavioral note**: Y when func is a string or callable. A UDTF is created to run transform on every group via apply. transform has the same limitations as apply except for string func also being valid for transform.

This behavior may differ from native Pandas. Recommended actions:
- Test with a representative sample of your data
- Compare results with native Pandas if precision is critical
- Use `.to_pandas()` if exact Pandas behavior is required

## Additional recommendations

- Check the [Snowpark Pandas documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/supported/index) to verify which scenarios are not supported for that specific element.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
