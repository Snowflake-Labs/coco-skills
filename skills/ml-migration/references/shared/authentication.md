# Snowflake Authentication

Authentication setup for ML workloads in Snowflake.

## Connection Methods

### 1. Connection Name (Recommended)
```python
import os
from snowflake.snowpark import Session

# Set connection name
os.environ["SNOWFLAKE_CONNECTION_NAME"] = "demo"

# Session auto-discovers connection
session = Session.builder.getOrCreate()
```

### 2. Snowflake Connector Config
```python
import snowflake.connector

conn = snowflake.connector.connect(
    connection_name="demo"  # Uses ~/.snowflake/connections.toml
)
```

### 3. Explicit Parameters
```python
session = Session.builder.configs({
    "account": "myaccount",
    "user": "myuser",
    "password": os.getenv("SNOWFLAKE_PASSWORD"),
    "warehouse": "COMPUTE_WH",
    "database": "MY_DB",
    "schema": "MY_SCHEMA"
}).create()
```

## Container Runtime Authentication

Inside ML Jobs (Container Runtime), authentication is automatic:

```python
# Inside @remote or submit_file() script
from snowflake.snowpark import Session

# No credentials needed - auto-authenticated
session = Session.builder.getOrCreate()
```

## Required Privileges

### For Model Registry
```sql
-- Grant to your role
GRANT CREATE MODEL ON SCHEMA DB.SCHEMA TO ROLE MY_ROLE;
GRANT USAGE ON DATABASE DB TO ROLE MY_ROLE;
GRANT USAGE ON SCHEMA DB.SCHEMA TO ROLE MY_ROLE;
```

### For ML Jobs
```sql
-- Compute pool access
GRANT USAGE ON COMPUTE POOL CPU_X64_M TO ROLE MY_ROLE;
GRANT USAGE ON COMPUTE POOL GPU_NV_S TO ROLE MY_ROLE;

-- Stage access
GRANT READ, WRITE ON STAGE TRAINING_STAGE TO ROLE MY_ROLE;
```

### For SPCS Services
```sql
-- Create service
GRANT CREATE SERVICE ON SCHEMA DB.SCHEMA TO ROLE MY_ROLE;

-- Public endpoints (optional)
GRANT BIND SERVICE ENDPOINT ON ACCOUNT TO ROLE MY_ROLE;

-- Image repository
GRANT READ ON IMAGE REPOSITORY DB.SCHEMA.ML_IMAGES TO ROLE MY_ROLE;
```

### External Access (PyPI)
```sql
-- For downloading packages during training
GRANT USAGE ON INTEGRATION PYPI_ACCESS_INTEGRATION TO ROLE MY_ROLE;
```

## Verify Permissions

```sql
-- Check your current role
SELECT CURRENT_ROLE();

-- List grants
SHOW GRANTS TO ROLE MY_ROLE;

-- Check specific privileges
SHOW GRANTS ON COMPUTE POOL CPU_X64_M;
SHOW GRANTS ON SCHEMA DB.SCHEMA;
```

## Common Authentication Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| "Permission denied" | Missing grants | Request grants from admin |
| "Compute pool not found" | Wrong pool name or no access | Check pool exists and grants |
| "Cannot create model" | Missing CREATE MODEL | Grant CREATE MODEL on schema |
| "Integration not accessible" | Missing PYPI access | Grant USAGE on integration |

## Multi-Account Setup

For cross-account migrations:

```python
# Source account connection
source_session = Session.builder.configs({
    "account": "source_account",
    "connection_name": "source_conn"
}).create()

# Target account connection  
target_session = Session.builder.configs({
    "account": "target_account",
    "connection_name": "target_conn"
}).create()
```
