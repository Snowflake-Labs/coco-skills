# PNDSPY1011

pandas.core.groupby.generic.DataFrameGroupBy.resample

Message: pandas.core.groupby.generic.DataFrameGroupBy.resample has a partial mapping because there is a not supported scenario in Snowpark pandas.

Category: Warning.

## Description

This issue appears when the SMA identifies a [`pandas.core.groupby.generic.DataFrameGroupBy.resample`](https://pandas.pydata.org/docs/reference/api/pandas.core.groupby.DataFrameGroupBy.resample.html) usage.
Snowpark pandas currently has limitations with `DataFrameGroupBy.resample`. The `rule` parameter only supports `s`, `min`, `h`, and `D` as frequency values.

## Scenarios

**Input**

The example below illustrates the not supported use of `pandas.core.groupby.generic.DataFrameGroupBy.resample`.

```python
import pandas as pd

df = pd.DataFrame({
        "category": ["A", "A", "B", "B"],
        "date": pd.to_datetime(["2023-01-01", "2023-01-15", "2023-01-01", "2023-01-20"]),
        "value": [10, 20, 30, 40]
    })

df = df.set_index("date")
df.groupby("category").resample("ME").sum()
```

**Output**

The SMA adds the EWI `PNDSPY1011` to the output code to indicate that has a scenario not supported in Snowpark Pandas.

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd

df = pd.DataFrame({
        "category": ["A", "A", "B", "B"],
        "date": pd.to_datetime(["2023-01-01", "2023-01-15", "2023-01-01", "2023-01-20"]),
        "value": [10, 20, 30, 40]
    })

df = df.set_index("date")
#EWI: PNDSPY1011 => pandas.core.groupby.generic.DataFrameGroupBy.resample has a partial mapping because there is a not supported scenario in Snowpark pandas.
df.groupby("category").resample("ME").sum()
```

**Recommended fix**

This requires a manual adjustment, you can use the `pd.Grouper` method to create a new column that represents the period you want to group by, and then use the `groupby` method.
To illustrate the recommended fix, here is the output code with the changes applied:

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd

df = pd.DataFrame({
        "category": ["A", "A", "B", "B"],
        "date": pd.to_datetime(["2023-01-01", "2023-01-15", "2023-01-01", "2023-01-20"]),
        "value": [10, 20, 30, 40]
    })

df = df.set_index("date")
df.groupby(["category", pd.Grouper(freq="ME")]).sum()
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
