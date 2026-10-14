# SPRKCNTSCL1000

Element is not supported

Message: The element < ***element*** > is not supported for Snowpark Connect

Category: Conversion error.

## Description

This issue appears when the SMA detects the use of a `Spark element for Scala` that is not supported in `Snowpark Connect`, and does not have its own error code associated with it. This is the generic error code used by the SMA for an unsupported element.

## Scenario

**Input**

Below is an example of a Scala Spark element that is not supported by Snowpark Connect, and therefore it generates this EWI.

```scala
import org.apache.spark.NotSupportedElement
import org.apache.spark.rdd.RDD
import org.apache.spark.{SparkConf, SparkContext}

val conf = new SparkConf().setAppName("GraphXExample").setMaster("local")
val sc = new SparkContext(conf)
val vertices: RDD[(VertexId, String)] = sc.parallelize(Seq((1L, "A"), (2L, "B")))
val edges: RDD[Edge[String]] = sc.parallelize(Seq(Edge(1L, 2L, "edge")))
val graph = NotSupportedElement(vertices, edges)
```

**Output**

The SMA adds the EWI `SPRKCNTSCL1000` to the output code to let you know that this element is not supported by Snowpark Connect.

```scala
import org.apache.spark.NotSupportedElement
import org.apache.spark.rdd.RDD
import org.apache.spark.{SparkConf, SparkContext}

val conf = new SparkConf().setAppName("GraphXExample").setMaster("local")
val sc = new SparkContext(conf)
val vertices: RDD[(VertexId, String)] = sc.parallelize(Seq((1L, "A"), (2L, "B")))
val edges: RDD[Edge[String]] = sc.parallelize(Seq(Edge(1L, 2L, "edge")))
// EWI SPRKCNTSCL1000: The element 'NotSupportedElement' is not supported for Snowpark Connect
val graph = NotSupportedElement(vertices, edges)
```

**Recommended fix**

Since this is a generic error code that applies to a range of unsupported functions, there is not a single and specific fix. The appropriate action will depend on the particular element in use.

Please note that even though the element is not supported, it does not necessarily mean that a solution or workaround cannot be found. It only means that the SMA itself cannot find the solution.

## Additional recommendations

- If you believe that Snowpark Connect already supports this element or that there is some kind of workaround, please report that you encountered a conversion error on that particular element using [the Report an Issue option](https://docs.snowflake.com/en/migrations/sma-docs/user-guide/project-overview/configuration-and-settings#report-an-issue) in the SMA and include any additional information that you think may be helpful.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
