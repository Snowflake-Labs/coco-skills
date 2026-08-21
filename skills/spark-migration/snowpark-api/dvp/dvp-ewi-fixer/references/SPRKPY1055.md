# SPRKPY1055

pyspark.sql.readwriter.DataFrameReader.option

Message: pyspark.sql.readwriter.DataFrameReader.option key value is not supported.

Category: Warning.

## Description

This issue appears when the [pyspark.sql.readwriter.DataFrameReader.option](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrameWriter.option.html) key value is not supported by SnowFlake.

The tool analyzes the option call parameters and depends on the method (CSV or JSON or PARQUET) the key value might
have or not have an equivalent in Snowpark, if all the parameters have an equivalent, the tool does not add the EWI,
and it replaces the key value for his equivalent, otherwise, the tool adds the EWI.

**List of equivalences:**

- Equivalences for CSV:

| Spark option keys | Snowpark Equivalences        |
|-------------------|------------------------------|
| sep               | FIELD_DELIMITER              |
| header            | PARSE_HEADER                 |
| lineSep           | RECORD_DELIMITER             |
| pathGlobFilter    | PATTERN                      |
| quote             | FIELD_OPTIONALLY_ENCLOSED_BY |
| nullValue         | NULL_IF                      |
| dateFormat        | DATE_FORMAT                  |
| timestampFormat   | TIMESTAMP_FORMAT             |
| inferSchema       | INFER_SCHEMA                 |
| delimiter         | FIELD_DELIMITER              |


- Equivalences for JSON:

| Spark option keys | Snowpark Equivalences |
|-------------------|-----------------------|
| dateFormat        | DATE_FORMAT           |
| timestampFormat   | TIMESTAMP_FORMAT      |
| pathGlobFilter    | 	PATTERN              |

- Equivalences for PARQUET:

| Spark option keys | Snowpark Equivalences |
|-------------------|-----------------------|
| pathGlobFilter    | 	PATTERN              |

Any other key option that's not in one of the tables above,
are not supported or doesn't have an equivalent in Snowpark. If that's the case,
the tool adds the EWI with the parameter information and removes it from the chain.

## Scenarios

> The below scenarios apply for CSV, JSON, and PARQUET.

There are a couple of scenarios depending on the value of the key used in the `option` method.

### Scenario 1

**Input**

Below is an example of a `option` call using a `equivalent key`.

```python
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

# CSV example:
spark.read.option("header", True).csv(csv_file_path)

# Json example:
spark.read.option("dateFormat", "dd-MM-yyyy").json(json_file_path)

# Parquet example:
spark.read.option("pathGlobFilter", "*.parquet").parquet(parquet_file_path)
```

**Output**

The tool transforms the key with the correct equivalent.

```python
from snowflake.snowpark import Session

spark = Session.builder.getOrCreate()

# CSV example:
spark.read.option("PARSE_HEADER", True).csv(csv_file_path)

# Json example:
spark.read.option("DATE_FORMAT", "dd-MM-yyyy").json(json_file_path)

# Parquet example:
spark.read.option("PATTERN", "*.parquet").parquet(parquet_file_path)
```

**Recommended fix**

Since the tool transforms the value of the key, there is no necessary fix.

### Scenario 2

**Input**

Below is an example of a `option` call using a `non-equivalent key`.

```python
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

# CSV example:
spark.read.option("anotherKeyValue", "myVal").csv(csv_file_path)

# Json example:
spark.read.option("anotherKeyValue", "myVal").json(json_file_path)

# Parquet example:
spark.read.option("anotherKeyValue", "myVal").parquet(parquet_file_path)
```

**Output**

The tool adds the EWI `SPRKPY1055` indicating the key is not supported and removes the `option` call.

```python
from snowflake.snowpark import Session

spark = Session.builder.getOrCreate()

# CSV example:
#EWI: SPRKPY1055 => pyspark.sql.readwriter.DataFrameReader.option with key value "anotherKeyValue" is not supported.
spark.read.csv(csv_file_path)

# Json example:
#EWI: SPRKPY1055 => pyspark.sql.readwriter.DataFrameReader.option with key value "anotherKeyValue" is not supported.
spark.read.json(json_file_path)

# Parquet example:
#EWI: SPRKPY1055 => pyspark.sql.readwriter.DataFrameReader.option with key value "anotherKeyValue" is not supported.
spark.read.parquet(parquet_file_path)
```

**Recommended fix**

It is recommended to check the behavior after the transformation.

## Additional recommendations

- When non-equivalent parameters are present, it is recommended to check the behavior after the transformation.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
