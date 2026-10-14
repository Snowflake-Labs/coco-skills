# PNDSPY1017

pandas.core.reshape.encoding.get_dummies

Message: pandas.core.reshape.encoding.get_dummies has a partial mapping because there is a not supported scenario in Snowpark pandas.

Category: Warning.

## Description

This issue appears when the SMA identifies a [`pandas.core.reshape.encoding.get_dummies`](https://pandas.pydata.org/docs/reference/api/pandas.get_dummies.html) usage.
Snowpark pandas currently has limitations with `Pandas.get_dummies`. It is supported if parameters "dummy_na" and "drop_first" are both false, otherwise it is not supported.

## Scenarios

**Input**

The example below illustrates the not supported use of `pandas.core.reshape.encoding.get_dummies`.

```python
import pandas as pd
import numpy as np

s1 = ['a', 'b', np.nan]
pd.get_dummies(s1, dummy_na=True)

s2 = list('abcaa')
pd.get_dummies(s2, drop_first=True)
```

**Output**

The SMA adds the EWI `PNDSPY1017` to the output code to indicate that has a scenario not supported in Snowpark Pandas.

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd
import numpy as np

s1 = ['a', 'b', np.nan]
#EWI: PNDSPY1017 => pandas.core.reshape.encoding.get_dummies is supported if parameters "dummy_na" and "drop_first" are both false, otherwise it is not supported. Check Snowpark Pandas documentation for more detail.
pd.get_dummies(s1, dummy_na=True)

s2 = list('abcaa')
#EWI: PNDSPY1017 => pandas.core.reshape.encoding.get_dummies is supported if parameters "dummy_na" and "drop_first" are both false, otherwise it is not supported. Check Snowpark Pandas documentation for more detail.
pd.get_dummies(s2, drop_first=True)
```

**Recommended fix**

***For the `dummy_na` parameter***
This requires a manual adjustment.
Step 1, replace the `np.nan` value for a acceptable value such as `'np.nan'`.
Step 2, remove the use of the parameter `dummy_na`.
Step 3, rename the column `'np.nan'` to the original `np.nan` value.
To illustrate the recommended fix, here is the output code with the changes applied:
```python
s1 = s1.replace(np.nan, 'np.nan') if isinstance(s1, (pd.DataFrame, pd.Series)) else ['np.nan' if pd.isna(item) else item for item in s1]
pd.get_dummies(s1).rename(columns={'np.nan': np.nan})
```

***For the `drop_first` parameter***
This requires a manual adjustment.
Step 1, remove the use of the parameter `drop_first`.
Step 2, remove the first column of the result (you can use the `iloc` indexer for it).
To illustrate the recommended fix, here is the output code with the changes applied:
```python
pd.get_dummies(s2).iloc[:, 1:]
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
