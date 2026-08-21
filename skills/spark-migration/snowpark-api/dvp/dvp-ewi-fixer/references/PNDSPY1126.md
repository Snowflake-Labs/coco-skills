# PNDSPY1126

Element has a partial mapping but there are a few scenarios not supported in Snowpark.

Message: Pandas < ***pandas.core.groupby.groupby.GroupBy.apply*** > has a partial mapping with a few scenarios not supported in Snowpark.

Category: Warning.

## Description

This issue appears when the SMA detects the use of a Pandas element that has a direct equivalent in Snowpark Pandas, but some scenarios might behave differently than Pandas.


**Missing or Unsupported Parameters:** ``axis other than 0 is not implemented.``

**Reason (from Snowpark documentation):** Y if the following are true, otherwise N: - func is a callable that always returns either a pandas DataFrame, a pandas Series, or objects that are neither DataFrame nor Series. - grouping on axis=0 - Not applying transform to a dataframe with a non-unique index - Not applying func that returns two dataframes that have different labels for the column at a given position - Not applying func that returns two dataframes that have different names for a given index label - Not applying func that returns two Series that have different labels for the row at a given position - Not applying func that returns two Series that have different names - Not grouping by an "external" by, i.e. an object that is not a label for a column or level of the dataframe

## Scenario

**Input**

Below is an example of a method with a few scenarios not supported in Snowpark.

```python
import pandas as pd

df = pd.DataFrame({'A': ['foo', 'bar', 'foo', 'bar'], 'B': [1, 2, 3, 4]})
grouped = df.groupby('A')
result = grouped.apply()
```

**Output**

The SMA adds the EWI `PNDSPY1126` on the output code to let you know that this element has a few scenarios not supported in Snowpark.

```python
import snowflake.snowpark.modin.pandas as pd

#EWI: PNDSPY1126 => pandas.core.groupby.groupby.GroupBy.apply has a partial mapping, with few scenarios not supported. Check Snowpark Pandas documentation for more detail.
df = pd.DataFrame({'A': ['foo', 'bar', 'foo', 'bar'], 'B': [1, 2, 3, 4]})
grouped = df.groupby('A')
result = grouped.apply()
```

**Recommended fix**

The parameter `axis other than 0 is not implemented.` is not supported in Snowpark Pandas. If your code uses this parameter, consider one of these approaches:

1. **Remove the parameter**: If the parameter is not essential for your use case, simply remove it from the function call.

2. **Use default behavior**: The function will work with default values for the unsupported parameter.

3. **Post-process with native Pandas**: If the parameter is critical, collect the result using `.to_pandas()` and apply the operation with native Pandas:
   ```python
   # Convert to native pandas for unsupported parameter
   result = df.to_pandas().apply(axis other than 0 is not implemented.=value)
   ```

**NULL/NaN handling difference**: Y if the following are true, otherwise N: - func is a callable that always returns either a pandas DataFrame, a pandas Series, or objects that are neither DataFrame nor Series. - grouping on axis=0 - Not applying transform to a dataframe with a non-unique index - Not applying func that returns two dataframes that have different labels for the column at a given position - Not applying func that returns two dataframes that have different names for a given index label - Not applying func that returns two Series that have different labels for the row at a given position - Not applying func that returns two Series that have different names - Not grouping by an "external" by, i.e. an object that is not a label for a column or level of the dataframe

Snowpark Pandas may handle NULL/NaN values differently:
- Pre-filter NULL values using `.dropna()` or `.fillna()` before the operation
- Verify NULL handling behavior with a small sample dataset
- Use explicit NULL checks: `df[df['column'].notna()]`

## Additional recommendations

- Check the [Snowpark Pandas documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/supported/index) to verify which scenarios are not supported for that specific element.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
