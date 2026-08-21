# SPRKPY1002

Element is not supported

Message: < element > is not supported, Spark element is not supported.

Category: Conversion error.

## Description

This issue appears when the tool detects the usage of an element that is not supported in Snowpark, and does not have it's own error code associated with it. This is the generic error code used by the SMA for an unsupported element.


## Additional recommendations

- Even though the option or the element on the message is not supported, this does not mean that a solution cannot be found. It only means that the tool itself cannot find the solution.

- If you have encountered an unsupported element from a pyspark.ml library, consider some alternative approached. There are additional guides available to walkthrough issues related to ml such as this one from Snowflake.

- Check if the source code has the correct syntax. (You can use the issues.csv file to determine where the conversion error(s) are occurring.) If the syntax is correct, report that you encountered a conversion error on a particular element using the Report an Issue option in the SMA. Include the line of code that was causing the error in the description when you file this issue.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
