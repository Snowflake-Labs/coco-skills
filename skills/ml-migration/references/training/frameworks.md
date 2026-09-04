# Framework Conversion Patterns

Quick reference for converting training scripts to Snowflake ML Jobs.

> **For complete ML Jobs templates and submission patterns, see `../../ml-jobs/SKILL.md`.**
> This file contains migration-specific mappings only.

---

## Common Conversion Patterns

| Original Pattern | Snowflake Equivalent |
|------------------|---------------------|
| `pd.read_csv("file.csv")` | `session.table("TABLE").to_pandas()` |
| `model.save("model.pkl")` | `registry.log_model(model, ...)` |
| `torch.save(model.state_dict(), "model.pth")` | `registry.log_model(model, ...)` |
| `model.save("model.keras")` | `registry.log_model(model, ...)` |
| `model.save_model("model.ubj")` | `registry.log_model(model, ...)` |
| `print(metrics)` | `return {"metrics": metrics}` |
| Local file paths | Stage paths or Snowflake tables |
| Environment variables | Function parameters |
| `if __name__ == "__main__"` | `@remote` decorator |
| Hardcoded credentials | `Session.builder.getOrCreate()` |

---

## Data Loading Comparison

| Source Platform | Snowflake Equivalent |
|-----------------|---------------------|
| `pd.read_csv("s3://...")` | `session.table("TABLE").to_pandas()` |
| `pd.read_csv("abfs://...")` | `session.table("TABLE").to_pandas()` |
| `pd.read_csv("gs://...")` | `session.table("TABLE").to_pandas()` |
| `spark.read.table(...)` | `session.table("TABLE")` |
| `spark.read.parquet(...)` | `session.table("TABLE")` |
| `tf.data.Dataset.from_...` | Convert from Snowpark DataFrame |
| `torch.utils.data.DataLoader` | Load from `session.table().to_pandas()` |
| Local files | Stage files via `session.file.get()` |

---

## Model Saving Comparison

| Source Pattern | Snowflake Equivalent |
|----------------|---------------------|
| `pickle.dump(model, f)` | `registry.log_model(model, model_name=..., version_name=...)` |
| `joblib.dump(model, path)` | `registry.log_model(model, ...)` |
| `torch.save(model, path)` | `registry.log_model(model, sample_input_data=...)` |
| `model.save(path)` (Keras) | `registry.log_model(model, sample_input_data=...)` |
| `mlflow.log_model(model)` | `registry.log_model(model, ...)` |
| S3/GCS/Azure upload | Model Registry handles storage |

---

## Framework-Specific Notes

### sklearn / XGBoost / LightGBM
- Use `CPU_X64_M` compute pool (no GPU needed)
- Pre-installed in ML Runtime - no `pip_requirements` needed for core packages
- Use sklearn API wrappers (e.g., `XGBClassifier`) for easier registry integration

### PyTorch
- Use `GPU_NV_S` or larger for GPU training
- Add `.cuda()` calls for GPU tensors
- Provide `sample_input_data` for `log_model()` schema inference
- Return `model.cpu()` before registration

### TensorFlow/Keras
- Use `GPU_NV_S` or larger for GPU training
- Pre-installed in ML Runtime
- Provide `sample_input_data` for `log_model()` schema inference

---

## Next Steps

For complete submission patterns, templates, and job management:
- **See `../../ml-jobs/SKILL.md`** - Steps 5-7 for submission templates
- **See `../../model-registry/SKILL.md`** - for `log_model()` parameters and verification
