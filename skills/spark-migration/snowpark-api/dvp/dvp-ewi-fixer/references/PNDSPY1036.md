# PNDSPY1036

Element has a partial mapping but there are a few scenarios not supported in Snowpark.

Message: Pandas < ***pandas.core.frame.DataFrame.applymap*** > has a partial mapping with a few scenarios not supported in Snowpark.

Category: Warning.

## Description

This issue appears when the SMA detects the use of a Pandas element that has a direct equivalent in Snowpark Pandas, but some scenarios might behave differently than Pandas.


**Reason (from Snowpark documentation):** N if na_action == "ignore"

## Scenario

**Input**

Below is an example of a method with a few scenarios not supported in Snowpark.

```python
import pandas as pd

df = pd.DataFrame({'A': [1, 2, 3], 'B': [4, 5, 6]})
result = df.applymap()
```

**Output**

The SMA adds the EWI `PNDSPY1036` on the output code to let you know that this element has a few scenarios not supported in Snowpark.

```python
import snowflake.snowpark.modin.pandas as pd

#EWI: PNDSPY1036 => pandas.core.frame.DataFrame.applymap has a partial mapping, with few scenarios not supported. Check Snowpark Pandas documentation for more detail.
df = pd.DataFrame({'A': [1, 2, 3], 'B': [4, 5, 6]})
result = df.applymap()
```

**Recommended fix**

**NULL/NaN handling difference**: N if na_action == "ignore"

Snowpark Pandas may handle NULL/NaN values differently:
- Pre-filter NULL values using `.dropna()` or `.fillna()` before the operation
- Verify NULL handling behavior with a small sample dataset
- Use explicit NULL checks: `df[df['column'].notna()]`

## Additional recommendations

- Check the [Snowpark Pandas documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/supported/index) to verify which scenarios are not supported for that specific element.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
