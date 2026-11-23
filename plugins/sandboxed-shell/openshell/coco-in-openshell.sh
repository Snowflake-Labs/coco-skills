#!/bin/bash
# Run Snowflake's CoCo CLI entirely inside an OpenShell sandbox that can reach one
# destination: your Snowflake account host on port 443. The agent, its shell commands and
# its file edits all stay in the sandbox. Nothing is installed on this machine.
#
# Recommended, the token stays out of the sandbox (one-time setup: openshell/snowflake-provider.sh):
#   PROVIDER=snowflake SNOWFLAKE_ACCOUNT=myorg-myaccount SNOWFLAKE_USER=me@example.com \
#     openshell/coco-in-openshell.sh "Run uname -s and list the files"
#
# Simpler, the token is streamed into a file inside the sandbox:
#   SNOWFLAKE_ACCOUNT=myorg-myaccount SNOWFLAKE_USER=me@example.com SNOWFLAKE_TOKEN=<token> \
#     openshell/coco-in-openshell.sh "Run uname -s and list the files"
#
# SNOWFLAKE_USER is the login name (for users created through SSO this is usually the email,
# not the user name Snowsight shows). With PROVIDER, the sandbox sees only a placeholder and
# OpenShell's proxy puts the real token into the sign-in request. Without it, commands in the
# sandbox can read the token file (they can reach only Snowflake). The token is never written
# to a file on this machine. Optional: SANDBOX (default coco-inside), ROLE (default PUBLIC),
# OPENSHELL_GATEWAY as usual. The sandbox is kept for the next run; remove it with
# openshell sandbox delete "${SANDBOX:-coco-inside}".
set -euo pipefail

: "${SNOWFLAKE_ACCOUNT:?set SNOWFLAKE_ACCOUNT, for example myorg-myaccount}"
: "${SNOWFLAKE_USER:?set SNOWFLAKE_USER to your login name}"
[ -n "${PROVIDER:-}" ] || : "${SNOWFLAKE_TOKEN:?set PROVIDER (see openshell/snowflake-provider.sh) or SNOWFLAKE_TOKEN}"
PROMPT=${1:?usage: $0 PROMPT}
SANDBOX=${SANDBOX:-coco-inside}
ROLE=${ROLE:-PUBLIC}
HERE=$(cd "$(dirname "$0")" && pwd)
HOST="$(printf '%s' "$SNOWFLAKE_ACCOUNT" | tr '[:upper:]_' '[:lower:]-').snowflakecomputing.com"
REPO=https://sfc-repo.snowflakecomputing.com/cortex-code-cli/a4643c4278  # from Snowflake's install script
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT; umask 077

run() { openshell sandbox exec --name "$SANDBOX" --no-tty -- "$@" < /dev/null; }
phase() { openshell sandbox get "$SANDBOX" -o json < /dev/null 2>/dev/null | python3 -I -c 'import json,sys; print(json.load(sys.stdin).get("phase",""))' 2>/dev/null || true; }

if [ "$(phase)" != "Ready" ]; then
  if [ -n "${PROVIDER:-}" ]; then  # the provider adds the only network rule, inspected
    openshell sandbox create --name "$SANDBOX" --detach --no-tty --no-auto-providers --provider "$PROVIDER" \
      --policy "$HERE/sandbox-policy.yaml" -- sleep infinity < /dev/null
  else
    sed "s/__SNOWFLAKE_HOST__/$HOST/" "$HERE/snowflake-only-policy.yaml" > "$WORK/policy.yaml"
    openshell sandbox create --name "$SANDBOX" --detach --no-tty --no-auto-providers --policy "$WORK/policy.yaml" \
      -- sleep infinity < /dev/null
  fi
  for _ in $(seq 1 90); do [ "$(phase)" = "Ready" ] && break; sleep 2; done
  [ "$(phase)" = "Ready" ] || { echo "sandbox $SANDBOX did not become ready" >&2; exit 1; }
fi

if ! run sh -c 'ls /sandbox/coco-*/cortex' > /dev/null 2>&1; then
  arch=$(run uname -m | tr -d '[:space:]'); case "$arch" in aarch64|arm64) arch=arm64 ;; x86_64|amd64) arch=amd64 ;; esac
  version=$(curl -fsSL "$REPO/stable_version.txt" | tr -d '[:space:]'); enc=${version//+/%2B}
  read -r name sum < <(curl -fsSL "$REPO/$enc/manifest.json" | python3 -I -c "import json,sys; p=json.load(sys.stdin)['packages']['linux']['$arch']; print(p['name'], p['checksum'])")
  curl -fsSL -o "$WORK/coco.tar.gz" "$REPO/$enc/${name//+/%2B}"
  [ "$(shasum -a 256 "$WORK/coco.tar.gz" | awk '{print $1}')" = "$sum" ] || { echo "checksum mismatch for $name" >&2; exit 1; }
  openshell sandbox upload "$SANDBOX" "$WORK/coco.tar.gz" /sandbox/coco.tar.gz --no-git-ignore < /dev/null
  run sh -c 'cd /sandbox && tar -xzf coco.tar.gz && rm coco.tar.gz'
fi

{ printf '[snowflake]\naccount = "%s"\nuser = "%s"\nrole = "%s"\nauthenticator = "PROGRAMMATIC_ACCESS_TOKEN"\n' \
    "$SNOWFLAKE_ACCOUNT" "$SNOWFLAKE_USER" "$ROLE"
  [ -n "${PROVIDER:-}" ] || printf 'token_file_path = "/sandbox/sfhome/pat"\n'; } > "$WORK/connections.toml"
printf '{\n  "autoUpdate": false\n}\n' > "$WORK/settings.json"
run sh -c 'mkdir -p /sandbox/sfhome/cortex /sandbox/project && chmod 700 /sandbox/sfhome'
openshell sandbox upload "$SANDBOX" "$WORK/connections.toml" /sandbox/sfhome/connections.toml --no-git-ignore < /dev/null > /dev/null
openshell sandbox upload "$SANDBOX" "$WORK/settings.json" /sandbox/sfhome/cortex/settings.json --no-git-ignore < /dev/null > /dev/null
if [ -z "${PROVIDER:-}" ]; then
  printf '%s' "$SNOWFLAKE_TOKEN" | openshell sandbox exec --name "$SANDBOX" --no-tty -- sh -c 'umask 077; cat > /sandbox/sfhome/pat'
fi
run sh -c 'chmod 600 /sandbox/sfhome/connections.toml; [ ! -e /sandbox/sfhome/pat ] || chmod 600 /sandbox/sfhome/pat'

run sh -c 'exec env SNOWFLAKE_HOME=/sandbox/sfhome /sandbox/coco-*/cortex -c snowflake --no-auto-update --sql-read-only \
  --only-explicit-mcp-servers -w /sandbox/project -p "$0" --allowed-tools bash' "$PROMPT"
