
# SCOS Local Testing

Set up and run PySpark code locally against Snowflake using Snowpark Connect for Spark (SCOS).

## When to Use

- User wants to test Spark code locally against Snowflake
- User needs to set up a local SCOS development environment
- User wants to run PySpark code without snowpark-submit or compute pools
- User asks about local Spark testing with Snowflake

## Overview

SCOS Local testing runs your PySpark code on your local machine while computation executes on Snowflake warehouses. This is different from `snowpark-submit` which runs jobs on SPCS compute pools.

```
Local Machine                    Snowflake
┌──────────────┐                ┌─────────────────┐
│ Python +     │  Spark Connect │                 │
│ snowpark_connect ────────────►│  Warehouse      │
│              │   Protocol     │  (not compute   │
└──────────────┘                │   pool)         │
                                └─────────────────┘
```

## Prerequisites

- Conda (Miniconda or Anaconda)
- Snowflake account with active warehouse
- `spark-connect` connection in `~/.snowflake/config.toml`

## Workflow

### Step 1: Check/Create Project Structure

First, check if the user has an existing SCOS local testing project or needs to create one.

```bash
# Check for existing project files
ls -la requirements.txt run_scos_test.sh convert_to_scos.sh 2>/dev/null || echo "Project not set up"
```

If project doesn't exist, create the following files:

#### requirements.txt
```
snowpark-connect[jdk]>=1.14.0
pyspark[connect]>=3.5.0,<4
scikit-learn
```

#### run_scos_test.sh
```bash
#!/bin/bash
# SCOS Local Runner - runs PySpark code locally against Snowflake
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONDA_ENV="scos"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
OUTPUT_DIR="$SCRIPT_DIR/output"
LOGS_DIR="$SCRIPT_DIR/logs"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <python_file> [args...]"
    exit 1
fi

PYTHON_FILE="$1"; shift; PYTHON_ARGS="$@"
[ ! -f "$PYTHON_FILE" ] && [ -f "$SCRIPT_DIR/$PYTHON_FILE" ] && PYTHON_FILE="$SCRIPT_DIR/$PYTHON_FILE"
[ ! -f "$PYTHON_FILE" ] && echo "Error: File not found: $PYTHON_FILE" && exit 1

PYTHON_FILE="$(cd "$(dirname "$PYTHON_FILE")" && pwd)/$(basename "$PYTHON_FILE")"
PYTHON_BASENAME=$(basename "$PYTHON_FILE" .py)

mkdir -p "$OUTPUT_DIR" "$LOGS_DIR"
LOG_FILE="$LOGS_DIR/${PYTHON_BASENAME}_${TIMESTAMP}.log"
OUTPUT_FILE="$OUTPUT_DIR/${PYTHON_BASENAME}_${TIMESTAMP}.txt"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

log "=== SCOS Local Runner ==="
source ~/miniconda3/etc/profile.d/conda.sh

if ! conda env list | grep -q "^${CONDA_ENV} "; then
    log "Creating environment '$CONDA_ENV'..."
    conda create -n "$CONDA_ENV" python=3.11 -y >> "$LOG_FILE" 2>&1
    conda activate "$CONDA_ENV"
    pip install -r "$SCRIPT_DIR/requirements.txt" >> "$LOG_FILE" 2>&1
else
    conda activate "$CONDA_ENV"
fi

log "Running: python $PYTHON_FILE $PYTHON_ARGS"
python "$PYTHON_FILE" $PYTHON_ARGS 2>&1 | tee "$OUTPUT_FILE" >> "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}
log "Exit code: $EXIT_CODE"
exit $EXIT_CODE
```

#### convert_to_scos.sh
```bash
#!/bin/bash
# Converts PySpark scripts to SCOS-compatible versions
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/scos_code"
CONDA_ENV="scos"

[ $# -lt 1 ] && echo "Usage: $0 <spark_file.py> [files...]" && exit 1

mkdir -p "$OUTPUT_DIR"
source ~/miniconda3/etc/profile.d/conda.sh
conda activate "$CONDA_ENV" 2>/dev/null || { conda create -n "$CONDA_ENV" python=3.11 -y; conda activate "$CONDA_ENV"; pip install -r "$SCRIPT_DIR/requirements.txt"; }

for INPUT_FILE in "$@"; do
    [ ! -f "$INPUT_FILE" ] && echo "[ERROR] Not found: $INPUT_FILE" && continue
    BASENAME=$(basename "$INPUT_FILE" .py)
    OUTPUT_FILE="$OUTPUT_DIR/${BASENAME}_scos.py"
    python "$SCRIPT_DIR/migrate_to_scos.py" "$INPUT_FILE" "$OUTPUT_FILE" && echo "Converted: $OUTPUT_FILE"
done
echo "Output: $OUTPUT_DIR/"
```

Make scripts executable:
```bash
chmod +x run_scos_test.sh convert_to_scos.sh
```

**⚠️ MANDATORY STOPPING POINT**: Wait for user to confirm project structure before proceeding.

### Step 2: Configure Snowflake Connection

Check if `spark-connect` connection exists:

```bash
snow connection test --connection spark-connect 2>/dev/null || echo "Connection not configured"
```

If not configured, the user needs to add to `~/.snowflake/config.toml`:

```toml
default_connection_name = "spark-connect"

[connections.spark-connect]
host = "account.snowflakecomputing.com"
account = "account"
user = "user"
password = "password"
warehouse = "ACTIVE_WAREHOUSE"
database = "database"
schema = "schema"
role = "role"
```

**Important**: The warehouse must be active (not suspended).

**⚠️ MANDATORY STOPPING POINT**: Verify connection is working before proceeding.

### Step 3: Convert PySpark to SCOS (if needed)

If the user has PySpark code that needs conversion:

```bash
./convert_to_scos.sh spark_code/my_script.py
```

This creates `scos_code/my_script_scos.py` with:
- `snowpark_connect.init_spark_session()` replacing `SparkSession.builder`
- Migration header with changes documented
- SCOS comments for compatibility notes

### Step 4: Run the Test

```bash
./run_scos_test.sh scos_code/my_script_scos.py [args...]
```

Output saved to:
- `output/{script}_{timestamp}.txt` - Script output
- `logs/{script}_{timestamp}.log` - Execution log

## Key Differences: SCOS Local vs Snowpark Submit

| Feature | SCOS Local | Snowpark Submit |
|---------|------------|-----------------|
| Compute | Warehouse | SPCS Compute Pool |
| Command | `python script.py` | `snowpark-submit script.py` |
| Setup | conda env + pip | compute pool required |
| Use Case | Development/Testing | Production |
| Cost | Warehouse credits | Container credits |

## Troubleshooting

### Warehouse Suspended
```
Warehouse 'X' is suspended
```
Update `spark-connect` connection to use an active warehouse.

### Protobuf Conflicts
```
duplicate file name spark/connect/types.proto
```
Recreate conda environment:
```bash
conda remove -n scos --all -y
./run_scos_test.sh script.py  # Recreates env
```

### Connection Failed
Verify connection:
```bash
snow connection test --connection spark-connect
```

## Stopping Points

- ✋ After Step 1: Confirm project structure is created correctly
- ✋ After Step 2: Verify Snowflake connection works
- ✋ After Step 4: Review test output with user

## Output

- Working SCOS local testing environment
- Test output in `output/` directory
- Execution logs in `logs/` directory
