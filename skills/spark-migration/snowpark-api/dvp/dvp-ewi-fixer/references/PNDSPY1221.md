# PNDSPY1221

Element has a partial mapping but there are a few scenarios not supported in Snowpark.

Message: Pandas < ***pandas.core.strings.accessor.StringMethods.len*** > has a partial mapping with a few scenarios not supported in Snowpark.

Category: Warning.

## Description

This issue appears when the SMA detects the use of a Pandas element that has a direct equivalent in Snowpark Pandas, but some scenarios might behave differently than Pandas.


**Reason (from Snowpark documentation):** For the column data type, only string, list, and dict values are supported. All column values must be of the same type.

## Scenario

**Input**

Below is an example of a method with a few scenarios not supported in Snowpark.

```python
import pandas as pd

s = pd.Series(['abc', 'def', 'ghi'])
result = s.str.len
```

**Output**

The SMA adds the EWI `PNDSPY1221` on the output code to let you know that this element has a few scenarios not supported in Snowpark.

```python
import snowflake.snowpark.modin.pandas as pd

#EWI: PNDSPY1221 => pandas.core.strings.accessor.StringMethods.len has a partial mapping, with few scenarios not supported. Check Snowpark Pandas documentation for more detail.
s = pd.Series(['abc', 'def', 'ghi'])
result = s.str.len
```

**Recommended fix**

**Data type consideration**: For the column data type, only string, list, and dict values are supported. All column values must be of the same type.

Ensure data types are compatible:
- Check column dtypes with `df.dtypes` before the operation
- Use `.astype()` to convert columns to expected types
- Numeric operations may require explicit casting: `df['col'].astype(float)`

## Additional recommendations

- Check the [Snowpark Pandas documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/supported/index) to verify which scenarios are not supported for that specific element.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
