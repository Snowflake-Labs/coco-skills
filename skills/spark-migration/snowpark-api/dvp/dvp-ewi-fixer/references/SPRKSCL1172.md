# SPRKSCL1172

org.apache.spark.sql.types.StructField.apply

Message: Snowpark does not support StructFiled with metadata parameter.

Category: Warning

## Description

This issue appears when the SMA detects that [org.apache.spark.sql.types.StructField.apply](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/types/StructField.html) with
[org.apache.spark.sql.types.Metadata](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/types/Metadata.html) as parameter. This is because Snowpark does not supported the metadata parameter.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.types.StructField.apply` function that generates this EWI. In this example, the `apply` function is used to generate and instance of StructField.


```scala
val result = StructField("f1", StringType(), True, metadata)
```

**Output**

The SMA adds the EWI `SPRKSCL1172` to the output code to let you know that metadata parameter is not supported by Snowflake.

```scala
/*EWI: SPRKSCL1172 => Snowpark does not support StructFiled with metadata parameter.*/
val result = StructField("f1", StringType(), True, metadata)
```

**Recommended fix**

Snowpark has an equivalent [com.snowflake.snowpark.types.StructField.apply](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/types/StructField$.html#apply(name:String,dataType:com.snowflake.snowpark.types.DataType):com.snowflake.snowpark.types.StructField) function that receives three parameters. Then, as workaround, you can try to remove the metadata argument.

```scala
val result = StructField("f1", StringType(), True, metadata)
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
