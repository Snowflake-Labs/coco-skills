#!/bin/bash
# Compare the agent's snow copy of the sources with SnowSQL on a live connection.
# eval.sh copies compare.py and cases.txt into this directory before the run.
fail() {
  echo "FAIL: $1"
  echo 0 > /logs/verifier/reward.txt
  exit 1
}
mkdir -p /logs/verifier
here=$(cd "$(dirname "$0")" && pwd)
cd /workspace || fail "workspace missing"

if [[ -f .orig-hashes ]]; then
  sha256sum -c --quiet .orig-hashes || fail "originals changed"
fi
test -d snowsql-migrated || fail "missing migrated sources"

python3 "$here/compare.py" \
  --sources /workspace \
  --migrated /workspace/snowsql-migrated \
  --cases "$here/cases.txt" \
  --results /logs/verifier/compare || fail "snow copy does not match SnowSQL"

echo "PASS"
echo 1 > /logs/verifier/reward.txt
