# AWS SageMaker - Inference Migration

Migrating SageMaker endpoints and models to Snowflake Model Registry and SPCS.

## Endpoint Discovery

### Connect and Describe Endpoint
```python
import boto3

client = boto3.client('sagemaker', region_name='us-west-2')

# Get endpoint details
endpoint = client.describe_endpoint(EndpointName='YOUR_ENDPOINT')
config = client.describe_endpoint_config(
    EndpointConfigName=endpoint['EndpointConfigName']
)
model = client.describe_model(
    ModelName=config['ProductionVariants'][0]['ModelName']
)

# Get S3 artifact location
model_data_url = model['PrimaryContainer']['ModelDataUrl']
# e.g., s3://bucket/path/model.tar.gz
```

### Download and Extract Model
```bash
# Download model.tar.gz from S3
aws s3 cp s3://bucket/path/model.tar.gz ./aws_models/model.tar.gz --profile <profile>

# Extract to get model files
cd ./aws_models
tar -xzf model.tar.gz
# Results: model.pth, model.pkl, or custom files
```

## Migration Strategy Decision

| Strategy | When to Use | Pros | Cons |
|----------|-------------|------|------|
| **Docker lift-and-shift** | Preserve exact behavior, complex inference logic | True lift-and-shift, no code changes | No SQL integration, manual versioning |
| **Model Registry + SPCS** | Want SQL integration, versioning, governance | Native `MODEL!PREDICT()`, built-in lineage | Requires model extraction |

**Ask user:**
```
How would you like to migrate this endpoint?

1. Container migration (lift-and-shift)
   Pull existing container and deploy to SPCS as-is.

2. Model extraction (native registration)
   Extract model, register in Snowflake Model Registry.
```

## Deployment Decision Matrix

| Use Case | Target Platform | Registration |
|----------|-----------------|--------------|
| Batch inference (SQL) | `WAREHOUSE` | Simple, SQL-integrated |
| Real-time API endpoint | `SNOWPARK_CONTAINER_SERVICES` | Requires SPCS setup |
| GPU acceleration | `SNOWPARK_CONTAINER_SERVICES` | GPU compute pool required |

## Model Registration

**⛔ For `log_model()` API patterns, see:** `../../../../model-registry/SKILL.md`

The patterns below are platform-specific extraction and conversion logic.

### Built-in Framework Registration (PyTorch Example)
```python
import torch
from snowflake.ml.registry import Registry
from snowflake.ml.model.target_platform import TargetPlatform

# Load model (must define class matching training)
class Net(torch.nn.Module):
    def __init__(self):
        super(Net, self).__init__()
        self.fc = torch.nn.Linear(10, 1)
    def forward(self, x):
        return self.fc(x)

model = Net()
model.load_state_dict(torch.load("model.pth", map_location='cpu', weights_only=True))
model.eval()

# Register with SPCS target
reg = Registry(session=session, database_name="DB", schema_name="SCHEMA")

mv = reg.log_model(
    model=model,
    model_name="PYTORCH_SAGEMAKER_MODEL",
    version_name="V1",
    sample_input_data=torch.rand(5, 10),  # REQUIRED for schema inference
    target_platforms=[TargetPlatform.SNOWPARK_CONTAINER_SERVICES],
    comment="Migrated from SageMaker endpoint: <endpoint_name>"
)
```

### CustomModel for Complex Logic

When model has custom preprocessing or isn't natively supported:

⚠️ **CRITICAL:** Use `artifacts` dict + `context.path()`, NOT `models` dict + `model_ref()`:

