---
name: ml-migration
title: ML Workload Migration
summary: Migrate ML training and inference workloads from SageMaker, Azure ML, Vertex AI, or Databricks to Snowflake.
description: Use when migrating ML models or training scripts from SageMaker, Azure ML, Vertex AI, or Databricks/MLflow into Snowflake Model Registry, SPCS, or ML Jobs. Triggers: migrate model, import model, deploy endpoint, convert training script, ML Jobs migration, sagemaker to snowflake, azureml to snowflake, vertex ai to snowflake, databricks to snowflake, mlflow migration.
tools:
  - snowflake_sql_execute
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
prompt: Migrate my SageMaker training script in train.py to Snowflake ML Jobs.
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
---

# ML Workload Migration

## Overview

This skill migrates ML workloads from SageMaker, Azure ML, Vertex AI, or Databricks/MLflow into Snowflake. Two paths:

- **Inference** — pretrained model files or live endpoints → Snowflake Model Registry (batch via warehouse) or SPCS service (real-time).
- **Training** — training scripts, pipelines, or notebooks → Snowflake ML Jobs running on compute pools.

Delegates to the bundled `machine-learning` skill for model registry, SPCS inference, and ML jobs workflows. Platform-specific patterns live under `references/platforms/<platform>/`.

## When to Use

- You have a model artifact (`.pkl`, `.pth`, `.h5`, `.tar.gz`) or live endpoint to bring into Snowflake.
- You have a training script (`train.py`) that imports `sagemaker`, `azureml`, `aiplatform`, or `mlflow`.
- You want SQL inference (`MODEL!PREDICT()`) instead of a remote endpoint.

Skip if: the model is already in Snowflake Model Registry, or the workflow has no Snowflake target.

## Workflow

### Phase 0 — Initialize

Create `migration-config.yaml` in the working directory. Read `RULES.md` and copy to `rules/migration-rule.md`.

### Phase 1 — Mode

Ask: generate code only, or generate and run?

### Phase 2 — Detect platform and route

Detect from imports / env vars / paths:

| Pattern | Platform |
|---|---|
| `sagemaker` SDK, `SM_*`, `/opt/ml/*` | SageMaker |
| `azureml.*`, `azure.ai.ml`, `MLClient` | Azure ML |
| `google.cloud.aiplatform`, `AIP_*`, `gs://` | Vertex AI |
| `mlflow.*`, `dbutils.*`, `spark.*` | Databricks |

Read `references/platforms/<platform>/common.md`. Then route by asset type — endpoint or model file → **Inference (I1–I8)**; script, pipeline, notebook → **Training (T1–T9)**.

### Inference path (I1–I8)

1. Read `references/platforms/<platform>/inference.md`.
2. For endpoints, ask: container lift-and-shift to SPCS, or extract model and register natively (enables `MODEL!PREDICT()`).
3. Run `SHOW ROLES; SHOW DATABASES; SHOW WAREHOUSES; SHOW COMPUTE POOLS; SHOW IMAGE REPOSITORIES; SHOW EXTERNAL ACCESS INTEGRATIONS;` and ask user to pick — never guess.
4. Generate full `migration-config.yaml`. ⚠️ STOPPING POINT: present config and wait for confirmation.
5. Download artifacts using user-selected cloud profile (`aws configure list-profiles`, `az account list`, `gcloud auth list`, `databricks auth profiles`).
6. Load model, identify framework (sklearn, XGBoost sklearn API, LightGBM, CatBoost, Prophet, PyTorch, TF/Keras, sentence-transformers, HF pipelines work directly; `xgb.core.Booster` needs `CustomModel`).
7. Present plan. ⚠️ STOPPING POINT: wait for approval.
8. Execute via the bundled `machine-learning` skill (model registry for batch, SPCS inference for real-time). Validate: `SELECT MODEL!PREDICT(col1, col2):prediction FROM test_table LIMIT 5;`.

### Training path (T1–T9)

1. Read `references/platforms/<platform>/training.md` and `references/training/frameworks.md`. Add `distributed.md` if `torch.distributed`/`horovod`/`DataParallel`/`MirroredStrategy` detected; add `hpo.md` for `optuna`/`HyperparameterTuner`.
2. Run `SHOW COMPUTE POOLS; SHOW STAGES; SHOW EXTERNAL ACCESS INTEGRATIONS;`. Delegate to the bundled `machine-learning` skill for compute pool selection and EAI patterns. Ask user to pick.
3. Generate `migration-config.yaml` with `compute_pool`, `stage_name`, `entry_point`, `pip_requirements`, model name/version. ⚠️ STOPPING POINT.
4. Analyze source: data loading → `session.table()` or `DataConnector`; model saves → `registry.log_model()` or stage upload; env vars → function args; hyperparams → argparse.
5. Present conversion plan. ⚠️ STOPPING POINT.
6. Convert into `migrated_training/{train.py, launcher.py, requirements.txt, README.md}` using the bundled `machine-learning` skill for ML jobs patterns.
7. Validate: `python -m py_compile`, submit small-data test, `SHOW MODELS IN SCHEMA <db>.<schema>;`.
8. Output migration report.

## Common Mistakes

- **Assuming default role/database/warehouse/compute pool.** Always `SHOW` and ask.
- **Skipping reference reads.** Wrong CLI, wrong auth, broken registration.
- **Falling back silently when a resource fails.** Stop and report; never substitute.
- **Re-asking for inputs already in `migration-config.yaml`.** Read the config.
- **Wrapping built-in framework models in `CustomModel`.** Only `xgb.core.Booster` and unknown frameworks need it.
- **Forgetting External Access Integration** for `pip_requirements` not in Container Runtime.
- **Mixing inference and training configs** in one file. Separate runs, separate configs.

## Red Flags

Refuse these rationalizations:

- "I'll just pick a warehouse to save time." → No. Ask the user.
- "The reference file is long, I'll skim from memory." → No. Read it.
- "If the configured stage doesn't exist, I'll create one." → No. Stop and report.
- "I'll register with `CustomModel` since it's more flexible." → No. Use direct `log_model()` for supported frameworks.
- "The user probably wants real-time, I'll deploy SPCS." → No. Ask batch vs real-time.
- "I'll skip the stopping point since the config looks fine." → No. Always confirm.

## Stopping Points

- **Phase 2 — config generation (Inference I3 / Training T4):** present full `migration-config.yaml` and wait for confirmation before any execution.
- **Inference I6 — migration plan:** present plan (stage, registration method, target, dependencies) and wait for approval.
- **Inference I7 — execution:** confirm before running registration / SPCS deployment.
- **Training T5 — code analysis:** confirm framework and complexity assessment before converting.
- **Training T6 — conversion plan:** confirm entry point, data/model patterns, dependencies before code rewrite.

## Error Handling

| Error | Cause | Fix |
|---|---|---|
| `No compute pool found` | None created | `CREATE COMPUTE POOL ...` with right instance family |
| `Package not available` | Not in Container Runtime | Add to `pip_requirements` + EAI |
| `Permission denied` | Role/grants | Re-check `SHOW GRANTS`, update config |
| `Model type not supported` | Unknown framework | Wrap in `CustomModel` |
| `BIND SERVICE ENDPOINT` | Public ingress privilege missing | Grant or set internal-only |

## Output

- `migration-config.yaml`
- `rules/migration-rule.md`
- Registered Snowflake model (Model Registry)
- SPCS service (if real-time)
- `migrated_training/` (if training)
- Migration report
