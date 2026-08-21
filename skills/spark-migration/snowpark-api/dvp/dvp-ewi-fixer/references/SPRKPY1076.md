# SPRKPY1076
pyspark.sql.readwriter.DataFrameReader

Message: Parameters in [pyspark.sql.readwriter.DataFrameReader](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameReader.html) methods are not supported. This applies to CSV, JSON and PARQUET methods.

Category: Warning.

## Description

For the CSV, JSON and PARQUET methods on the [pyspark.sql.readwriter.DataFrameReader](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameReader.html) object, the tool will analyze the parameters and add a transformation according to each case:

- All the parameters match their equivalent name in Snowpark: in this case, the tool will transform the parameter into a .option() call. For this case, the parameter won't add this EWI.

- Some parameters do not match the equivalent in Snowpark: in this case, the tool will add this EWI with the parameter information and remove it from the method call.

**List of equivalences:**

- Equivalences for CSV:

| Spark keys      | Snowpark Equivalences         |
|-----------------|-------------------------------|
| sep             | FIELD_DELIMITER               |
| header          | PARSE_HEADER                  |
| lineSep         | 	RECORD_DELIMITER             |
| pathGlobFilter  | 	PATTERN                      |
| quote           | 	FIELD_OPTIONALLY_ENCLOSED_BY |
| nullValue       | 	NULL_IF                      |
| dateFormat      | 	DATE_FORMAT                  |
| timestampFormat | 	TIMESTAMP_FORMAT             |
| inferSchema     | 	INFER_SCHEMA                 |
| delimiter       | 	FIELD_DELIMITER              |


- Equivalences for JSON:

| Spark keys      | Snowpark Equivalences |
|-----------------|-----------------------|
| dateFormat      | DATE_FORMAT           |
| timestampFormat | TIMESTAMP_FORMAT      |
| pathGlobFilter  | 	PATTERN              |

- Equivalences for PARQUET:

| Spark keys      | Snowpark Equivalences |
|-----------------|-----------------------|
| pathGlobFilter  | 	PATTERN              |

## Scenarios

### Scenario 1
**Input**

For CVS here are some examples:
```python
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName('myapp').getOrCreate()

spark.read.csv("path3", None,None,None,None,None,None,True).show()

```
**Output**

In the converted code the parameters are added as individual options to the cvs function
```python
from snowflake.snowpark import Session

spark = Session.builder.app_name('myapp', True).getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})

#EWI: SPRKPY1076 => Some of the included parameters are not supported in the csv function, the supported ones will be added into a option method.
spark.read.option("FIELD_DELIMITER", None).option("PARSE_HEADER", True).option("FIELD_OPTIONALLY_ENCLOSED_BY", None).csv("path3").show()
```

## Scenario 2

**Input**

For JSON here are some example:
```python
from pyspark.sql import SparkSession
spark = SparkSession.builder.appName('myapp').getOrCreate()
spark.read.json("/myPath/jsonFile/", dateFormat='YYYY/MM/DD').show()
```
**Output**

In the converted code the parameters are added as individual options to the json function
```python
from snowflake.snowpark import Session
spark = Session.builder.app_name('myapp', True).getOrCreate()
#EWI: SPRKPY1076 => Some of the included parameters are not supported in the json function, the supported ones will be added into a option method.

spark.read.option("DATE_FORMAT", 'YYYY/MM/DD').json("/myPath/jsonFile/").show()
```

### Scenario 3
**Input**

For PARQUET here are some examples:
```python
from pyspark.sql import SparkSession
spark = SparkSession.builder.appName('myapp').getOrCreate()

spark.read.parquet("/path/to/my/file.parquet", pathGlobFilter="*.parquet").show()

```

**Output**

In the converted code the parameters are added as individual options to the parquet function
```python
from snowflake.snowpark import Session

spark = Session.builder.app_name('myapp', True).getOrCreate()
spark.update_query_tag({"origin":"sf_sit","name":"sma","version":{"major":0,"minor":0,"patch":0},"attributes":{"language":"Python"}})

#EWI: SPRKPY1076 => Some of the included parameters are not supported in the parquet function, the supported ones will be added into a option method.
#EWI: SPRKPY1029 => The parquet function require adjustments, in Snowpark the parquet files needs to be located in an stage. See the documentation for more info.

spark.read.option("PATTERN", "*.parquet").parquet("/path/to/my/file.parquet")
```

## Additional recommendations

- When non-equivalent parameters are present, it is recommended to check the behavior after the transformation.
- Also the documentation could be useful to find a better fit:
  - Options documentation for CSV:
    - [PySpark CSV Options](https://spark.apache.org/docs/latest/sql-data-sources-csv.html#data-source-option).
    - [Snowpark CSV Options](https://docs.snowflake.com/en/sql-reference/sql/create-file-format#type-csv).
  - Options documentation for JSON:
    - [PySpark JSON Options](https://spark.apache.org/docs/latest/sql-data-sources-json.html).
    - [Snowpark JSON Options](https://docs.snowflake.com/en/sql-reference/sql/create-file-format#type-json).
  - Options documentation for PARQUET:
    - [Pyspark PARQUET options](https://spark.apache.org/docs/latest/sql-data-sources-parquet.html#data-source-option).
    - [SnowPark PARQUET options.](https://docs.snowflake.com/en/sql-reference/sql/create-file-format#type-parquet).

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
