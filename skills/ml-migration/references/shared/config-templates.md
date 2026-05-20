# Migration Config Templates

Unified configuration file format for ML migrations.

## Config File Location

```
migration-config.yaml
```

## ⛔ CRITICAL: Required Reads Tracking

**The `required_reads` section is MANDATORY.** It ensures all reference files are actually read before proceeding.

### How It Works

1. **At each phase**, the skill specifies which files MUST be read
2. **Before reading**, add the file to `required_reads` with `status: pending`
3. **After reading** (using the Read tool), update to `status: read`
4. **Before proceeding** to the next phase, verify ALL required files for that phase show `status: read`
5. **NEVER proceed** if any required file shows `status: pending`

### Required Reads Section

```yaml
required_reads:
  # Phase 0 - Always required
  - file: "RULES.md"
    phase: "0"
    status: pending  # → read (after using Read tool)
    
  # Phase 2 - Platform-specific (add based on detected platform)
  - file: "references/platforms/<platform>/common.md"
    phase: "2"
    status: pending
    
  # Workflow-specific (add based on detected workflow)
  # INFERENCE:
  - file: "references/platforms/<platform>/inference.md"
    phase: "I1"
    status: pending
  - file: "references/shared/config-templates.md"
    phase: "I3"
    status: pending
  - file: "references/inference/source-access.md"
    phase: "I4"
    status: pending
  - file: "../model-registry/SKILL.md"
    phase: "I7"
    status: pending
  - file: "../spcs-inference/SKILL.md"
    phase: "I7"
    status: pending  # Only if SPCS deployment
    
  # TRAINING:
  - file: "references/platforms/<platform>/training.md"
    phase: "T1"
    status: pending
  - file: "references/training/frameworks.md"
    phase: "T2"
    status: pending
  - file: "references/training/distributed.md"
    phase: "T2"
    status: pending  # Only if distributed/multi-GPU
  - file: "../ml-jobs/SKILL.md"
    phase: "T3"
    status: pending
  - file: "references/shared/config-templates.md"
    phase: "T4"
    status: pending
```

### Verification Before Phase Transition

**MANDATORY CHECK** before moving to next phase:

```python
# Pseudocode - agent must verify this mentally
def can_proceed_to_phase(target_phase, config):
    required_for_phase = [r for r in config["required_reads"] 
                          if r["phase"] == target_phase or r["phase"] < target_phase]
    unread = [r for r in required_for_phase if r["status"] == "pending"]
    
    if unread:
        print(f"⛔ BLOCKED: Must read these files first:")
        for r in unread:
            print(f"  - {r['file']}")
        return False
    return True
```

---

## Full Template (with Required Reads)

```yaml
# migration-config.yaml
version: "1.0"

# ========================================
# REQUIRED READS TRACKING
# ========================================
# ⛔ CRITICAL: Update status from "pending" to "read" ONLY AFTER 
# actually reading each file with the Read tool.
# DO NOT proceed to a phase until all files for that phase are "read".

required_reads:
  # Phase 0 - Rules (ALWAYS required)
  - file: "RULES.md"
    phase: "0"
    status: pending
    
  # Phase 2 - Platform common (filled after platform detection)
  # Example for SageMaker:
  - file: "references/platforms/sagemaker/common.md"
    phase: "2"
    status: pending

# Add workflow-specific reads as you proceed (see templates below)

# ========================================
# MIGRATION CONFIGURATION
# ========================================

migration_type: inference  # inference, training, or both

source:
  platform: sagemaker  # sagemaker, azure-ml, vertex-ai, databricks
  
  # For inference migrations
  model:
    type: endpoint      # endpoint, model-registry, artifact
    endpoint_name: my-endpoint
    
  # For training migrations
  training:
    script_path: ./train.py
    
  # Cloud credentials profile
  credentials:
    profile: default

target:
  connection: demo
  database: ML_MODELS
  schema: PRODUCTION
  
  registry:
    model_name: MIGRATED_MODEL
    version: v1
    
  compute:
    pool: CPU_X64_M
    service_pool: GPU_NV_S

inference:
  target_platform: WAREHOUSE  # WAREHOUSE or SNOWPARK_CONTAINER_SERVICES
  spcs:
    service_name: MODEL_ENDPOINT
    ingress: public
    max_instances: 1

training:
  job_type: single  # single, distributed, hpo
  
dependencies:
  pip:
    - scikit-learn==1.2.2
```

