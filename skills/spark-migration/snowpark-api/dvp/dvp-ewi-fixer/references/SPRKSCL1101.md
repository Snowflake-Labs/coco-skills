# SPRKSCL1101

org.apache.spark.sql.functions.broadcast

> This issue code has been **deprecated** since [Spark Conversion Core 2.3.22](https://docs.snowconvert.com/sma/general/release-notes)

Message: Broadcast is not supported

Category: Warning

## Description

This issue appears when the SMA detects a use of the [org.apache.spark.sql.functions.broadcast](https://spark.apache.org/docs/latest/api/scala/org/apache/spark/sql/functions$.html#broadcast[T](df:org.apache.spark.sql.Dataset[T]):org.apache.spark.sql.Dataset[T]) function, which is not supported by Snowpark.
This function is not supported because Snowflake does not support [broadcast variables](https://spark.apache.org/docs/latest/api/java/org/apache/spark/broadcast/Broadcast.html).

## Scenario

**Input**

Below is an example of the `org.apache.spark.sql.functions.broadcast` function used to create a broadcast object to use on each Spark cluster:

```scala
    var studentData = Seq(
      ("James", "Orozco", "Science"),
      ("Andrea", "Larson", "Bussiness"),
    )

    var collegeData = Seq(
      ("Arts", 1),
      ("Bussiness", 2),
      ("Science", 3)
    )

    val dfStudent = studentData.toDF("FirstName", "LastName", "CollegeName")
    val dfCollege = collegeData.toDF("CollegeName", "CollegeCode")

    dfStudent.join(
      broadcast(dfCollege),
      Seq("CollegeName")
    )
```
**Output**

The SMA adds the EWI `SPRKSCL1101` to the output code to let you know that this function is not supported by Snowpark.

```scala
    var studentData = Seq(
      ("James", "Orozco", "Science"),
      ("Andrea", "Larson", "Bussiness"),
    )

    var collegeData = Seq(
      ("Arts", 1),
      ("Bussiness", 2),
      ("Science", 3)
    )

    val dfStudent = studentData.toDF("FirstName", "LastName", "CollegeName")
    val dfCollege = collegeData.toDF("CollegeName", "CollegeCode")

    dfStudent.join(
      /*EWI: SPRKSCL1101 => Broadcast is not supported*/
      broadcast(dfCollege),
      Seq("CollegeName")
    )
```

**Recommended fix**

Since Snowflake manages the storage and the workload on the clusters making broadcast objects inapplicable. This means that the use of broadcast could not be required at all, but each case should require further analysis.

The recommended approach is replace a Spark dataframe broadcast by a Snowpark regular dataframe or by using a dataframe method as  [Join](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/api/snowflake.snowpark.DataFrame.join).

For the proposed input the fix is to adapt the join to use directly the dataframe `collegeDF` without the use of broadcast for the dataframe.

```scala
    var studentData = Seq(
      ("James", "Orozco", "Science"),
      ("Andrea", "Larson", "Bussiness"),
    )

    var collegeData = Seq(
      ("Arts", 1),
      ("Bussiness", 2),
      ("Science", 3)
    )

    val dfStudent = studentData.toDF("FirstName", "LastName", "CollegeName")
    val dfCollege = collegeData.toDF("CollegeName", "CollegeCode")

    dfStudent.join(
      dfCollege,
      Seq("CollegeName")
    ).show()
```

## Additional recommendations

- The [Snowflake's architecture guide](https://docs.snowflake.com/en/user-guide/intro-key-concepts) provides insight about Snowflake storage management.
- Snowpark [Dataframe reference](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/1.23.0/snowpark/dataframe) could be useful in how to adapt a particular broadcast scenario.
- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
