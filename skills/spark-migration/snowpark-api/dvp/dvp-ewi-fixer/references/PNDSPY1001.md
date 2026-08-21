# PNDSPY1001

Element is not supported

Message: < ***element*** > is not supported, Pandas element is not supported yet.

Category: Conversion error.

## Description

This issue appears when the SMA detects the use of a Pandas element that is not supported in Snowpark Pandas, and does not have it's own error code associated with it. This is the generic error code used by the SMA for an unsupported element.

## Scenario

**Input**

Below is an example of a Spark element that is not supported by Snowpark, and therefore it generates this EWI.

```python
import pandas as pd

pd.not_supported_function()
```

**Output**

The SMA adds the EWI `PNDSPY1001` to the output code to let you know that this element is not supported by Snowpark.

```python
import snowflake.snowpark.modin.pandas as pd

#EWI: PNDSPY1001 => pandas.not_supported_function is not supported
pd.not_supported_function()
```

**Recommended fix**

Since this is a generic error code that applies to a range of unsupported functions, there is not a single and specific fix. The appropriate action will depend on the particular element in use.

Please note that even though the element is not supported, it does not necessarily mean that a solution or workaround cannot be found. It only means that the SMA itself cannot find the solution.


## Additional recommendations

- If you believe that Snowpark Pandas already supports this element or that there is some kind of workaround, please report that you encountered a conversion error on that particular element using [the Report an Issue option](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue) in the SMA and include any additional information that you think may be helpful.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
