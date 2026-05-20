# Databricks/MLflow: Common Reference

Shared patterns and utilities for migrating from Databricks and MLflow to Snowflake.

## Platform Detection

### Indicators
```python
# SDK imports
import mlflow
from pyspark.sql import SparkSession
from databricks import feature_store
from delta.tables import DeltaTable

# File patterns
"MLmodel"                  # MLflow model manifest
"conda.yaml"               # MLflow environment
"databricks.yml"           # Databricks asset bundles
"*.dbc"                    # Databricks archive

# Code patterns
"spark.read.table("
"dbutils.fs."
"mlflow.autolog()"
```

### Detection Logic
```python
def detect_databricks():
    indicators = [
        grep_files("import mlflow"),
        grep_files("from pyspark"),
        grep_files("spark.read.table"),
        grep_files("dbutils"),
        os.path.exists("MLmodel"),
    ]
    return any(indicators)
```

## Asset Mapping

| Databricks Asset | Snowflake Equivalent | Migration Path |
|------------------|---------------------|----------------|
| Workspace | Database/Schema | Map workspace to DB.SCHEMA |
| MLflow Model | Model Registry | Export → register |
| Model Serving Endpoint | SPCS Service | Extract → deploy |
| Job (Training) | ML Job | Convert notebook/script |
| Cluster | Compute Pool | CPU_X64/GPU_NV |
| Feature Store | Feature Views | Recreate |
| Delta Table | Snowflake Table | Migrate data |
| Notebook | ML Job script | Convert code |
| DBFS | Snowflake Stage | Stage operations |

## MLflow Tracking URI

### Setup for Export
```python
import mlflow

# For Databricks-hosted MLflow
mlflow.set_tracking_uri("databricks")

# For Unity Catalog models
mlflow.set_registry_uri("databricks-uc")

# For self-hosted MLflow
mlflow.set_tracking_uri("http://mlflow-server:5000")
```

## Environment Verification

### Prerequisites Checklist
- [ ] Databricks CLI configured (`databricks configure`)
- [ ] Workspace URL available
- [ ] Personal access token or OAuth configured
- [ ] `mlflow` and `databricks-sdk` packages installed
- [ ] Access to target workspace

### Validate Access
```python
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
clusters = w.clusters.list()
print(f"Found {len(list(clusters))} clusters")
```

## Spark to Snowpark Conversion

| Spark | Snowpark | Notes |
|-------|----------|-------|
| `spark.read.table("db.table")` | `session.table("DB.SCHEMA.TABLE")` | Case sensitivity |
| `df.select("col")` | `df.select("COL")` | Upper case in Snowflake |
| `df.filter(df.col > 5)` | `df.filter(col("COL") > 5)` | Import col() |
| `df.groupBy("col").agg(...)` | `df.group_by("COL").agg(...)` | Snake case |
| `df.toPandas()` | `df.to_pandas()` | Snake case |
| `df.write.saveAsTable()` | `df.write.save_as_table()` | Snake case |

## dbutils to Snowflake Stage

| dbutils | Snowflake | Notes |
|---------|-----------|-------|
| `dbutils.fs.ls("path")` | `session.sql("LIST @STAGE")` | Stage listing |
| `dbutils.fs.cp("src", "dst")` | `session.file.put() / get()` | File operations |
| `dbutils.fs.rm("path")` | `session.sql("REMOVE @STAGE/path")` | Delete files |
| `dbutils.fs.head("path")` | `session.file.get()` then read | Read file |
| `dbutils.secrets.get()` | Snowflake secrets | Secret management |

## Data Source Migration

| Databricks Source | Snowflake Target | Method |
|-------------------|------------------|--------|
| Delta Lake | Iceberg or native | Delta sharing or export |
| DBFS | Internal Stage | Upload files |
| Unity Catalog | Snowflake catalog | Migrate tables |
| S3/ADLS | External Stage | Direct access |

### Create External Stage for Databricks Storage
```sql
CREATE STAGE dbx_stage
  URL = 's3://databricks-bucket/path/'
  CREDENTIALS = (AWS_KEY_ID='...' AWS_SECRET_KEY='...');
```

## MLmodel File Structure

```yaml
# MLmodel file - key for framework detection
artifact_path: model
flavors:
  python_function:
    env: conda.yaml
    loader_module: mlflow.sklearn  # ← Framework indicator
    model_path: model.pkl
  sklearn:
    pickled_model: model.pkl
    sklearn_version: 1.2.0
```

## Framework Detection from MLflow

| MLflow Flavor | Framework | Snowflake Registration |
|---------------|-----------|------------------------|
| `sklearn` | sklearn | Direct log_model() |
| `xgboost` | XGBoost | Direct log_model() |
| `lightgbm` | LightGBM | Direct log_model() |
| `pytorch` | PyTorch | log_model() or CustomModel |
| `tensorflow` | TensorFlow | log_model() or CustomModel |
| `pyfunc` | Custom | Check underlying or SPCS |
