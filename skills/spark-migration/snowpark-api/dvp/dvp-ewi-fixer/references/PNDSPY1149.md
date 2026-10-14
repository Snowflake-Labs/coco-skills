# PNDSPY1149

Element has a partial mapping but there are a few scenarios not supported in Snowpark.

Message: Pandas < ***pandas.core.indexes.datetimes.DatetimeIndex.tz_convert*** > has a partial mapping with a few scenarios not supported in Snowpark.

Category: Warning.

## Description

This issue appears when the SMA detects the use of a Pandas element that has a direct equivalent in Snowpark Pandas, but some scenarios might behave differently than Pandas.


**Reason (from Snowpark documentation):** N if timezone format is not supported. Only timezones listed in pytz.all_timezones are supported. For example, UTC is supported but UTC+/-<offset>, such as, UTC+09:00 is not supported.

## Scenario

**Input**

Below is an example of a method with a few scenarios not supported in Snowpark.

```python
import pandas as pd

idx = pd.DatetimeIndex(['2023-01-01', '2023-02-01', '2023-03-01'])
result = idx.tz_convert()
```

**Output**

The SMA adds the EWI `PNDSPY1149` on the output code to let you know that this element has a few scenarios not supported in Snowpark.

```python
import snowflake.snowpark.modin.pandas as pd

#EWI: PNDSPY1149 => pandas.core.indexes.datetimes.DatetimeIndex.tz_convert has a partial mapping, with few scenarios not supported. Check Snowpark Pandas documentation for more detail.
idx = pd.DatetimeIndex(['2023-01-01', '2023-02-01', '2023-03-01'])
result = idx.tz_convert()
```

**Recommended fix**

**Timezone handling difference**: N if timezone format is not supported. Only timezones listed in pytz.all_timezones are supported. For example, UTC is supported but UTC+/-<offset>, such as, UTC+09:00 is not supported.

When working with timezones in Snowpark Pandas:
- Ensure your timezone strings are valid IANA timezone names (e.g., 'UTC', 'America/New_York')
- Test timezone conversions with sample data before running on full dataset
- Consider using `.to_pandas()` for complex timezone operations if results differ

## Additional recommendations

- Check the [Snowpark Pandas documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/supported/index) to verify which scenarios are not supported for that specific element.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
