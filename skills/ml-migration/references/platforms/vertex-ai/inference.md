# Vertex AI: Inference Migration

Migrating Vertex AI models and endpoints to Snowflake for inference.

## Model Discovery

### List Models
```python
from google.cloud import aiplatform

aiplatform.init(project="my-project", location="us-central1")

models = aiplatform.Model.list()
for model in models:
    print(f"{model.display_name}: {model.resource_name}")
```

### Get Model Details
```python
model = aiplatform.Model(model_name="projects/.../models/...")
print(f"Container: {model.container_spec}")
print(f"Artifact URI: {model.artifact_uri}")
print(f"Export formats: {model.supported_export_formats}")
```

## Endpoint Discovery

### List Endpoints
```python
endpoints = aiplatform.Endpoint.list()
for ep in endpoints:
    print(f"{ep.display_name}: {ep.resource_name}")
```

### Get Deployed Models
```python
endpoint = aiplatform.Endpoint(endpoint_name="projects/.../endpoints/...")
for deployed in endpoint.gca_resource.deployed_models:
    print(f"Model: {deployed.model}")
    print(f"Container: {deployed.private_endpoints}")
```

## Model Export

### From Model Registry
```python
model = aiplatform.Model(model_name="projects/.../models/...")
model.export_model(
    export_format_id="tf-saved-model",  # or "custom-trained"
    artifact_destination="gs://bucket/export/"
)
```

### Download from GCS
```bash
gsutil cp -r gs://bucket/model/* ./model/
```

### Export Format Options
- `tf-saved-model` - TensorFlow SavedModel
- `custom-trained` - Original training artifacts
- Check `model.supported_export_formats` for available options

## Framework Detection

| Vertex AI Container | Framework | Snowflake Registration |
|--------------------|-----------|------------------------|
| `us-docker.pkg.dev/.../sklearn-*` | sklearn | Direct log_model() |
| `us-docker.pkg.dev/.../xgboost-*` | XGBoost | Direct log_model() |
| `us-docker.pkg.dev/.../pytorch-*` | PyTorch | log_model() or CustomModel |
| `us-docker.pkg.dev/.../tf-*` | TensorFlow | log_model() or CustomModel |
| Custom container | Custom | SPCS deployment |

## Snowflake Registration

**⛔ For `log_model()` API patterns, see:** `../../../../model-registry/SKILL.md`

The patterns below are platform-specific extraction and conversion logic.

### Direct Registration (sklearn, xgboost)
```python
from snowflake.ml.registry import Registry

registry = Registry(session, database_name="DB", schema_name="SCHEMA")

# Load exported model
import joblib
model = joblib.load("./model/model.pkl")

# Register
mv = registry.log_model(
    model,
    model_name="VERTEX_MIGRATED_MODEL",
    version_name="v1",
    conda_dependencies=["scikit-learn"],
    sample_input_data=sample_df
)
```

### CustomModel for Complex Types
```python
from snowflake.ml.model import custom_model
import tensorflow as tf

class VertexModelWrapper(custom_model.CustomModel):
    def __init__(self, context: custom_model.ModelContext):
        super().__init__(context)
        self.model = tf.saved_model.load(context.path("saved_model"))
    
    @custom_model.inference_api
    def predict(self, input_df: pd.DataFrame) -> pd.DataFrame:
        inputs = input_df.values
        predictions = self.model.signatures["serving_default"](
            tf.constant(inputs, dtype=tf.float32)
        )
        return pd.DataFrame({"prediction": predictions["output_0"].numpy()})

# Register
mv = registry.log_model(
    VertexModelWrapper,
    model_name="VERTEX_TF_MODEL",
    version_name="v1",
    artifacts={"saved_model": "./model/saved_model"}
)
```

## Custom Prediction Routine Migration

### Vertex CPR Pattern
```python
# Vertex predictor.py
class Predictor:
    def __init__(self):
        pass
    
    def load(self, artifacts_dir):
        self._model = joblib.load(os.path.join(artifacts_dir, "model.pkl"))
    
    def predict(self, instances):
        return self._model.predict(instances).tolist()
```

### Snowflake CustomModel Equivalent
```python
from snowflake.ml.model import custom_model

class VertexCPRWrapper(custom_model.CustomModel):
    def __init__(self, context: custom_model.ModelContext):
        super().__init__(context)
        import joblib
        # load() logic here
        self._model = joblib.load(context.path("model.pkl"))
    
    @custom_model.inference_api
    def predict(self, input_df: pd.DataFrame) -> pd.DataFrame:
        # predict() logic here
        instances = input_df.values.tolist()
        predictions = self._model.predict(instances)
        return pd.DataFrame({"prediction": predictions})
```

## SPCS Deployment

### Service Specification
```yaml
spec:
  containers:
    - name: inference
      image: /DB/SCHEMA/REPO/vertex-migrated:v1
      env:
        MODEL_NAME: VERTEX_MIGRATED_MODEL
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

## AutoML Model Considerations

AutoML models have limited export options:

| AutoML Type | Export Support | Migration Path |
|-------------|----------------|----------------|
| Tables | TensorFlow SavedModel | Export → CustomModel |
| Vision | Limited | May require re-training |
| NLP | Limited | May require re-training |

Check available formats:
```python
model = aiplatform.Model(model_name="...")
print(model.supported_export_formats)
```

## Migration Checklist

- [ ] Identify Vertex model/endpoint
- [ ] Check supported export formats
- [ ] Export model artifacts
- [ ] Download from GCS
- [ ] Detect framework from container
- [ ] Register in Snowflake Model Registry
- [ ] Wrap with CustomModel if needed (TF, PyTorch, CPR)
- [ ] Deploy to SPCS if real-time inference needed
- [ ] Validate inference results match
