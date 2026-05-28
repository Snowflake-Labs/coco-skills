# Vertex AI: Common Reference

Shared patterns and utilities for migrating from Google Vertex AI to Snowflake.

## Platform Detection

### Indicators
```python
# SDK imports
from google.cloud import aiplatform
from google.cloud import storage
from google.cloud.aiplatform import hyperparameter_tuning as hpt

# File patterns
"gs://"                    # GCS URIs in code
"aiplatform.init("         # SDK initialization
"us-docker.pkg.dev/vertex-ai/"  # Pre-built containers
```

### Detection Logic
```python
def detect_vertex_ai():
    indicators = [
        grep_files("from google.cloud import aiplatform"),
        grep_files("aiplatform.init"),
        grep_files(r"gs://"),
        grep_files("CustomJob"),
    ]
    return any(indicators)
```

## Asset Mapping

| Vertex AI Asset | Snowflake Equivalent | Migration Path |
|-----------------|---------------------|----------------|
| Project | Database | Map project to database |
| Model Registry | Model Registry | Download → register |
| Endpoint | SPCS Service | Extract → deploy |
| CustomJob | @remote decorator | Convert script |
| HyperparameterTuningJob | Snowflake Tuner | HPO migration |
| Worker Pool | Compute Pool | CPU_X64/GPU_NV |
| Pipeline | Tasks/Python | Orchestration |
| Feature Store | Feature Views | Recreate |
| Dataset | Table/Stage | Load via connector |

## GCP Authentication

### SDK Initialization
```python
from google.cloud import aiplatform

aiplatform.init(
    project="my-project",
    location="us-central1"
)
```

### Required GCP Permissions
- `roles/aiplatform.user` - Access models, endpoints
- `roles/storage.objectViewer` - Download from GCS
- `roles/aiplatform.viewer` - List resources

## Environment Verification

### Prerequisites Checklist
- [ ] GCP CLI authenticated (`gcloud auth login`)
- [ ] Project ID available
- [ ] Region/location confirmed
- [ ] `google-cloud-aiplatform` package installed
- [ ] Service account or user credentials configured

### Validate Access
```python
from google.cloud import aiplatform

aiplatform.init(project="my-project", location="us-central1")
models = aiplatform.Model.list()
print(f"Found {len(models)} models")
```

## Environment Variable Mapping

| Vertex AI | Snowflake Equivalent | Notes |
|-----------|---------------------|-------|
| `AIP_MODEL_DIR` | Return model or Registry | Model output |
| `AIP_TRAINING_DATA_URI` | `session.table()` | Training data |
| `AIP_CHECKPOINT_DIR` | Stage path | Checkpointing |
| `CLUSTER_SPEC` | Ray cluster config | Distributed |

## Pre-built Container Mapping

| Vertex AI Container | Snowflake pip_requirements |
|--------------------|-----------------------------|
| `pytorch-gpu.2-0` | `["torch==2.0"]` |
| `tensorflow-gpu.2-12` | `["tensorflow==2.12"]` |
| `sklearn-cpu.1-0` | sklearn pre-installed |
| `xgboost-cpu.1-6` | xgboost pre-installed |

## Data Source Migration

| Vertex AI Source | Snowflake Target | Method |
|------------------|------------------|--------|
| GCS bucket | External Stage | `gcs://bucket/path` |
| BigQuery | Table | BigQuery connector |
| Cloud SQL | Table | ETL or connector |
| Vertex Dataset | Table | Export → load |
