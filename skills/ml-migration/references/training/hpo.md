# Hyperparameter Optimization Patterns

Converting HPO jobs to Snowflake ML Jobs using the **native Snowflake Tuner API**.

## ⛔ CRITICAL: Execution Model

**The Tuner API runs in Container Runtime, NOT locally.**

| Script Type | Where it runs | Can import Tuner? |
|-------------|---------------|-------------------|
| **Launcher** (submits the job) | Local machine | ❌ NO - `ModuleNotFoundError` |
| **Training** (submitted via `submit_file`) | Container Runtime | ✅ YES |

```python
# ❌ WRONG - launcher.py (local)
from snowflake.ml.modeling.tune import Tuner  # ModuleNotFoundError!

# ✅ CORRECT - launcher.py (local)
from snowflake.ml.jobs import submit_file
job = submit_file("train_hpo.py", "COMPUTE_POOL", stage_name="STAGE")

# ✅ CORRECT - train_hpo.py (runs in Container Runtime)
from snowflake.ml.modeling.tune import Tuner, TunerConfig  # Works here!
```

## ⚠️ CRITICAL: Use Native Snowflake Tuner

**DO NOT use Optuna or Ray Tune patterns.** Use the native `snowflake.ml.modeling.tune.Tuner` API.

**Before writing HPO code, fetch docs:** `https://docs.snowflake.com/en/developer-guide/snowpark-ml/reference/latest/container-runtime/tune.tuner`

## Quick Reference

| Component | Import |
|-----------|--------|
| Tuner | `from snowflake.ml.modeling.tune import Tuner, TunerConfig` |
| Sampling | `from snowflake.ml.modeling.tune import uniform, loguniform, randint, choice` |
| Context | `from snowflake.ml.modeling.tune import get_tuner_context` |
| Search | `from snowflake.ml.modeling.tune.search import BayesOpt, RandomSearch, GridSearch` |

## ⚠️ Search Algorithm Compatibility

**BayesOpt only supports continuous parameters:**

| Algorithm | `uniform()` | `loguniform()` | `randint()` | `choice()` |
|-----------|-------------|----------------|-------------|------------|
| **BayesOpt** | ✅ | ✅ | ❌ | ❌ |
| **RandomSearch** | ✅ | ✅ | ✅ | ✅ |
| **GridSearch** | ❌ | ❌ | ❌ | ✅ (lists only) |

**If migrating from platform using Bayesian + integers:** Either switch to `RandomSearch()` or use `uniform()` and cast to `int` in `train_func`.

## Basic Pattern

```python
from snowflake.ml.modeling.tune import Tuner, TunerConfig, uniform, loguniform, randint
from snowflake.ml.modeling.tune import get_tuner_context
from snowflake.ml.modeling.tune.search import RandomSearch
from snowflake.ml.data import DataConnector
from snowflake.ml.registry import Registry
from snowflake.snowpark import Session

session = Session.builder.getOrCreate()

# Prepare data as DataConnectors
train_df = session.table("DB.SCHEMA.TRAIN_DATA").to_pandas()
test_df = session.table("DB.SCHEMA.TEST_DATA").to_pandas()

dataset_map = {
    "train": DataConnector.from_dataframe(session.create_dataframe(train_df)),
    "test": DataConnector.from_dataframe(session.create_dataframe(test_df)),
}

# Define training function
def train_func():
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.metrics import accuracy_score
    
    tuner_context = get_tuner_context()
    config = tuner_context.get_hyper_params()
    dm = tuner_context.get_dataset_map()
    
    train_data = dm["train"].to_pandas()
    test_data = dm["test"].to_pandas()
    
    X_train, y_train = train_data.drop("TARGET", axis=1), train_data["TARGET"]
    X_test, y_test = test_data.drop("TARGET", axis=1), test_data["TARGET"]
    
    model = GradientBoostingClassifier(
        n_estimators=config["n_estimators"],
        max_depth=config["max_depth"],
        learning_rate=config["learning_rate"]
    )
    model.fit(X_train, y_train)
    
    accuracy = accuracy_score(y_test, model.predict(X_test))
    
    # CRITICAL: Report metrics AND model
    tuner_context.report(metrics={"accuracy": accuracy}, model=model)

# Search space
search_space = {
    "n_estimators": randint(50, 500),
    "max_depth": randint(3, 15),
    "learning_rate": loguniform(0.01, 0.3),
}

# Config
tuner_config = TunerConfig(
    metric="accuracy",
    mode="max",
    search_alg=RandomSearch(),
    num_trials=50,
    max_concurrent_trials=4,
)

# Run
tuner = Tuner(train_func, search_space, tuner_config)
results = tuner.run(dataset_map=dataset_map)

# MANDATORY: Register best model
registry = Registry(session, database_name="DB", schema_name="SCHEMA")
registry.log_model(results.best_model, model_name="TUNED_MODEL", version_name="V1")
```

## BayesOpt with Integer Params (Workaround)

```python
# Use uniform() and cast in train_func
search_space = {
    "n_estimators": uniform(50, 500),  # NOT randint
    "max_depth": uniform(3, 15),
}

def train_func():
    config = get_tuner_context().get_hyper_params()
    n_estimators = int(round(config["n_estimators"]))
    max_depth = int(round(config["max_depth"]))
    # ...

tuner_config = TunerConfig(search_alg=BayesOpt(), ...)
```

## Multi-Node HPO

```python
from snowflake.ml.runtime_cluster import scale_cluster

scale_cluster(2)  # Scale BEFORE running tuner
results = tuner.run(dataset_map=dataset_map)
```

## GPU Allocation

```python
tuner_config = TunerConfig(
    metric="accuracy",
    mode="max",
    search_alg=BayesOpt(),
    num_trials=50,
    resource_per_trial={"CPU": 2, "GPU": 1},
)
```

## Platform Migration Reference

### SageMaker HPO → Snowflake

| SageMaker | Snowflake |
|-----------|-----------|
| `HyperparameterTuner` | `Tuner` |
| `ContinuousParameter` | `uniform()` / `loguniform()` |
| `IntegerParameter` | `randint()` (RandomSearch) or `uniform()` + cast (BayesOpt) |
| `CategoricalParameter` | `choice()` (RandomSearch only) |

### Azure ML HPO → Snowflake

| Azure ML | Snowflake |
|----------|-----------|
| `sweep()` | `Tuner.run()` |
| `uniform()` | `uniform()` |
| `choice()` | `choice()` (RandomSearch) |

### Optuna → Snowflake

| Optuna | Snowflake |
|--------|-----------|
| `study.optimize()` | `tuner.run()` |
| `trial.suggest_float()` | `uniform()` / `loguniform()` |
| `trial.suggest_int()` | `randint()` |
| `trial.suggest_categorical()` | `choice()` |

## Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `BayesOpt does not support Integer` | Using `randint()` with BayesOpt | Use `RandomSearch()` or `uniform()` + cast |
| `Model not registered` | Missing `tuner_context.report(model=...)` | Always pass model to report() |
| No results | Missing `dataset_map` | Pass dataset_map to `tuner.run()` |

## Sampling Functions

```python
from snowflake.ml.modeling.tune import uniform, loguniform, randint, choice

search_space = {
    "dropout": uniform(0.1, 0.5),           # Continuous [0.1, 0.5]
    "learning_rate": loguniform(1e-5, 1e-1), # Log-scale
    "n_layers": randint(1, 10),              # Integer [1, 10)
    "optimizer": choice(["adam", "sgd"]),    # Categorical
    "batch_size": [16, 32, 64],              # Grid (GridSearch only)
}
```
