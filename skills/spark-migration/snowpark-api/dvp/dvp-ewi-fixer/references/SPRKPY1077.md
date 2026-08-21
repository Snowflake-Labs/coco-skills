# SPRKPY1077
Pyspark.SQL

Message: SQL embedded code cannot be processed.

Category: Warning.

## Description

This issue appears when the tool detects an SQL-embedded code that cannot be converted to Snowpark.

Check the SQL-embedded code section for more information.

## Scenario

**Input**

In this example the SQL code is embedded on a variable called query that is used as parameter for the Pyspark.sql method.
```python
query = f"SELECT * from myTable"
spark.sql(query)
```

**Output**

SMA detects that the PySpark.sql parameter is a variable and not a SQL Code, so the EWI SPRKPY1077 message is added to the PySpark.sql line.
```python
query = f"SELECT * myTable"
#EWI: SPRKPY1077 => SQL embedded code cannot be processed.
spark.sql(query)
```

## Additional recommendations

- For the transformation of SQL, this code must be directly inside as parameter of the method only as string values and without interpolation. Please check the SQL send to the PySpark.SQL function to validate it's functionality on Snowflake.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
