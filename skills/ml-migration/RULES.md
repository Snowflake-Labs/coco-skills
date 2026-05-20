# ML Migration Skill Rules

## ⛔ Required Reads Tracking (MANDATORY)

**This is the MOST IMPORTANT rule.** You MUST track all file reads in `migration-config.yaml`.

### Why This Exists

Agents often skip reading reference files even when instructed to read them. This tracking system ensures:
1. Every required file is actually read (using the Read tool)
2. Progress is visible and verifiable
3. You cannot proceed to the next phase without completing reads

### How to Track Reads

1. **Before each phase**, check `required_reads` in config for files needed
2. **Add the file** to `required_reads` with `status: pending`
3. **Actually read the file** using the Read tool
4. **Update status to `read`** in config ONLY AFTER reading
5. **Verify all phase reads** show `status: read` before proceeding

### Required Reads Format

```yaml
required_reads:
  - file: "path/to/file.md"
    phase: "I3"
    status: pending  # → read (after using Read tool)
```

### Gate Check Before Each Phase

**MANDATORY:** Before transitioning to any phase, output this check:

```
⛔ PHASE [X] GATE CHECK:
Required reads:
- [x] file1.md (status: read)
- [ ] file2.md (status: pending) ← BLOCKED

Status: BLOCKED - Must read file2.md first
```

**NEVER proceed with any `status: pending` reads for the current or earlier phases.**

### What Happens If You Skip Reads

- Wrong CLI commands for the detected platform
- Failed authentication patterns
- Incorrect model registration code
- Broken SPCS deployments
- User frustration and wasted time

### ⚠️ Sub-Skill Files ARE Required Reads

**CRITICAL:** Sub-skill files (`SKILL.md` from other skills) are tracked the same way as reference files.

| Phase | Required Sub-Skill | Why |
|-------|-------------------|-----|
| I7 | `../model-registry/SKILL.md` | Contains actual registration workflow |
| I7 | `../spcs-inference/SKILL.md` | Contains actual SPCS deployment workflow |
| T3 | `../ml-jobs/SKILL.md` | Contains actual job submission workflow |

**Common mistake:** Reading a reference file (like `xgboost-booster.md`) and skipping the sub-skill file. Reference files provide context, but sub-skill files provide the **workflow you must execute**.

```
❌ WRONG: Read xgboost-booster.md → Skip model-registry/SKILL.md → Guess at registration
✅ RIGHT: Read xgboost-booster.md → Read model-registry/SKILL.md → Follow its workflow
```

---

## Universal Rules

### Resource Selection

- **NEVER assume** which role, database, schema, warehouse, compute pool, or image repository to use
- **ALWAYS list available options** and ask the user to select
- Even if only one option exists, confirm with the user before proceeding
- Present options with brief descriptions (e.g., instance size, purpose)
- Make sure you are running all commands with the role the user specified

### Authentication

- **Use programmatic authentication** when possible instead of asking users to run commands manually
- **Snowflake image registry:**
  - **NEVER use username/password** - only token-based authentication
  - Use: `snow spcs image-registry token --format=JSON | $CONTAINER_CMD login <url> -u 0sessiontoken --password-stdin`
  - Get URL with: `snow spcs image-registry url --connection <conn>`
- For AWS: check `aws configure list-profiles` and ask user to select a profile
- For Azure: check `az account list` for available subscriptions
- For GCP: check `gcloud auth list` for authenticated accounts
- For Databricks: check `databricks auth profiles` for available profiles
- Only fall back to interactive login if programmatic methods fail

### Config File

- **Generate `migration-config.yaml`** after collecting all user decisions
- **Only include fields relevant** to the detected migration type - do not include all possible fields
- **Stop and wait** for user to review/edit the config before execution
- **Read from config** during execution - never re-prompt for values already in config
- Config file should be in the current working directory, not /tmp/

### Communication

- **Explain assumptions** when you make them - let user know what you detected and decided
- **Present trade-offs** when multiple approaches exist
- **Stop at defined checkpoints** - don't proceed through multiple phases without user confirmation
- When errors occur, explain what went wrong and what alternatives exist

### Error Recovery - NO FALLBACKS

**This is a critical rule across all workflows.**

