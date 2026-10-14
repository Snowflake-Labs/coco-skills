# XGBoost Booster to Snowflake Model Registry

Migrating raw XGBoost Booster objects (not sklearn wrappers) to Snowflake.

## Problem

Raw `xgboost.core.Booster` objects cannot be registered directly to Snowflake Model Registry using `log_model()`. Only sklearn-style wrappers (`XGBClassifier`, `XGBRegressor`) have native support.

## Detection

```python
import xgboost as xgb

if isinstance(model, xgb.core.Booster):
    # CustomModel wrapper REQUIRED - use pattern below
    pass
elif isinstance(model, (xgb.XGBClassifier, xgb.XGBRegressor)):
    # Direct registration OK
    registry.log_model(model=model, ...)
```

## Solution: CustomModel Wrapper

```python
from snowflake.ml.model import custom_model
import xgboost as xgb
import pandas as pd
import pickle

class XGBoostBoosterModel(custom_model.CustomModel):
    """
    CustomModel wrapper for raw XGBoost Booster objects.
    Required because xgb.core.Booster lacks sklearn interface.
    """
    
    def __init__(self, context: custom_model.ModelContext) -> None:
        super().__init__(context)
        with open(context.path('model.pkl'), 'rb') as f:
            self.booster = pickle.load(f)
    
    @custom_model.inference_api
    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        """Generate predictions using the XGBoost Booster."""
        dmatrix = xgb.DMatrix(X, feature_names=self.booster.feature_names)
        predictions = self.booster.predict(dmatrix)
        return pd.DataFrame({'PREDICTION': predictions})
```

## Complete Registration Example

```python
# Step 1: Save booster to temp file for artifact
artifact_path = '/tmp/model.pkl'
with open(artifact_path, 'wb') as f:
    pickle.dump(booster, f)

# Step 2: Create ModelContext with artifact
mc = custom_model.ModelContext(
    artifacts={'model.pkl': artifact_path}
)

# Step 3: Instantiate custom model
model = XGBoostBoosterModel(mc)

# Step 4: Test locally before registration
test_result = model.predict(sample_input)
print(f"Local test:\n{test_result}")

# Step 5: Register to Model Registry
mv = registry.log_model(
    model=model,
    model_name='MY_XGB_MODEL',
    version_name='V1',
    sample_input_data=sample_df,
    conda_dependencies=['xgboost>=2.0.0'],
    comment='XGBoost Booster migrated with CustomModel wrapper'
)
```

## Extracting Booster Metadata

Critical for creating correct `sample_input_data`:

```python
import json

# Feature information
feature_names = booster.feature_names  # List[str]
num_features = booster.num_features()

# Model configuration
config = json.loads(booster.save_config())
objective = config['learner']['objective']['name']
num_rounds = booster.num_boosted_rounds()

print(f"Features: {num_features}")
print(f"Feature names: {feature_names}")
print(f"Objective: {objective}")
print(f"Trees: {num_rounds}")
```

## Creating sample_input_data

Use EXACT feature names from booster:

```python
import pandas as pd

feature_names = booster.feature_names

# Create sample with correct columns and types
sample_data = {name: [1.0, 2.0] for name in feature_names}
sample_input = pd.DataFrame(sample_data)

# Verify column order matches
assert list(sample_input.columns) == feature_names
```

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| Version warning on pickle load | Model saved with older XGBoost | Safe to ignore |
| `sample_input_data required` | Missing sample data | Provide DataFrame with correct feature names |
| Wrong predictions | Feature order mismatch | Use `feature_names` in DMatrix |
| `Booster has no predict_proba` | Using raw Booster like sklearn | Booster's `predict()` returns probabilities |

## When to Use This Pattern

- Model type is `xgboost.core.Booster` (not sklearn wrapper)
- Migrating XGBoost models trained with raw `xgb.train()` API
- Models saved as pickle of Booster object
- GPU-trained XGBoost models (often use Booster directly)

## SQL Usage After Registration

```sql
SELECT 
    MY_XGB_MODEL!PREDICT(feature1, feature2, feature3):PREDICTION::FLOAT as probability
FROM my_table;
```
