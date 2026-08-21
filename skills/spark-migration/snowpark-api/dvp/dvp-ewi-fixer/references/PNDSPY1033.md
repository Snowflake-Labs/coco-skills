# PNDSPY1033

Element has a partial mapping but there are a few scenarios not supported in Snowpark.

Message: Pandas < ***pandas.core.frame.DataFrame.align*** > has a partial mapping with a few scenarios not supported in Snowpark.

Category: Warning.

## Applies to

This EWI applies to the following elements (same implementation):

- `pandas.core.frame.DataFrame.align`
- `pandas.core.generic.NDFrame.align`
- `pandas.core.series.Series.align`

## Description

This issue appears when the SMA detects the use of a Pandas element that has a direct equivalent in Snowpark Pandas, but some scenarios might behave differently than Pandas.


**Missing or Unsupported Parameters:** ``copy``, ``level``, ``fill_value``

**Reason (from Snowpark documentation):** N for MultiIndex, for deprecated parameters method, limit, fill_axis, broadcast_axis, or if fill_value is not default of np.nan

## Scenario

**Input**

Below is an example of a method with a few scenarios not supported in Snowpark.

```python
import pandas as pd

df = pd.DataFrame({'A': [1, 2, 3], 'B': [4, 5, 6]})
result = df.align()
```

**Output**

The SMA adds the EWI `PNDSPY1033` on the output code to let you know that this element has a few scenarios not supported in Snowpark.

```python
import snowflake.snowpark.modin.pandas as pd

#EWI: PNDSPY1033 => pandas.core.frame.DataFrame.align has a partial mapping, with few scenarios not supported. Check Snowpark Pandas documentation for more detail.
df = pd.DataFrame({'A': [1, 2, 3], 'B': [4, 5, 6]})
result = df.align()
```

**Recommended fix**

The following parameters are not supported in Snowpark Pandas: `copy`, `level`, `fill_value`.

**Recommended approaches:**

1. **Avoid unsupported parameters**: Modify your code to not use these parameters if they are not essential.

2. **Use `.to_pandas()` for full compatibility**: If you need these parameters, convert to native Pandas first:
   ```python
   # Convert to native pandas when unsupported parameters are needed
   native_df = df.to_pandas()
   result = native_df.align(...)  # Use all parameters
   ```

3. **Split the operation**: Perform supported operations in Snowpark Pandas, then use native Pandas only for the unsupported functionality.

**NULL/NaN handling difference**: N for MultiIndex, for deprecated parameters method, limit, fill_axis, broadcast_axis, or if fill_value is not default of np.nan

Snowpark Pandas may handle NULL/NaN values differently:
- Pre-filter NULL values using `.dropna()` or `.fillna()` before the operation
- Verify NULL handling behavior with a small sample dataset
- Use explicit NULL checks: `df[df['column'].notna()]`

## Additional recommendations

- Check the [Snowpark Pandas documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/supported/index) to verify which scenarios are not supported for that specific element.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
