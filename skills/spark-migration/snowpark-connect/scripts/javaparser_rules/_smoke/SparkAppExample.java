// Smoke-test fixture for ScosJavaFacts and ScosJavaRewrite.
// Covers patterns matched by all 12 SCOS rules.
// Excluded from the production jar (see pom.xml <excludes>).
import org.apache.spark.sql.Dataset;
import org.apache.spark.sql.Row;
import org.apache.spark.sql.SparkSession;
import org.apache.spark.api.java.JavaSparkContext;
import static org.apache.spark.sql.functions.*;

public class SparkAppExample {

    public static void main(String[] args) {

        // Rule 1: ScosSparkSessionBuilderRewrite
        SparkSession spark = SparkSession.builder()
            .appName("SmokeTest")
            .master("local[*]")
            .config("spark.sql.shuffle.partitions", "2")
            .enableHiveSupport()
            .getOrCreate();

        // Rule 2: ScosCheckpointToCache
        Dataset<Row> df = spark.read().parquet("/data/input");
        Dataset<Row> checkpointed = df.checkpoint(true);

        // Rule 3: ScosMapSubscriptToElementAt
        Dataset<Row> mapped = df.select(df.col("mapField").getItem("key"));

        // Rule 4: ScosWildcardReadAnnotate
        Dataset<Row> wildcardDf = spark.read().parquet("s3://bucket/data/*.parquet");

        // Rule 5: ScosSaveAsTableDropStorageOpts
        df.write().format("parquet")
          .option("path", "s3://bucket/output")
          .mode("overwrite")
          .saveAsTable("my_table");

        // Rule 6: ScosExternalCloudReadAnnotate
        Dataset<Row> cloudDf = spark.read().csv("gs://my-bucket/data.csv");

        // Rule 7: ScosSelfJoinUnaliasedAnnotate
        Dataset<Row> joined = df.join(df, df.col("id").equalTo(df.col("id")));

        // Rule 8: ScosSparkContextPropertyFallbackAnnotate
        JavaSparkContext sc = new JavaSparkContext(spark.sparkContext());
        sc.parallelize(java.util.Arrays.asList(1, 2, 3));

        // Rule 9: ScosUdtfCompatibilityModeAnnotate  — see MyUdtf class below

        // Rule 10: ScosUnionByNameAllowMissingAnnotate
        Dataset<Row> other = spark.read().parquet("/data/other");
        Dataset<Row> unioned = df.unionByName(other, true);

        // Rule 11: ScosDriverHotPathAnnotate
        for (int i = 0; i < 3; i++) {
            java.util.List<Row> rows = df.collect();
        }

        // Rule 12: ScosTempViewMultiUseCache
        df.createOrReplaceTempView("events");
        spark.sql("SELECT count(*) FROM events");
        spark.sql("SELECT max(ts) FROM events");

        spark.stop();
    }
}

// Rule 9: ScosUdtfCompatibilityModeAnnotate
class MyUdtf extends org.apache.hadoop.hive.ql.udf.generic.GenericUDTF {
    @Override
    public org.apache.hadoop.hive.serde2.objectinspector.StructObjectInspector initialize(
            org.apache.hadoop.hive.serde2.objectinspector.ObjectInspector[] argOIs) {
        return null;
    }
    @Override
    public void process(Object[] args) {}
    @Override
    public void close() {}
}
