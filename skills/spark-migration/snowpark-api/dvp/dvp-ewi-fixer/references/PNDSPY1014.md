# PNDSPY1014

pandas.core.series.Series.aggregate

Message: pandas.core.series.Series.aggregate has a partial mapping because there is a not supported scenario in Snowpark pandas.

Category: Warning.

## Description

This issue appears when the SMA identifies a [`pandas.core.series.Series.aggregate`](https://pandas.pydata.org/docs/reference/api/pandas.Series.aggregate.html) usage.
Snowpark pandas currently has limitations with `Series.aggregate`. Check [`Supported Aggregation Functions`](https://github.com/snowflakedb/snowpark-python/blob/main/docs/source/modin/supported/agg_supp.rst) for a list of supported functions.

## Scenarios

**Input**

The example below illustrates the not supported use of `pandas.core.series.Series.aggregate`.

```python
import pandas as pd
import numpy as np

s = pd.Series([1, 2, 3, 4])
s.aggregate(['min', 'max'])
```

**Output**

The SMA adds the EWI `PNDSPY1014` to the output code to indicate that has a scenario not supported in Snowpark Pandas.

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd
import numpy as np

df = pd.DataFrame([[1, 2, 3],
                       [4, 5, 6],
                       [7, 8, 9],
                       [np.nan, np.nan, np.nan]],
                      columns=['A', 'B', 'C'])
#EWI: PNDSPY1014 => pandas.core.series.Series.aggregate does not support some combinations of parameters for specific aggregate functions. Check Snowpark Pandas documentation for more detail.
df.aggregate(['sum', 'min'])
```

**Recommended fix**

Since this is an error that applies to a range of partial supported aggregate functions, there is not a specific fix. The appropriate action will depend on the particular aggregate function in use.

Please note that even though the element is not supported in a few scenarios, it does not necessarily mean that a solution or workaround cannot be found. It only means that the SMA itself cannot find the solution.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
