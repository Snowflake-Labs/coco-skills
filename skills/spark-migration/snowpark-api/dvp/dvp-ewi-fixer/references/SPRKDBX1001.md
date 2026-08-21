# SPRKDBX1001

The %run command has a partial mapping, because it has a different behavior in Snowpark.

Message: The %run command has a partial mapping, because it has a different behavior in Snowpark.

Category: Conversion Error.

## Description

This issue appears when the SMA detects the use of the %run command that does not have a direct equivalent in Snowpark.
The way the %run command works in DBX is similar to an import, meaning it includes the code
from another notebook into the current one, sharing variables, functions and states with the notebook
where the command was executed.

## Scenario

**Input**

Below is an example the %run command.

```python
%run /Workspace/Users/path/to/Notebook/notebookName
```

**Output**

The SMA adds the EWI `SPRKDBX1001` on the output code to let you know that this element has a different behavior in Snowpark.

```python
EWI: SPRKDBX1001 => The %run command has a partial mapping, because it has a different behavior in Snowpark.
spark.sql("EXECUTE NOTEBOOK <DATABASE>.<SCHEMA>.notebookName()")
```

**Recommended fix**

There is a recommended fix, it would be to identify the used elements (functions, variables) from the executed notebook
and encapsulate them into a file. Then import that file into the executing notebook.

This fix would emulate the behavior of the original `%run` command.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
