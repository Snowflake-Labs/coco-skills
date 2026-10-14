# PNDSPY1007

pandas.core.series.Series.str.slice

Message: pandas.core.series.Series.str.slice has a partial mapping because has several scenarios not supported in Snowpark pandas.

Category: Warning.

## Description

This issue appears when the SMA identifies a [`pandas.core.series.Series.str.slice`](https://pandas.pydata.org/docs/reference/api/pandas.Series.str.slice.html) usage.
Snowpark Pandas offers a partial equivalence, but the current implementation has two scenarios not supported.

## Scenarios

### Scenario 1

The first scenario is when the method comes with mixed data type columns, making this method not behave as expected.
All values within a column must be of the same type.

**Input**

The example below illustrates the not supported use of `pandas.core.series.Series.str.slice`.

```python
import pandas as pd

s = pd.Series(["String", (1, 2, 3), ["a", "b", "c"], 123, -456, {1: "Hello", "2": "World"}])
print(s.str.slice(1))
```

**Output**

The SMA adds the EWI `PNDSPY1007` to the output code to indicate that has a scenario not supported in Snowpark Pandas.

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd

s = pd.Series(["String", (1, 2, 3), ["a", "b", "c"], 123, -456, {1: "Hello", "2": "World"}])
#EWI: PNDSPY1007 => pandas.core.series.Series.str.slice has a partial mapping,because has several scenarios not supported in Snowpark pandas.
print(s.str.slice(1))
```

**Recommended fix**

The recommended fix is to ensure that the Series contains only one type of data (all strings, all lists, or all dicts).
No code change is strictly required, but be aware that this operation may not work as expected in Snowpark Pandas.

### Scenario 2

The second scenario is the case when a column contains list values and the `step` parameter is set to a value other than one.

**Input**

The example below illustrates the not supported use of `pandas.core.series.Series.str.slice`.

```python
import pandas as pd

ser = pd.Series(["koala", "dog", "chameleon","cat", "mouse", "elephant","lion", "tiger", "bear"])
print(ser.str.slice(step=3))
```

**Output**

The SMA adds the EWI `PNDSPY1007` to the output code to indicate that has a scenario not supported in Snowpark Pandas.

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd

ser = pd.Series(["koala", "dog", "chameleon","cat", "mouse", "elephant","lion", "tiger", "bear"])
#EWI: PNDSPY1007 => pandas.core.series.Series.str.slice has a partial mapping, because has several scenarios not supported in Snowpark pandas.
print(ser.str.slice(step=3))
```

**Recommended fix**

Requires a manual change by using the function `[apply]`(https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/pandas_api/modin.pandas.Series.apply)
and trying to achieve the same behavior with a lambda function.
Here is the above output code with the recommended fix:

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd

ser = pd.Series(["koala", "dog", "chameleon","cat", "mouse", "elephant","lion", "tiger", "bear"])
print(ser.apply(lambda lst: lst[::3]))
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
