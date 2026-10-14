# PNDSPY1009

pandas.core.frame.DataFrame.apply

Message: pandas.core.frame.DataFrame.apply has a partial mapping because has several scenarios not supported in Snowpark pandas.

Category: Warning.

## Description

This issue appears when the SMA identifies a [`pandas.core.frame.DataFrame.apply`](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.apply.html) usage.
Snowpark Pandas offers a partial equivalence, but the current implementation has two scenarios not supported.

## Scenarios

### Scenario 1

Snowpark pandas `DataFrame.apply` API doesn't support yet `result_type` parameter.

**Input**

The example below illustrates the not supported use of `pandas.core.frame.DataFrame.apply`.

```python
import pandas as pd

df = pd.DataFrame({"A": [1, 2], "B": [3, 4], "C": [5, 6]})
df.apply(np.mean, axis=1, result_type="expand")
```

**Output**

The SMA adds the EWI `PNDSPY1009` to the output code to indicate that has a scenario not supported in Snowpark Pandas.

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd

df = pd.DataFrame({"A": [1, 2], "B": [3, 4], "C": [5, 6]})
#EWI: PNDSPY1007 => pandas.core.frame.DataFrame.apply has a partial mapping, because has several scenarios not supported in Snowpark pandas.
df.apply(np.mean, axis=1, result_type="expand")
```

**Recommended fix**

The recommended fix is to ensure that the `apply` method does not contain the `result_type` parameter.

### Scenario 2

Snowpark pandas `DataFrame.apply` API doesn't support yet `DataFrame` or `Series` as `args` or `kwargs` parameters.

**Input**

The example below illustrates the not supported use of `pandas.core.frame.DataFrame.apply`.

```python
import pandas as pd

df = pd.DataFrame({"A": [1, 2], "B": [3, 4], "C": [5, 6]})
ser = pd.Series([10, 20])

def custom_func(row, ser):
    return row["A"] + ser[row.name]

df.apply(custom_func, axis=1, args=(ser, ))
```

**Output**

The SMA adds the EWI `PNDSPY1009` to the output code to indicate that has a scenario not supported in Snowpark Pandas.

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd

df = pd.DataFrame({"A": [1, 2], "B": [3, 4], "C": [5, 6]})
ser = pd.Series([10, 20])

def custom_func(row, ser):
    return row["A"] + ser[row.name]

#EWI: PNDSPY1009 => pandas.core.frame.DataFrame.apply has a partial mapping, because has several scenarios not supported in Snowpark pandas.
df.apply(custom_func, axis=1, args=(ser, ))
```

**Recommended fix**

For this scenario, the recommended fix is to use the `values` attribute of the Series and create a new column in the
DataFrame to hold the Series values, then use the `apply` method without passing the `Series` as an argument.

```python
import pandas as pd

ser = pd.Series([10, 20])

df = pd.DataFrame({"A": [1, 2], "B":[3, 4], "C": [5, 6]})
df["extra"] = ser.values

def custom_func(row):
    return row["A"] + row["extra"]

df.apply(custom_func, axis=1)
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
