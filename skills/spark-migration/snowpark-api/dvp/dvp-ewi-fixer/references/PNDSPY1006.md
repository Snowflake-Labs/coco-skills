# PNDSPY1006

pandas.core.series.Series.apply

Message: pandas.core.series.Series.apply has a partial mapping because Snowpark pandas does not support a non-callable function as parameter.

Category: Warning.

## Description

This issue appears when the SMA identifies a [`pandas.core.series.Series.apply`](https://pandas.pydata.org/docs/reference/api/pandas.Series.apply.html) usage.
Snowpark Pandas offers a partial equivalent, but it does not support callable functions as parameter.

## Scenario

**Input**

The example below illustrates the not supported use of `pandas.core.series.Series.apply`.

```python
import pandas as pd

ser = pd.Series([20, 21, 12], index=['London', 'New York', 'Helsinki'])
print(ser.apply(5))
```

**Output**

The SMA adds the EWI `PNDSPY1006` to the output code to indicate that has a scenario not supported in Snowpark Pandas.

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd

ser = pd.Series([20, 21, 12], index=['London', 'New York', 'Helsinki'])
#EWI: PNDSPY1006 => pandas.core.series.Series.apply has a partial mapping, because Snowpark pandas does not support callable functions as parameter.
print(ser.apply(5))
```

**Recommended fix**

The recommended fix is to ensure the function used within the apply method is callable.

```python
import pandas as pd

def my_function(x):
    return x * 5

ser = pd.Series([20, 21, 12], index=['London', 'New York', 'Helsinki'])
print(ser.apply(my_function))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
