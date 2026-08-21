# PNDSPY1018

Element defaults to single node pandas execution

Message: < ***element*** > defaults to single node pandas execution via UDF/Sproc.

Category: Warning.

## Description

This issue appears when the SMA identifies a Pandas element that is supported in Snowpark Pandas but defaults to single node pandas execution via UDF/Sproc instead of distributed execution.

This means the operation will work correctly, but it may have performance implications for large datasets as it will be executed locally on a single node rather than being distributed across Snowflake's compute resources.

## Scenario

**Input**

Below is an example of a Pandas element that defaults to single node execution.

```python
import pandas as pd

df = pd.DataFrame({'A': [1, 2, 3], 'B': [4, 5, 6]})
result = df.some_method()  # This method defaults to UDF/Sproc execution
```

**Output**

The SMA adds the EWI `PNDSPY1018` to the output code to let you know that this element defaults to single node pandas execution.

```python
from snowflake.snowpark.modin import plugin
import modin.pandas as pd

df = pd.DataFrame({'A': [1, 2, 3], 'B': [4, 5, 6]})
#EWI: PNDSPY1018 => Element defaults to single node pandas execution via UDF/Sproc.
result = df.some_method()
```

**Recommended fix**

No immediate fix is required. The code will execute correctly. However, be aware that:

- **Performance impact**: Operations may be slower for large datasets since they run on a single node instead of being distributed across Snowflake's compute cluster.
- **Memory limitations**: Single node execution is subject to memory constraints of a single worker.
- **Scalability**: For very large datasets, consider alternative approaches that leverage distributed execution.

If performance is critical for this operation, consider:
1. Breaking down the operation into smaller, distributable steps
2. Using native Snowpark functions where available
3. Pre-filtering data to reduce the dataset size before applying the operation

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
