# PNDSPY1159

Element has a partial mapping but there are a few scenarios not supported in Snowpark.

Message: Pandas < ***pandas.core.resample.Resampler.quantile*** > has a partial mapping with a few scenarios not supported in Snowpark.

Category: Warning.

## Description

This issue appears when the SMA detects the use of a Pandas element that has a direct equivalent in Snowpark Pandas, but some scenarios might behave differently than Pandas.


**Reason (from Snowpark documentation):** N for list-like q

## Scenario

**Input**

Below is an example of a method with a few scenarios not supported in Snowpark.

```python
import pandas as pd

df = pd.DataFrame({'A': [1, 2, 3]}, index=pd.date_range('2023-01-01', periods=3, freq='D'))
resampled = df.resample('D')
result = resampled.quantile
```

**Output**

The SMA adds the EWI `PNDSPY1159` on the output code to let you know that this element has a few scenarios not supported in Snowpark.

```python
import snowflake.snowpark.modin.pandas as pd

#EWI: PNDSPY1159 => pandas.core.resample.Resampler.quantile has a partial mapping, with few scenarios not supported. Check Snowpark Pandas documentation for more detail.
df = pd.DataFrame({'A': [1, 2, 3]}, index=pd.date_range('2023-01-01', periods=3, freq='D'))
resampled = df.resample('D')
result = resampled.quantile
```

**Recommended fix**

**Behavioral note**: N for list-like q

This behavior may differ from native Pandas. Recommended actions:
- Test with a representative sample of your data
- Compare results with native Pandas if precision is critical
- Use `.to_pandas()` if exact Pandas behavior is required

## Additional recommendations

- Check the [Snowpark Pandas documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/supported/index) to verify which scenarios are not supported for that specific element.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
