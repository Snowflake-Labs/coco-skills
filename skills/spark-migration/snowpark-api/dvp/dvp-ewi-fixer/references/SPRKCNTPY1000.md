# SPRKCNTPY1000

Element is not supported

Message: The element < ***element*** > is not supported for Snowpark Connect

Category: Conversion error.

## Description

This issue appears when the SMA detects the use of a `Spark element for Python` that is not supported in `Snowpark Connect`, and does not have its own error code associated with it. This is the generic error code used by the SMA for an unsupported element.

## Scenario

**Input**

Below is an example of a Python Spark element that is not supported by Snowpark Connect, and therefore it generates this EWI.

```python
from pyspark import NotSupportedElement
sc.addFile("data.txt")
print(NotSupportedElement.get("data.txt"))
```

**Output**

The SMA adds the EWI `SPRKCNTPY1000` to the output code to let you know that this element is not supported by Snowpark Connect.

```python
from pyspark import NotSupportedElement
sc.addFile("data.txt")
# EWI SPRKCNTPY1000: The element 'NotSupportedElement' is not supported for Snowpark Connect
print(NotSupportedElement.get("data.txt"))
```

**Recommended fix**

Since this is a generic error code that applies to a range of unsupported functions, there is not a single and specific fix. The appropriate action will depend on the particular element in use.

Please note that even though the element is not supported, it does not necessarily mean that a solution or workaround cannot be found. It only means that the SMA itself cannot find the solution.

## Additional recommendations

- If you believe that Snowpark Connect already supports this element or that there is some kind of workaround, please report that you encountered a conversion error on that particular element using [the Report an Issue option](https://docs.snowflake.com/en/migrations/sma-docs/user-guide/project-overview/configuration-and-settings#report-an-issue) in the SMA and include any additional information that you think may be helpful.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
