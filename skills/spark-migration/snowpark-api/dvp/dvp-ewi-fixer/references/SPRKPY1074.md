# SPRKPY1074

Mixed indentation symbols

Message: File has mixed indentation (spaces and tabs).

Category: Parsing error.

## Description

This issue appears when the tool detects the file has a mixed indentation. It means, file has a combination of spaces and tabs to indent code lines.

## Scenario

**Input**

In Pyspark you can mix spaces and tabs for the identation level.
```python
def foo():
    x = 5 # spaces
    y = 6 # tab
```

**Output**

SMA cannot handle mixed indentation symbols. When this is detected on a python code file SMA adds the EWI SPRKPY1074 on first line.
```python
# EWI: SPRKPY1074 => File has mixed indentation (spaces and tabs).
# This file was not converted, so it is expected to still have references to the Spark API
def foo():
    x = 5 # spaces
    y = 6 # tabs
```

**Recommended fix**

The solution is to make all the indentation symbols the same.

```python
def foo():
  x = 5 # tab
  y = 6 # tab
```


## Additional recommendations

- Useful indent tools [PEP-8](https://peps.python.org/pep-0008/) and [Reindent](https://pypi.org/project/reindent/).

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
