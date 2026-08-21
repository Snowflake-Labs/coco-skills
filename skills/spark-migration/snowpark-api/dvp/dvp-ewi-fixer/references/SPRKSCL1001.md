# SPRKSCL1001

This code section has parsing errors

Message: This code section has parsing errors. The parsing error was found at: line ***line number***, column ***column number***. When trying to parse ***statement***. This file was not converted, so it is expected to still have references to the Spark API.

Category: Parsing error.

## Description

This issue appears when the SMA detects some statement that cannot correctly read or understand in the code of a file, it is called as **parsing error**.
Besides, this issue appears when a file has one or more parsing error(s).

## Scenario

**Input**

Below is an example of invalid Scala code.

```scala
/#/(%$"$%

Class myClass {

    def function1() = { 1 }

}
```

**Output**

The SMA adds the EWI `SPRKSCL1001` to the output code to let you know that the code of the file has parsing errors. Therefore, SMA is not able to process a file with this error.

```scala
// **********************************************************************************************************************
// EWI: SPRKSCL1001 => This code section has parsing errors
// The parsing error was found at: line 0, column 0. When trying to parse ''.
// This file was not converted, so it is expected to still have references to the Spark API
// **********************************************************************************************************************
/#/(%$"$%

Class myClass {

    def function1() = { 1 }

}
```

**Recommended fix**

Since the message pinpoint the error statement you can try to identify the invalid syntax and remove it or comment out that statement to avoid the parsing error.

```scala
Class myClass {

    def function1() = { 1 }

}
```

```scala
// /#/(%$"$%

Class myClass {

    def function1() = { 1 }

}
```

## Additional recommendations

- Check that the code of the file is a valid Scala code.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
