# Azure ML: Common Reference

Shared patterns and utilities for migrating from Azure Machine Learning to Snowflake.

## Platform Detection

### Indicators
```python
# File patterns
"*.azureml"           # Azure ML config directories
"azureml-*.yaml"      # Job/environment definitions
"score.py"            # Azure scoring script convention
"conda_dependencies.yml"

# SDK imports
from azure.ai.ml import MLClient
from azure.ai.ml import command, Input, Output
from azure.ai.ml.entities import Model, Environment
from azure.identity import DefaultAzureCredential
```

### Detection Logic
```python
def detect_azure_ml():
    indicators = [
        glob.glob("**/.azureml", recursive=True),
        grep_files("from azure.ai.ml"),
        grep_files("MLClient"),
        os.path.exists("score.py"),
    ]
    return any(indicators)
```

## Asset Mapping

| Azure ML Asset | Snowflake Equivalent | Migration Path |
|----------------|---------------------|----------------|
| Workspace | Database/Schema | Map workspace to DB.SCHEMA |
| Registered Model | Model Registry | Download → register |
| Managed Endpoint | SPCS Service | Extract → deploy |
| Compute Instance | ML Job | Convert to `@remote` |
| Compute Cluster | Compute Pool | Map to CPU_X64/GPU_NV |
| Pipeline | Tasks/Python | Convert orchestration |
| Data Asset | Table/Stage | Load via connector |
| Environment | Container Runtime | Extract packages |

## Azure Authentication

### SDK Client Setup
```python
from azure.ai.ml import MLClient
from azure.identity import DefaultAzureCredential

# Required credentials
ml_client = MLClient(
    DefaultAzureCredential(),
    subscription_id="<subscription-id>",
    resource_group="<resource-group>",
    workspace_name="<workspace-name>"
)
```

### Required Azure Permissions
- `Azure ML Data Scientist` - Read models, endpoints
- `Storage Blob Data Reader` - Access model artifacts
- `Azure ML Workspace Reader` - List workspace resources

## Environment Verification

### Prerequisites Checklist
- [ ] Azure CLI authenticated (`az login`)
- [ ] Subscription ID available
- [ ] Resource group name known
- [ ] Workspace name confirmed
- [ ] `azure-ai-ml` package installed

### Validate Access
```python
# Test workspace connectivity
try:
    workspace = ml_client.workspaces.get(workspace_name)
    print(f"Connected to: {workspace.name}")
except Exception as e:
    print(f"Access failed: {e}")
```

## Environment Migration

### Export Environment
```bash
# From conda
conda env export > environment.yml

# From Azure ML environment
az ml environment show --name my-env --version 1 > env.json
```

### Convert to Snowflake
```python
# Extract packages from environment.yml
import yaml
with open("environment.yml") as f:
    env = yaml.safe_load(f)
    
pip_packages = [dep for dep in env.get("dependencies", []) 
                if isinstance(dep, str) and not dep.startswith("python")]

# Use in @remote or submit_file
pip_requirements = pip_packages
```

## Data Source Migration

| Azure Source | Snowflake Target | Method |
|--------------|------------------|--------|
| Blob Storage | External Stage | `azure://container/path` |
| ADLS Gen2 | External Stage | Azure credentials |
| Azure SQL | Table | Snowflake connector |
| MLTable | Table | Convert to DataFrame → load |
