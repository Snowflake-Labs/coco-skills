# SPRKDBX1004

The method '< ***element*** >' has no equivalence on Snowflake/Snowsight.

Message: The method '< ***element*** >' has no equivalence on Snowflake/Snowsight.

Category: Conversion Error.

## Description

This issue appears when the SMA detects the use of a DBX method that has no equivalence in Snowsight,
and does not have it's own error code associated with it. This is the generic error code used by the SMA for an unsupported DBX element.

## Scenario

**Input**

Below is an example of DBX utility element.

```python
dbutils.data.summarize(df)
```

**Output**

The SMA adds the EWI `SPRKDBX1004` on the output code to let you know that the method has no equivalence in Snowsight.

```python
# EWI: SPRKPY1004 => The method 'dbutils.data.summarize ' has no equivalence on Snowflake/Snowsight.
dbutils.data.summarize(df)
```

**Recommended fix**

Since this is a generic error code that applies to a range of unsupported functions, there is not a single and specific fix. The appropriate action will depend on the particular element in use.

Please note that even though the element is not supported, it does not necessarily mean that a solution or workaround cannot be found. It only means that the SMA itself cannot find the solution.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
