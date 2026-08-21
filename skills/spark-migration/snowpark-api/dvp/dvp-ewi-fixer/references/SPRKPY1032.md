# SPRKPY1032

Element is not defined

Message: ***spark element*** is not defined

Category: Conversion error

## Description

This issue appears when the SMA could not determine an appropriate mapping status for the given element. This means, the SMA doesn't know yet if this element is supported or not by Snowpark. Please note, this is a generic error code used by the SMA for any not defined element.

## Scenario

**Input**

Below is an example of a function for which the SMA could not determine an appropriate mapping status. In this case, you should assume that `not_defined_function()` is a valid PySpark function and the code runs.

```python
sc.parallelize(["a", "b", "c", "d", "e"], 3).not_defined_function().collect()
```

**Output**

The SMA adds the EWI `SPRKPY1032` to the output code to let you know that this element is not defined.

```python
#EWI: SPRKPY1032 => pyspark.rdd.RDD.not_defined_function is not defined
sc.parallelize(["a", "b", "c", "d", "e"], 3).not_defined_function().collect()
```

**Recommended fix**

To try to identify the problem, you can perform the following validations:
  - Check if it is a valid PySpark element.
  - Check if the element has the correct syntax and it is spelled correctly.
  - Check if you are using a PySpark version supported by the SMA.

If this is a valid PySpark element, please report that you encountered a conversion error on that particular element using the [Report an Issue](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue) option of the SMA and include any additional information that you think may be helpful.

Please note that if an element is not defined, it does not mean that it is not supported by Snowpark. You should check the [Snowpark Documentation](https://docs.snowflake.com/developer-guide/snowpark/reference/python/latest/snowpark/index) to verify if an equivalent element exist.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
