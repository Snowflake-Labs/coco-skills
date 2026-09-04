#!/bin/bash
set -e

usage() {
    echo "Usage: $0 -a ACCOUNT_IDENTIFIER -t TOKEN_FILE -p PAYLOAD_FILE [-j]"
    echo ""
    echo "Send OpenLineage events to Snowflake External Lineage API"
    echo ""
    echo "Options:"
    echo "  -a ACCOUNT   Snowflake account identifier (e.g., MYORG-MYACCOUNT)"
    echo "  -t TOKEN     Path to authentication token file"
    echo "  -p PAYLOAD   Path to JSON payload file"
    echo "  -j           Use JWT authentication (default: PAT)"
    echo "  -h           Show this help message"
    echo ""
    echo "Example:"
    echo "  $0 -a SFPSCOGS-KHENG_AWS_DEMO -t ~/token.txt -p /tmp/lineage.json"
    exit 1
}

AUTH_TYPE="PROGRAMMATIC_ACCESS_TOKEN"

while getopts "a:t:p:jh" opt; do
    case $opt in
        a) ACCOUNT="$OPTARG" ;;
        t) TOKEN_FILE="$OPTARG" ;;
        p) PAYLOAD_FILE="$OPTARG" ;;
        j) AUTH_TYPE="KEYPAIR_JWT" ;;
        h) usage ;;
        *) usage ;;
    esac
done

if [[ -z "$ACCOUNT" || -z "$TOKEN_FILE" || -z "$PAYLOAD_FILE" ]]; then
    echo "Error: Missing required arguments"
    usage
fi

if [[ ! -f "$TOKEN_FILE" ]]; then
    echo "Error: Token file not found: $TOKEN_FILE"
    exit 1
fi

if [[ ! -f "$PAYLOAD_FILE" ]]; then
    echo "Error: Payload file not found: $PAYLOAD_FILE"
    exit 1
fi

TOKEN=$(cat "$TOKEN_FILE")
ENDPOINT="https://${ACCOUNT}.snowflakecomputing.com/api/v2/lineage/external-lineage"

echo "Sending lineage event to: $ENDPOINT"
echo "Auth type: $AUTH_TYPE"
echo ""

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Accept: application/json" \
    -H "User-Agent: external-lineage-skill/1.0" \
    -H "X-Snowflake-Authorization-Token-Type: $AUTH_TYPE" \
    -d @"$PAYLOAD_FILE" \
    "$ENDPOINT")

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [[ "$HTTP_CODE" == "200" ]]; then
    echo "Success! Lineage event sent successfully."
    echo "Response: $BODY"
    echo ""
    echo "Verify in Snowsight: Catalog -> Database Explorer -> [Your Table] -> Lineage tab"
else
    echo "Error: HTTP $HTTP_CODE"
    echo "Response: $BODY"
    exit 1
fi
