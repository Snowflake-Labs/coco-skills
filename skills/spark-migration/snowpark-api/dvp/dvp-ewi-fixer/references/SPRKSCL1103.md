# SPRKSCL1103

The method on SparkBuilder method chaining is not supported.

> This issue code has been **deprecated**

Message: SparkBuilder method is not supported ***method name***

Category: Conversion Error

## Description

This issue appears when the SMA detects a method that is not supported by Snowflake in the SparkBuilder method chaining.
Therefore, it might affects the migration of the reader statement.

The following are the not supported SparkBuilder methods:
- master
- appName
- enableHiveSupport
- withExtensions

## Scenario

**Input**

Below is an example of a SparkBuilder method chaining with many methods are not supported by Snowflake.

```scala
val spark = SparkSession.builder()
           .master("local")
           .appName("testApp")
           .config("spark.sql.broadcastTimeout", "3600")
           .enableHiveSupport()
           .getOrCreate()
```
**Output**

The SMA adds the EWI `SPRKSCL1103` to the output code to let you know that master, appName and enableHiveSupport methods are not supported by Snowpark. Then, it might affects the migration of the Spark Session statement.

```scala
val spark = Session.builder.configFile("connection.properties")
/*EWI: SPRKSCL1103 => SparkBuilder Method is not supported .master("local")*/
/*EWI: SPRKSCL1103 => SparkBuilder Method is not supported .appName("testApp")*/
/*EWI: SPRKSCL1103 => SparkBuilder method is not supported .enableHiveSupport()*/
.create
```

**Recommended fix**

To create the session is required to add the proper Snowflake Snowpark configuration.

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
