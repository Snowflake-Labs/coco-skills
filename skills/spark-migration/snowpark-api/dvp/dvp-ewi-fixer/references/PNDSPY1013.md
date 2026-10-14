# PNDSPY1013

pandas.core.frame.DataFrame.aggregate

Message: pandas.core.frame.DataFrame.aggregate has a partial mapping because there is a not supported scenario in Snowpark pandas.

Category: Warning.

## Description

This issue appears when the SMA identifies a [`pandas.core.frame.DataFrame.aggregate`](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.aggregate.html) usage.
Snowpark pandas currently has limitations with `DataFrame.aggregate`. Check [`Supported Aggregation Functions`](https://github.com/snowflakedb/snowpark-python/blob/main/docs/source/modin/supported/agg_supp.rst) for a list of supported functions.

## Scenarios

**Input**

The example below illustrates the not supported use of `pandas.core.frame.DataFrame.aggregate`.

```python
import pandas as pd
import numpy as np

df = pd.DataFrame([[1, 2, 3],
                       [4, 5, 6],
                       [7, 8, 9],
                       [np.nan, np.nan, np.nan]],
                      columns=['A', 'B', 'C'])
df.aggregate(['sum', 'min'])
```

**Output**

The SMA adds the EWI `PNDSPY1013` to the output code to indicate that has a scenario not supported in Snowpark Pandas.

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd
import numpy as np

df = pd.DataFrame([[1, 2, 3],
                       [4, 5, 6],
                       [7, 8, 9],
                       [np.nan, np.nan, np.nan]],
                      columns=['A', 'B', 'C'])
#EWI: PNDSPY1013 => pandas.core.frame.DataFrame.aggregate does not support some combinations of parameters for specific aggregate functions. Check Snowpark Pandas documentation for more detail.
df.aggregate(['sum', 'min'])
```

**Recommended fix**

Since this is an error that applies to a range of partial supported aggregate functions, there is not a specific fix. The appropriate action will depend on the particular aggregate function in use.

Please note that even though the element is not supported in a few scenarios, it does not necessarily mean that a solution or workaround cannot be found. It only means that the SMA itself cannot find the solution.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
