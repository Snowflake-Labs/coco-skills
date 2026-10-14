"""
Test fixture: Python file with various path types
"""
from snowflake.snowpark import Session

# Static S3 paths
input_s3 = "s3://my-bucket/raw/data.csv"
output_s3 = "s3://my-bucket/processed/output.parquet"

# Static HDFS paths
hdfs_input = "hdfs://prod-cluster/warehouse/table_data.parquet"
hdfs_output = "hdfs://prod-cluster/results/final.csv"

# Local paths
local_temp = "/tmp/temp_data.csv"
local_cache = "/var/cache/app/cache.json"

# Relative paths
config_path = "./config/settings.json"
shared_data = "../shared/common_data.parquet"

# Dynamic paths (f-strings)
env = "production"
region = "us-west-2"
dynamic_s3 = f"s3://data-{env}/region-{region}/data.csv"
dynamic_hdfs = f"hdfs://{env}-cluster/data/{region}/output.parquet"

# Snowpark operations
def process_data(session: Session):
    # Read from S3
    df1 = session.read.csv("s3://source-bucket/input/raw_data.csv")
    
    # Read from HDFS
    df2 = session.read.parquet("hdfs://analytics/warehouse/dimension.parquet")
    
    # Write to S3
    result.write.csv("s3://target-bucket/output/results.csv")
    
    # Dynamic read
    table = "customers"
    df3 = session.read.parquet(f"hdfs://warehouse/{table}/latest.parquet")
    
    return df1, df2, df3
