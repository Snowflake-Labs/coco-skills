# Databricks/MLflow: Training Migration

Migrating Databricks notebooks and MLflow training workflows to Snowflake ML Jobs.

## Concept Mapping

| Databricks | Snowflake | Notes |
|------------|-----------|-------|
| Workspace | Database/Schema | Namespace mapping |
| Cluster | Compute Pool | CPU_X64_*/GPU_NV_* |
| Notebook | ML Job script | Convert to Python |
| MLflow experiment | Model Registry + returns | Manual tracking |
| MLflow Model Registry | Snowflake Model Registry | Model storage |
| Delta Lake table | Snowflake Table | Data storage |
| DBFS | Snowflake Stage | File storage |
| Spark DataFrame | Snowpark DataFrame | API conversion |
| dbutils | Stage operations | File utilities |

## MLflow Autologging Migration

### Databricks + MLflow Autolog
```python
import mlflow

mlflow.autolog()

df = spark.read.table("catalog.schema.data").toPandas()
model = RandomForestClassifier(n_estimators=100)
model.fit(X_train, y_train)
# MLflow auto-logs params, metrics, model
```

### Snowflake Manual Tracking
```python
from snowflake.ml.jobs import remote
from snowflake.ml.registry import Registry

@remote("CPU_X64_M", stage_name="TRAINING_STAGE", pip_requirements=["scikit-learn"])
def train(training_table: str, n_estimators: int = 100):
    from snowflake.snowpark import Session
    
    session = Session.builder.getOrCreate()
    df = session.table(training_table).to_pandas()
    
    X_train, X_test, y_train, y_test = train_test_split(df)
    
    model = RandomForestClassifier(n_estimators=n_estimators)
    model.fit(X_train, y_train)
    accuracy = model.score(X_test, y_test)
    
    # Manual tracking via return + registry
    registry = Registry(session, database_name="DB", schema_name="SCHEMA")
    registry.log_model(
        model,
        model_name="RF_MODEL",
        version_name="V1",
        metrics={"accuracy": accuracy}
    )
    
    return {
        "model": model,
        "metrics": {"accuracy": accuracy},
        "params": {"n_estimators": n_estimators}
    }
```

## Hyperopt Migration

### Databricks Hyperopt + SparkTrials
```python
from hyperopt import fmin, tpe, hp, SparkTrials

search_space = {
    "lr": hp.loguniform("lr", -5, -1),
    "n_est": hp.quniform("n_est", 50, 500, 1)
}

spark_trials = SparkTrials(parallelism=4)

best = fmin(
    train_fn,
    search_space,
    algo=tpe.suggest,
    max_evals=50,
    trials=spark_trials
)
```

### Snowflake Tuner
```python
from snowflake.ml.modeling.tune import Tuner, TunerConfig
from snowflake.ml.modeling.tune import loguniform, randint
from snowflake.ml.modeling.tune.search import RandomSearch

def train_func(config, dataset_map):
    lr = config["lr"]
    n_est = config["n_est"]
    train_df = dataset_map["train"]
    
    model = train_model(train_df, lr=lr, n_estimators=n_est)
    accuracy = evaluate(model)
    return {"accuracy": accuracy}

search_space = {
    "lr": loguniform(1e-5, 0.1),
    "n_est": randint(50, 500)
}

tuner_config = TunerConfig(
    metric="accuracy",
    mode="max",
    search_alg=RandomSearch(),
    num_trials=50,
    max_concurrent_trials=4
)

tuner = Tuner(train_func, search_space, tuner_config)
results = tuner.run(dataset_map={"train": train_df})
```

## TorchDistributor Migration

### Databricks TorchDistributor
```python
from pyspark.ml.torch.distributor import TorchDistributor

distributor = TorchDistributor(
    num_processes=4,
    local_mode=False,
    use_gpu=True
)

result = distributor.run(train_fn)
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
        train_fn,
        scaling_config=ScalingConfig(
            num_workers=4,
            use_gpu=True
        )
    )
    return trainer.fit()
```

## Notebook Conversion

### Key Differences
| Databricks Notebook | Snowflake ML Job |
|--------------------|------------------|
| `%sql SELECT ...` | `session.sql("SELECT ...")` |
| `%python` cells | Python script |
| `display(df)` | Return df or print |
| `dbutils.widgets` | Function parameters |
| `%run ./other_notebook` | Import module |

### Example Conversion
```python
# BEFORE: Databricks notebook cells

# Cell 1: %sql
# CREATE TABLE features AS SELECT ...

# Cell 2: %python
df = spark.read.table("features")
model = train(df)
mlflow.log_model(model, "model")

# AFTER: Snowflake ML Job
from snowflake.ml.jobs import remote

@remote("CPU_X64_M", stage_name="TRAINING_STAGE")
def train_job(input_table: str):
    from snowflake.snowpark import Session
    from snowflake.ml.registry import Registry
    
    session = Session.builder.getOrCreate()
    
    # Create features table
    session.sql("""
        CREATE TABLE IF NOT EXISTS features AS SELECT ...
    """).collect()
    
    # Train model
    df = session.table("features").to_pandas()
    model = train(df)
    
    # Log model
    registry = Registry(session, database_name="DB", schema_name="SCHEMA")
    registry.log_model(model, model_name="MODEL", version_name="V1")
    
    return model
```

## MLflow Logging Conversion

| MLflow | Snowflake | Notes |
|--------|-----------|-------|
| `mlflow.log_param("key", val)` | Return in dict | `{"params": {"key": val}}` |
| `mlflow.log_metric("key", val)` | Return or Registry | `metrics={"key": val}` |
| `mlflow.log_model(model)` | `registry.log_model()` | Model Registry |
| `mlflow.log_artifact("path")` | Stage upload | `session.file.put()` |
| `mlflow.autolog()` | Manual tracking | Must be explicit |

## Conversion Checklist

- [ ] Replace `spark.read.table()` with `session.table()`
- [ ] Replace `mlflow.autolog()` with manual tracking
- [ ] Replace `mlflow.log_model()` with `registry.log_model()`
- [ ] Replace `mlflow.log_param/metric()` with return values
- [ ] Replace `TorchDistributor` with Ray
- [ ] Replace `SparkTrials` / Hyperopt with Snowflake Tuner
- [ ] Replace `dbutils.fs.*` with stage operations
- [ ] Convert `.toPandas()` to `.to_pandas()`
- [ ] Convert notebook cells to Python script
- [ ] Replace `display()` with return or logging
