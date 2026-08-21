# PNDSPY1012

pandas.core.frame.DataFrame.query

Message: pandas.core.frame.DataFrame.query has a partial mapping because there is an unsupported scenario in Snowpark pandas.

Category: Warning.

## Description

This issue arises when the SMA detects the use of [`pandas.core.frame.DataFrame.query`](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.query.html).
While this method is commonly used for filtering data in pandas DataFrames, Snowpark pandas currently has limitations in supporting it.
Specifically, it does not support DataFrames that have a row MultiIndex, which can lead to compatibility issues during migration or execution.


## Scenarios

**Input**

The `query()` method does not support DataFrames with a row MultiIndex in Snowpark pandas, here is a code example that demonstrates both scenarios:

```python
import modin.pandas as pd  # Snowpark pandas

# Create a DataFrame with single-level index
data = {
    'name': ['Alice', 'Bob', 'Charlie', 'David', 'Eve', 'Frank'],
    'age': [25, 30, 35, 28, 32, 45],
    'salary': [50000, 60000, 75000, 55000, 80000, 90000],
    'department': ['Sales', 'IT', 'HR', 'Sales', 'IT', 'HR']
}

df = pd.DataFrame(data)

# Set a single-level index
df = df.set_index('name')

print("DataFrame with single-level index:")
print(df)

# Use query() - This works fine!
result = df.query("age > 30 and salary < 85000")


# Create a DataFrame with MultiIndex on rows
data = {
    'A': [1, 2, 3, 4, 5, 6],
    'B': [10, 20, 30, 40, 50, 60],
    'C': ['x', 'y', 'x', 'y', 'x', 'y']
}

df = pd.DataFrame(data)

# Create MultiIndex
df = df.set_index([
    pd.Index(['group1', 'group1', 'group2', 'group2', 'group3', 'group3']),
    pd.Index(['a', 'b', 'a', 'b', 'a', 'b'])
])
df.index.names = ['group', 'subgroup']

# This will ERROR in Snowpark pandas!
result = df.query("A > 2 and B < 55")
```

**Output**

The SMA adds the EWI `PNDSPY1012` to the output code to indicate that it has a scenario not supported in Snowpark Pandas.

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd  # Snowpark pandas

# Create a DataFrame with single-level index
data = {
    'name': ['Alice', 'Bob', 'Charlie', 'David', 'Eve', 'Frank'],
    'age': [25, 30, 35, 28, 32, 45],
    'salary': [50000, 60000, 75000, 55000, 80000, 90000],
    'department': ['Sales', 'IT', 'HR', 'Sales', 'IT', 'HR']
}

df = pd.DataFrame(data)

# Set a single-level index
df = df.set_index('name')

print("DataFrame with single-level index:")
print(df)

# Use query() - This works fine!
#EWI: PNDSPY1012 => pandas.core.frame.DataFrame.query does not support DataFrames that have a row MultiIndex. Check Snowpark Pandas documentation for more detail.
result = df.query("age > 30 and salary < 85000")


# Create a DataFrame with MultiIndex on rows
data = {
    'A': [1, 2, 3, 4, 5, 6],
    'B': [10, 20, 30, 40, 50, 60],
    'C': ['x', 'y', 'x', 'y', 'x', 'y']
}

df = pd.DataFrame(data)

# Create MultiIndex
df = df.set_index([
    pd.Index(['group1', 'group1', 'group2', 'group2', 'group3', 'group3']),
    pd.Index(['a', 'b', 'a', 'b', 'a', 'b'])
])
df.index.names = ['group', 'subgroup']

# This will ERROR in Snowpark pandas!
#EWI: PNDSPY1012 => pandas.core.frame.DataFrame.query does not support DataFrames that have a row MultiIndex. Check Snowpark Pandas documentation for more detail.
result = df.query("A > 2 and B < 55")
```

**Recommended fix**

If the DataFrame contains a MultiIndex, it is necessary to validate the behavior of the `query()` method in Snowpark pandas. Ensure that the DataFrame structure is compatible with Snowpark pandas' limitations, as MultiIndex rows are not supported. Consider restructuring the DataFrame to use a single-level index or alternative filtering methods.


## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
