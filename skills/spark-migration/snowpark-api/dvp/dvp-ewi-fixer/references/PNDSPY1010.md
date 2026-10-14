# PNDSPY1010

pandas.core.groupby.grouper.Grouper

Message: pandas.core.groupby.grouper.Grouper has a partial mapping because there is a not supported scenario in Snowpark pandas.

Category: Warning.

## Description

This issue appears when the SMA identifies a [`pandas.core.groupby.grouper.Grouper`](https://pandas.pydata.org/docs/reference/api/pandas.Grouper.html) usage.
Snowpark pandas currently has limitations with `Grouper` parameters. It does not support `origin`, `offset`, `dropna`, or `closed`.

## Scenarios

**Input**

The example below illustrates the not supported use of `pandas.core.groupby.grouper.Grouper`.

```python
import pandas as pd

df = pd.DataFrame({
        "date": pd.to_datetime([
            "2023-01-01", "2023-01-02", "2023-01-03", None, "2023-01-05", "2023-01-06", None
        ]),
        "value": [0, 1, 2, 3, 4, 5, 6]
    })

df.groupby(pd.Grouper(key="date", freq="3D", origin="epoch" offset="1D", dropna=True)).sum()
```

**Output**

The SMA adds the EWI `PNDSPY1010` to the output code to indicate that has a scenario not supported in Snowpark Pandas.

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd

df = pd.DataFrame({
        "date": pd.to_datetime([
            "2023-01-01", "2023-01-02", "2023-01-03", None, "2023-01-05", "2023-01-06", None
        ]),
        "value": [0, 1, 2, 3, 4, 5, 6]
    })

#EWI: PNDSPY10010 => pandas.core.groupby.grouper.Grouper has a partial mapping, because there is a not supported scenario in Snowpark pandas.
df.groupby(pd.Grouper(key="date", freq="3D", origin="epoch" offset="1D", dropna=True)).sum()
```

**Recommended fix**

This requires a manual adjustment based on the parameters used in the `Grouper` method, essentially mimicking its behavior:

- Sort and Dropna: These parameters can be replaced with the ones in the [`groupby`](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/pandas_api/modin.pandas.DataFrame.groupby) method .
- Offset and Origin: You can use `pd.Timedelta` to represents these values and manually adjust the datetime column by subtracting the `offset` or `origin` before using `groupby`.

The `gruopby` does not have a frequency parameter, so you can use the `pd.Timedelta` to create a new column that represents the period you want to group by.

To illustrate the recommended fix, here is the output code with the changes applied:
```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd

df = pd.DataFrame({
        "date": pd.to_datetime([
            "2023-01-01", "2023-01-02", "2023-01-03", None, "2023-01-05", "2023-01-06", None
        ]),
        "value": [0, 1, 2, 3, 4, 5, 6]
    })

# To simulate the behavior of the Grouper with freq="3D"
freq = pd.Timedelta("3D")

# In this case we will use the epoch as the origin
origin = pd.Timestamp("1970-01-01")

# If you are using an offset, you can adjust the origin accordingly
origin += pd.Timedelta("1D")  # Example offset of 1 day

# Now you have to adjust your datetime column by subtracting the origin
df["period"] = origin + ((df["date"] - origin) // freq) * freq
result = df.groupby("period", dropna=True)["value"].sum()
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
