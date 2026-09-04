# AWS SageMaker - Common

Shared patterns for SageMaker migrations to Snowflake.

## Platform Detection

**Indicators that source is SageMaker:**

| Pattern | Example |
|---------|---------|
| SageMaker ARN | `arn:aws:sagemaker:us-west-2:123456789:endpoint/my-endpoint` |
| SM_* environment variables | `SM_MODEL_DIR`, `SM_CHANNEL_TRAINING` |
| SageMaker SDK imports | `import sagemaker`, `from sagemaker.pytorch import PyTorch` |
| /opt/ml/* paths | `/opt/ml/model`, `/opt/ml/input/data` |
| S3 model artifacts | `s3://sagemaker-*/model.tar.gz` |

## Asset Mapping

| SageMaker Asset | Snowflake Equivalent | Migration Method |
|-----------------|---------------------|------------------|
| Model artifact (S3) | Model Registry | Download → register via inference workflow |
| Endpoint (real-time) | SPCS Service | Extract model → inference workflow |
| Endpoint (batch) | Warehouse inference | Register with `target_platforms=[WAREHOUSE]` |
| Training job | ML Job | Convert script → training workflow |
| Processing job | Snowpark / SQL | Rewrite in Snowpark |
| Feature Store | Feature Views | Recreate with Snowflake Feature Store |

## Environment Verification

**Step 1: Verify AWS CLI access**

Ask the user which AWS profile to use, then verify:
```bash
aws configure list-profiles
# User selects profile

aws sts get-caller-identity --profile <selected-profile>
```

**Step 2: Verify Snowflake resources**

```sql
-- 1. Check current role and grants
SELECT CURRENT_ROLE();
SHOW GRANTS TO ROLE <your_role>;

-- 2. Verify image repository exists (for SPCS)
SHOW IMAGE REPOSITORIES IN SCHEMA <db>.<schema>;

-- 3. Verify compute pool access
SHOW COMPUTE POOLS;

-- 4. Check external access integration
SHOW EXTERNAL ACCESS INTEGRATIONS;
```

## Required Infrastructure

| Resource | Purpose | Creation Command |
|----------|---------|------------------|
| Image Repository | Store container images for SPCS | `CREATE IMAGE REPOSITORY IF NOT EXISTS DB.SCHEMA.ML_IMAGES;` |
| Compute Pool (CPU) | Run inference/training | Use `SYSTEM_COMPUTE_POOL_CPU` or create custom |
| Compute Pool (GPU) | GPU workloads | Create custom GPU pool |
| External Access | Install pip packages | `PYPI_ACCESS_INTEGRATION` |

## AWS Authentication

### Profile Selection
```bash
# List available profiles
aws configure list-profiles

# Verify selected profile works
aws sts get-caller-identity --profile <profile>
```

### ECR Login (for container images)
```bash
# Get ECR login token
aws ecr get-login-password --region <region> --profile <profile> | \
  docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
```

### S3 Access
```bash
# Download model artifacts
aws s3 cp s3://bucket/path/model.tar.gz ./model.tar.gz --profile <profile>

# Extract
tar -xzf model.tar.gz
```

## Framework Detection from Artifacts

| File Pattern | Framework | Notes |
|--------------|-----------|-------|
| `model.pkl`, `*.joblib` | sklearn | Built-in support |
| `xgboost-model`, `*.ubj` | XGBoost | Check if Booster or sklearn API |
| `model.pt`, `model.pth` | PyTorch | Need model class definition |
| `saved_model.pb`, `variables/` | TensorFlow | Built-in support |
| `inference.py` + custom files | Custom | Requires CustomModel wrapper |

## Common Issues

| Error | Cause | Solution |
|-------|-------|----------|
| `ExpiredToken` | AWS credentials expired | Run `aws sso login --profile <profile>` |
| `AccessDenied` on S3 | Missing S3 permissions | Check IAM policy for s3:GetObject |
| `No such bucket` | Wrong region | Verify bucket region matches profile region |
| ECR login failed | Wrong account/region | Verify ECR URL matches your account |
