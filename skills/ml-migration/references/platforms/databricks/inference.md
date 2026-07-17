# Databricks/MLflow: Inference Migration

Migrating Databricks MLflow models and serving endpoints to Snowflake for inference.

## Model Discovery

### From MLflow Model Registry
```python
import mlflow

mlflow.set_tracking_uri("databricks")

# List registered models
client = mlflow.MlflowClient()
for model in client.search_registered_models():
    print(f"{model.name}")
    for version in model.latest_versions:
        print(f"  v{version.version}: {version.current_stage}")
```

### From Unity Catalog
```python
mlflow.set_registry_uri("databricks-uc")

# Unity Catalog model URI
model_uri = "models:/catalog.schema.model_name/1"
```

## Model Export

### Download Model Artifacts
```python
import mlflow

# From Model Registry
model_uri = "models:/my-model/Production"
local_path = mlflow.artifacts.download_artifacts(model_uri)

# From Run Artifacts
run_id = "abc123..."
artifact_path = mlflow.artifacts.download_artifacts(
    run_id=run_id,
    artifact_path="model"
)
```

### Load Model for Inspection
```python
# Load as pyfunc
model = mlflow.pyfunc.load_model(model_uri)

# Get model info
model_info = mlflow.models.get_model_info(model_uri)
print(f"Flavors: {model_info.flavors}")
```

## Framework Detection

Parse `MLmodel` file to identify framework:

```python
import yaml

with open("MLmodel") as f:
    mlmodel = yaml.safe_load(f)

flavors = mlmodel.get("flavors", {})
if "sklearn" in flavors:
    framework = "sklearn"
elif "xgboost" in flavors:
    framework = "xgboost"
elif "pytorch" in flavors:
    framework = "pytorch"
elif "tensorflow" in flavors:
    framework = "tensorflow"
else:
    framework = "pyfunc"  # Custom model
```

| MLflow Flavor | Framework | Snowflake Registration |
|---------------|-----------|------------------------|
| `sklearn` | sklearn | Direct log_model() |
| `xgboost` | XGBoost | Direct log_model() |
| `lightgbm` | LightGBM | Direct log_model() |
| `pytorch` | PyTorch | log_model() or CustomModel |
| `tensorflow` | TensorFlow | log_model() or CustomModel |
| `pyfunc` | Custom | CustomModel wrapper |

## Snowflake Registration

**⛔ For `log_model()` API patterns, see:** `../../../../model-registry/SKILL.md`

The patterns below are platform-specific extraction and conversion logic.

### Direct Registration (sklearn, xgboost, lightgbm)
```python
from snowflake.ml.registry import Registry
import mlflow

# Download and load model
model_uri = "models:/my-model/Production"
local_path = mlflow.artifacts.download_artifacts(model_uri)
model = mlflow.sklearn.load_model(local_path)

# Register in Snowflake
registry = Registry(session, database_name="DB", schema_name="SCHEMA")

mv = registry.log_model(
    model,
    model_name="DBX_MIGRATED_MODEL",
    version_name="v1",
    conda_dependencies=["scikit-learn"],
    sample_input_data=sample_df
)
```

### CustomModel for pyfunc
```python
from snowflake.ml.model import custom_model
import mlflow

class MLflowPyfuncWrapper(custom_model.CustomModel):
    def __init__(self, context: custom_model.ModelContext):
        super().__init__(context)
        self.model = mlflow.pyfunc.load_model(context.path("mlflow_model"))
    
    @custom_model.inference_api
    def predict(self, input_df: pd.DataFrame) -> pd.DataFrame:
        predictions = self.model.predict(input_df)
        return pd.DataFrame({"prediction": predictions})

# Register
mv = registry.log_model(
    MLflowPyfuncWrapper,
    model_name="DBX_PYFUNC_MODEL",
    version_name="v1",
    artifacts={"mlflow_model": local_path}
)
```

### CustomModel for PyTorch/TensorFlow
```python
from snowflake.ml.model import custom_model
import torch

class PyTorchWrapper(custom_model.CustomModel):
    def __init__(self, context: custom_model.ModelContext):
        super().__init__(context)
        self.model = torch.load(context.path("model.pt"))
        self.model.eval()
    
    @custom_model.inference_api
    def predict(self, input_df: pd.DataFrame) -> pd.DataFrame:
        with torch.no_grad():
            inputs = torch.tensor(input_df.values, dtype=torch.float32)
            outputs = self.model(inputs)
        return pd.DataFrame({"prediction": outputs.numpy()})
```

## Serving Endpoint Migration

### Get Endpoint Details
```python
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
endpoint = w.serving_endpoints.get("my-endpoint")

for served_model in endpoint.config.served_models:
    print(f"Model: {served_model.model_name}")
    print(f"Version: {served_model.model_version}")
```

### SPCS Deployment
```yaml
spec:
  containers:
    - name: inference
      image: /DB/SCHEMA/REPO/dbx-migrated:v1
      env:
        MODEL_NAME: DBX_MIGRATED_MODEL
        MODEL_VERSION: v1
      resources:
        requests:
          memory: 4Gi
          cpu: 2
  endpoints:
    - name: predict
      port: 8080
      public: true
```

## Feature Store Migration

Databricks Feature Store → Snowflake Feature Views:

1. Export feature definitions
```python
from databricks import feature_store

fs = feature_store.FeatureStoreClient()
features = fs.get_table("catalog.schema.features")
```

2. Recreate in Snowpark
```python
# Create feature engineering logic in Snowpark
from snowflake.snowpark.functions import col, sum, avg

features_df = session.table("RAW_DATA").group_by("ENTITY_ID").agg(
    sum("AMOUNT").alias("TOTAL_AMOUNT"),
    avg("AMOUNT").alias("AVG_AMOUNT")
)

# Register as Feature View (if using Snowflake Feature Store)
```

## Migration Checklist

- [ ] Identify MLflow model/endpoint
- [ ] Download model artifacts
- [ ] Parse MLmodel for framework
- [ ] Extract conda dependencies
- [ ] Register in Snowflake Model Registry
- [ ] Wrap with CustomModel if pyfunc/pytorch/tensorflow
- [ ] Deploy to SPCS if real-time inference needed
- [ ] Migrate Feature Store definitions if used
- [ ] Validate inference results match
