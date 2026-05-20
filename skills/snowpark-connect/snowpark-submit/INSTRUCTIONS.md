
# Snowpark Submit Pipeline

Deploy PySpark applications to run on Snowflake compute using `snowpark-submit` CLI and Snowpark Connect.

## When to Use

- Deploy a PySpark application to Snowflake compute pools
- Run Spark jobs on SPCS (Snowpark Container Services)
- Set up snowpark-submit CLI for production pipelines
- Package and upload Spark applications to Snowflake stages

## Prerequisites

1. **Snowflake CLI** installed with valid connection profile
2. **snowpark-submit** CLI installed (`pip install snowpark-submit`)
3. **Compute Pool** available in Snowflake account
4. **Stages** for application code and data

## Workflow

### Step 1: Gather Connection Details

**Ask** user for deployment configuration:

```
Snowpark Submit Configuration:
1. Connection name (from ~/.snowflake/config.toml): 
2. Compute pool name: 
3. Database.Schema for stages: 
4. Application entry point (e.g., MainApplication.py): 
```

**Defaults:**
- Connection: `snowpark-connect`
- Compute pool: `SNOWPARK_SUBMIT_POOL_XS`

**⚠️ MANDATORY STOPPING POINT**: Wait for user to provide configuration before proceeding.

### Step 2: Setup Snowflake Resources

**Create required stages:**

```sql
CREATE STAGE IF NOT EXISTS <APPS_STAGE> DIRECTORY = (ENABLE = TRUE);
CREATE STAGE IF NOT EXISTS <DATA_STAGE> DIRECTORY = (ENABLE = TRUE);
```

**Verify compute pool exists:**

```sql
SHOW COMPUTE POOLS LIKE '<COMPUTE_POOL>';
```

**If compute pool doesn't exist, create it:**

```sql
CREATE COMPUTE POOL IF NOT EXISTS <COMPUTE_POOL>
    MIN_NODES = 1
    MAX_NODES = 3
    INSTANCE_FAMILY = CPU_X64_XS
    AUTO_RESUME = TRUE
    AUTO_SUSPEND_SECS = 300;
```

### Step 3: Package Application

**Create modules zip from source directory:**

```bash
cd <PROJECT_DIR>/src
zip -r <OUTPUT_DIR>/modules.zip . -x "*.pyc" -x "*__pycache__*" -x "*.DS_Store"
```

### Step 4: Upload to Snowflake

**Upload application files:**

```bash
snow sql -q "PUT file://<OUTPUT_DIR>/modules.zip @<APPS_STAGE>/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE" -c <CONNECTION>
snow sql -q "PUT file://<PROJECT_DIR>/MainApplication.py @<APPS_STAGE>/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE" -c <CONNECTION>
snow sql -q "PUT file://<PROJECT_DIR>/config.json @<APPS_STAGE>/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE" -c <CONNECTION>
```

### Step 5: Submit Job

**Build and execute snowpark-submit command (async submission):**

```bash
snowpark-submit \
    --py-files @<APPS_STAGE>/modules.zip \
    --snowflake-stage @<APPS_STAGE> \
    --snowflake-workload-name <WORKLOAD_NAME> \
    --snowflake-connection-name <CONNECTION> \
    --compute-pool <COMPUTE_POOL> \
    @<APPS_STAGE>/MainApplication.py \
    --configFile @<APPS_STAGE>/config.json
```

Note the workload name in the output — a UTC timestamp is auto-appended (e.g., `MY_JOB_241112_143025`).

**⚠️ MANDATORY STOPPING POINT**: Confirm submission succeeded before checking status.

### Step 6: Check Status and Retrieve Logs

**Check workload status (non-blocking):**

```bash
snowpark-submit \
    --snowflake-connection-name <CONNECTION> \
    --compute-pool <COMPUTE_POOL> \
    --snowflake-workload-name <FULL_WORKLOAD_NAME> \
    --workload-status
```

**Check status and wait for completion (blocking):**

```bash
snowpark-submit \
    --snowflake-connection-name <CONNECTION> \
    --compute-pool <COMPUTE_POOL> \
    --snowflake-workload-name <FULL_WORKLOAD_NAME> \
    --workload-status \
    --wait-for-completion \
    --fail-on-error
```

**Retrieve logs (requires `--workload-status`):**

```bash
snowpark-submit \
    --snowflake-connection-name <CONNECTION> \
    --compute-pool <COMPUTE_POOL> \
    --snowflake-workload-name <FULL_WORKLOAD_NAME> \
    --workload-status \
    --display-logs \
    --number-of-most-recent-log-lines 500
```

**List all workloads with a name prefix:**

```bash
snowpark-submit \
    --snowflake-connection-name <CONNECTION> \
    --compute-pool <COMPUTE_POOL> \
    --list-workloads-with-name <WORKLOAD_NAME>
```

**Kill a running workload:**

```bash
snowpark-submit \
    --snowflake-connection-name <CONNECTION> \
    --compute-pool <COMPUTE_POOL> \
    --snowflake-workload-name <FULL_WORKLOAD_NAME> \
    --kill-workload
```

For detailed logging, monitoring, and progress tracking patterns, **read** `references/logging-monitoring.md`.

## snowpark-submit CLI Reference

