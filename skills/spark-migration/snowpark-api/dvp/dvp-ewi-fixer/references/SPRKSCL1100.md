# SPRKSCL1100

org.apache.spark.sql.DataFrame.repartition

> This issue code has been **deprecated** since [Spark Conversion Core 2.3.22](https://docs.snowconvert.com/sma/general/release-notes)

Message: Repartition is not supported.

Category: Parsing error.

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.DataFrame.repartition](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/Dataset.html#repartition(partitionExprs:org.apache.spark.sql.Column*):org.apache.spark.sql.Dataset[T]) function, which is not supported by Snowpark.
Snowflake manages the storage and the workload on the clusters making repartition operation inapplicable.

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.DataFrame.repartition` function used to return a new `DataFrame` partitioned by the given partitioning expressions.

```scala
    var nameData = Seq("James", "Sarah", "Dylan", "Leila, "Laura", "Peter")
    var jobData = Seq("Police", "Doctor", "Actor", "Teacher, "Dentist", "Fireman")
    var ageData = Seq(40, 38, 34, 27, 29, 55)

    val dfName = nameData.toDF("name")
    val dfJob = jobData.toDF("job")
    val dfAge = ageData.toDF("age")

    val dfRepartitionByExpresion = dfName.repartition($"name")

    val dfRepartitionByNumber = dfJob.repartition(3)

    val dfRepartitionByBoth = dfAge.repartition(3, $"age")

    val joinedDf = dfRepartitionByExpresion.join(dfRepartitionByNumber)
```
**Output**

The SMA adds the EWI `SPRKSCL1100` to the output code to let you know that this function is not supported by Snowpark.

```scala
    var nameData = Seq("James", "Sarah", "Dylan", "Leila, "Laura", "Peter")
    var jobData = Seq("Police", "Doctor", "Actor", "Teacher, "Dentist", "Fireman")
    var ageData = Seq(40, 38, 34, 27, 29, 55)

    val dfName = nameData.toDF("name")
    val dfJob = jobData.toDF("job")
    val dfAge = ageData.toDF("age")

    /*EWI: SPRKSCL1100 => Repartition is not supported*/
    val dfRepartitionByExpresion = dfName.repartition($"name")

    /*EWI: SPRKSCL1100 => Repartition is not supported*/
    val dfRepartitionByNumber = dfJob.repartition(3)

    /*EWI: SPRKSCL1100 => Repartition is not supported*/
    val dfRepartitionByBoth = dfAge.repartition(3, $"age")

    val joinedDf = dfRepartitionByExpresion.join(dfRepartitionByNumber)
```

**Recommended Fix**

Since Snowflake manages the storage and the workload on the clusters making repartition operation inapplicable. This means that the use of repartition before the join is not required at all.

```scala
    var nameData = Seq("James", "Sarah", "Dylan", "Leila, "Laura", "Peter")
    var jobData = Seq("Police", "Doctor", "Actor", "Teacher, "Dentist", "Fireman")
    var ageData = Seq(40, 38, 34, 27, 29, 55)

    val dfName = nameData.toDF("name")
    val dfJob = jobData.toDF("job")
    val dfAge = ageData.toDF("age")

    val dfRepartitionByExpresion = dfName

    val dfRepartitionByNumber = dfJob

    val dfRepartitionByBoth = dfAge

    val joinedDf = dfRepartitionByExpresion.join(dfRepartitionByNumber)
```

## Additional recommendations

- The [Snowflake's architecture guide](https://docs.snowflake.com/en/user-guide/intro-key-concepts) provides insight about Snowflake storage management.

- Snowpark [Dataframe reference](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/dataframe) could be useful in how to adapt a particular scenario without the need of repartition.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
