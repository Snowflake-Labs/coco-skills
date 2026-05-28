# AWS SageMaker - Training Migration

Migrating SageMaker training jobs to Snowflake ML Jobs.

## Environment Variable Mapping

| SageMaker | Snowflake Equivalent |
|-----------|---------------------|
| `SM_MODEL_DIR` | Return model or Registry |
| `SM_CHANNEL_TRAINING` | `session.table(training_table)` |
| `SM_CHANNEL_VALIDATION` | `session.table(validation_table)` |
| `SM_OUTPUT_DATA_DIR` | Stage upload |
| `SM_NUM_GPUS` | `torch.cuda.device_count()` |
| `SM_HP_<NAME>` | Function parameter |

## Script Mode → submit_file / @remote

```python
# BEFORE: SageMaker entry point
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=10)
    args = parser.parse_args()
    
    train_dir = os.environ["SM_CHANNEL_TRAINING"]
    train_data = pd.read_csv(f"{train_dir}/train.csv")
    model = train(train_data, args.epochs)
    
    model_dir = os.environ["SM_MODEL_DIR"]
    joblib.dump(model, f"{model_dir}/model.pkl")

# AFTER: Snowflake ML Job (using @remote for simple cases)
@remote("CPU_X64_M", stage_name="TRAINING_STAGE", pip_requirements=["scikit-learn"])
def train(training_table: str, epochs: int = 10):
    from snowflake.snowpark import Session
    from snowflake.ml.registry import Registry
    
    session = Session.builder.getOrCreate()
    train_data = session.table(training_table).to_pandas()
    model = train_model(train_data, epochs)
    
    # MANDATORY: Register model
    registry = Registry(session, database_name="DB", schema_name="SCHEMA")
    registry.log_model(model, model_name="MY_MODEL", version_name="V1")
    return model

job = train("DB.SCHEMA.TRAINING_DATA", epochs=10)
```

**Preferred approach using submit_file:**
```python
# launcher.py (runs locally)
from snowflake.ml.jobs import submit_file

job = submit_file(
    "train.py",
    "CPU_X64_M",
    stage_name="TRAINING_STAGE",
    pip_requirements=["scikit-learn", "pandas"]
)
result = job.result()
```

## PyTorch Estimator → ML Jobs

```python
# BEFORE: SageMaker PyTorch
from sagemaker.pytorch import PyTorch
estimator = PyTorch(
    entry_point="train.py",
    instance_type="ml.p3.2xlarge",
    instance_count=1,
    hyperparameters={"epochs": 10, "batch-size": 32}
)
estimator.fit({"training": "s3://bucket/training"})

# AFTER: Snowflake ML Job
from snowflake.ml.jobs import submit_file

# train.py contains the training logic
job = submit_file(
    "train.py",
    "GPU_NV_S",
    stage_name="TRAINING_STAGE",
    pip_requirements=["torch"],
    args=["--epochs", "10", "--batch-size", "32"]
)
```

Or using @remote:
```python
@remote("GPU_NV_S", stage_name="TRAINING_STAGE", pip_requirements=["torch"])
def train(training_table: str, epochs: int = 10, batch_size: int = 32):
    import torch
    from snowflake.snowpark import Session
    
    session = Session.builder.getOrCreate()
    train_data = session.table(training_table).to_pandas()
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    # Same training logic
    return model
```

## SageMaker Distributed → Ray

```python
# BEFORE: SageMaker distributed
estimator = PyTorch(
    instance_count=4,
    instance_type="ml.p3.16xlarge",
    distribution={"pytorchddp": {"enabled": True}}
)

# AFTER: Snowflake + Ray
@remote("GPU_NV_S", stage_name="TRAINING_STAGE", pip_requirements=["torch", "ray[train]"], target_instances=4)
def train_distributed(training_table: str):
    import ray
    from ray.train.torch import TorchTrainer
    from ray.train import ScalingConfig
    
    ray.init()
    trainer = TorchTrainer(
        train_func,
        scaling_config=ScalingConfig(num_workers=4, use_gpu=True)
    )
    return trainer.fit()
```

## SageMaker HPO → Snowflake Tuner

**⚠️ CRITICAL:** Tuner API only available in Container Runtime. Put this code in the training script, not the launcher.

```python
# BEFORE: SageMaker tuner
from sagemaker.tuner import HyperparameterTuner, ContinuousParameter, IntegerParameter
tuner = HyperparameterTuner(
    estimator,
    objective_metric_name="validation:accuracy",
    hyperparameter_ranges={
        "learning-rate": ContinuousParameter(0.001, 0.1),
        "batch-size": IntegerParameter(16, 128)
    },
    max_jobs=20
)

# AFTER: Snowflake Tuner (in training script that runs in Container Runtime)
from snowflake.ml.modeling.tune import Tuner, TunerConfig, loguniform, randint
from snowflake.ml.modeling.tune.search import RandomSearch

search_space = {
    "learning_rate": loguniform(0.001, 0.1),
    "batch_size": randint(16, 128)  # Use RandomSearch for integer params
}
tuner_config = TunerConfig(
    metric="accuracy",
    mode="max",
    search_alg=RandomSearch(),  # NOT BayesOpt - it doesn't support integers
    num_trials=20
)
tuner = Tuner(train_func, search_space, tuner_config)
results = tuner.run(dataset_map=dataset_map)
```

**HPO Parameter Type Mapping:**

| SageMaker | Snowflake | Notes |
|-----------|-----------|-------|
| `ContinuousParameter(0.001, 0.1)` | `uniform(0.001, 0.1)` or `loguniform(0.001, 0.1)` | Use loguniform for learning rates |
| `IntegerParameter(16, 128)` | `randint(16, 128)` | **Only with RandomSearch** |
| `CategoricalParameter(["a", "b"])` | `choice(["a", "b"])` | **Only with RandomSearch** |

**⚠️ BayesOpt limitation:** Only supports `uniform()` and `loguniform()`. If you need integers or categoricals, use `RandomSearch()`.

## Conversion Checklist

- [ ] Replace `SM_CHANNEL_*` with `session.table()`
- [ ] Replace `SM_MODEL_DIR` with Registry `log_model()`
- [ ] Convert argparse to function parameters (for @remote) or keep argparse (for submit_file)
- [ ] Replace SageMaker distributed with Ray integration
- [ ] Replace `HyperparameterTuner` with Snowflake Tuner (in training script)
- [ ] Add model registration (MANDATORY - return values not accessible with submit_file)
- [ ] Extract dependencies to `pip_requirements`
- [ ] Select appropriate compute pool (CPU vs GPU)

## Common Issues

| Error | Cause | Solution |
|-------|-------|----------|
| `ModuleNotFoundError: snowflake.ml.modeling.tune` | Trying to import Tuner locally | Tuner only available in Container Runtime - put in training script |
| Model not saved | Missing registry.log_model() | Always register model - return values not accessible |
| Python version mismatch | Local Python != 3.10 | Use submit_file instead of @remote |
| `BayesOpt does not support Integer` | Using randint with BayesOpt | Switch to RandomSearch |
