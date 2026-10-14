# PNDSPY1002

Element has a partial mapping but there are a few scenarios not supported in Snowpark.

Message: Pandas < ***element*** > has a partial mapping with a few scenarios not supported in Snowpark.

Category: Warning.

## Description

This issue appears when the SMA detects the use of a Pandas element that has a direct equivalent in Snowpark Pandas, but some scenarios might behave differently than Pandas.

## Scenario

**Input**

Below is an example of a method with a few scenarios not supported in Snowpark.

```python
import pandas as pd

pd.melt(df, id_vars=['A'], value_vars=['B'])
```

**Output**

The SMA adds the EWI `PNDSPY1002` on the output code to let you know that this element has a few scenarios not supported in Snowpark.

```python
import snowflake.snowpark.modin.pandas as pd

#EWI: PNDSPY1002 => pandas.core.reshape.melt.melt has a partial mapping, with few scenarios not supported. Check Snowpark Pandas documentation for more detail.
pd.melt(df, id_vars=['A'], value_vars=['B'])
```

**Recommended fix**

Since this is a generic error code that applies to a range of partial supported functions, there is not a single and specific fix. The appropriate action will depend on the particular element in use.

Please note that even though the element is not supported in a few scenarios, it does not necessarily mean that a solution or workaround cannot be found. It only means that the SMA itself cannot find the solution.

## Additional recommendations

- Check the [Snowpark Pandas documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/supported/index) to verify which scenarios are not supported for that specific element.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