When a user specifies resources in their config (role, database, schema, compute pool, stage, warehouse), you MUST:

1. **ONLY use those exact resources** - no substitutions
2. **NEVER try alternatives** if the specified resource fails
3. **STOP and report the error** if access is denied
4. **Ask user to update config** with valid resources

**WHY:** The config-driven approach exists so users control exactly what resources are used. Trying alternatives:
- May use resources the user doesn't want to use
- May incur unexpected costs
- May write data to wrong locations
- Violates user trust and expectations

**WRONG:**
```
User config specifies: compute_pool: MY_POOL
Error: Permission denied on MY_POOL
Agent: "Let me try ANOTHER_POOL instead..."  ❌ NEVER DO THIS
```

**RIGHT:**
```
User config specifies: compute_pool: MY_POOL
Error: Permission denied on MY_POOL
Agent: "Permission denied on MY_POOL. Please either:
1. Update your config to use a different compute pool
2. Ask your admin to grant USAGE on MY_POOL to your role
Run: SHOW COMPUTE POOLS; to see available pools."  ✅ CORRECT
```

### Migration Rules File

- **ALWAYS create `rules/migration-rule.md` FIRST** before any other files (Phase 0)
- Create the `rules/` directory in the current working directory if it doesn't exist
- The rules file guides the agent throughout the migration

---

## Inference-Specific Rules

### Docker Operations

- Prefer **pulling existing images** over building new ones for lift-and-shift migrations
- Check for available container runtimes: `docker`, `podman`, `nerdctl` in that order
- Use `--platform linux/amd64` when pulling images for Snowflake SPCS
- For model artifacts stored separately (e.g., S3), prefer mounting from Snowflake stage over baking into image

### SPCS Service Deployment

- **Ask user for ingress preference FIRST** before checking privileges:
  - Public ingress (HTTP access from outside Snowflake)
  - Internal-only (SQL/Python within Snowflake only)
- **If user chose Public ingress, CHECK privilege before proceeding:**
  ```sql
  SHOW GRANTS TO ROLE <user_role>;
  -- Look for: BIND SERVICE ENDPOINT on ACCOUNT
  ```
- **⛔ BLOCKING RULE: If user chose Public but lacks BIND SERVICE ENDPOINT privilege:**
  - **DO NOT create an internal-only endpoint as a fallback**
  - **STOP and inform user** they must either:
    1. Get the privilege granted: `GRANT BIND SERVICE ENDPOINT ON ACCOUNT TO ROLE <role>;`
    2. Switch to a role that has the privilege
    3. Explicitly choose internal-only access (restart the choice)
  - **Never silently downgrade** from public to internal-only
- Only set `ingress_enabled=False` if user **explicitly chose** internal-only access

### Framework Support

- **Known built-in supported types** (direct `log_model()`):
  - scikit-learn, XGBoost (sklearn API), LightGBM, CatBoost, Prophet
  - PyTorch, TensorFlow, Keras
  - Sentence Transformers, Hugging Face pipeline, MLFlow PyFunc
- **Known exceptions** requiring CustomModel:
  - `xgb.core.Booster` (raw Booster lacks sklearn interface)
- **If model type not in either list above:**
  1. Check official docs for current support
  2. If supported → direct `log_model()`
  3. If not supported → CustomModel required
- **Do NOT assume** an unknown type requires CustomModel without checking docs first

### SageMaker-Specific

- SageMaker endpoints separate container image from model artifacts
- Model artifacts are typically in S3, mounted at `/opt/ml/model` at runtime
- Entry point is specified via `SAGEMAKER_PROGRAM` environment variable
- AWS Deep Learning Container images require ECR login before pulling

---

## Training-Specific Rules

### Execution Model (Local vs Container Runtime)

**⚠️ CRITICAL: Understand where code runs.**

There are TWO execution contexts - never confuse them:

| Context | Where it runs | What APIs are available |
|---------|---------------|------------------------|
| **Launcher script** | Your local machine | `snowflake.ml.jobs` (submit_file, remote, etc.) |
| **Training script** | Container Runtime | `snowflake.ml.modeling.tune` (Tuner), `snowflake.ml.data` (DataConnector), etc. |

