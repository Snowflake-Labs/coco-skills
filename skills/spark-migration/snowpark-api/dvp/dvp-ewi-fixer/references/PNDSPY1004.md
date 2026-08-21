# PNDSPY1004

Element has a direct mapping, but it's restricted to running on a single node. As a result, performance may be impacted,
especially with large datasets.

Message: Pandas < ***element*** > has a direct mapping, but it's restricted to running on a single node. As a result, performance may be impacted,
especially with large datasets.

Category: Warning.

## Description

This issue appears when the SMA detects the use of a Pandas element that has a direct equivalent in Snowpark Pandas,
but it's restricted to running on a single node, so it's performance may be impacted.

## Scenario

**Input**

Below is an example of a method that runs on a single node.

```python
import pandas as pd

ser = pd.Series([1, 2, 3, 3])
ser.plot(kind='hist', title="My plot")
```

**Output**

The SMA adds the EWI `PNDSPY1004` on the output code to let you know that this element is running on a single node, and it's performance may be impacted.

```python
import snowflake.snowpark.modin.pandas as pd

ser = pd.Series([1, 2, 3, 3])
#EWI: PNDSPY1004 => pandas.core.series.Series.plot has a direct mapping, but it's restricted to running on a single node. As a result, performance may be impacted,
especially with large datasets.
ser.plot(kind='hist', title="My plot")
```

**Recommended fix**

Since this is a generic error code that applies to a range of partial supported functions, there is not recommended fix.

## Additional recommendations

- Check the [Snowpark Pandas documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/supported/index) to verify the pandas elements that running on a single node.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
