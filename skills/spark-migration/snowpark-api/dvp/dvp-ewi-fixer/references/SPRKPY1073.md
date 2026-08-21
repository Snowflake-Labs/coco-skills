# SPRKPY1073
pyspark.sql.functions.udf

Message: pyspark.sql.functions.udf without parameters or return type parameter are not supported

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.sql.functions.udf](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.udf.html) as function or decorator and is not supported in two specifics cases, when it has no parameters or return type parameter.

## Scenarios


### Scenario 1
**Input**

In Pyspark you can create an User Defined Function without input or return type parameters:

```Python
from pyspark.sql import SparkSession, DataFrameStatFunctions
from pyspark.sql.functions import col, udf

spark = SparkSession.builder.getOrCreate()
data = [['Q1', 'Test 1'],
        ['Q2', 'Test 2'],
        ['Q3', 'Test 1'],
        ['Q4', 'Test 1']]

columns = ['Quadrant', 'Value']
df = spark.createDataFrame(data, columns)

my_udf = udf(lambda s: len(s))
df.withColumn('Len Value' ,my_udf(col('Value')) ).show()
```

**Output**

Snowpark requires the input and return types for Udf function. Because they are not provided and SMA cannot this parameters.
```python
from snowflake.snowpark import Session, DataFrameStatFunctions
from snowflake.snowpark.functions import col, udf

spark = Session.builder.getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})
data = [['Q1', 'Test 1'],
        ['Q2', 'Test 2'],
        ['Q3', 'Test 1'],
        ['Q4', 'Test 1']]

columns = ['Quadrant', 'Value']
df = spark.createDataFrame(data, columns)
#EWI: SPRKPY1073 => pyspark.sql.functions.udf function without the return type parameter is not supported. See documentation for more info.
my_udf = udf(lambda s: len(s))

df.withColumn('Len Value' ,my_udf(col('Value')) ).show()
```

**Recommended fix**

To fix this scenario is required to add the import for the returns types of the input and output, and then the parameters of return_type and input_types[] on the [udf](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.udf) function _my_udf_.

```python
from snowflake.snowpark import Session, DataFrameStatFunctions
from snowflake.snowpark.functions import col, udf
from snowflake.snowpark.types import IntegerType, StringType

spark = Session.builder.getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})
data = [['Q1', 'Test 1'],
        ['Q2', 'Test 2'],
        ['Q3', 'Test 1'],
        ['Q4', 'Test 1']]

columns = ['Quadrant', 'Value']
df = spark.createDataFrame(data, columns)

my_udf = udf(lambda s: len(s), return_type=IntegerType(), input_types=[StringType()])

df.with_column("result", my_udf(df.Value)).show()
```

### Scenario 2

In PySpark you can use a @udf decorator without parameters

**Input**
```python
from pyspark.sql.functions import col, udf

spark = SparkSession.builder.getOrCreate()
data = [['Q1', 'Test 1'],
        ['Q2', 'Test 2'],
        ['Q3', 'Test 1'],
        ['Q4', 'Test 1']]

columns = ['Quadrant', 'Value']
df = spark.createDataFrame(data, columns)

@udf()
def my_udf(str):
    return len(str)


df.withColumn('Len Value' ,my_udf(col('Value')) ).show()
```

**Output**

In Snowpark all the parameters of a [udf](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.udf) decorator are required.

```python
from snowflake.snowpark import Session, DataFrameStatFunctions
from snowflake.snowpark.functions import col, udf
from snowflake.snowpark.types import IntegerType, StringType

spark = Session.builder.getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})
data = [['Q1', 'Test 1'],
        ['Q2', 'Test 2'],
        ['Q3', 'Test 1'],
        ['Q4', 'Test 1']]

columns = ['Quadrant', 'Value']
df = spark.createDataFrame(data, columns)

#EWI: SPRKPY1073 => pyspark.sql.functions.udf decorator without parameters is not supported. See documentation for more info.

@udf()
def my_udf(str):
    return len(str)

df.withColumn('Len Value' ,my_udf(col('Value')) ).show()
```

**Recommended fix**

To fix this scenario is required to add the import for the returns types of the input and output, and then the parameters of return_type and input_types[] on the [udf](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.udf) @udf decorator.

```python
from snowflake.snowpark.functions import col, udf
from snowflake.snowpark.types import IntegerType, StringType

spark = Session.builder.getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})
data = [['Q1', 'Test 1'],
        ['Q2', 'Test 2'],
        ['Q3', 'Test 1'],
        ['Q4', 'Test 1']]

columns = ['Quadrant', 'Value']
df = spark.createDataFrame(data, columns)

@udf(return_type=IntegerType(), input_types=[StringType()])
def my_udf(str):
    return len(str)

df.withColumn('Len Value' ,my_udf(col('Value')) ).show()
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
