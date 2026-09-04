# PySpark to Snowpark Connect (SCOS) Migration with Cortex Code

This document captures the end-to-end workflow for migrating a PySpark workload to Snowflake SCOS using the Cortex Code **Snowpark Connect** skill.

## Quick Reference

### Setup
```bash
conda run -n scos python -c "from snowflake import snowpark_connect; print('OK')"  # verify runtime
snow sql -q "SELECT 1" -c snowpark-connect  # verify Snowflake connection
```

### Migrate
```bash
# In Cortex Code: activate snowpark-connect skill → select "Migrate"
# Produces pyspark_transform_scos.py + analysis.json from pyspark_transform.py
```

### Validate
```bash
cd pyspark_transform_scos_test && conda run -n scos --no-capture-output python entrypoint.py
```

### Optimize
```bash
# In Cortex Code: activate snowpark-connect skill → select "Optimize"
# Converts Python UDFs to native SQL expressions, adds case sensitivity guard
```

### Deploy
```bash
snow sql -q "CREATE STAGE IF NOT EXISTS SCOS_APPS_STAGE DIRECTORY=(ENABLE=TRUE)" -c snowpark-connect
snow sql -q "CREATE STAGE IF NOT EXISTS SCOS_DATA_STAGE DIRECTORY=(ENABLE=TRUE)" -c snowpark-connect
snow sql -q "PUT file://pyspark_transform_scos.py @SCOS_APPS_STAGE/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE" -c snowpark-connect
snow sql -q "PUT file://data/jobs.parquet @SCOS_DATA_STAGE/data/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE" -c snowpark-connect
snow sql -q "PUT file://data/companies.parquet @SCOS_DATA_STAGE/data/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE" -c snowpark-connect
snow sql -q "PUT file://data/applications.parquet @SCOS_DATA_STAGE/data/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE" -c snowpark-connect
conda run -n scos --no-capture-output snowpark-submit \
    --snowflake-stage=@DEMO.SPCONN.SCOS_APPS_STAGE \
    --snowflake-workload-name=scos_job_analytics \
    --snowflake-connection-name=snowpark-connect \
    --compute-pool=SNOWPARK_SUBMIT_POOL_XS \
    pyspark_transform_scos.py
```

---

## Project Structure

```
example/
├── pyspark_transform.py              # Original PySpark workload
├── pyspark_transform_scos.py         # Migrated SCOS workload
├── analysis.json                     # Compatibility analysis results
├── data/                             # Source parquet files
├── output/                           # Pipeline output
├── pyspark_transform_scos_test/      # Validation test directory
│   ├── entrypoint.py                 # Test entrypoint with synthetic data
│   ├── pyspark_transform_scos.py     # Copy of migrated workload
│   ├── data/                         # Synthetic test data
│   ├── output/                       # Test output
│   └── output.log                    # Validation run log
└── README.md
```

---

## Detailed Steps

### 1. Local Testing Environment Setup

**Prerequisites:** conda env `scos` with `snowpark-connect`, Snowflake connection `snowpark-connect` in `~/.snowflake/config.toml`, Python 3.11.

```bash
conda run -n scos python -c "from snowflake import snowpark_connect; print('OK')"
```

The migration analyzer uses a RAG-based Cortex Search Service (`SCOS_MIGRATION.PUBLIC.SCOS_COMPAT_ISSUES_SERVICE`). Initialized automatically on first use.

---

### 2. Migration

Activate the Snowpark Connect skill in Cortex Code and select **Migrate**. The 6-step workflow: analyze → copy → apply fixes → update imports → add header → verify.

**Analysis found 8 issues** in `pyspark_transform.py`:

| Lines | Risk | Issue | Action |
|-------|------|-------|--------|
| 46 | **1.0** | `spark.sparkContext.setLogLevel()` - RDD API not supported | Removed |
| 49-51 | 0.2 | Local parquet file reads | Added stage performance tip |
| 100-104 | 0.2 | `coalesce(1)` is a no-op in SCOS | Commented as no-op |
| 57-80 | 0.1-0.15 | Window/filter/groupBy patterns | Reviewed, safe |

**Key change** — session initialization:
```python
# BEFORE                                    # AFTER
from pyspark.sql import SparkSession        from snowflake import snowpark_connect
spark = SparkSession.builder \              spark = snowpark_connect.init_spark_session()
    .master("local[*]").getOrCreate()
```

---

### 3. Validation

Smoke test using synthetic data on the real SCOS runtime. The entrypoint creates 5 jobs, 3 companies, 5 applications as parquet, then calls the real `main()`.

```bash
cd pyspark_transform_scos_test && conda run -n scos --no-capture-output python entrypoint.py
```

**Result:** All pipeline stages passed — parquet reads, window functions, joins, aggregations, parquet write.

---

### 4. Optimization

Activate the Snowpark Connect skill and select **Optimize**. Changes applied:

- **Python UDFs → native SQL expressions**: Replaced `@F.udf` functions with `F.when/otherwise` chains (eliminates serde overhead)
- **Case sensitivity**: Added `spark.conf.set("spark.sql.caseSensitive", "true")` to prevent column uppercasing
- **Array indexing**: Replaced `parts[Column]` with `F.element_at(parts, -1)` (required for Spark Connect mode)

---

### 5. Deployment

Activate the Snowpark Connect skill and select **Deploy**. Uses `snowpark-submit` to run on SPCS compute pools.

**Key pattern** — dual-mode session for local dev vs. snowpark-submit:
```python
def create_session():
    if os.environ.get("SPARK_REMOTE"):
        return SparkSession.builder.remote(os.environ["SPARK_REMOTE"]).getOrCreate()
    else:
        from snowflake import snowpark_connect
        return snowpark_connect.init_spark_session()
```

Without this, `snowpark-submit` fails with `RuntimeError: Snowpark Connect cannot be run inside of a Spark environment` because it already provides a Spark Connect session.

**Deployment result:** 51K jobs + 200K applications processed, output written to `@SCOS_DATA_STAGE/output/job_analytics/` (893 KB).
