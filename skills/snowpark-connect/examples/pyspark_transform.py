import os

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType
from pyspark.sql.window import Window

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


def create_session():
    return SparkSession.builder \
        .appName("PySpark-Example") \
        .master("local[*]") \
        .getOrCreate()


@F.udf(returnType=StringType())
def salary_bucket(salary_min, salary_max):
    if salary_min is None or salary_max is None:
        return "Unknown"
    avg_salary = (salary_min + salary_max) / 2
    if avg_salary < 80000:
        return "Entry"
    elif avg_salary < 120000:
        return "Mid"
    elif avg_salary < 160000:
        return "Senior"
    else:
        return "Executive"


@F.udf(returnType=StringType())
def extract_state(location):
    if location is None:
        return "Unknown"
    if location == "Remote":
        return "Remote"
    parts = location.split(", ")
    return parts[-1] if len(parts) > 1 else "Unknown"


def main():
    spark = create_session()
    spark.sparkContext.setLogLevel("WARN")

    print("=== Loading data from Parquet ===")
    jobs = spark.read.parquet(os.path.join(DATA_DIR, "jobs.parquet"))
    companies = spark.read.parquet(os.path.join(DATA_DIR, "companies.parquet"))
    applications = spark.read.parquet(os.path.join(DATA_DIR, "applications.parquet"))
    print(f"  Jobs: {jobs.count():,}  Companies: {companies.count():,}  Applications: {applications.count():,}")

    print("\n=== Dedup jobs ===")
    w = Window.partitionBy("company_id", "title", "location", "salary_min") \
        .orderBy("posted_date")
    jobs_deduped = jobs.withColumn("_rn", F.row_number().over(w)) \
        .filter(F.col("_rn") == 1) \
        .drop("_rn")
    jobs_clean = jobs_deduped \
        .filter(F.col("salary_min").isNotNull()) \
        .filter(F.col("salary_max") > F.col("salary_min"))
    print(f"  After dedup + salary filter: {jobs_clean.count():,} rows")

    print("\n=== Feature engineering (UDFs) ===")
    jobs_enriched = jobs_clean \
        .withColumn("salary_bucket", salary_bucket(F.col("salary_min"), F.col("salary_max"))) \
        .withColumn("state", extract_state(F.col("location"))) \
        .withColumn("salary_midpoint", ((F.col("salary_min") + F.col("salary_max")) / 2).cast("int")) \
        .withColumn("posted_month", F.substring("posted_date", 1, 7))
    jobs_enriched.select("title", "salary_bucket", "state", "salary_midpoint", "posted_month").show(5, truncate=False)

    print("=== Joins & aggregations ===")
    jobs_with_company = jobs_enriched.join(companies, on="company_id", how="inner")

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
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    final.select(
        "job_id", "company_name", "industry", "title", "state",
        "salary_bucket", "salary_midpoint", "posted_month",
        "total_applications", "unique_applicants", "hires",
    ).coalesce(1).write.mode("overwrite").parquet(os.path.join(OUTPUT_DIR, "job_analytics"))
    print(f"  Results written to {OUTPUT_DIR}/job_analytics/")

    spark.stop()
    print("Done!")


if __name__ == "__main__":
    main()
