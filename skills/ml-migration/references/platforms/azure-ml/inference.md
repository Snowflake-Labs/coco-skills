# Azure ML: Inference Migration

Migrating Azure ML models and managed endpoints to Snowflake for inference.

## Model Discovery

### List Registered Models
```python
from azure.ai.ml import MLClient
from azure.identity import DefaultAzureCredential

ml_client = MLClient(
    DefaultAzureCredential(),
    subscription_id="...",
    resource_group="...",
    workspace_name="..."
)

# List all models
models = ml_client.models.list()
for model in models:
    print(f"{model.name} v{model.version}: {model.type}")
```

### Get Model Details
```python
model = ml_client.models.get(name="my-model", version="1")
print(f"Path: {model.path}")
print(f"Type: {model.type}")
print(f"Framework: {model.properties.get('framework', 'unknown')}")
```

## Endpoint Discovery

### List Managed Endpoints
```python
endpoints = ml_client.online_endpoints.list()
for ep in endpoints:
    print(f"{ep.name}: {ep.provisioning_state}")
```

### Get Deployment Details
```python
endpoint = ml_client.online_endpoints.get(name="my-endpoint")
deployment = ml_client.online_deployments.get(
    name="my-deployment", 
    endpoint_name="my-endpoint"
)

# Model reference
model_ref = deployment.model
print(f"Model: {model_ref}")
```

## Model Download

### From Model Registry
```python
ml_client.models.download(
    name="my-model", 
    version="1", 
    download_path="./model"
)
```

### Identify Framework
| Azure ML Model Type | Framework | Detection |
|--------------------|-----------|-----------|
| `mlflow` | MLflow (various) | Check `MLmodel` file |
| `custom` | Various | Inspect file patterns |
| `triton` | Triton | ONNX/TensorRT files |

## Scoring Script Analysis

Azure uses `score.py` with:
- `init()` - Model loading (runs once)
- `run(data)` - Prediction logic (per request)

### Extract Logic
```python
# Example Azure score.py
def init():
    global model
    model_path = os.getenv("AZUREML_MODEL_DIR")
    model = joblib.load(os.path.join(model_path, "model.pkl"))

def run(raw_data):
    data = json.loads(raw_data)
    predictions = model.predict(data["input"])
    return predictions.tolist()
```

## Snowflake Registration

**⛔ For `log_model()` API patterns, see:** `../../../../model-registry/SKILL.md`

The patterns below are platform-specific extraction and conversion logic.

### Direct Registration (sklearn, xgboost, etc.)
```python
from snowflake.ml.registry import Registry

registry = Registry(session, database_name="DB", schema_name="SCHEMA")

# Load downloaded model
import joblib
model = joblib.load("./model/model.pkl")

# Register
mv = registry.log_model(
    model,
    model_name="AZURE_MIGRATED_MODEL",
    version_name="v1",
    conda_dependencies=["scikit-learn"],
    sample_input_data=sample_df
)
```

### CustomModel for Unsupported Types
```python
from snowflake.ml.model import custom_model

class AzureModelWrapper(custom_model.CustomModel):
    def __init__(self, context: custom_model.ModelContext):
        super().__init__(context)
        import joblib
        self.model = joblib.load(context.path("model.pkl"))
    
    @custom_model.inference_api
    def predict(self, input_df: pd.DataFrame) -> pd.DataFrame:
        # Converted from Azure score.py run() logic
        predictions = self.model.predict(input_df.values)
        return pd.DataFrame({"prediction": predictions})

# Register custom model
mv = registry.log_model(
    AzureModelWrapper,
    model_name="AZURE_CUSTOM_MODEL",
    version_name="v1",
    artifacts={"model.pkl": "./model/model.pkl"}
)
```

## SPCS Deployment

### Service Specification
```yaml
spec:
  containers:
    - name: inference
      image: /DB/SCHEMA/REPO/azure-migrated:v1
      env:
        MODEL_NAME: AZURE_MIGRATED_MODEL
        MODEL_VERSION: v1
      resources:
        requests:
          memory: 4Gi
          cpu: 2
        limits:
          nvidia.com/gpu: 1
  endpoints:
    - name: predict
      port: 8080
      public: true
```

### Build Container
```dockerfile
FROM python:3.10-slim

# Install from Azure environment.yml
COPY environment.yml .
RUN pip install pyyaml && \
    python -c "import yaml; deps=yaml.safe_load(open('environment.yml')); print(' '.join([d for d in deps.get('dependencies',[]) if isinstance(d,str)]))" | xargs pip install

COPY inference_service.py .
CMD ["python", "inference_service.py"]
```

## UDF Alternative

For simpler models, convert to Snowflake UDF:
```python
from snowflake.snowpark.functions import udf

@udf(packages=["scikit-learn", "joblib"])
def predict_azure_model(input_data: dict) -> dict:
    # Converted from Azure score.py
    import joblib
    model = joblib.load("@STAGE/model.pkl")
    prediction = model.predict([list(input_data.values())])
    return {"prediction": prediction[0]}
```

## Migration Checklist

- [ ] Identify Azure model/endpoint
- [ ] Download model artifacts
- [ ] Analyze score.py for prediction logic
- [ ] Export environment dependencies
- [ ] Register in Snowflake Model Registry
- [ ] Wrap with CustomModel if needed
- [ ] Deploy to SPCS or create UDF
- [ ] Validate inference results match
