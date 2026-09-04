# Vertex AI: Training Migration

Migrating Vertex AI custom training jobs to Snowflake ML Jobs.

## Concept Mapping

| Vertex AI | Snowflake | Notes |
|-----------|-----------|-------|
| Project | Database | Namespace |
| CustomJob | @remote decorator | Or submit_file() |
| HyperparameterTuningJob | Snowflake Tuner | HPO migration |
| Worker Pool | Compute Pool | CPU_X64_*/GPU_NV_* |
| Pre-built container | Container Runtime | + pip_requirements |
| GCS bucket | Snowflake Stage | Data storage |
| Vertex AI Model | Model Registry | Model storage |

## CustomJob Migration

### Vertex AI CustomJob
```python
from google.cloud import aiplatform

job = aiplatform.CustomJob.from_local_script(
    display_name="pytorch-training",
    script_path="train.py",
    container_uri="us-docker.pkg.dev/vertex-ai/training/pytorch-gpu.2-0:latest",
    requirements=["pandas==2.0.0"],
    args=["--epochs", "10", "--data-uri", "gs://bucket/data"],
    machine_type="n1-standard-8",
    accelerator_type="NVIDIA_TESLA_V100"
)
job.run()
```

### Snowflake Equivalent
```python
from snowflake.ml.jobs import remote

@remote(
    compute_pool="GPU_NV_S",
    stage_name="TRAINING_STAGE",
    pip_requirements=["torch", "pandas==2.0.0"]
)
def train(training_table: str, epochs: int = 10):
    from snowflake.snowpark import Session
    session = Session.builder.getOrCreate()
    
    train_df = session.table(training_table).to_pandas()
    model = train_model(train_df, epochs)
    return model
```

## HyperparameterTuningJob Migration

### Vertex AI HPO
```python
from google.cloud.aiplatform import hyperparameter_tuning as hpt

hp_job = aiplatform.HyperparameterTuningJob(
    display_name="hp-tuning",
    custom_job=job,
    metric_spec={"accuracy": "maximize"},
    parameter_spec={
        "learning_rate": hpt.DoubleParameterSpec(min=0.001, max=0.1, scale="log"),
        "n_estimators": hpt.IntegerParameterSpec(min=10, max=200),
        "max_depth": hpt.DiscreteParameterSpec(values=[3, 5, 7, 10])
    },
    max_trial_count=20,
    parallel_trial_count=4
)
hp_job.run()
```

### Snowflake Tuner
```python
from snowflake.ml.modeling.tune import Tuner, TunerConfig
from snowflake.ml.modeling.tune import loguniform, randint, choice
from snowflake.ml.modeling.tune.search import RandomSearch

def train_func(config, dataset_map):
    lr = config["learning_rate"]
    n_estimators = config["n_estimators"]
    max_depth = config["max_depth"]
    train_df = dataset_map["train"]
    
    model = train_model(train_df, lr=lr, n_estimators=n_estimators, max_depth=max_depth)
    accuracy = evaluate(model)
    return {"accuracy": accuracy}

search_space = {
    "learning_rate": loguniform(0.001, 0.1),
    "n_estimators": randint(10, 200),
    "max_depth": choice([3, 5, 7, 10])
}

tuner_config = TunerConfig(
    metric="accuracy",
    mode="max",
    search_alg=RandomSearch(),
    num_trials=20,
    max_concurrent_trials=4
)

tuner = Tuner(train_func, search_space, tuner_config)
results = tuner.run(dataset_map={"train": train_df})
```

## Distributed Training Migration

### Vertex AI Distributed
```python
job = aiplatform.CustomJob(
    worker_pool_specs=[
        {
            "machine_spec": {
                "accelerator_type": "NVIDIA_TESLA_V100",
                "accelerator_count": 2
            },
            "replica_count": 1
        },
        {
            "machine_spec": {
                "accelerator_type": "NVIDIA_TESLA_V100",
                "accelerator_count": 2
            },
            "replica_count": 3
        }
    ]
)
```

### Snowflake + Ray
```python
from snowflake.ml.jobs import remote

@remote(
    compute_pool="GPU_NV_S",
    stage_name="TRAINING_STAGE",
    pip_requirements=["torch", "ray[train]"],
    target_instances=4
)
def train_distributed(training_table: str):
    import ray
    from ray.train.torch import TorchTrainer
    from ray.train import ScalingConfig
    
    ray.init()
    
    trainer = TorchTrainer(
        train_func,
        scaling_config=ScalingConfig(
            num_workers=4,
            use_gpu=True,
            resources_per_worker={"GPU": 2}
        )
    )
    return trainer.fit()
```

## Script Conversion Patterns

### GCS Data Access
```python
# BEFORE: Vertex AI GCS access
from google.cloud import storage
data_uri = os.environ.get("AIP_TRAINING_DATA_URI")
client = storage.Client()
bucket = client.bucket("my-bucket")
blob = bucket.blob("data/train.csv")
blob.download_to_filename("train.csv")
df = pd.read_csv("train.csv")

# AFTER: Snowflake table
from snowflake.snowpark import Session
session = Session.builder.getOrCreate()
df = session.table(training_table).to_pandas()
```

### Model Output
```python
# BEFORE: Vertex AI model dir
model_dir = os.environ.get("AIP_MODEL_DIR")
joblib.dump(model, os.path.join(model_dir, "model.pkl"))

# AFTER: Snowflake return or registry
from snowflake.ml.registry import Registry

# Option 1: Return model
return model

# Option 2: Register model
registry = Registry(session, database_name="DB", schema_name="SCHEMA")
registry.log_model(model, model_name="MODEL", version_name="V1")
```

### Hypertune Reporting
```python
# BEFORE: Vertex AI hypertune
import hypertune
hpt = hypertune.HyperTune()
hpt.report_hyperparameter_tuning_metric(
    hyperparameter_metric_tag="accuracy",
    metric_value=accuracy
)

# AFTER: Snowflake Tuner
# Return metrics from train_func - Tuner handles reporting
return {"accuracy": accuracy}
```

## Conversion Checklist

- [ ] Map Vertex project to Snowflake database
- [ ] Replace `CustomJob` with `@remote` or `submit_file()`
- [ ] Replace GCS URIs with Snowflake tables/stages
- [ ] Remove `google.cloud.storage` usage
- [ ] Replace `AIP_MODEL_DIR` with return or Registry
- [ ] Replace `HyperparameterTuningJob` with Tuner
- [ ] Replace distributed worker pools with Ray
- [ ] Replace `hypertune.report_*` with Tuner return values
- [ ] Map pre-built containers to pip_requirements
