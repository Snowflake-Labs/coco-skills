---
name: ml-migration
title: ML Workload Migration
summary: Migrate ML workloads from SageMaker, Azure ML, Vertex AI, or Databricks into Snowflake Model Registry, SPCS, and ML Jobs.
description: |
  Use when migrating a model inference endpoint, a saved model artifact, or a training script from SageMaker, Azure ML, Vertex AI, or Databricks to Snowflake — covering Model Registry registration, SPCS real-time deployment, warehouse batch inference, and ML Jobs training. Triggers: migrate model, import model, deploy endpoint, convert training script, ML Jobs migration, SageMaker migration, Vertex AI migration, Azure ML migration, Databricks ML migration, Model Registry migration, SPCS deployment.
tools:
  - snowflake_sql_execute
  - snowflake_object_search
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
prompt: Migrate my SageMaker XGBoost endpoint to Snowflake Model Registry.
language: en
status: Published
author: Snowflake Solutions Team
type: snowflake
parent_skill: machine-learning-v2
---

# ML Migration

> Create all resources in the current working directory, not `/tmp/`.

## Overview

Two workflows under one skill:

- **INFERENCE** — migrate a trained model or live endpoint into Snowflake Model Registry, with serving on a warehouse (batch) or SPCS (real-time).
- **TRAINING** — convert a training script into a Snowflake ML Job that runs on a compute pool and registers the result.

Source platform is auto-detected from code/SDK patterns. All decisions go through `migration-config.yaml`. The skill never picks resources for the user.

## When to Use

- You have a SageMaker, Azure ML, Vertex AI, or Databricks model and want SQL-callable inference via `MODEL!PREDICT()`.
- You need to lift-and-shift a container endpoint to SPCS, or extract the model and re-register it natively.
- You have a `train.py` (or notebook) that should run on Snowflake compute pools instead of an external platform.

## Workflow

```
Phase 0  → init migration-config.yaml
Phase 1  → ask: generate-only OR generate-and-run
Phase 2  → detect platform + asset type → INFERENCE or TRAINING
```

**INFERENCE (I1–I8)**
1. **I1** Asset type → `docker-lift-and-shift` OR Model Registry (extract).
2. **I2** Show roles, databases, schemas, warehouses, compute pools, image repos, EAIs. Ask the user to select. No defaults.
3. **I3** Generate `migration-config.yaml` → **STOP** for confirmation.
4. **I4** Source access via `aws`/`az`/`gcloud`/`databricks` profile.
5. **I5** Load model, extract framework, signature, deps. Flag exceptions (e.g. `xgb.core.Booster` needs `CustomModel`).
6. **I6** Present migration plan → **STOP**.
7. **I7** Read `../model-registry/SKILL.md` (and `../spcs-inference/SKILL.md` if real-time), then execute.
8. **I8** Validate: `SELECT MODEL!PREDICT(...)` and `mv.run(test_data)`.

**TRAINING (T1–T9)**
1. **T1** Detect platform from imports/env vars.
2. **T2** Classify complexity: single-node, multi-GPU, distributed, HPO.
3. **T3** `SHOW COMPUTE POOLS` / `SHOW STAGES` / `SHOW EXTERNAL ACCESS INTEGRATIONS`. Ask user to select.
4. **T4** Generate config → **STOP**.
5. **T5** Map data loading → `session.table()`/`DataConnector`; model saving → `registry.log_model()`; env vars → function args; hyperparams → CLI flags.
6. **T6** Present conversion plan → **STOP**.
7. **T7** Emit `migrated_training/{train.py, launcher.py, requirements.txt, README.md}`.
8. **T8** `python -m py_compile`, run a small ML Job, `SHOW MODELS`.
9. **T9** Migration report.

## Source Detection

| Indicator | Platform |
|---|---|
| `import sagemaker`, `SM_*` env vars, `:sagemaker:` ARN | SageMaker |
| `azureml.core` / `azure.ai.ml`, `MLClient`, `${{inputs.*}}` | Azure ML |
| `google.cloud.aiplatform`, `AIP_*` env vars, `gs://` paths | Vertex AI |
| `mlflow.*`, `spark.*`, `dbutils.*`, Databricks workspace URLs | Databricks |

## Snowflake Targets

- **Model Registry** — `snowflake.ml.registry.Registry.log_model()`, then `MODEL!PREDICT()`.
- **Warehouse inference** — default for sklearn, XGBoost, LightGBM, CatBoost, Prophet, PyTorch, TensorFlow, Keras, Sentence Transformers, Hugging Face pipelines.
- **SPCS inference** — container service on a compute pool with optional public ingress (requires `BIND SERVICE ENDPOINT`).
- **ML Jobs** — `submit_file()` runs `train.py` on a compute pool.

## Required Reads

Track each in `migration-config.yaml` under `required_reads` (`pending` → `read`). Do not skip the gate.

| Phase | Files |
|---|---|
| 2 | `references/platforms/<platform>/common.md` |
| I1 | `references/platforms/<platform>/inference.md` |
| I3, T4 | `references/shared/config-templates.md` |
| I4 | `references/inference/source-access.md` |
| I7 | `../model-registry/SKILL.md`, `../spcs-inference/SKILL.md` (SPCS only) |
| T1 | `references/platforms/<platform>/training.md` |
| T2 | `references/training/frameworks.md`, `distributed.md` if distributed |
| T3 | `../ml-jobs/SKILL.md` |

## Common Mistakes

- Picking a role/database/warehouse/compute pool from `SHOW ...` output instead of asking the user.
- Skipping the I3/I6/T4/T6 stopping points and running the migration unconfirmed.
- Calling `log_model()` on `xgb.core.Booster` without a `CustomModel` wrapper.
- Forgetting `external_access_integrations` on the ML Job, so `pip install` fails inside the job.
- Hardcoding `s3://`, `gs://`, or `dbfs://` paths instead of `session.table()` / `DataConnector`.
- Enabling SPCS public ingress without granting `BIND SERVICE ENDPOINT` first.
- Registering a model without `sample_input_data` — signature inference fails silently downstream.
- Reading `references/inference/xgboost-booster.md` and treating it as a substitute for `../model-registry/SKILL.md`.

## Red Flags

Refuse and stop on any of these rationalizations:

- "I'll just pick the first warehouse from `SHOW WAREHOUSES`." → No. Ask the user.
- "The user probably wants Model Registry over lift-and-shift." → No. Ask, don't assume.
- "If the selected compute pool is full, I'll fall back to another." → No fallbacks. Stop and report.
- "I've read enough reference files; I can skip `../model-registry/SKILL.md`." → No. The actual `log_model()` parameters and grants live there.
- "Container Runtime probably has this package, no EAI needed." → Verify with `SHOW EXTERNAL ACCESS INTEGRATIONS`.
- "I can infer the model signature without sample input." → No. Sample input is required.
- "I'll proceed even though `required_reads` shows `pending`." → No. The gate is load-bearing.

## Output

- `migration-config.yaml` — single source of truth.
- `rules/migration-rule.md` — copied from skill `RULES.md` in Phase 0.
- Registered Snowflake model in Model Registry.
- SPCS service (real-time inference only).
- `migrated_training/` directory (training workflow only).
- Migration report summarizing source, target, and changes.
