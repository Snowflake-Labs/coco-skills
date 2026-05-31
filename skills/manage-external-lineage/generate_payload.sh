#!/bin/bash
set -e

usage() {
    echo "Usage: $0 -a ACCOUNT -o OUTPUT_TABLE [-i INPUT...]"
    echo ""
    echo "Generate OpenLineage payload JSON for external lineage"
    echo ""
    echo "Required:"
    echo "  -a ACCOUNT       Snowflake account (ORG-ACCOUNT format)"
    echo "  -o OUTPUT        Output table (DATABASE.SCHEMA.TABLE)"
    echo ""
    echo "Optional:"
    echo "  -i INPUT         Input source (namespace::name format, can repeat)"
    echo "  -j JOB_NAME      Job name (default: auto-generated)"
    echo "  -n JOB_NAMESPACE Job namespace (default: external-etl)"
    echo "  -f OUTPUT_FILE   Output file (default: stdout)"
    echo "  -h               Show this help"
    echo ""
    echo "Examples:"
    echo "  # Single input"
    echo "  $0 -a MYORG-MYACCOUNT -o DB.SCHEMA.TABLE \\"
    echo "     -i 'postgres://host:5432::db.schema.table'"
    echo ""
    echo "  # Multiple inputs"
    echo "  $0 -a MYORG-MYACCOUNT -o DB.SCHEMA.TABLE \\"
    echo "     -i 'postgres://host:5432::public.users' \\"
    echo "     -i 's3://bucket::path/to/file.parquet' \\"
    echo "     -f payload.json"
    exit 1
}

INPUTS=()
JOB_NAMESPACE="external-etl"
JOB_NAME=""
OUTPUT_FILE=""

while getopts "a:o:i:j:n:f:h" opt; do
    case $opt in
        a) ACCOUNT="$OPTARG" ;;
        o) OUTPUT_TABLE="$OPTARG" ;;
        i) INPUTS+=("$OPTARG") ;;
        j) JOB_NAME="$OPTARG" ;;
        n) JOB_NAMESPACE="$OPTARG" ;;
        f) OUTPUT_FILE="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

if [[ -z "$ACCOUNT" || -z "$OUTPUT_TABLE" ]]; then
    echo "Error: -a ACCOUNT and -o OUTPUT_TABLE are required"
    usage
fi

if [[ ${#INPUTS[@]} -eq 0 ]]; then
    echo "Error: At least one -i INPUT is required"
    usage
fi

if [[ -z "$JOB_NAME" ]]; then
    TABLE_NAME=$(echo "$OUTPUT_TABLE" | tr '.' '_' | tr '[:upper:]' '[:lower:]')
    JOB_NAME="${TABLE_NAME}_pipeline"
fi

RUN_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
EVENT_TIME=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")

INPUT_JSON=""
for input in "${INPUTS[@]}"; do
    NAMESPACE=$(echo "$input" | cut -d':' -f1-3)
    NAME=$(echo "$input" | cut -d':' -f4-)
    
    if [[ -n "$INPUT_JSON" ]]; then
        INPUT_JSON="$INPUT_JSON,"
    fi
    INPUT_JSON="$INPUT_JSON
    {\"namespace\": \"$NAMESPACE\", \"name\": \"$NAME\"}"
done

PAYLOAD=$(cat <<EOF
{
  "eventType": "COMPLETE",
  "eventTime": "$EVENT_TIME",
  "job": {
    "namespace": "$JOB_NAMESPACE",
    "name": "$JOB_NAME"
  },
  "run": {
    "runId": "$RUN_ID"
  },
  "producer": "https://github.com/OpenLineage/OpenLineage/blob/v1-0-0/client",
  "schemaURL": "https://openlineage.io/spec/0-0-1/OpenLineage.json",
  "inputs": [$INPUT_JSON
  ],
  "outputs": [
    {
      "namespace": "snowflake://$ACCOUNT",
      "name": "$OUTPUT_TABLE"
    }
  ]
}
EOF
)

if [[ -n "$OUTPUT_FILE" ]]; then
    echo "$PAYLOAD" > "$OUTPUT_FILE"
    echo "Payload written to: $OUTPUT_FILE"
else
    echo "$PAYLOAD"
fi
