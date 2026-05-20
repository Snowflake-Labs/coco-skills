"""
Full workload test entrypoint for pyspark_transform_scos SCOS migration.
Initializes synthetic parquet data and runs the processing pipeline
using the real snowflake.snowpark_connect SCOS runtime.
"""
import os
import sys

# Add the test directory to path so the workload can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from snowflake import snowpark_connect
from pyspark.sql.types import (
    StructType, StructField, StringType, IntegerType, FloatType
)

# Initialize real SCOS session
spark = snowpark_connect.init_spark_session()

# ============================================================
# SYNTHETIC DATA - Create parquet files the workload expects
# ============================================================
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(TEST_DIR, "data")
OUTPUT_DIR = os.path.join(TEST_DIR, "output")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- jobs.parquet ---
jobs_schema = StructType([
    StructField("job_id", StringType(), True),
    StructField("company_id", StringType(), True),
    StructField("title", StringType(), True),
    StructField("location", StringType(), True),
    StructField("salary_min", IntegerType(), True),
    StructField("salary_max", IntegerType(), True),
    StructField("posted_date", StringType(), True),
])
jobs_data = [
    ("j1", "c1", "Data Engineer", "San Francisco, CA", 120000, 160000, "2025-01-15"),
    ("j2", "c1", "Data Engineer", "San Francisco, CA", 120000, 160000, "2025-02-10"),  # duplicate
    ("j3", "c2", "ML Engineer", "New York, NY", 140000, 180000, "2025-01-20"),
    ("j4", "c3", "Analyst", "Remote", 60000, 80000, "2025-03-01"),
    ("j5", "c2", "Staff Engineer", "Seattle, WA", 180000, 250000, "2025-02-15"),
]
spark.createDataFrame(jobs_data, jobs_schema).write.mode("overwrite").parquet(
    os.path.join(DATA_DIR, "jobs.parquet")
)

# --- companies.parquet ---
companies_schema = StructType([
    StructField("company_id", StringType(), True),
    StructField("company_name", StringType(), True),
    StructField("industry", StringType(), True),
])
companies_data = [
    ("c1", "TechCorp", "Technology"),
    ("c2", "DataInc", "Technology"),
    ("c3", "FinanceGroup", "Finance"),
]
spark.createDataFrame(companies_data, companies_schema).write.mode("overwrite").parquet(
    os.path.join(DATA_DIR, "companies.parquet")
)

# --- applications.parquet ---
applications_schema = StructType([
    StructField("job_id", StringType(), True),
    StructField("applicant_id", StringType(), True),
    StructField("status", StringType(), True),
])
applications_data = [
    ("j1", "a1", "applied"),
    ("j1", "a2", "hired"),
    ("j3", "a3", "applied"),
    ("j3", "a4", "applied"),
    ("j5", "a5", "hired"),
]
spark.createDataFrame(applications_data, applications_schema).write.mode("overwrite").parquet(
    os.path.join(DATA_DIR, "applications.parquet")
)

print("Synthetic data written to", DATA_DIR)

# ============================================================
# RUN WORKLOAD - Import and call the REAL main() function
# ============================================================
# Override DATA_DIR and OUTPUT_DIR in the workload module before calling main()
import pyspark_transform_scos as workload

workload.DATA_DIR = DATA_DIR
workload.OUTPUT_DIR = OUTPUT_DIR

print("Running workload main()...")
workload.main()
print("SUCCESS: Workload completed")
