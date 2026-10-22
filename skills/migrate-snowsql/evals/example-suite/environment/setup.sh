#!/bin/bash
# The eval image has Snowflake CLI. This script adds SnowSQL 1.5.3 and the pinned CLI.
# Install the full SnowSQL binary, not the bootstrap. The verifier sets HOME to a
# throwaway directory; the bootstrap then looks in the wrong place.
set -euo pipefail

if ! command -v rpm2cpio >/dev/null 2>&1 || ! command -v cpio >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq rpm2cpio cpio ca-certificates
fi

case "$(uname -m)" in
  aarch64|arm64)
    rpm=snowflake-snowsql-1.5.3-1.aarch64.rpm
    url="https://sfc-repo.snowflakecomputing.com/snowsql/bootstrap/1.5/linux_aarch64/${rpm}"
    ;;
  x86_64)
    rpm=snowflake-snowsql-1.5.3-1.x86_64.rpm
    url="https://sfc-repo.snowflakecomputing.com/snowsql/bootstrap/1.5/linux_x86_64/${rpm}"
    ;;
  *)
    echo "unsupported architecture $(uname -m)" >&2
    exit 1
    ;;
esac

root=/opt/snowsql-1.5.3
if [[ ! -x /usr/local/bin/snowsql ]]; then
  curl -fsSL "$url" -o "/tmp/${rpm}"
  mkdir -p "$root"
  (cd "$root" && rpm2cpio "/tmp/${rpm}" | cpio -idm)
  real=$(find "$root" -type f -path '*/lib64/snowflake/snowsql/snowsql' | head -n 1)
  if [[ -z "$real" ]]; then
    echo "SnowSQL binary missing from $rpm" >&2
    exit 1
  fi
  ln -sfn "$real" /usr/local/bin/snowsql
fi
snowsql --version

here=$(cd "$(dirname "$0")" && pwd)
wheel=$(ls "$here"/wheels/snowflake_cli-*.whl 2>/dev/null | head -n 1 || true)
if [[ -z "$wheel" ]]; then
  echo "missing snowflake-cli wheel in $here/wheels" >&2
  exit 1
fi
# The image already ships 3.31.0.dev0. Same-version pip install is a no-op
# unless we force the #486 wheel (it is the build that implements !spool).
python3 -m pip install --upgrade --force-reinstall --no-cache-dir "$wheel"
# Image `snow` is often a bundled binary that ignores site-packages. Drive
# every snow on PATH through the pip #486 package.
stable=/opt/snowflake-cli-486
mkdir -p "$stable"
cat >"$stable/snow" <<'EOF'
#!/bin/sh
exec python3 -m snowflake.cli._app.__main__ "$@"
EOF
chmod 0755 "$stable/snow"
old_ifs=$IFS
IFS=:
for d in $PATH /usr/local/bin /usr/bin; do
  [[ -n "$d" ]] || continue
  dest="$d/snow"
  if [[ -e "$dest" || -L "$dest" || "$d" == /usr/local/bin || "$d" == /usr/bin ]]; then
    install -m 0755 "$stable/snow" "$dest" || true
  fi
done
IFS=$old_ifs
hash -r
python3 -c "
from snowflake.cli._plugins.sql.repl_commands import is_registered_command
from snowflake.cli._plugins.sql.statement_reader import parse_statement
assert is_registered_command('!spool'), 'this snowflake-cli build lacks !spool'
parsed = parse_statement('!spool out/spool.txt', ())
assert parsed.error is None, parsed.error
"
type -a snow
head -n 2 "$(command -v snow)"
snow --version

# Scripts use ${USER} under set -u. Docker often leaves USER unset.
export USER="${USER:-snowsql-eval}"
if [[ -d /etc/profile.d ]]; then
  printf 'export USER="${USER:-snowsql-eval}"\nexport PATH="/usr/local/bin:$PATH"\n' >/etc/profile.d/snowsql-eval.sh
fi
if [[ -w /etc/environment ]] && ! grep -q '^USER=' /etc/environment; then
  echo 'USER=snowsql-eval' >>/etc/environment
fi
