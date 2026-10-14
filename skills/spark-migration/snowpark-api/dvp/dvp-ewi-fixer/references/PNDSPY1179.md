# PNDSPY1179

Element has a partial mapping but there are a few scenarios not supported in Snowpark.

Message: Pandas < ***pandas.core.series.Series.dropna*** > has a partial mapping with a few scenarios not supported in Snowpark.

Category: Warning.

## Description

This issue appears when the SMA detects the use of a Pandas element that has a direct equivalent in Snowpark Pandas, but some scenarios might behave differently than Pandas.


## Scenario

**Input**

Below is an example of a method with a few scenarios not supported in Snowpark.

```python
import pandas as pd

s = pd.Series([1, 2, 3, 4, 5])
result = s.dropna()
```

**Output**

The SMA adds the EWI `PNDSPY1179` on the output code to let you know that this element has a few scenarios not supported in Snowpark.

```python
import snowflake.snowpark.modin.pandas as pd

#EWI: PNDSPY1179 => pandas.core.series.Series.dropna has a partial mapping, with few scenarios not supported. Check Snowpark Pandas documentation for more detail.
s = pd.Series([1, 2, 3, 4, 5])
result = s.dropna()
```

**Recommended fix**

This element has partial support in Snowpark Pandas. General recommendations:

1. **Test with sample data**: Verify the operation works as expected with a subset of your data

2. **Check parameters**: Review which parameters are supported in the [Snowpark Pandas documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/supported/index)

3. **Fallback option**: If exact Pandas behavior is required:
   ```python
   # Convert to native pandas for full compatibility
   result = df.to_pandas().dropna(...)
   ```

4. **Consider SQL alternative**: For complex operations, Snowflake SQL may offer better performance

## Additional recommendations

- Check the [Snowpark Pandas documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/supported/index) to verify which scenarios are not supported for that specific element.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
