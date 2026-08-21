# SPRKDBX1000

Magic command is not supported.

Message: < ***Magic Name*** > is not supported in Snowsight. It is necessary to rewrite code.

Category: Conversion Error.

## Description

This issue appears when the SMA detects a magic command in a DBX notebook, Snowsight does not support magic commands.

## Scenario

**Input**

Below is an example of a magic command in a DBX notebook.

```python
# %alias myalias echo \"This is an alias\"
```

**Output**

The SMA adds the EWI `SPRKDBX1000` on the output code to let you know that this magic is not supported.

```python
# EWI: SPRKDBX1000 => Then %alias magic command is not supported in Snowsight. It is necessary to rewrite code.
# %alias myalias echo \"This is an alias\"
```

**Recommended fix**

There is no direct fix for this issue. It is necessary to rewrite the code.


## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
