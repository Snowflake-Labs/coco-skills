"""
SCOS Migration Output
=====================
Source File: /Users/pjain/git/coco-work/test_scos_migration/example/pyspark_transform.py
Migrated on: 2026-03-24

Changes Overview:
- [Line 3] Replaced 'from pyspark.sql import SparkSession' with 'from snowflake import snowpark_connect'.
- [Lines 12-13] Replaced legacy SparkSession.builder initialization with snowpark_connect.init_spark_session().
- [Line 43] Removed spark.sparkContext.setLogLevel("WARN") - .sparkContext not supported in SCOS.
- [Lines 49-50] Added performance tip comment for local file reads (jobs, companies, applications parquet).
- [Lines 59] Added SCOS review comment for window + filter dedup pattern.
- [Lines 63] Added SCOS review comment for column filtering pattern.
- [Lines 80] Added SCOS review comment for groupBy/agg aliasing pattern.
- [Line 105] Added comment that coalesce(1) is a no-op in SCOS.

Known Limitations:
- coalesce(1) is ignored in SCOS; output may be multiple files instead of one.
- Local file reads work but may be slower than Snowflake stage reads.
"""
import os

from pyspark.sql import SparkSession, functions as F
from pyspark.sql.window import Window

# When running via snowpark-submit on SPCS, read/write from Snowflake stages.
# Locally, use the filesystem paths.
if os.environ.get("SPARK_REMOTE"):
    DATA_DIR = "@DEMO.SPCONN.SCOS_DATA_STAGE/data"
    OUTPUT_DIR = "@DEMO.SPCONN.SCOS_DATA_STAGE/output"
else:
    DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
    OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


def _path(base, name):
    """Build a path that works for both local filesystem and Snowflake stage (@) paths."""
    if base.startswith("@"):
        return f"{base}/{name}"
    return os.path.join(base, name)


def create_session():
    # When running via snowpark-submit, SPARK_REMOTE is set (e.g. sc://localhost:15003).
    # In that case, use the existing Spark Connect session instead of starting a new SCOS server.
    if os.environ.get("SPARK_REMOTE"):
        return SparkSession.builder.remote(os.environ["SPARK_REMOTE"]).getOrCreate()
    else:
        from snowflake import snowpark_connect
        return snowpark_connect.init_spark_session()


# SCOS Optimization: Replaced Python UDFs with native SQL expressions for better performance.
# Python UDFs require serialization/deserialization overhead; SQL expressions run natively in Snowflake.
def salary_bucket_expr(salary_min, salary_max):
    """Native SQL CASE WHEN replacement for salary_bucket UDF."""
    avg_salary = (salary_min + salary_max) / 2
    return (
        F.when(salary_min.isNull() | salary_max.isNull(), F.lit("Unknown"))
        .when(avg_salary < 80000, F.lit("Entry"))
        .when(avg_salary < 120000, F.lit("Mid"))
        .when(avg_salary < 160000, F.lit("Senior"))
        .otherwise(F.lit("Executive"))
    )


def extract_state_expr(location):
    """Native SQL replacement for extract_state UDF."""
    parts = F.split(location, ", ")
    # Use element_at with -1 to get the last element (1-based, negative indexes from end)
    last_element = F.element_at(parts, -1)
    return (
        F.when(location.isNull(), F.lit("Unknown"))
        .when(location == "Remote", F.lit("Remote"))
        .when(F.size(parts) > 1, last_element)
        .otherwise(F.lit("Unknown"))
    )


def main():
    spark = create_session()
    # SCOS: Removed spark.sparkContext.setLogLevel() - .sparkContext is not supported in SCOS (RDD API unavailable)

    # SCOS Optimization: Set case sensitivity to avoid silent column name uppercasing
    spark.conf.set("spark.sql.caseSensitive", "true")

    print("=== Loading data from Parquet ===")
    # SCOS: Performance tip - Consider uploading these files to a Snowflake stage
    # for faster processing. Use: session.file.put("local_path", "@STAGE_NAME/path")
    jobs = spark.read.parquet(_path(DATA_DIR, "jobs.parquet"))
    companies = spark.read.parquet(_path(DATA_DIR, "companies.parquet"))
    applications = spark.read.parquet(_path(DATA_DIR, "applications.parquet"))
    print(f"  Jobs: {jobs.count():,}  Companies: {companies.count():,}  Applications: {applications.count():,}")

    print("\n=== Dedup jobs ===")
    w = Window.partitionBy("company_id", "title", "location", "salary_min") \
        .orderBy("posted_date")
    # SCOS: Reviewed - window + filter on newly created column in same chain is safe in SCOS
    jobs_deduped = jobs.withColumn("_rn", F.row_number().over(w)) \
        .filter(F.col("_rn") == 1) \
        .drop("_rn")
    # SCOS: Reviewed - filtering on existing columns of the DataFrame is safe in SCOS
    jobs_clean = jobs_deduped \
        .filter(F.col("salary_min").isNotNull()) \
        .filter(F.col("salary_max") > F.col("salary_min"))
    print(f"  After dedup + salary filter: {jobs_clean.count():,} rows")

    print("\n=== Feature engineering (UDFs) ===")
    jobs_enriched = jobs_clean \
        .withColumn("salary_bucket", salary_bucket_expr(F.col("salary_min"), F.col("salary_max"))) \
        .withColumn("state", extract_state_expr(F.col("location"))) \
        .withColumn("salary_midpoint", ((F.col("salary_min") + F.col("salary_max")) / 2).cast("int")) \
        .withColumn("posted_month", F.substring("posted_date", 1, 7))
    jobs_enriched.select("title", "salary_bucket", "state", "salary_midpoint", "posted_month").show(5, truncate=False)

    print("=== Joins & aggregations ===")
    jobs_with_company = jobs_enriched.join(companies, on="company_id", how="inner")

    # SCOS: Reviewed - groupBy/agg with proper aliasing avoids ambiguous column issues in SCOS
    app_stats = applications.groupBy("job_id").agg(
        F.count("*").alias("total_applications"),
        F.countDistinct("applicant_id").alias("unique_applicants"),
        F.sum(F.when(F.col("status") == "hired", 1).otherwise(0)).alias("hires"),
    )

    final = jobs_with_company.join(app_stats, on="job_id", how="left") \
        .fillna(0, subset=["total_applications", "unique_applicants", "hires"])

    final.groupBy("industry").agg(
        F.avg("salary_midpoint").cast("int").alias("avg_salary"),
        F.count("*").alias("job_count"),
        F.sum("total_applications").alias("total_apps"),
    ).orderBy(F.desc("avg_salary")).show(10, truncate=False)

    w_rank = Window.partitionBy("industry").orderBy(F.desc("total_applications"))
    hottest = final.withColumn("rank", F.row_number().over(w_rank)) \
        .filter(F.col("rank") <= 3) \
        .select("industry", "title", "company_name", "state", "salary_midpoint", "total_applications")
    print("--- Top 3 most applied-to jobs per industry ---")
    hottest.show(30, truncate=False)

    print("=== Writing results ===")
    if not OUTPUT_DIR.startswith("@"):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = _path(OUTPUT_DIR, "job_analytics")
    # SCOS: coalesce(1) is a no-op in SCOS - may produce multiple output files instead of one
    final.select(
        "job_id", "company_name", "industry", "title", "state",
        "salary_bucket", "salary_midpoint", "posted_month",
        "total_applications", "unique_applicants", "hires",
    ).coalesce(1).write.mode("overwrite").parquet(output_path)
    print(f"  Results written to {output_path}/")

    spark.stop()
    print("Done!")


if __name__ == "__main__":
    main()
