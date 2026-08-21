# SPRKSCL1102

org.apache.spark.sql.functions.explode

> This issue code has been **deprecated** since [Spark Conversion Core 2.3.22](https://docs.snowconvert.com/sma/general/release-notes)

Message: Explode is not supported

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.explode](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#explode(e:org.apache.spark.sql.Column):org.apache.spark.sql.Column) function, which is not supported by Snowpark.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.explode` function used to get the consolidated information of the array fields of the dataset.

```scala
    val explodeData = Seq(
      Row("Cat", Array("Gato","Chat")),
      Row("Dog", Array("Perro","Chien")),
      Row("Bird", Array("Ave","Oiseau"))
    )

    val explodeSchema = StructType(
      List(
        StructField("Animal", StringType),
        StructField("Translation", ArrayType(StringType))
      )
    )

    val rddExplode = session.sparkContext.parallelize(explodeData)

    val dfExplode = session.createDataFrame(rddExplode, explodeSchema)

    dfExplode.select(explode(dfExplode("Translation").alias("exploded")))
```
**Output**

The SMA adds the EWI `SPRKSCL1102` to the output code to let you know that this function is not supported by Snowpark.

```scala
    val explodeData = Seq(
      Row("Cat", Array("Gato","Chat")),
      Row("Dog", Array("Perro","Chien")),
      Row("Bird", Array("Ave","Oiseau"))
    )

    val explodeSchema = StructType(
      List(
        StructField("Animal", StringType),
        StructField("Translation", ArrayType(StringType))
      )
    )

    val rddExplode = session.sparkContext.parallelize(explodeData)

    val dfExplode = session.createDataFrame(rddExplode, explodeSchema)

    /*EWI: SPRKSCL1102 => Explode is not supported */
    dfExplode.select(explode(dfExplode("Translation").alias("exploded")))
```

**Recommended Fix**

Since explode is not supported by Snowpark, the function [flatten](https://docs.snowflake.com/developer-guide/snowpark/reference/scala/com/snowflake/snowpark/DataFrame.html#flatten(input:com.snowflake.snowpark.Column):com.snowflake.snowpark.DataFrame) could be used as a substitute.

The following fix creates flatten of the dfExplode dataframe, then makes the query to replicate the result in Spark.

```scala
    val explodeData = Seq(
      Row("Cat", Array("Gato","Chat")),
      Row("Dog", Array("Perro","Chien")),
      Row("Bird", Array("Ave","Oiseau"))
    )

    val explodeSchema = StructType(
      List(
        StructField("Animal", StringType),
        StructField("Translation", ArrayType(StringType))
      )
    )

    val rddExplode = session.sparkContext.parallelize(explodeData)

    val dfExplode = session.createDataFrame(rddExplode, explodeSchema)

     var dfFlatten = dfExplode.flatten(col("Translation")).alias("exploded")
                              .select(col("exploded.value").alias("Translation"))

```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
