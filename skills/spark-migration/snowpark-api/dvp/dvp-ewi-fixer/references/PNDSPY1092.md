# PNDSPY1092

Element has a partial mapping but there are a few scenarios not supported in Snowpark.

Message: Pandas < ***pandas.core.frame.DataFrame.rolling*** > has a partial mapping with a few scenarios not supported in Snowpark.

Category: Warning.

## Applies to

This EWI applies to the following elements (same implementation):

- `pandas.core.frame.DataFrame.rolling`
- `pandas.core.generic.NDFrame.rolling`
- `pandas.core.series.Series.rolling`

## Description

This issue appears when the SMA detects the use of a Pandas element that has a direct equivalent in Snowpark Pandas, but some scenarios might behave differently than Pandas.


**Missing or Unsupported Parameters:** ``method is ignored``, ``step``, ``win_type``, ``closed``, ``on``

**Reason (from Snowpark documentation):** N for non-integer window, axis = 1, or min_periods = 0

## Scenario

**Input**

Below is an example of a method with a few scenarios not supported in Snowpark.

```python
import pandas as pd

df = pd.DataFrame({'A': [1, 2, 3], 'B': [4, 5, 6]})
result = df.rolling()
```

**Output**

The SMA adds the EWI `PNDSPY1092` on the output code to let you know that this element has a few scenarios not supported in Snowpark.

```python
import snowflake.snowpark.modin.pandas as pd

#EWI: PNDSPY1092 => pandas.core.frame.DataFrame.rolling has a partial mapping, with few scenarios not supported. Check Snowpark Pandas documentation for more detail.
df = pd.DataFrame({'A': [1, 2, 3], 'B': [4, 5, 6]})
result = df.rolling()
```

**Recommended fix**

The following parameters are not supported in Snowpark Pandas: `method is ignored`, `step`, `win_type`, `closed`, `on`.

**Recommended approaches:**

1. **Avoid unsupported parameters**: Modify your code to not use these parameters if they are not essential.

2. **Use `.to_pandas()` for full compatibility**: If you need these parameters, convert to native Pandas first:
   ```python
   # Convert to native pandas when unsupported parameters are needed
   native_df = df.to_pandas()
   result = native_df.rolling(...)  # Use all parameters
   ```

3. **Split the operation**: Perform supported operations in Snowpark Pandas, then use native Pandas only for the unsupported functionality.

**Behavioral note**: N for non-integer window, axis = 1, or min_periods = 0

This behavior may differ from native Pandas. Recommended actions:
- Test with a representative sample of your data
- Compare results with native Pandas if precision is critical
- Use `.to_pandas()` if exact Pandas behavior is required

## Additional recommendations

- Check the [Snowpark Pandas documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/supported/index) to verify which scenarios are not supported for that specific element.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