### Required Flags

| Flag | Description | Example |
|------|-------------|---------|
| `--snowflake-connection-name` | Connection from `~/.snowflake/config.toml` | `snowpark-connect` |
| `--compute-pool` | Snowflake compute pool | `SNOWPARK_SUBMIT_POOL_XS` |
| `--snowflake-stage` | Stage for temporary files and logs | `@DB.SCHEMA.APPS_STAGE` |

### Submission Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--py-files` | Comma-separated Python zip/egg files | None |
| `--snowflake-workload-name` | Name prefix (UTC timestamp auto-appended) | Auto-generated |
| `--wait-for-completion` | Block until job finishes (submission or status) | Async |
| `--fail-on-error` | Raise exception on failure (requires `--wait-for-completion`) | No |
| `--packages` | Maven coordinates for JARs | None |
| `--jars` | JAR files to include | None |
| `--requirements-file` | Path to requirements.txt for pip dependencies | None |
| `--files` | Comma-separated files to place in workload node | None |

### Status and Monitoring Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--workload-status` | Print detailed status of the workload | Off |
| `--display-logs` | Print logs to console (requires `--workload-status`) | No logs |
| `--number-of-most-recent-log-lines` | Log lines to retrieve with `--display-logs` | 100 |
| `--list-workloads-with-name` | List workloads matching name prefix | None |
| `--kill-workload` | Terminate a running workload | No |

### Execution Modes

**Asynchronous submit (default):**
```bash
snowpark-submit \
    --snowflake-connection-name snowpark-connect \
    --compute-pool SNOWPARK_SUBMIT_POOL_XS \
    --snowflake-stage @APPS_STAGE \
    @APPS_STAGE/MainApplication.py
```

**Synchronous submit (blocks until done):**
```bash
snowpark-submit \
    --snowflake-connection-name snowpark-connect \
    --compute-pool SNOWPARK_SUBMIT_POOL_XS \
    --snowflake-stage @APPS_STAGE \
    --wait-for-completion \
    --fail-on-error \
    @APPS_STAGE/MainApplication.py
```

**Check status with logs (separate command after async submit):**
```bash
snowpark-submit \
    --snowflake-connection-name snowpark-connect \
    --compute-pool SNOWPARK_SUBMIT_POOL_XS \
    --snowflake-workload-name MY_JOB_241112_143025 \
    --workload-status \
    --display-logs
```

### Connection Configuration

In `~/.snowflake/config.toml`:

```toml
default_connection_name = "snowpark-connect"

[connections.snowpark-connect]
account = "your_account"
user = "your_user"
authenticator = "externalbrowser"
database = "YOUR_DB"
schema = "YOUR_SCHEMA"
warehouse = "YOUR_WAREHOUSE"
role = "YOUR_ROLE"
```

## Best Practices

### Case Sensitivity (CRITICAL)

```python
spark = SparkSession.builder \
    .config("spark.sql.caseSensitive", "true") \
    .getOrCreate()
```

### Avoid Eager Evaluation

| Operation | Risk | Alternative |
|-----------|------|-------------|
| `.count()` | High | SQL `SELECT COUNT(*)` via SnowflakeSession |
| `.collect()` | High | Only for small results |
| `.toPandas()` | High | Process in Snowflake |
| `.cache()` | Medium | Checkpoint to temp table |

### Checkpointing (SQL CTAS)

```python
# PREFER: SQL CTAS (preserves column names)
snowflake_session.sql(f"CREATE OR REPLACE TABLE {table} AS {query}").collect()
df = spark.table(table)

# AVOID: DataFrame write (column name issues)
df.write.mode("overwrite").saveAsTable(table)
```

### Data Ingestion

Use Snowflake COPY INTO (not Spark) for bulk loading:

```sql
COPY INTO landing_table
FROM @data_stage/raw/
FILE_FORMAT = 'json_format'
PATTERN = '.*\.json.gz'
FORCE = TRUE;
```

## Runner Script

For a full-featured runner script template with phase-based execution, skip flags, logging, and timing, **read** `references/runner-script-template.md`.

## Stopping Points

- ✋ After Step 1: Confirm configuration before resource creation
- ✋ After Step 5: Confirm submission succeeded before checking status
- ✋ After Step 6: Review workload status and logs with user

## Troubleshooting

**Job hangs or times out:**
- Check for `.count()` or `.collect()` on large DataFrames
- Look for CROSS JOINs or cartesian products
- Verify compute pool has available resources
- Do NOT use `--display-logs` during submission — it requires `--workload-status`
- Use async submit, then check logs separately with `--workload-status --display-logs`

**Column name issues (uppercase):**
- Set `spark.sql.caseSensitive=true`
- Use SQL CTAS for checkpointing
- Use quoted identifiers: `as "column_name"`

**Data not loading (COPY returns 0 rows):**
- Add `FORCE = TRUE` to reload previously-loaded files
- Verify files in stage: `LIST @stage/path/`

**Out of memory errors:**
- Remove `.cache()` calls — use temp tables
- Replace `.count()` with SQL COUNT(*)
- Process in Snowflake, not Spark driver

## Output

- Deployed application running on Snowflake compute pool
- Logs available via `--display-logs` or Snowsight UI
- Output tables/views in specified database.schema
