# SPRKPY1054

pyspark.sql.readwriter.DataFrameReader.format

Message: pyspark.sql.readwriter.DataFrameReader.format is not supported.

Category: Warning.

## Description

This issue appears when the [pyspark.sql.readwriter.DataFrameReader.format](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameReader.format.html) has an argument that is not supported by Snowpark.

## Scenarios

There are some scenarios depending on the type of format you are trying to load. It can be a `supported`, or `non-supported` format.


### Scenario 1

**Input**

The tool analyzes the type of format that is trying to load, the supported formats are:

- Csv
- JSON
- Parquet
- Orc

The below example shows how the tool transforms the `format` method when passing a `Csv` value. 


```python
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()

df1 = spark.read.format('csv').load('/path/to/file')
```

**Output**

The tool transforms the `format` method into a `Csv` method call. 

```python
from snowflake.snowpark import Session
spark = Session.builder.getOrCreate()

df1 = spark.read.csv('/path/to/file')
```

**Recommended fix**

In this case, the tool does not show the EWI, meaning there is no fix necessary.

### Scenario 2

**Input**

The below example shows how the tool transforms the `format` method when passing a `Jdbc` value. 


```python
from snowflake.snowpark import Session
spark = Session.builder.getOrCreate()

df2 = spark.read.format('jdbc') \
    .option("driver", "com.mysql.cj.jdbc.Driver") \
    .option("url", "jdbc:mysql://localhost:3306/emp") \
    .option("dbtable", "employee") \
    .option("user", "root") \
    .option("password", "root") \
    .load()
```

**Output**

The tool shows the EWI `SPRKPY1054` indicating that the value "jdbc" is not supported. 

```python
from snowflake.snowpark import Session
spark = Session.builder.getOrCreate()

#EWI: SPRKPY1054 => pyspark.sql.readwriter.DataFrameReader.format with argument value "jdbc" is not supported.
#EWI: SPRKPY1002 => pyspark.sql.readwriter.DataFrameReader.load is not supported

df2 = spark.read.format('jdbc') \
    .option("driver", "com.mysql.cj.jdbc.Driver") \
    .option("url", "jdbc:mysql://localhost:3306/emp") \
    .option("dbtable", "employee") \
    .option("user", "root") \
    .option("password", "root") \
    .load()
```

**Recommended fix**

For the `not supported` scenarios there is no specific fix since it depends on the files that are trying to be read.

### Scenario 3

**Input**

The below example shows how the tool transforms the `format` method when passing a `CSV`, but using a variable instead. 


```python
from snowflake.snowpark import Session
spark = Session.builder.getOrCreate()

myFormat = 'csv'
df3 = spark.read.format(myFormat).load('/path/to/file')
```

**Output**

Since the tool can not determine the value of the variable in runtime, shows the EWI `SPRKPY1054` indicating that the value "" is not supported. 

```python
from snowflake.snowpark import Session
spark = Session.builder.getOrCreate()

myFormat = 'csv'
#EWI: SPRKPY1054 => pyspark.sql.readwriter.DataFrameReader.format with argument value "" is not supported.
#EWI: SPRKPY1002 => pyspark.sql.readwriter.DataFrameReader.load is not supported
df3 = spark.read.format(myFormat).load('/path/to/file')
```

**Recommended fix**

As a workaround, you can check the value of the variable and add it as a string to the `format` call.

## Additional recommendations

- The Snowpark location only accepts cloud locations using a [snowflake stage](https://docs.snowflake.com/en/user-guide/data-load-local-file-system-create-stage).

- The documentation of methods supported by Snowpark can be found in the [documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.20.0/snowpark/api/snowflake.snowpark.DataFrameReader#snowflake.snowpark.DataFrameReader)

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
