# Snowpark Submit Runner Script Template

Full-featured shell script for reproducible pipeline deployments. Handles all phases: setup, build, data ingestion, upload, and job submission.

## Usage

```bash
# Full pipeline run (first time)
./run_pipeline.sh --wait --logs

# Quick iteration (code changes only)
./run_pipeline.sh --skip-setup --skip-ingest --wait --logs

# Rebuild and resubmit only
./run_pipeline.sh --skip-setup --skip-ingest --skip-upload --clean --wait --logs

# Use different connection
SNOWFLAKE_CONNECTION=prod-connection ./run_pipeline.sh --wait
```

## Key Features

1. **Phase-Based Execution**: Separates setup, build, ingest, upload, and submit phases
2. **Skip Flags**: `--skip-setup`, `--skip-ingest`, `--skip-build`, `--skip-upload` for incremental runs
3. **Logging**: All output logged to timestamped files in `output/logs/`
4. **Timing**: Per-phase and total execution timing
5. **Environment Variables**: Configurable via `SNOWFLAKE_CONNECTION` and `SNOWPARK_COMPUTE_POOL`

## Template

```bash
#!/bin/bash

# ============================================================================
# Snowpark Connect ETL Pipeline Runner
# ============================================================================
# Uses database/schema from Snowflake CLI connection profile
# Single source of truth: ~/.snowflake/config.toml
#
# Usage:
#   ./run_pipeline.sh [OPTIONS]
#
# Options:
#   --skip-setup       Skip resource setup (stages, compute pool already exist)
#   --skip-ingest      Skip data ingestion (data already in table)
#   --skip-build       Use existing modules.zip
#   --skip-upload      Skip Snowflake upload (already uploaded)
#   --wait             Wait for job completion (synchronous)
#   --logs             Show real-time logs (implies --wait)
#   --clean            Clean output directory before build
#   --output-dir DIR   Custom output directory (default: ./output)
#   -h, --help         Show this help message
#
# Environment Variables:
#   SNOWFLAKE_CONNECTION     Connection name (default: snowpark-connect)
#   SNOWPARK_COMPUTE_POOL    Compute pool (default: SNOWPARK_SUBMIT_POOL_XS)
#
# ============================================================================

set -euo pipefail

# --- Configuration ---
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${PROJECT_DIR}/output"
LOGS_DIR="${OUTPUT_DIR}/logs"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="${LOGS_DIR}/run_${TIMESTAMP}.log"

CONNECTION="${SNOWFLAKE_CONNECTION:-snowpark-connect}"
COMPUTE_POOL="${SNOWPARK_COMPUTE_POOL:-SNOWPARK_SUBMIT_POOL_XS}"

APPS_STAGE="APPS_STAGE"
DATA_STAGE="DATA_STAGE"
WORKLOAD_NAME="SPARK_ETL"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# --- Parse Arguments ---
SKIP_SETUP=false
SKIP_INGEST=false
SKIP_BUILD=false
SKIP_UPLOAD=false
WAIT=false
SHOW_LOGS=false
CLEAN=false

show_help() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --skip-setup       Skip resource setup"
    echo "  --skip-ingest      Skip data ingestion"
    echo "  --skip-build       Use existing modules.zip"
    echo "  --skip-upload      Skip Snowflake upload"
    echo "  --wait             Wait for job completion"
    echo "  --logs             Show real-time logs (implies --wait)"
    echo "  --clean            Clean output directory before build"
    echo "  --output-dir DIR   Custom output directory"
    echo "  -h, --help         Show this help message"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-setup)   SKIP_SETUP=true; shift ;;
        --skip-ingest)  SKIP_INGEST=true; shift ;;
        --skip-build)   SKIP_BUILD=true; shift ;;
        --skip-upload)  SKIP_UPLOAD=true; shift ;;
        --wait)         WAIT=true; shift ;;
        --logs)         SHOW_LOGS=true; WAIT=true; shift ;;
        --clean)        CLEAN=true; shift ;;
        --output-dir)   OUTPUT_DIR="$2"; LOGS_DIR="${OUTPUT_DIR}/logs"; shift 2 ;;
        -h|--help)      show_help; exit 0 ;;
        *)              echo -e "${RED}Unknown option: $1${NC}"; exit 1 ;;
    esac
done

# --- Utility Functions ---
log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo ""; echo -e "${BLUE}$1${NC}"; echo ""; }

run_snow_sql() {
    snow sql -q "$1" -c "$CONNECTION" 2>&1 | grep -v "UserWarning" || true
}

# --- Setup ---
if [ "$CLEAN" = true ]; then rm -rf "${OUTPUT_DIR}"; fi
mkdir -p "${OUTPUT_DIR}" "${LOGS_DIR}"
exec > >(tee -a "$LOG_FILE")
exec 2>&1

PIPELINE_START=$(date +%s)

# Phase 0: Setup Resources
if [ "$SKIP_SETUP" = false ]; then
    log_step "Phase 0/4: Setup Snowflake Resources"
    run_snow_sql "CREATE STAGE IF NOT EXISTS ${DATA_STAGE} DIRECTORY = (ENABLE = TRUE)"
    run_snow_sql "CREATE STAGE IF NOT EXISTS ${APPS_STAGE} DIRECTORY = (ENABLE = TRUE)"
    
    POOL_CHECK=$(snow sql -q "SHOW COMPUTE POOLS LIKE '${COMPUTE_POOL}'" -c "$CONNECTION" 2>&1 | grep "$COMPUTE_POOL" || true)
    if [ -z "$POOL_CHECK" ]; then
        run_snow_sql "CREATE COMPUTE POOL IF NOT EXISTS ${COMPUTE_POOL} MIN_NODES = 1 MAX_NODES = 3 INSTANCE_FAMILY = CPU_X64_XS AUTO_RESUME = TRUE AUTO_SUSPEND_SECS = 300"
    fi
fi

# Phase 1: Build
if [ "$SKIP_BUILD" = false ]; then
    log_step "Phase 1/4: Build modules.zip"
    [ -f "${OUTPUT_DIR}/modules.zip" ] && rm "${OUTPUT_DIR}/modules.zip"
    cd "${PROJECT_DIR}/src"
    zip -r "${OUTPUT_DIR}/modules.zip" . -x "*.pyc" -x "*__pycache__*" -x "*.DS_Store" -q
    cd "${PROJECT_DIR}"
fi

# Phase 2: Data Ingestion
if [ "$SKIP_INGEST" = false ]; then
    log_step "Phase 2/4: Data Ingestion"
    for sql_file in "${PROJECT_DIR}/scripts/"*.sql; do
        [ -f "$sql_file" ] && snow sql -f "$sql_file" -c "$CONNECTION" 2>&1 | grep -v "UserWarning" || true
    done
fi

# Phase 3: Upload
if [ "$SKIP_UPLOAD" = false ]; then
    log_step "Phase 3/4: Upload to Snowflake Stage"
    snow sql -q "PUT file://${OUTPUT_DIR}/modules.zip @${APPS_STAGE}/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE" -c "$CONNECTION" 2>&1 | grep -v "UserWarning" || true
    [ -f "${PROJECT_DIR}/MainApplication.py" ] && snow sql -q "PUT file://${PROJECT_DIR}/MainApplication.py @${APPS_STAGE}/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE" -c "$CONNECTION" 2>&1 | grep -v "UserWarning" || true
fi

# Phase 4: Submit
log_step "Phase 4/4: Submit Snowpark Connect Job"
CMD="snowpark-submit --py-files @${APPS_STAGE}/modules.zip --snowflake-stage @${APPS_STAGE} --snowflake-workload-name ${WORKLOAD_NAME} --snowflake-connection-name $CONNECTION --compute-pool $COMPUTE_POOL"
[ "$WAIT" = true ] && CMD="$CMD --wait-for-completion --fail-on-error"
CMD="$CMD @${APPS_STAGE}/MainApplication.py"
eval "$CMD"

# Phase 5: Retrieve logs (if requested)
if [ "$SHOW_LOGS" = true ]; then
    log_step "Phase 5: Retrieve Workload Logs"
    snowpark-submit \
        --snowflake-connection-name "$CONNECTION" \
        --compute-pool "$COMPUTE_POOL" \
        --snowflake-workload-name "${WORKLOAD_NAME}" \
        --workload-status \
        --display-logs \
        --number-of-most-recent-log-lines 500
fi

PIPELINE_END=$(date +%s)
echo "Total Duration: $(((PIPELINE_END - PIPELINE_START) / 60))m $(((PIPELINE_END - PIPELINE_START) % 60))s"
```
