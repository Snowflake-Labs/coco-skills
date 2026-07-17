# Distributed Training Patterns

Quick reference for migrating distributed and multi-GPU training to Snowflake ML Jobs.

> **For complete distributed training patterns and code templates, see `../../ml-jobs/SKILL.md` Multi-Node Jobs section.**
> This file contains platform migration mappings only.

---

## Options Overview

| Type | Use Case | Configuration |
|------|----------|---------------|
| **Multi-GPU single node** | Medium models | `GPU_NV_M/L`, `target_instances=1` |
| **Ray distributed** | Large scale | `GPU_NV_S/M`, `target_instances=2+` |
| **PyTorch DDP** | Data parallelism | Via Ray TorchTrainer |

---

## Platform Migration Reference

### SageMaker Distributed → Snowflake

| SageMaker | Snowflake |
|-----------|-----------|
| `instance_count=4` | `target_instances=4` |
| `smdistributed.dataparallel` | Ray TorchTrainer |
| `smdistributed.modelparallel` | Ray TorchTrainer with FSDP |
| `ml.p3.16xlarge` | `GPU_NV_M` or `GPU_NV_L` |
| `ml.p4d.24xlarge` | `GPU_NV_L` |
| `S3DataSource` | `session.table()` |
| `SM_NUM_GPUS` | `torch.cuda.device_count()` |
| `SM_HOSTS` / `SM_CURRENT_HOST` | Ray handles node discovery |

### Azure ML Distributed → Snowflake

| Azure ML | Snowflake |
|----------|-----------|
| `ResourceConfiguration(instance_count=2)` | `target_instances=2` |
| `distribution={"type": "PyTorch"}` | Ray TorchTrainer |
| `distribution={"type": "MPI"}` | Ray TorchTrainer |
| `process_count_per_instance=4` | `ScalingConfig(num_workers=4)` |
| `Standard_NC24ads_A100_v4` | `GPU_NV_L` |
| `Standard_NC6s_v3` | `GPU_NV_S` |
| `AZUREML_DATAREFERENCE_*` | `session.table()` |

### Vertex AI Distributed → Snowflake

| Vertex AI | Snowflake |
|-----------|-----------|
| `replica_count=4` | `target_instances=4` |
| `machine_type="n1-standard-8"` | `CPU_X64_M` |
| `accelerator_type="NVIDIA_TESLA_V100"` | `GPU_NV_M` |
| `accelerator_count=4` | `ScalingConfig(num_workers=4, use_gpu=True)` |
| `AIP_MODEL_DIR` | `MLRS_STAGE_RESULT_PATH` |

### Databricks TorchDistributor → Snowflake

| Databricks | Snowflake |
|------------|-----------|
| `TorchDistributor(num_processes=8)` | `ScalingConfig(num_workers=8)` |
| `distributor.run(train_fn)` | `TorchTrainer(train_fn).fit()` |
| `local_mode=True` | `target_instances=1` |
| `num_gpus_per_node=4` | `GPU_NV_M` (4 GPUs per node) |
| `spark.read.table(...)` | `session.table(...)` |

---

## Resource Selection Guide

| Training Type | Compute Pool | target_instances |
|--------------|--------------|------------------|
| Single GPU | GPU_NV_S | 1 |
| Multi-GPU single node | GPU_NV_M or GPU_NV_L | 1 |
| Distributed (small) | GPU_NV_S | 2-4 |
| Distributed (large) | GPU_NV_M | 4-8 |
| Very large models | GPU_NV_L | 4+ |

---

## Key Conversion Notes

| Source Pattern | Snowflake Approach |
|----------------|-------------------|
| `torch.distributed.init_process_group()` | Ray handles initialization |
| `DistributedDataParallel(model)` | `ray.train.torch.prepare_model(model)` |
| `DistributedSampler` | Ray `get_dataset_shard()` |
| Manual checkpoint saving | Ray `Checkpoint` API |
| Environment-based rank detection | Ray handles worker coordination |

---

## Next Steps

For complete code templates and submission patterns:
- **See `../../ml-jobs/SKILL.md`** - Multi-Node Jobs section for `target_instances` usage
- **See `../../ml-jobs/SKILL.md`** - Step 6 templates for job submission
