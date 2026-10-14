# PNDSPY1184

Element has a partial mapping but there are a few scenarios not supported in Snowpark.

Message: Pandas < ***pandas.core.series.Series.groupby*** > has a partial mapping with a few scenarios not supported in Snowpark.

Category: Warning.

## Description

This issue appears when the SMA detects the use of a Pandas element that has a direct equivalent in Snowpark Pandas, but some scenarios might behave differently than Pandas.


**Missing or Unsupported Parameters:** ``observed is ignored since Categoricals are not implemented yet``

**Reason (from Snowpark documentation):** Y, support axis == 0 and by is column label or Series from the current DataFrame, or a pd.Grouper object; otherwise N. If a pd.Grouper object is passed, then only the default values of the sort, closed, label, and convention arguments are supported. The origin argument currently supports "start_day" and "start". Note that supported functions are agg, count, cumcount, cummax, cummin, cumsum, first, last, max, mean, median, min, quantile, shift, size, std, sum, and var. Otherwise N

## Scenario

**Input**

Below is an example of a method with a few scenarios not supported in Snowpark.

```python
import pandas as pd

s = pd.Series([1, 2, 3, 4, 5])
result = s.groupby()
```

**Output**

The SMA adds the EWI `PNDSPY1184` on the output code to let you know that this element has a few scenarios not supported in Snowpark.

```python
import snowflake.snowpark.modin.pandas as pd

#EWI: PNDSPY1184 => pandas.core.series.Series.groupby has a partial mapping, with few scenarios not supported. Check Snowpark Pandas documentation for more detail.
s = pd.Series([1, 2, 3, 4, 5])
result = s.groupby()
```

**Recommended fix**

The parameter `observed is ignored since Categoricals are not implemented yet` is not supported in Snowpark Pandas. If your code uses this parameter, consider one of these approaches:

1. **Remove the parameter**: If the parameter is not essential for your use case, simply remove it from the function call.

2. **Use default behavior**: The function will work with default values for the unsupported parameter.

3. **Post-process with native Pandas**: If the parameter is critical, collect the result using `.to_pandas()` and apply the operation with native Pandas:
   ```python
   # Convert to native pandas for unsupported parameter
   result = df.to_pandas().groupby(observed is ignored since Categoricals are not implemented yet=value)
   ```

**Behavioral note**: Y, support axis == 0 and by is column label or Series from the current DataFrame, or a pd.Grouper object; otherwise N. If a pd.Grouper object is passed, then only the default values of the sort, closed, label, and convention arguments are supported. The origin argument currently supports "start_day" and "start". Note that supported functions are agg, count, cumcount, cummax, cummin, cumsum, first, last, max, mean, median, min, quantile, shift, size, std, sum, and var. Otherwise N

This behavior may differ from native Pandas. Recommended actions:
- Test with a representative sample of your data
- Compare results with native Pandas if precision is critical
- Use `.to_pandas()` if exact Pandas behavior is required

## Additional recommendations

- Check the [Snowpark Pandas documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/supported/index) to verify which scenarios are not supported for that specific element.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
