# Snowflake ML Infrastructure

Infrastructure components for ML workloads.

## Compute Pools

### Available Pools

| Pool | vCPUs | Memory | GPUs | Best For |
|------|-------|--------|------|----------|
| CPU_X64_S | 2 | 8 GB | - | Small sklearn jobs |
| CPU_X64_M | 4 | 16 GB | - | Medium sklearn/xgboost |
| CPU_X64_L | 8 | 32 GB | - | Large data processing |
| GPU_NV_S | 4 | 16 GB | 1 | Single GPU training |
| GPU_NV_M | 8 | 64 GB | 4 | Multi-GPU training |
| GPU_NV_L | 16 | 128 GB | 8 | Large model training |

### Pool Management
```sql
-- List available pools
SHOW COMPUTE POOLS;

-- Check pool status
DESCRIBE COMPUTE POOL CPU_X64_M;

-- Create custom pool (admin)
CREATE COMPUTE POOL MY_POOL
  MIN_NODES = 1
  MAX_NODES = 4
  INSTANCE_FAMILY = GPU_NV_S;
```

## Stages

### Training Stage Setup
```sql
CREATE STAGE IF NOT EXISTS TRAINING_STAGE
  DIRECTORY = (ENABLE = TRUE)
  COMMENT = 'Stage for ML training artifacts';
```

### Upload Files
```python
# Python
session.file.put("local_file.py", "@TRAINING_STAGE/", auto_compress=False)

# SQL
PUT file://./train.py @TRAINING_STAGE AUTO_COMPRESS=FALSE;
```

### List Stage Contents
```sql
LIST @TRAINING_STAGE;
```

## Model Registry

### Database/Schema Setup
```sql
CREATE DATABASE IF NOT EXISTS ML_MODELS;
CREATE SCHEMA IF NOT EXISTS ML_MODELS.REGISTRY;
```

### Registry Operations
```python
from snowflake.ml.registry import Registry

registry = Registry(
    session=session,
    database_name="ML_MODELS",
    schema_name="REGISTRY"
)

# List models
models = registry.show_models()

# Get model
model = registry.get_model("MY_MODEL")
mv = model.version("V1")
```

### SQL Operations
```sql
SHOW MODELS IN SCHEMA ML_MODELS.REGISTRY;
DESC MODEL ML_MODELS.REGISTRY.MY_MODEL;
```

## SPCS Infrastructure

### Image Repository
```sql
CREATE IMAGE REPOSITORY IF NOT EXISTS ML_IMAGES;

-- List images
SHOW IMAGES IN IMAGE REPOSITORY ML_IMAGES;
```

### Service Compute Pool
```sql
-- Check system pools
SHOW COMPUTE POOLS LIKE 'SYSTEM%';

-- Services typically use
-- SYSTEM_COMPUTE_POOL_CPU for CPU inference
-- GPU pools for GPU inference
```

### External Access Integration
```sql
-- Create integration for PyPI access (admin)
CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION PYPI_ACCESS_INTEGRATION
  ALLOWED_NETWORK_RULES = (pypi_network_rule)
  ALLOWED_AUTHENTICATION_SECRETS = ()
  ENABLED = TRUE;

-- Grant to roles
GRANT USAGE ON INTEGRATION PYPI_ACCESS_INTEGRATION TO ROLE ML_ROLE;
```

## Data Infrastructure

### Tables for Training Data
```sql
CREATE TABLE IF NOT EXISTS TRAIN_DATA (
    ID INT,
    FEATURE1 FLOAT,
    FEATURE2 FLOAT,
    TARGET INT
);
```

### External Stages (Cloud Storage)

#### AWS S3
```sql
CREATE STAGE S3_STAGE
  URL = 's3://bucket/path/'
  CREDENTIALS = (AWS_KEY_ID='...' AWS_SECRET_KEY='...');
```

#### Azure Blob
```sql
CREATE STAGE AZURE_STAGE
  URL = 'azure://account.blob.core.windows.net/container/'
  CREDENTIALS = (AZURE_SAS_TOKEN='...');
```

#### GCS
```sql
CREATE STAGE GCS_STAGE
  URL = 'gcs://bucket/path/'
  STORAGE_INTEGRATION = gcs_int;
```

## Resource Monitoring

### Check Compute Usage
```sql
-- Active jobs
SHOW ML JOBS;

-- Service status
SHOW SERVICES IN SCHEMA DB.SCHEMA;

-- Compute pool utilization
SELECT * FROM SNOWFLAKE.ACCOUNT_USAGE.COMPUTE_POOL_USAGE
WHERE COMPUTE_POOL_NAME = 'CPU_X64_M'
ORDER BY START_TIME DESC
LIMIT 10;
```

### Cost Management
```sql
-- ML job costs
SELECT * FROM SNOWFLAKE.ACCOUNT_USAGE.MLTRAINING_HISTORY
ORDER BY START_TIME DESC
LIMIT 10;
```

## Infrastructure Checklist

### For Training
- [ ] Compute pool accessible (CPU_X64_* or GPU_NV_*)
- [ ] Training stage created
- [ ] PYPI_ACCESS_INTEGRATION available
- [ ] Training data loaded to tables

### For Inference
- [ ] Model Registry schema created
- [ ] Image repository created (SPCS only)
- [ ] Service compute pool accessible
- [ ] BIND SERVICE ENDPOINT granted (public endpoints only)
