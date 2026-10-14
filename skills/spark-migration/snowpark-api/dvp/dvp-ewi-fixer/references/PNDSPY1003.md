# PNDSPY1003

Element is not recognized

Message: < ***element*** > is not recognized, Pandas element is not recognized yet.

Category: Conversion error.

## Description

This issue appears when there is a Pandas element that is not yet recognized by the SMA.

## Scenarios

This issue can occur for different reasons, such as:
- An element that does not exist in Pandas.
- An element that was added in a Pandas version that the SMA does not yet support.
- An internal error of the SMA when processing the element.

### Scenario 1

**Input**

Below is an example of an element that does not exist in Pandas.

```python
import pandas as pd
​
df = pd.DataFrame(
    {
        "Name": ["Alice", "Bob", "Charlie"],
        "Age": [25, 30, 35],
        "City": ["New York", "Los Angeles", "Chicago"],
    }
)
​
df.non_existent_function()
```

**Output**

Since the element does not exist in Pandas, the tool adds the EWI on the output code.

```python
import snowflake.snowpark.modin.pandas as pd
​
df = pd.DataFrame(
    {
        "Name": ["Alice", "Bob", "Charlie"],
        "Age": [25, 30, 35],
        "City": ["New York", "Los Angeles", "Chicago"],
    }
)
​
#EWI: PNDSPY1003 => pandas.core.frame.DataFrame.non_existent_function is not yet recognized
df.non_existent_function()
```

**Recommended fix**

Check the [Pandas documentation](https://pandas.pydata.org/docs/reference/index.html) to verify if the element exists in Pandas.

If it is a valid Pandas element, please report that you encountered a conversion error on that particular element using [the Report an Issue option](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue) in the SMA and include any additional information that you think may be helpful.

If it is not a valid Pandas element removed and use a valid Pandas function.
```python
import snowflake.snowpark.modin.pandas as pd
​
df = pd.DataFrame(
    {
        "Name": ["Alice", "Bob", "Charlie"],
        "Age": [25, 30, 35],
        "City": ["New York", "Los Angeles", "Chicago"],
    }
)
​
df.valid_existent_function()
```

### Scenario 2

**Input**

Below is an example of an element that was added in a Pandas version that the SMA does not yet support.

```python
import pandas as pd
​
df = pd.DataFrame(
    {
        "Name": ["Alice", "Bob", "Charlie"],
        "Age": [25, 30, 35],
        "City": ["New York", "Los Angeles", "Chicago"],
    }
)
​
df.valid_function_since_x.x.x_version()
```

**Output**

Since the element was added in a Pandas version that the tool does not support yet, the tool adds the EWI on the output code.

```python
import snowflake.snowpark.modin.pandas as pd
​
df = pd.DataFrame(
    {
        "Name": ["Alice", "Bob", "Charlie"],
        "Age": [25, 30, 35],
        "City": ["New York", "Los Angeles", "Chicago"],
    }
)
​
#EWI: PNDSPY1003 => pandas.core.frame.DataFrame.valid_function_since_x.x.x_version is not yet recognized
df.valid_function_since_x.x.x_version()
```

**Recommended fix**

Please verify the [Snowpark Pandas documentation](https://docs.snowflake.com/developer-guide/snowpark/reference/python/latest/modin/index), if it is a valid Pandas element, please report that you encountered a conversion error on that particular element using [the Report an Issue option](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue) in the SMA and include any additional information that you think may be helpful.

### Scenario 3

**Input**

Below is an example of an internal error of the SMA when processing the element.

```python
import pandas as pd
​
df = pd.DataFrame(
    {
        "Name": ["Alice", "Bob", "Charlie"],
        "Age": [25, 30, 35],
        "City": ["New York", "Los Angeles", "Chicago"],
    }
)
​
df.valid_function()
```

**Output**

If it was an error while processing the element and that cause the tool can't recognize, the tool adds the EWI on the output code.

```python
import snowflake.snowpark.modin.pandas as pd
​
df = pd.DataFrame(
    {
        "Name": ["Alice", "Bob", "Charlie"],
        "Age": [25, 30, 35],
        "City": ["New York", "Los Angeles", "Chicago"],
    }
)
​
#EWI: PNDSPY1003 => pandas.core.frame.DataFrame.valid_function is not yet recognized
df.valid_function()
```

**Recommended fix**

Verify if the element exists in the [Pandas documentation](https://pandas.pydata.org/docs/reference/index.html) and also check the [Snowpark Pandas documentation](https://docs.snowflake.com/developer-guide/snowpark/reference/python/latest/modin/index).
If it is a valid Pandas element, please report that you encountered a conversion error on that particular element using [the Report an Issue option](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue) in the SMA and include any additional information that you think may be helpful.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