---

## Required Reads by Workflow

### Inference Workflow Required Reads

Add these to `required_reads` when inference workflow is detected:

```yaml
required_reads:
  # Phase 0
  - file: "RULES.md"
    phase: "0"
    status: pending
    
  # Phase 2 - Platform (replace <platform> with detected)
  - file: "references/platforms/<platform>/common.md"
    phase: "2"
    status: pending
    
  # I1 - Platform inference patterns
  - file: "references/platforms/<platform>/inference.md"
    phase: "I1"
    status: pending
    
  # I3 - Config templates
  - file: "references/shared/config-templates.md"
    phase: "I3"
    status: pending
    
  # I4 - Source access
  - file: "references/inference/source-access.md"
    phase: "I4"
    status: pending
    
  # I7 - Model Registry (ALWAYS for inference)
  - file: "../model-registry/SKILL.md"
    phase: "I7"
    status: pending
    
  # I7 - SPCS (only if target_platform: SNOWPARK_CONTAINER_SERVICES)
  - file: "../spcs-inference/SKILL.md"
    phase: "I7"
    status: pending
    required_if: "inference.target_platform == SNOWPARK_CONTAINER_SERVICES"
```

### Training Workflow Required Reads

Add these to `required_reads` when training workflow is detected:

```yaml
required_reads:
  # Phase 0
  - file: "RULES.md"
    phase: "0"
    status: pending
    
  # Phase 2 - Platform (replace <platform> with detected)
  - file: "references/platforms/<platform>/common.md"
    phase: "2"
    status: pending
    
  # T1 - Platform training patterns
  - file: "references/platforms/<platform>/training.md"
    phase: "T1"
    status: pending
    
  # T2 - Framework patterns
  - file: "references/training/frameworks.md"
    phase: "T2"
    status: pending
    
  # T2 - Distributed (only if complexity: distributed or multi-gpu)
  - file: "references/training/distributed.md"
    phase: "T2"
    status: pending
    required_if: "training.job_type in [distributed, hpo]"
    
  # T3 - ML Jobs (ALWAYS for training)
  - file: "../ml-jobs/SKILL.md"
    phase: "T3"
    status: pending
    
  # T4 - Config templates
  - file: "references/shared/config-templates.md"
    phase: "T4"
    status: pending
```

---

## Config Validation

### Required Fields by Migration Type

#### Inference
- `source.platform`
- `source.model.type`
- `target.connection`
- `target.database`
- `target.schema`
- `target.registry.model_name`
- `required_reads` (with all phase reads marked as "read")

#### Training
- `source.platform`
- `source.training.script_path`
- `target.connection`
- `target.database`
- `target.schema`
- `target.compute.pool`
- `required_reads` (with all phase reads marked as "read")

---

## Config Loading

```python
import yaml

def load_config(path: str = "migration-config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)

def check_required_reads(config: dict, current_phase: str) -> list:
    """Returns list of unread files that block proceeding to current_phase."""
    unread = []
    for read in config.get("required_reads", []):
        # Check if this read is required for current or earlier phase
        if read["status"] == "pending":
            unread.append(read["file"])
    return unread

def mark_as_read(config: dict, filepath: str) -> None:
    """Mark a file as read. Call ONLY after actually reading it."""
    for read in config.get("required_reads", []):
        if read["file"] == filepath:
            read["status"] = "read"
            break

def save_config(config: dict, path: str = "migration-config.yaml") -> None:
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
```
