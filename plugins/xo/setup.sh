#!/bin/bash
# XO setup launcher: finds node and runs setup.js (all logic lives there).
set -euo pipefail
PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

find_node() {
    if command -v node >/dev/null 2>&1; then command -v node; return 0; fi
    local c
    for c in /opt/homebrew/bin/node /usr/local/bin/node "$HOME/.volta/bin/node" \
             "$HOME/.local/share/fnm/aliases/default/bin/node" "$HOME/.fnm/aliases/default/bin/node"; do
        [ -x "$c" ] && { echo "$c"; return 0; }
    done
    c=$(ls -d "$HOME"/.nvm/versions/node/*/bin/node 2>/dev/null | sort -V | tail -n 1 || true)
    [ -n "$c" ] && [ -x "$c" ] && { echo "$c"; return 0; }
    return 1
}

if ! NODE=$(find_node); then
    echo "  ✗ node not found. XO setup and hooks require Node.js:"
    echo "      macOS: brew install node"
    echo "      Linux: your package manager (e.g. sudo apt install nodejs)"
    echo "      any:   https://nodejs.org"
    exit 1
fi
exec "$NODE" "$PLUGIN_DIR/setup.js" "$@"
