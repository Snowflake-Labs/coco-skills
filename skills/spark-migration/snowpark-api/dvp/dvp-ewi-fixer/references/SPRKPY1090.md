# SPRKPY1090
pyspark.sql.types.StructType.fieldNames

Message: The returned list of field names is in uppercase and is not case-sensitive.

Category: Warning

## Description

Field names are returned as a list of strings. The names are uppercase and differ from the original field names. This can cause issues if the field names are case-sensitive.

## Scenario

**Input**

This snippet shows how the field names are printed with case sensitivity.
```python
# Input Code
from pyspark.sql.types import StringType, StructField, StructType

struct_type = StructType(
    [
        StructField("f1_L_a", StringType(), True),
    ]
)
field_names = struct_type.fieldNames()
print(field_names)
```

**Output**

When evaluating the output code, the field names in the struct type are in uppercase and differ from the original field names. This can cause issues if field names are case-sensitive. An EWI message indicates that the field names are in uppercase.
```python
from snowflake.snowpark.types import StringType, StructField, StructType

struct_type = StructType(
    [
        StructField("f1_L_a", StringType(), True),
    ]
)
#EWI: SPRKPY1090 => The returned list of field names is in uppercase.
field_names = struct_type.fieldNames
print(field_names)
```

**Recommended fix**

As of now there is not a direct fix for this difference. However, you can manually change the field names to upper cases to avoid any issues with case sensitivity.

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
