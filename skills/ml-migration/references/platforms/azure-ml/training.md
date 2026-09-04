# Azure ML: Training Migration

Migrating Azure ML training workflows to Snowflake ML Jobs.

## Concept Mapping

| Azure ML | Snowflake | Notes |
|----------|-----------|-------|
| Workspace | Database/Schema | Namespace mapping |
| Compute Cluster | Compute Pool | CPU_X64_* / GPU_NV_* |
| Environment | Container Runtime | + pip_requirements |
| Command Job | @remote decorator | Or submit_file() |
| Sweep Job | Snowflake Tuner | HPO migration |
| Pipeline Job | Tasks or Python | Orchestration |
| Data Asset | Table/Stage | Data access |
| Model Registry | Snowflake Registry | Model storage |

## Command Job Migration

### Azure ML Command Job
```python
from azure.ai.ml import command, Input

job = command(
    code="./src",
    command="python train.py --data ${{inputs.data}} --lr ${{inputs.lr}}",
    environment="AzureML-pytorch-1.13@latest",
    compute="gpu-cluster",
    inputs={
        "data": Input(type="uri_folder"),
        "lr": 0.01
    }
)
ml_client.jobs.create_or_update(job)
```

### Snowflake Equivalent
```python
from snowflake.ml.jobs import remote

@remote(
    compute_pool="GPU_NV_S",
    stage_name="TRAINING_STAGE",
    pip_requirements=["torch==1.13"]
)
def train(training_table: str, lr: float = 0.01):
    from snowflake.snowpark import Session
    session = Session.builder.getOrCreate()
    
    data = session.table(training_table).to_pandas()
    model = train_model(data, lr)
    return model
```

## Input/Output Mapping

| Azure ML | Snowflake | Conversion |
|----------|-----------|------------|
| `Input(type="uri_folder")` | Stage path | `@STAGE/path/` |
| `Input(type="uri_file")` | Stage file | `session.file.get()` |
| `Input(type="mltable")` | Snowflake table | `session.table()` |
| `Output(type="mlflow_model")` | Model Registry | `registry.log_model()` |
| `${{inputs.param}}` | Function arg | Direct parameter |

## Sweep Job Migration (HPO)

### Azure ML Sweep
```python
from azure.ai.ml.sweep import Choice, Uniform

sweep_job = command_job.sweep(
    sampling_algorithm="random",
    primary_metric="accuracy",
    goal="maximize",
    max_total_trials=20,
    search_space={
        "lr": Uniform(0.001, 0.1),
        "batch_size": Choice([16, 32, 64])
    }
)
```

### Snowflake Tuner
```python
from snowflake.ml.modeling.tune import Tuner, TunerConfig
from snowflake.ml.modeling.tune import uniform, randint
from snowflake.ml.modeling.tune.search import RandomSearch

# Define train function
def train_func(config, dataset_map):
    lr = config["lr"]
    batch_size = config["batch_size"]
    train_df = dataset_map["train"]
    
    model = train_model(train_df, lr=lr, batch_size=batch_size)
    accuracy = evaluate(model)
    return {"accuracy": accuracy}

# Configure tuner
search_space = {
    "lr": uniform(0.001, 0.1),
    "batch_size": randint(16, 64)
}

tuner_config = TunerConfig(
    metric="accuracy",
    mode="max",
    search_alg=RandomSearch(),
    num_trials=20
)

tuner = Tuner(train_func, search_space, tuner_config)
results = tuner.run(dataset_map={"train": train_df})
```

## Pipeline Migration

### Azure ML Pipeline
```python
from azure.ai.ml import dsl

@dsl.pipeline(name="training-pipeline")
def pipeline(input_data: Input):
    preprocess_step = preprocess_component(data=input_data)
    train_step = train_component(data=preprocess_step.outputs.processed_data)
    return {"model": train_step.outputs.model}
```

### Snowflake Orchestration
```python
from snowflake.ml.jobs import submit_file

def run_pipeline(raw_table: str):
    # Step 1: Preprocess
    preprocess_job = submit_file(
        "preprocess.py",
        compute_pool="CPU_X64_M",
        stage_name="TRAINING_STAGE",
        args=["--input", raw_table, "--output", "PROCESSED_DATA"]
    )
    preprocess_job.wait()
    
    # Step 2: Train
    train_job = submit_file(
        "train.py",
        compute_pool="GPU_NV_S",
        stage_name="TRAINING_STAGE",
        args=["--data", "PROCESSED_DATA"]
    )
    return train_job.result()
```

## MLflow Tracking Migration

### Azure ML + MLflow
```python
import mlflow

mlflow.log_param("lr", 0.01)
mlflow.log_metric("accuracy", accuracy)
mlflow.sklearn.log_model(model, "model")
```

### Snowflake Pattern
```python
from snowflake.ml.jobs import remote
from snowflake.ml.registry import Registry

@remote("CPU_X64_M", stage_name="TRAINING_STAGE")
def train(training_table: str, lr: float = 0.01):
    from snowflake.snowpark import Session
    session = Session.builder.getOrCreate()
    
    # Train
    model = train_model(lr=lr)
    accuracy = evaluate(model)
    
    # Register (replaces mlflow.log_model)
    registry = Registry(session, database_name="DB", schema_name="SCHEMA")
    registry.log_model(
        model,
        model_name="MODEL",
        version_name="V1",
        metrics={"accuracy": accuracy},
        options={"params": {"lr": lr}}
    )
    
    return {"model": model, "metrics": {"accuracy": accuracy}}
```

## Script Conversion Patterns

### Argument Handling
```python
# BEFORE: Azure command line args
# python train.py --data ${{inputs.data}} --lr ${{inputs.lr}}
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--data", type=str)
parser.add_argument("--lr", type=float)
args = parser.parse_args()

# AFTER: Snowflake function params (with submit_file)
# Keep argparse for submit_file() compatibility
# Or convert to @remote function parameters
```

### Data Loading
```python
# BEFORE: Azure URI input
data_path = args.data  # uri_folder from input
df = pd.read_parquet(data_path)

# AFTER: Snowflake table
from snowflake.snowpark import Session
session = Session.builder.getOrCreate()
df = session.table(training_table).to_pandas()
```

## Conversion Checklist

- [ ] Map Azure compute to Snowflake compute pool
- [ ] Replace `command()` with `@remote` or `submit_file()`
- [ ] Convert `${{inputs.*}}` to function arguments
- [ ] Replace environment with `pip_requirements`
- [ ] Replace sweep job with Snowflake Tuner
- [ ] Convert pipeline to Tasks or Python orchestration
- [ ] Replace MLflow with return values + Registry
- [ ] Update data access from URI to Snowflake tables
