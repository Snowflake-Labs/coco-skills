# SPRKPY1101

This code section has recovery from parsing errors.

# Category

Parsing error.

### Description

When the tool recognizes a parsing error, it tries to recover from it and continues the process in the next line. In those cases, it shows the error and comments on the line.

This example shows how a mismatch error between spaces and tabs is handled.

**Input code**

```python
def foo():
    x = 5 # Spaces
	y = 6 # Tab

def foo2():
    x=6
    y=7
```
**Output code**

```python
def foo():
    x = 5 # Spaces
# EWI: SPRKPY1101 => Unrecognized or invalid CODE STATEMENT @(3, 2). Last valid token was '5' @(2, 9), failed token 'y' @(3, 2)
#	y = 6 # Tab

def foo2():
    x=6
    y=7
```

### Recommendations

- Try fixing the commented line.


- For more support, email us at sma-support@snowflake.com. If you have a support contract with Snowflake, reach out to your sales engineer, who can direct your support needs.
