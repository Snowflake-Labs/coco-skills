# SPRKPY1051

pyspark.sql.session.SparkSession.Builder.master

> This issue code has been **deprecated** since [Spark Conversion Core Version 2.4.0](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-2.4.0)
 
Message: pyspark.sql.session.SparkSession.Builder.master has a workaround

Category: Warning.

## Description

This issue appears when the tool detects [pyspark.sql.session.SparkSession.Builder.master](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.SparkSession.builder.master.html)  usage which has a workaround.

## Scenario

**Input**

Below is an example of the usage of the method `builder.master` to set the Spark Master URL to connect to local using 1 core.

```python
spark = SparkSession.builder.master("local[1]") 
```

**Output**

The tool adds the EWI `SPRKPY1051` indicating that a workaround can be implemented.

```python
#EWI: SPRKPY1051 => pyspark.sql.session.SparkSession.Builder.master has a workaround, see documentation for more info
spark = Session.builder.master("local[1]") 
```

**Recommended fix** 

`pyspark.sql.session.SparkSession.Builder.master` is used to set up a Spark Cluster. Snowpark doesn't use Spark Clusters so you can remove or comment the code. 

```python
# spark = Session.builder.master("local[1]") 
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).