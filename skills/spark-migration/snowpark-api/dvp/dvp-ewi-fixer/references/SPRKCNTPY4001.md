# SPRKCNTPY4001

pyspark.context.SparkContext

Message: SparkContext instantiation has been converted to a Snowpark Connect session

Category: Warning

## Description

This issue appears when the SMA detects [pyspark.context.SparkContext](https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.SparkContext.html) constructor calls in your code. The SMA automatically transforms these instantiations into equivalent Snowpark Connect session calls, enabling your Spark applications to run on Snowflake's infrastructure.

The transformation process involves:
- Replacing SparkContext instantiation with `snowpark_connect.server.init_spark_session()`
- Preserving any existing SparkConf configuration parameters

> **Important**: Before running the converted code, you **must** configure your connection details in a `connections.toml` or `config.toml` file. This configuration file should contain your Snowflake account credentials, warehouse information, and other connection parameters required for Snowpark Connect to establish a connection to your Snowflake account.
>
> For comprehensive setup instructions, please refer to the [official Snowpark Connect documentation](https://docs.snowflake.com/en/developer-guide/snowpark-connect/snowpark-connect-workloads-jupyter#set-up-your-environment).

## Scenarios

### Scenario 1

**Input code**

SparkContext instantiated with default parameters:

```python
sc = SparkContext()
```

**Output code**

The SMA sets the environment variable, starts the Snowpark Connect session, and retrieves the session without additional configuration:

```python
#EWI: SPRKCNTPY4001 => SparkContext instantiation has been converted to a Snowpark Connect session
sc = snowpark_connect.server.init_spark_session()
```

### Scenario 2

**Input code**

SparkContext instantiated with master and appName parameters:

```python
sc = SparkContext(master="local[*]", appName="MyApp")
# or
sc = SparkContext("local[*]", "MyApp")
```

**Output code**

The SMA sets the environment variable, starts the Snowpark Connect session, and passes the parameters via a SparkConf object:

```python
conf = SparkConf()
conf.setAppName("MyApp")
conf.setMaster("local[*]")
#EWI: SPRKCNTPY4001 => SparkContext instantiation has been converted to a Snowpark Connect session
sc = snowpark_connect.server.init_spark_session(conf = conf)
```

### Scenario 3

**Input code**

SparkContext instantiated using an existing SparkConf object.

```python
my_conf = SparkConf()
sc = SparkContext(conf=my_conf)
```

**Output code**

The SMA preserves the existing SparkConf object and passes it directly to the `snowpark_connect.server.init_spark_session()` method:

```python
my_conf = SparkConf()
#EWI: SPRKCNTPY4001 => SparkContext instantiation has been converted to a Snowpark Connect session
sc = snowpark_connect.server.init_spark_session(conf = my_conf)
```

## Additional Recommendations

- While the SMA preserves your SparkConf settings, not all Spark configurations may be supported in Snowpark Connect. Review your configurations to ensure compatibility.
- Ensure that downstream operations using the SparkContext object are compatible with Snowpark Connect, as some Spark-specific functionalities may not have direct equivalents.
- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
