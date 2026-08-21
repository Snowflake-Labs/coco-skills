# SPRKSCL1104

org.apache.spark.sql.SparkSession.Builder.config

> This issue code has been **deprecated**

Message: Spark Session builder option is not supported.

Category: Conversion Error.

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.SparkSession.Builder.config](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/SparkSession$$Builder.html#config(conf:org.apache.spark.SparkConf):org.apache.spark.sql.SparkSession.Builder) function, which is setting an option of the Spark Session and it is not supported by Snowpark.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.SparkSession.Builder.config` function used to set an option in the Spark Session.

```scala
val spark = SparkSession.builder()
           .master("local")
           .appName("testApp")
           .config("spark.sql.broadcastTimeout", "3600")
           .getOrCreate()
```
**Output**

The SMA adds the EWI `SPRKSCL1104` to the output code to let you know config method is not supported by Snowpark. Then, it is not possible to set options in the Spark Session via config function and it might affects the migration of the Spark Session statement.

```scala
val spark = Session.builder.configFile("connection.properties")
/*EWI: SPRKSCL1104 => SparkBuilder Option is not supported .config("spark.sql.broadcastTimeout", "3600")*/
.create()
```

**Recommended fix**

To create the session is require to add the proper Snowflake Snowpark configuration.

In this example a configs variable is used.

```scala
    val configs = Map (
      "URL" -> "https://<myAccount>.snowflakecomputing.com:<port>",
      "USER" -> <myUserName>,
      "PASSWORD" -> <myPassword>,
      "ROLE" -> <myRole>,
      "WAREHOUSE" -> <myWarehouse>,
      "DB" -> <myDatabase>,
      "SCHEMA" -> <mySchema>
    )
    val session = Session.builder.configs(configs).create
```

Also is recommended the use of a configFile (profile.properties) with the connection information:

```config
# profile.properties file (a text file)
URL = https://<account_identifier>.snowflakecomputing.com
USER = <username>
PRIVATEKEY = <unencrypted_private_key_from_the_private_key_file>
ROLE = <role_name>
WAREHOUSE = <warehouse_name>
DB = <database_name>
SCHEMA = <schema_name>
```

And with the `Session.builder.configFile` the session can be created:

```scala
val session = Session.builder.configFile("/path/to/properties/file").create
```

## Additional recommendations

- [Developer guide for create a session.](https://docs.snowflake.com/en/developer-guide/snowpark/scala/creating-session)

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