**Container Runtime APIs** (Tuner, PyTorchDistributor, etc.) are **ONLY available inside Container Runtime**. They do NOT exist in the pip-installed `snowflake-ml-python` package.

```python
# ❌ WRONG - This will fail locally with ModuleNotFoundError
from snowflake.ml.modeling.tune import Tuner  # NOT available locally!

# ✅ CORRECT - Launcher script (runs locally)
from snowflake.ml.jobs import submit_file
job = submit_file("train_hpo.py", "COMPUTE_POOL", stage_name="STAGE")

# ✅ CORRECT - Training script (runs in Container Runtime)
# train_hpo.py - this file is submitted and runs remotely
from snowflake.ml.modeling.tune import Tuner, TunerConfig  # Available here!
```

### Default Approach: submit_file()

**Use `submit_file()` as the default approach** for all training migrations because:
- More robust across Python versions (avoids serialization issues)
- Container Runtime uses Python 3.10 - if user's local Python differs, @remote will fail
- Better for multi-file projects
- Clearer separation of training code
- Easier to debug and iterate

```python
from snowflake.ml.jobs import submit_file

job = submit_file(
    "train.py",
    "<COMPUTE_POOL_FROM_CONFIG>",
    stage_name="<STAGE_FROM_CONFIG>",
    pip_requirements=["scikit-learn", "pandas"]
)
```

### @remote Decorator (Use Only When)

Only use `@remote` when ALL of these conditions are met:
1. User explicitly requests it
2. User confirms local Python version is 3.10 (matches Container Runtime)
3. Single-function training with no external file dependencies
4. Simple serializable return values

### Model Saving - MANDATORY

**Model persistence is REQUIRED, not optional.** With `submit_file()`, return values are NOT accessible.

Every training script MUST include model registration:
```python
from snowflake.ml.registry import Registry
registry = Registry(session, database_name="<DB>", schema_name="<SCHEMA>")
mv = registry.log_model(model, model_name="<MODEL_NAME>", version_name="v1")
```

- **NEVER skip model persistence** - user will lose their trained model
- **Use resources from config** - database, schema, stage must come from user's config

### Code Conversion

- **DO NOT modify** the core training logic (model architecture, loss functions, optimizers)
- **DO modify** data loading, model saving, and environment variable usage
- **Preserve** hyperparameter handling but convert to function arguments
- **Keep** the original file as a reference (`original_train.py.bak`)

### Data Loading

- **NEVER assume** data is in a specific location
- **ASK** which Snowflake table contains the training data
- **Use DataConnector** for large datasets that don't fit in memory
- For small datasets, `session.table().to_pandas()` is sufficient

### Dependencies

- **Extract** all dependencies from source (requirements.txt, environment.yaml, setup.py)
- **Verify** packages are available in Container Runtime before assuming they need installation
- **List** any packages that need to be added via the `pip_requirements` parameter

### Hyperparameter Optimization (HPO)

**⚠️ REMINDER: Tuner API runs in Container Runtime, NOT locally.**

**MANDATORY: Before writing ANY HPO code:**

1. Use ONLY the native Snowflake Tuner API (in submitted training script):
   ```python
   from snowflake.ml.modeling.tune import Tuner, TunerConfig, uniform, loguniform, randint, choice
   from snowflake.ml.modeling.tune.search import BayesOpt, RandomSearch, GridSearch
   ```

2. **DO NOT use Optuna, Ray Tune, or Hyperopt patterns** - use native Snowflake APIs only

3. **Understand search algorithm limitations:**
   - `BayesOpt()` only supports `uniform()` and `loguniform()` - NO integer or categorical params
   - `RandomSearch()` supports ALL parameter types including `randint()` and `choice()`
   - `GridSearch()` requires explicit value lists

4. **If migrating from a platform that uses Bayesian optimization with integer parameters:**
   - Either switch to `RandomSearch()` in Snowflake
   - Or use `uniform()` and cast to `int()` inside the training function

### Validation

- **ALWAYS validate** converted code compiles: `python -m py_compile`
- **Suggest a test run** with limited data before full training
- **Compare outputs** if possible - metrics should be similar between platforms
- **Document any differences** in behavior between source and target