```python
from snowflake.ml.model import custom_model
import pandas as pd
import pickle

class MyCustomModel(custom_model.CustomModel):
    def __init__(self, context: custom_model.ModelContext):
        super().__init__(context)
        # Load model using context.path() - NOT model_ref
        with open(context.path('model.pkl'), 'rb') as f:
            self.model = pickle.load(f)
    
    @custom_model.inference_api
    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        # Custom preprocessing
        processed = self._preprocess(X)
        # Model inference
        predictions = self.model.predict(processed)
        # Custom postprocessing
        return pd.DataFrame({'prediction': predictions})
    
    def _preprocess(self, X):
        # Your preprocessing logic from inference.py
        return X

# Save model to file first
with open('/tmp/model.pkl', 'wb') as f:
    pickle.dump(loaded_model, f)

# Create context with artifacts
mc = custom_model.ModelContext(artifacts={'model.pkl': '/tmp/model.pkl'})
model = MyCustomModel(mc)

mv = reg.log_model(
    model=model,
    model_name="CUSTOM_SAGEMAKER_MODEL",
    version_name="V1",
    sample_input_data=sample_df,
    target_platforms=[TargetPlatform.SNOWPARK_CONTAINER_SERVICES]
)
```

## SPCS Deployment

### Service Ingress Configuration

**⚠️ STOPPING POINT: Ask user for ingress preference FIRST:**

```
How should the service be accessed?

1. Public ingress - HTTP access from outside Snowflake
   Requires BIND SERVICE ENDPOINT privilege on your role
   
2. Internal-only - Service only callable via SQL/Python within Snowflake
   No additional privileges required

Which do you prefer?
```

**If user chose Public ingress, CHECK privilege:**
```sql
SHOW GRANTS TO ROLE <user_role>;
-- Look for: BIND SERVICE ENDPOINT on ACCOUNT
```

**⛔ If user chose Public but lacks privilege - DO NOT silently downgrade to internal.**

### Deploy as Service

```python
mv.create_service(
    service_name="MY_INFERENCE_ENDPOINT",
    service_compute_pool="SYSTEM_COMPUTE_POOL_CPU",  # or custom GPU pool
    image_repo="DB.SCHEMA.ML_IMAGES",
    build_external_access_integration="PYPI_ACCESS_INTEGRATION",
    ingress_enabled=True,  # Set to False ONLY if user explicitly chose internal-only
    gpu_requests=None,      # Set for GPU models
    max_instances=1
)
```

### Monitor Deployment
```sql
-- Check service status
SHOW SERVICES IN SCHEMA DB.SCHEMA;

-- View service logs
SELECT SYSTEM$GET_SERVICE_LOGS('DB.SCHEMA.MY_INFERENCE_ENDPOINT', 0, 'model-inference');
```

## Usage After Migration

### Python (SPCS Endpoint)
```python
mv = reg.get_model("MODEL_NAME").version("V1")
result = mv.run(
    input_data, 
    function_name="predict",  # or "forward" for PyTorch
    service_name="MY_INFERENCE_ENDPOINT"
)
```

### SQL (Warehouse)
```sql
SELECT MODEL_NAME!PREDICT(col1, col2, ...):prediction 
FROM my_table;

-- PyTorch forward
SELECT MODEL_NAME!FORWARD(feature_array) 
FROM my_table;
```

## Common Issues & Solutions

| Error | Cause | Solution |
|-------|-------|----------|
| `cloudpickle <=2.2.1 conflict` | Python 3.8/3.9 | **Use Python 3.11+** |
| `image_repo required` | Missing repository | Create IMAGE REPOSITORY first |
| `compute pool not authorized` | Pool access denied | Use SYSTEM_COMPUTE_POOL_CPU |
| `BIND SERVICE ENDPOINT privilege` | Missing privilege | Get privilege or use internal-only |
| `type not supported` | Wrong model wrapper | Use CustomModel with ModelContext |
| Build timeout (10-15 min) | Large dependencies (PyTorch) | Wait, it's normal |
| `sample_input_data required` | Schema inference fails | Always provide sample input |

## Migration Timeline

Based on production migrations:
- **First migration**: ~2 hours (environment setup + learning)
- **Subsequent migrations**: 15-30 minutes
- **Build time for SPCS**: 5-15 minutes (PyTorch longer than sklearn)
