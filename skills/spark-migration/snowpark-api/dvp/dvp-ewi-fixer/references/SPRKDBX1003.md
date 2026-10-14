# SPRKDBX1003

R cells are not supported.

Message: R cells are not supported in Snowsight.

Category: Conversion Error.

## Description

This issue appears when the SMA detects a cell with R code in a DBX notebook, Snowsight does not support R cells.
Only SQL, Python and Markdown are available in Snowsight.

## Scenario

**Input**

Below is an example of %r command.

```python
%r
my_vector <- c(1, 2, 3, 4, 5)
```

**Output**

The SMA adds the EWI `SPRKDBX1003` on the output code to let you know that this cell is not supported.

```python
# EWI: SPRKDBX1003 => R cells are not supported in Snowpark. It is necessary to rewrite the R code in Python.
# my_vector <- c(1, 2, 3, 4, 5)
```

**Recommended fix**

There is no direct fix for this issue. It is necessary to rewrite the R code in Python.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
