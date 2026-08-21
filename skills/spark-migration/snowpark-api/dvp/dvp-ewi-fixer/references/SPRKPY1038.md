# SPRKPY1038

Element is not yet recognized

Message: ***spark element*** is not yet recognized

Category: Conversion error

## Description

This issue appears when there is a PySpark element in your source code that was not recognized by the SMA. This can occur for different reasons, such as:
  - An element that does not exist in PySpark.
  - An element that was added in a PySpark version that the SMA does not support yet.
  - An internal error of the SMA when processing the element.

This is a generic error code used by the SMA for any not recognized element.

## Scenario

**Input**

Below is an example of a use of a function that could not be recognized by the SMA because it does not exist in PySpark.

```python
from pyspark.sql import functions as F
F.unrecognized_function()
```

**Output**

The SMA adds the EWI `SPRKPY1038` to the output code to let you know that this element could not be recognized.

```python
from snowflake.snowpark import functions as F
#EWI: SPRKPY1038 => pyspark.sql.functions.non_existent_function is not yet recognized
F.unrecognized_function()
```

**Recommended fix**

To try to identify the problem, you can perform the following validations:
  - Check if the element exists in PySpark.
  - Check if the element is spelled correctly.
  - Check if you are using a PySpark version supported by the SMA. To know which PySpark version is supported by the SMA at the moment of running the SMA, you can review the first page of the `DetailedReport.docx` file.

If it is a valid PySpark element, please report that you encountered a conversion error on that particular element using the [Report an Issue](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue) option of the SMA and include any additional information that you think may be helpful.

Please note that if an element could not be recognized by the SMA, it does not mean that it is not supported by Snowpark. You should check the [Snowpark Documentation](https://docs.snowflake.com/developer-guide/snowpark/reference/python/latest/snowpark/index) to verify if an equivalent element exist.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
