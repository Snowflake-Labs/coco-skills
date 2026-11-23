#!/bin/bash
# One-time setup for openshell/coco-in-openshell.sh in provider mode: register the Snowflake
# provider profile for your account and store your token in the OpenShell gateway, not in any
# sandbox.
#
#   SNOWFLAKE_ACCOUNT=myorg-myaccount SNOWFLAKE_TOKEN=<token> openshell/snowflake-provider.sh
#   PROVIDER=snowflake SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... openshell/coco-in-openshell.sh "prompt"
#
# Run it again with a new SNOWFLAKE_TOKEN to rotate the token. Optional: PROVIDER (default
# snowflake), OPENSHELL_GATEWAY as usual.
set -euo pipefail

: "${SNOWFLAKE_ACCOUNT:?set SNOWFLAKE_ACCOUNT, for example myorg-myaccount}"
: "${SNOWFLAKE_TOKEN:?set SNOWFLAKE_TOKEN to a programmatic access token}"
export SNOWFLAKE_TOKEN  # read by "provider create/update --credential SNOWFLAKE_TOKEN", never put in argv
PROVIDER=${PROVIDER:-snowflake}
HOST="$(printf '%s' "$SNOWFLAKE_ACCOUNT" | tr '[:upper:]_' '[:lower:]-').snowflakecomputing.com"
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT

sed "s/myorg-myaccount\.snowflakecomputing\.com/$HOST/g" "$(dirname "$0")/providers/snowflake.yaml" > "$WORK/snowflake.yaml"
if openshell profile describe snowflake < /dev/null > /dev/null 2>&1; then
  # An update must name the version it replaces; take it from the registered profile.
  version=$(openshell profile export snowflake -o json < /dev/null | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["resource_version"])')
  printf 'resource_version: %s\n' "$version" >> "$WORK/snowflake.yaml"
  openshell profile update -f "$WORK/snowflake.yaml" snowflake < /dev/null  # validates the file itself
else
  openshell profile lint -f "$WORK/snowflake.yaml" < /dev/null  # lint reports an existing profile as an error
  openshell profile import -f "$WORK/snowflake.yaml" < /dev/null
fi

if openshell provider get "$PROVIDER" < /dev/null > /dev/null 2>&1; then
  openshell provider update "$PROVIDER" --credential SNOWFLAKE_TOKEN < /dev/null
else
  openshell provider create --name "$PROVIDER" --type snowflake --credential SNOWFLAKE_TOKEN < /dev/null
fi
echo "Provider '$PROVIDER' holds the token for $HOST. Sandboxes created with --provider $PROVIDER see a placeholder."
