# PNDSPY1097

Element has a partial mapping but there are a few scenarios not supported in Snowpark.

Message: Pandas < ***pandas.core.frame.DataFrame.sample*** > has a partial mapping with a few scenarios not supported in Snowpark.

Category: Warning.

## Applies to

This EWI applies to the following elements (same implementation):

- `pandas.core.frame.DataFrame.sample`
- `pandas.core.generic.NDFrame.sample`
- `pandas.core.series.Series.sample`

## Description

This issue appears when the SMA detects the use of a Pandas element that has a direct equivalent in Snowpark Pandas, but some scenarios might behave differently than Pandas.


**Reason (from Snowpark documentation):** N if weights is specified when axis = 0, or if random_state is not either an integer or None. Setting random_state to a value other than None may slow down this method because the sample implementation will use a sort instead of the Snowflake warehouse's built-in SAMPLE construct.

## Scenario

**Input**

Below is an example of a method with a few scenarios not supported in Snowpark.

```python
import pandas as pd

df = pd.DataFrame({'A': [1, 2, 3], 'B': [4, 5, 6]})
result = df.sample()
```

**Output**

The SMA adds the EWI `PNDSPY1097` on the output code to let you know that this element has a few scenarios not supported in Snowpark.

```python
import snowflake.snowpark.modin.pandas as pd

#EWI: PNDSPY1097 => pandas.core.frame.DataFrame.sample has a partial mapping, with few scenarios not supported. Check Snowpark Pandas documentation for more detail.
df = pd.DataFrame({'A': [1, 2, 3], 'B': [4, 5, 6]})
result = df.sample()
```

**Recommended fix**

**Performance consideration**: N if weights is specified when axis = 0, or if random_state is not either an integer or None. Setting random_state to a value other than None may slow down this method because the sample implementation will use a sort instead of the Snowflake warehouse's built-in SAMPLE construct.

For better performance:
- Filter data before applying this operation to reduce data volume
- Consider breaking the operation into smaller chunks
- Use Snowflake's native SQL functions via `session.sql()` for large datasets

## Additional recommendations

- Check the [Snowpark Pandas documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/supported/index) to verify which scenarios are not supported for that specific element.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
