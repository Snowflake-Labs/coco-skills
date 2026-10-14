# PNDSPY1005

pandas.core.series.Series.str.get

Message: pandas.core.series.Series.str.get has a partial mapping because in one scenario it has a different behavior in Snowpark.

Category: Warning.

## Description

This issue appears when the SMA identifies a [`pandas.core.series.Series.str.get`](https://pandas.pydata.org/docs/reference/api/pandas.Series.str.get.html) usage.
Snowpark Pandas offers a partial equivalent, but when it comes columns with mixed data types
the method may not behave as expected. All values within a column must be of the same type.

## Scenario

**Input**

The example below illustrates the not supported use of `pandas.core.series.Series.str.get`.

```python
import pandas as pd

s = pd.Series(["String", (1, 2, 3), ["a", "b", "c"], 123, -456, {1: "Hello", "2": "World"}])
print(s.str.get(1))
```

**Output**

The SMA adds the EWI `PNDSPY1005` to the output code to indicate that in one scenario it has a different behavior in Snowpark.

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd

s = pd.Series(["String", (1, 2, 3), ["a", "b", "c"], 123, -456, {1: "Hello", "2": "World"}])
#EWI: PNDSPY1005 => pandas.core.series.Series.str.get has a partial mapping, because in one scenario it has a different behavior in Snowpark.
print(s.str.get(1))
```

**Recommended fix**

The recommended fix is to ensure that the Series contains only one type of data (all strings, all lists, or all dicts).
No code change is strictly required, but be aware that this operation may not work as expected in Snowpark Pandas.


## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
