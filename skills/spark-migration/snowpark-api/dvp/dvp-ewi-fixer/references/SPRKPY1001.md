# SPRKPY1001

File with parsing errors

Message: This code section has parsing errors

Category: Parsing error.

## Description

A parsing error is reported by the Snowpark Migration Accelerator (SMA) when it cannot correctly read or understand the code in a file (it cannot correctly "parse" the file). This issue code appears when a file has one or more parsing error(s).

## Scenario
**Input:**

The EWI message appears when the code has invalid syntax, for example:
```python
def foo():
    x = %%%%%%1###1
```

**Output:**

SMA find a parsing error and comment the parsing error adding the corresponding EWI message:

```python
def foo():
    x
# EWI: SPRKPY1101 => Unrecognized or invalid CODE STATEMENT @(2, 7). Last valid token was 'x' @(2, 5), failed token '=' @(2, 7)
#      = %%%%%%1###1
```

## Additional recommendations

- Check that the file contains valid Python code. (You can use the issues.csv file to find all files with this EWI code to determine which file(s) were not processed by the tool due to parsing error(s).) Many parsing errors occur because only part of the code is input into the tool, so it's bets to ensure that the code will run in the source. If it is valid, report that you encountered a parsing error using the Report an Issue option [in the SMA](https://app.gitbook.com/o/-MB4z_O8Sl--Tfl3XVml/s/6on4bNAZUZGzMpdEum8X/~/changes/410/user-guide/project-overview/configuration-and-settings#report-an-issue). Include the line of code that was causing a parsing error in the description when you file this issue.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
