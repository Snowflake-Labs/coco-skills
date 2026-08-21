# PNDSPY1016

pandas.core.series.Series.interpolate

Message: pandas.core.series.Series.interpolate has a partial mapping because there is a not supported scenario in Snowpark pandas.

Category: Warning.

## Description

This issue appears when the SMA identifies a [`pandas.core.series.Series.interpolate`](https://pandas.pydata.org/docs/reference/api/pandas.Series.interpolate.html) usage.
Snowpark pandas currently has limitations with `Series.interpolate`. It is not supported if axis == 1, limit is set, limit_area is "outside", or method is not "linear", "bfill", "backfill", "ffill", or "pad". And limit_area="inside" is supported only when method is linear.

## Scenarios

**Input**

The example below illustrates the not supported use of `pandas.core.series.Series.interpolate`.

```python
import pandas as pd
import numpy as np

s = pd.Series([0, 2, np.nan, 8])
s.interpolate(method='polynomial', order=2)
```

**Output**

The SMA adds the EWI `PNDSPY1016` to the output code to indicate that has a scenario not supported in Snowpark Pandas.

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd
import numpy as np

df = pd.DataFrame([(0.0, np.nan, -1.0, 1.0),
                   (np.nan, 2.0, np.nan, np.nan),
                   (2.0, 3.0, np.nan, 9.0),
                   (np.nan, 4.0, -4.0, 16.0)],
                  columns=list('abcd'))
#EWI: PNDSPY1016 => pandas.core.series.Series.interpolate is not support if axis == 1, limit is set, limit_area is "outside", or method is not "linear", "bfill", "backfill", "ffill", or "pad". And limit_area="inside" is supported only when method is linear. Check Snowpark Pandas documentation for more detail.
df['d'].interpolate(method='polynomial', order=2)
```

**Recommended fix**

Since this is an error that applies to a range of partial supported parameters, there is not a specific fix. The appropriate action will depend on the particular parameter combination in use.

Please note that even though the element is not supported in a few scenarios, it does not necessarily mean that a solution or workaround cannot be found. It only means that the SMA itself cannot find the solution.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
