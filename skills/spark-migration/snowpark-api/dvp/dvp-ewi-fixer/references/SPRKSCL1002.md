# SPRKSCL1002

This code section has recovery from parsing errors.

Message: This code section has recovery from parsing errors ***statement***

Category: Parsing error.

## Description

This issue appears when the SMA detects some statement that cannot correctly read or understand in the code of a file, it is called as **parsing error**, however the SMA can recovery from that parsing error and continue analyzing the code of the file.
In this case, the SMA is able to process the code of the file without errors.

## Scenario

**Input**

Below is an example of invalid Scala code where the SMA can recovery.

```scala
Class myClass {

    def function1() & = { 1 }

    def function2() = { 2 }

    def function3() = { 3 }

}
```
**Output**

The SMA adds the EWI `SPRKSCL1002` to the output code to let you know that the code of the file has parsing errors, however the SMA can recovery from that error and continue analyzing the code of the file.

```scala
class myClass {

    def function1();//EWI: SPRKSCL1002 => Unexpected end of declaration. Failed token: '&' @(3,21).
    & = { 1 }

    def function2() = { 2 }

    def function3() = { 3 }

}
```
**Recommended fix**

Since the message pinpoint the error in the statement you can try to identify the invalid syntax and remove it or comment out that statement to avoid the parsing error.

```scala
Class myClass {

    def function1() = { 1 }

    def function2() = { 2 }

    def function3() = { 3 }

}
```

```scala
Class myClass {

    // def function1() & = { 1 }

    def function2() = { 2 }

    def function3() = { 3 }

}
```

## Additional recommendations

- Check that the code of the file is a valid Scala code.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
