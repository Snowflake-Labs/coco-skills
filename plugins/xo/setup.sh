#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$SCRIPT_DIR"
XO_DIR="$(dirname "$SCRIPT_DIR")"

SNOWFLAKE_HOME="${SNOWFLAKE_HOME:-$HOME/.snowflake}"
USER_PROFILE_DIR="$SNOWFLAKE_HOME/cortex"

# ─── Utility ──────────────────────────────────────────────────────────────────

info()  { echo "  $*"; }
ok()    { echo "  ✓ $*"; }
warn()  { echo "  ⚠ $*"; }
fail()  { echo "  ✗ $*"; }
die()   { fail "$*"; exit 1; }

INIT_GH_USER=""
INIT_VAULT_OWNER=""

print_tool_install_guidance() {
    local tool="$1"
    local label="" mac_hint="" linux_hint="" any_url=""

    case "$tool" in
        git)
            label="Git"
            mac_hint="brew install git   (no brew? https://brew.sh)"
            linux_hint="https://git-scm.com/download/linux"
            any_url="https://git-scm.com/downloads"
            ;;
        gh)
            label="the GitHub CLI"
            mac_hint="brew install gh   (no brew? https://brew.sh)"
            linux_hint="https://github.com/cli/cli#installation"
            any_url="https://cli.github.com"
            ;;
        jq)
            label="jq"
            mac_hint="brew install jq   (no brew? https://brew.sh)"
            linux_hint="https://jqlang.github.io/jq/download/"
            any_url="https://jqlang.github.io/jq/"
            ;;
        node)
            label="Node.js"
            mac_hint="brew install node   (no brew? https://brew.sh)"
            linux_hint="https://nodejs.org/en/download"
            any_url="https://nodejs.org"
            ;;
        *)
            label="$tool"
            any_url=""
            ;;
    esac

    fail "$tool not found. Install $label:"
    case "$(uname -s)" in
        Darwin)
            info "  macOS:  $mac_hint"
            ;;
        Linux)
            info "  Linux:  $linux_hint"
            ;;
        *)
            [ -n "$mac_hint" ] && info "  macOS:  $mac_hint"
            [ -n "$linux_hint" ] && info "  Linux:  $linux_hint"
            ;;
    esac
    [ -n "$any_url" ] && info "  any:    $any_url"
}

check_jq() {
    if ! command -v jq &>/dev/null; then
        print_tool_install_guidance jq
        echo ""
        die "jq is required."
    fi
}

preflight_init() {
    local requested_owner="${1:-}"
    local gap_count=0
    local gh_user=""
    local owner=""
    local git_name=""
    local git_email=""
    local tool

    info "Checking prerequisites..."
    echo ""

    for tool in git gh jq node; do
        if ! command -v "$tool" &>/dev/null; then
            print_tool_install_guidance "$tool"
            echo ""
            gap_count=$((gap_count + 1))
        fi
    done

    if command -v git &>/dev/null; then
        git_name=$(git config user.name 2>/dev/null || true)
        git_email=$(git config user.email 2>/dev/null || true)
        if [ -z "$git_name" ] || [ -z "$git_email" ]; then
            fail "Git identity is not configured."
            info "  Run:"
            [ -z "$git_name" ] && info "    git config --global user.name \"Your Name\""
            [ -z "$git_email" ] && info "    git config --global user.email \"you@example.com\""
            echo ""
            gap_count=$((gap_count + 1))
        fi
    fi

    if command -v gh &>/dev/null; then
        if gh auth status &>/dev/null; then
            gh_user=$(gh api user --jq '.login' 2>/dev/null || true)
            if [ -z "$gh_user" ]; then
                fail "Could not determine your GitHub username from gh."
                info "  Run:"
                info "    gh auth login"
                echo ""
                gap_count=$((gap_count + 1))
            fi
        else
            fail "GitHub CLI is not authenticated."
            info "  Run:"
            info "    gh auth login"
            echo ""
            gap_count=$((gap_count + 1))
        fi
    fi

    if [ -n "$gh_user" ]; then
        if [ -n "$requested_owner" ]; then
            owner="$requested_owner"
        else
            read -r -p "  GitHub owner for the private vault (your account or an org) [$gh_user]: " owner
            owner="${owner:-$gh_user}"
        fi

        if [ "$owner" != "$gh_user" ]; then
            if gh api "orgs/$owner" --jq '.login' &>/dev/null; then
                :
            else
                fail "Cannot access GitHub org: $owner"
                info "  Run:"
                info "    gh auth login"
                info "  Then authorize access for $owner in the browser flow and re-run init."
                echo ""
                gap_count=$((gap_count + 1))
            fi
        fi
    fi

    if [ "$gap_count" -gt 0 ]; then
        die "Fix the prerequisite gaps above and re-run setup.sh init."
    fi

    INIT_GH_USER="$gh_user"
    INIT_VAULT_OWNER="${owner:-$gh_user}"
    ok "Prerequisites OK"
    ok "GitHub user: $INIT_GH_USER"
    if [ "$INIT_VAULT_OWNER" = "$INIT_GH_USER" ]; then
        ok "Vault owner: $INIT_VAULT_OWNER (personal account)"
    else
        ok "Vault owner: $INIT_VAULT_OWNER (org)"
    fi
    echo ""
}

vault_repo_name() {
    local owner="$1" gh_user="$2"
    if [ "$owner" = "$gh_user" ]; then
        printf 'xocortex'
    else
        printf 'xocortex-%s' "$gh_user"
    fi
}

vault_owner_label() {
    local owner="$1" gh_user="$2"
    if [ "$owner" = "$gh_user" ]; then
        printf '%s account' "$owner"
    else
        printf '%s org' "$owner"
    fi
}

write_xocortex_scaffold() {
    local xocortex_dir="$1"
    local vault_owner="$2"
    local gh_user="$3"
    local owner_label

    owner_label=$(vault_owner_label "$vault_owner" "$gh_user")

    mkdir -p "$xocortex_dir"/diary "$xocortex_dir"/notes "$xocortex_dir"/tasks/current "$xocortex_dir"/tasks/archive

    cat > "$xocortex_dir/.gitignore" <<'GITIGNORE'
tmp/
.DS_Store
.snowflake/
GITIGNORE

    cat > "$xocortex_dir/README.md" <<'XOREADME'
# xocortex

Private operator memory vault. Part of the XO system.

## Structure

- `diary/` — Daily checkpoint logs
- `notes/` — Deep investigation notes
- `tasks/` — Work item tracking

## New Machine Setup

Clone the coco-skills repo and run the plugin's setup script. `init` creates (or clones) this vault, registers the XO plugin in your Cortex `settings.json`, and sets `XOCORTEX_HOME`:

```bash
git clone https://github.com/Snowflake-Labs/coco-skills.git
coco-skills/plugins/xo/setup.sh init
```

The plugin is delivered via `settings.json`. Run `coco-skills/plugins/xo/setup.sh status` any time to check installation health.

## Privacy

This repo is PRIVATE in the VAULT_OWNER_LABEL on GitHub.
Only you (the owner) can access it.
XOREADME

    sed "s|VAULT_OWNER_LABEL|$owner_label|g" "$xocortex_dir/README.md" > "$xocortex_dir/README.md.tmp" && mv "$xocortex_dir/README.md.tmp" "$xocortex_dir/README.md"
}

vault_git_has_commits() {
    local xocortex_dir="$1"
    git -C "$xocortex_dir" rev-parse --verify HEAD &>/dev/null
}

vault_git_has_remote() {
    local xocortex_dir="$1"
    [ -d "$xocortex_dir/.git" ] && [ -n "$(git -C "$xocortex_dir" remote 2>/dev/null)" ]
}

current_git_branch() {
    local repo_dir="$1"
    local branch
    branch=$(git -C "$repo_dir" branch --show-current 2>/dev/null || true)
    printf '%s' "${branch:-HEAD}"
}

xo_dir_is_git_repo() {
    git -C "$XO_DIR" rev-parse --is-inside-work-tree &>/dev/null
}

initialize_vault_git_repo() {
    local xocortex_dir="$1"

    if [ ! -d "$xocortex_dir/.git" ]; then
        info "Initializing git repository..."
        git -C "$xocortex_dir" init -q
    else
        info "Git repository already present."
    fi

    if vault_git_has_commits "$xocortex_dir"; then
        ok "Git commit already present"
        return
    fi

    info "Creating initial commit..."
    git -C "$xocortex_dir" add .
    git -C "$xocortex_dir" commit -q -m "Initial xocortex setup"
    ok "Git initialized"
}

ensure_vault_origin_remote() {
    local xocortex_dir="$1"
    local repo_url="$2"

    if git -C "$xocortex_dir" remote get-url origin &>/dev/null; then
        return
    fi

    git -C "$xocortex_dir" remote add origin "$repo_url"
    ok "Added origin remote"
}

handle_existing_xocortex_dir() {
    local xocortex_dir="$1"
    local target="$2"
    local workspace="$3"

    if [ ! -d "$xocortex_dir/.git" ]; then
        warn "$xocortex_dir already exists but is not a git repository."
    elif vault_git_has_commits "$xocortex_dir" && vault_git_has_remote "$xocortex_dir"; then
        info "Found existing git-backed xocortex vault at $xocortex_dir"
        echo ""
        info "Running install against the existing vault..."
        echo ""
        cmd_install --target "$target" ${workspace:+--workspace "$workspace"} --xocortex "$xocortex_dir"
        return 0
    else
        warn "$xocortex_dir exists but looks partial or poisoned."
        if ! vault_git_has_commits "$xocortex_dir"; then
            info "  Missing: at least one commit"
        fi
        if ! vault_git_has_remote "$xocortex_dir"; then
            info "  Missing: a git remote"
        fi
    fi

    echo ""
    read -r -p "  Resume this init, clean up the directory, or abort? [r/c/a]: " existing_dir_action
    case "$existing_dir_action" in
        r|R)
            info "Resuming with the existing directory..."
            return 1
            ;;
        c|C|clean|CLEAN|Clean)
            rm -rf "$xocortex_dir"
            ok "Removed $xocortex_dir"
            return 1
            ;;
        a|A)
            die "Init aborted."
            ;;
        *)
            die "Init aborted. Directory left unchanged."
            ;;
    esac
}

resolve_settings_json() {
    local target="$1" workspace="$2"
    case "$target" in
        user-profile) echo "$USER_PROFILE_DIR/settings.json" ;;
        workspace)    echo "${workspace}/.snowflake/cortex/settings.json" ;;
    esac
}

# ─── Install Functions ────────────────────────────────────────────────────────

install_plugin() {
    local settings_file="$1"
    local plugin_path="$PLUGIN_DIR"
    check_jq

    if [ ! -f "$settings_file" ]; then
        mkdir -p "$(dirname "$settings_file")"
        jq -n --arg p "$plugin_path" '{"plugins": [$p]}' > "$settings_file"
        ok "Created settings.json with XO plugin registered"
        return
    fi

    local already
    already=$(jq --arg p "$plugin_path" \
        '[.plugins[]? | select(. == $p)] | length > 0' "$settings_file" 2>/dev/null || echo "false")
    if [ "$already" = "true" ]; then
        ok "Plugin already registered in settings.json"
        return
    fi

    local result
    result=$(jq --arg p "$plugin_path" '.plugins = ((.plugins // []) + [$p])' "$settings_file")
    echo "$result" | jq '.' > "$settings_file"
    ok "Registered XO plugin in settings.json"
}

remove_plugin() {
    local settings_file="$1"
    local plugin_path="$PLUGIN_DIR"
    if [ ! -f "$settings_file" ]; then
        info "No settings.json found"
        return
    fi
    check_jq

    local result
    result=$(jq --arg p "$plugin_path" \
        'if .plugins then
            .plugins = [.plugins[] | select(. != $p)] |
            if .plugins == [] then del(.plugins) else . end
        else . end' "$settings_file")
    echo "$result" | jq '.' > "$settings_file"
    ok "Removed XO plugin from settings.json"
}

# Removes a legacy .env.XOCORTEX_HOME entry. This setting never worked — CoCo does
# not inject settings.json env into the hook process — so we only clean it up for
# users upgrading from an older install. XOCORTEX_HOME is set via the shell profile
# (see install_shell_env), which is what the hooks actually read.
remove_settings_env() {
    local settings_file="$1"
    if [ ! -f "$settings_file" ]; then return; fi
    check_jq

    if jq -e '.env.XOCORTEX_HOME' "$settings_file" &>/dev/null; then
        local result
        result=$(jq 'del(.env.XOCORTEX_HOME) | if .env == {} then del(.env) else . end' "$settings_file")
        echo "$result" | jq '.' > "$settings_file"
        ok "Removed legacy XOCORTEX_HOME from settings.json (shell profile is the working mechanism)"
    fi
}

detect_shell_rc() {
    local shell_name
    shell_name=$(basename "${SHELL:-/bin/zsh}")
    case "$shell_name" in
        zsh)  echo "$HOME/.zshenv" ;;
        bash)
            if [ -f "$HOME/.bash_profile" ]; then
                echo "$HOME/.bash_profile"
            else
                echo "$HOME/.bashrc"
            fi
            ;;
        fish) echo "$HOME/.config/fish/config.fish" ;;
        *)    echo "$HOME/.profile" ;;
    esac
}

install_shell_env() {
    local xocortex_path="$1"
    local rc_file
    rc_file=$(detect_shell_rc)
    local shell_name
    shell_name=$(basename "${SHELL:-/bin/zsh}")
    local export_line grep_pattern sed_pattern
    if [ "$shell_name" = "fish" ]; then
        export_line="set -gx XOCORTEX_HOME \"$xocortex_path\""
        grep_pattern='set -gx XOCORTEX_HOME\|export XOCORTEX_HOME='
        sed_pattern='/# XO: xocortex memory repo/d; /set -gx XOCORTEX_HOME/d; /export XOCORTEX_HOME=/d'
    else
        export_line="export XOCORTEX_HOME=\"$xocortex_path\""
        grep_pattern='export XOCORTEX_HOME='
        sed_pattern='/# XO: xocortex memory repo/d; /export XOCORTEX_HOME=/d'
    fi

    if grep -q "$grep_pattern" "$rc_file" 2>/dev/null; then
        local existing
        existing=$(grep -E 'XOCORTEX_HOME' "$rc_file" | tail -1 | sed 's/.*XOCORTEX_HOME[= ]*//' | tr -d '"')
        if [ "$existing" = "$xocortex_path" ]; then
            ok "XOCORTEX_HOME already set correctly in $rc_file"
            return
        fi
        warn "XOCORTEX_HOME already exported in $rc_file with different value: $existing"
        echo ""
        read -r -p "  Replace with $xocortex_path? [y/N] " answer
        if [[ "$answer" =~ ^[Yy]$ ]]; then
            local tmp
            tmp=$(mktemp)
            sed "$sed_pattern" "$rc_file" > "$tmp"
            mv "$tmp" "$rc_file"
        else
            info "Skipping — XOCORTEX_HOME unchanged"
            return
        fi
    fi

    echo ""
    info "XO needs to add to $rc_file:"
    info "  $export_line"
    echo ""
    read -r -p "  Add this to your shell profile? [Y/n] " answer
    if [[ "$answer" =~ ^[Nn]$ ]]; then
        warn "Skipped — XOCORTEX_HOME is set in settings.json for hooks; shell export is supplemental for CLI use"
        return
    fi

    echo "" >> "$rc_file"
    echo "# XO: xocortex memory repo" >> "$rc_file"
    echo "$export_line" >> "$rc_file"
    ok "Added XOCORTEX_HOME export to $rc_file"
    warn "Run 'source $rc_file' or open a new terminal for it to take effect"
}

uninstall_shell_env() {
    local rc_file
    rc_file=$(detect_shell_rc)
    if [ ! -f "$rc_file" ]; then return; fi

    if grep -qE 'export XOCORTEX_HOME=|set -gx XOCORTEX_HOME' "$rc_file"; then
        local tmp
        tmp=$(mktemp)
        sed '/# XO: xocortex memory repo/d; /export XOCORTEX_HOME=/d; /set -gx XOCORTEX_HOME/d' "$rc_file" > "$tmp"
        mv "$tmp" "$rc_file"
        ok "Removed XOCORTEX_HOME export from $rc_file"
    fi
}

# Marker for the node-PATH entry we add (and remove on uninstall).
NODE_PATH_MARKER="# XO: node on PATH for hooks"

# Computes the PATH a GUI-/hook-spawned process inherits before any shell rc runs.
# On macOS this is what /usr/libexec/path_helper builds from /etc/paths(.d); a hook
# spawned by CoCo Desktop starts from here, NOT from the user's interactive PATH.
hook_base_path() {
    local p=""
    if [ -x /usr/libexec/path_helper ]; then
        p=$(env -i /bin/sh -c 'eval "$(/usr/libexec/path_helper -s)" 2>/dev/null; printf %s "$PATH"' 2>/dev/null || true)
    fi
    [ -z "$p" ] && p="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    printf '%s' "$p"
}

# Ensures `node` is resolvable for the hook process. CoCo spawns hooks through a
# non-interactive shell that sources only .zshenv (NOT .zshrc/.zprofile) — the same
# mechanism XOCORTEX_HOME relies on. Version managers (asdf, nvm, fnm) put node on
# PATH in interactive/login init only, so the hook spawn can't find it: the hook
# command fails before the script runs and no hooks fire. This adds node's dir to
# the rc file so the hook shell can resolve it.
install_node_path() {
    local rc_file shell_name
    rc_file=$(detect_shell_rc)
    shell_name=$(basename "${SHELL:-/bin/zsh}")

    # 1. node must be runnable in the installing user's shell.
    if ! command -v node &>/dev/null; then
        warn "node not found on your PATH — XO hooks require Node.js."
        warn "Install node (or activate your version manager) and re-run setup, or hooks will not fire."
        return
    fi

    local base_path
    base_path=$(hook_base_path)

    # 2. Skip if a hook-style shell can already RUN node from the base PATH alone
    #    (e.g. a Homebrew or /usr/local install). Test execution, not mere
    #    resolvability — a version-manager shim can resolve yet fail to run.
    if env -i HOME="$HOME" PATH="$base_path" sh -c 'node --version' &>/dev/null; then
        ok "node already runnable from the base PATH — hooks can use it; no $rc_file change needed"
        return
    fi

    # 3. Resolve node's REAL binary dir via node itself (process.execPath).
    #    This is stable and manager-agnostic: it bypasses version-manager shims
    #    (asdf's shim execs `asdf exec`, needing asdf on PATH) and ephemeral
    #    wrapper dirs (fnm's per-shell multishell dir), yielding the actual
    #    install dir, which runs node with no version-manager runtime on PATH.
    local node_real node_dir
    node_real=$(node -e 'process.stdout.write(process.execPath)' 2>/dev/null || true)
    if [ -z "$node_real" ] || [ ! -x "$node_real" ]; then
        warn "node is on PATH but could not report a stable binary location."
        warn "Add your version manager's init to $rc_file manually so non-interactive shells can run node."
        return
    fi
    node_dir=$(dirname "$node_real")

    # 4. Add node's dir to the rc file (idempotent via marker).
    local export_line
    if [ "$shell_name" = "fish" ]; then
        export_line="set -gx PATH \"$node_dir\" \$PATH"
    else
        export_line="export PATH=\"$node_dir:\$PATH\""
    fi
    if [ -f "$rc_file" ] && grep -qF "$NODE_PATH_MARKER" "$rc_file" 2>/dev/null; then
        ok "node PATH entry already present in $rc_file"
    else
        echo "" >> "$rc_file"
        echo "$NODE_PATH_MARKER" >> "$rc_file"
        echo "$export_line" >> "$rc_file"
        ok "Added node ($node_dir) to $rc_file so hooks can find it"
    fi

    # 5. Self-test by EXECUTION (not mere resolvability — a shim can resolve yet
    #    fail to run). Only meaningful for zsh: non-interactive `zsh -c` sources
    #    .zshenv, whereas non-interactive bash sources neither .bash_profile nor
    #    .bashrc (only $BASH_ENV), so the entry cannot be self-verified there.
    if [ "$shell_name" = "zsh" ]; then
        if env -i HOME="$HOME" PATH="$base_path" zsh -c 'node --version' &>/dev/null; then
            ok "Verified: a hook-style shell can now run node"
        else
            warn "Added the entry, but a clean zsh still cannot RUN node from $rc_file."
            warn "Check that '$node_dir' holds a working node, then re-run setup to re-verify."
        fi
    else
        info "Added node to $rc_file (execution self-test runs for zsh only; note a non-interactive bash hook shell won't source $rc_file — verify it can run node)."
    fi
}

uninstall_node_path() {
    local rc_file
    rc_file=$(detect_shell_rc)
    if [ ! -f "$rc_file" ]; then return; fi

    if grep -qF "$NODE_PATH_MARKER" "$rc_file" 2>/dev/null; then
        local tmp
        tmp=$(mktemp)
        # Positional N;d removes the marker line and the export line written
        # directly after it (the install pairs them); assumes they're adjacent.
        sed "\|$NODE_PATH_MARKER|{N;d;}" "$rc_file" > "$tmp"
        mv "$tmp" "$rc_file"
        ok "Removed node PATH entry from $rc_file"
    fi
}

# Informational note about tgrep, Cortex Code's built-in search index. XO cannot
# toggle the client setting or provision model access, so this only informs the
# user how to confirm it is on — XO subagents use it opportunistically and fall
# back to grep when it is absent.
print_tgrep_note() {
    info "tgrep (optional search index):"
    info "  XO uses Cortex Code's built-in tgrep index for faster search when it is"
    info "  available, and falls back to grep otherwise. tgrep auto-indexes repos"
    info "  under 1000 files; confirm it is on via the 'tgrep.enabled' Cortex Code"
    info "  setting. It needs account access to the arctic-embed embedding model —"
    info "  a 403 there disables it. No action required; grep works either way."
}

# ─── Status ───────────────────────────────────────────────────────────────────

check_installation() {
    local settings_file="$1" label="$2"
    local found=0

    info "── $label ──"

    if [ -f "$settings_file" ] && command -v jq &>/dev/null; then
        local registered
        registered=$(jq --arg p "$PLUGIN_DIR" \
            '[.plugins[]? | select(. == $p)] | length > 0' "$settings_file" 2>/dev/null || echo "false")
        if [ "$registered" = "true" ]; then
            ok "Plugin: registered ($PLUGIN_DIR)"
            found=1
        else
            info "Plugin: not registered in settings.json"
        fi
    else
        info "Plugin: no settings.json"
    fi

    # Only trust the real mechanism: the env var the hooks actually receive
    # (set via the shell profile). A settings.json .env entry does not reach
    # the hook process, so we do not read it here — doing so would falsely
    # report a broken install as healthy.
    local xocortex_home="${XOCORTEX_HOME:-}"
    if [ -n "$xocortex_home" ]; then
        if [ -d "$xocortex_home" ]; then
            ok "Memory repo (XOCORTEX_HOME): $xocortex_home"
            found=1
        else
            warn "Memory repo configured but not found: $xocortex_home"
        fi
    else
        info "XOCORTEX_HOME: not set (XO will use local fallback at ~/.snowflake/cortex/memory/xocortex/)"
    fi

    # Flag a legacy settings.json .env entry so upgrading users know to clean it
    if [ -f "$settings_file" ] && command -v jq &>/dev/null; then
        if jq -e '.env.XOCORTEX_HOME' "$settings_file" &>/dev/null; then
            warn "Legacy .env.XOCORTEX_HOME in settings.json — this never reaches hooks; run uninstall to remove it, set XOCORTEX_HOME via your shell profile instead"
        fi
    fi

    local legacy_agents="$USER_PROFILE_DIR/AGENTS.md"
    if [ -f "$legacy_agents" ] && grep -q "XO:START" "$legacy_agents" 2>/dev/null; then
        warn "Legacy AGENTS.md found at $legacy_agents — safe to delete (directives now delivered by plugin hooks)"
    fi

    local broken_symlinks=0
    if [ -d "$USER_PROFILE_DIR/agents" ]; then
        for link in "$USER_PROFILE_DIR/agents"/xo-*.agent.md; do
            [ -L "$link" ] && [ ! -e "$link" ] && broken_symlinks=$((broken_symlinks + 1))
        done
        if [ "$broken_symlinks" -gt 0 ]; then
            warn "$broken_symlinks broken xo-* agent symlinks in $USER_PROFILE_DIR/agents/ — delete them: rm $USER_PROFILE_DIR/agents/xo-*.agent.md"
        fi
    fi

    local legacy_hooks
    legacy_hooks=$(jq -r '.hooks // empty' "$settings_file" 2>/dev/null)
    if [ -n "$legacy_hooks" ] && [ "$legacy_hooks" != "null" ]; then
        warn "Legacy hooks{} block found in settings.json — remove it (hooks now delivered by plugin)"
    fi

    info "tgrep index: optional; XO uses it when on ('tgrep.enabled' setting) and falls back to grep otherwise"

    return $((1 - found))
}

# ─── Commands ─────────────────────────────────────────────────────────────────

cmd_init() {
    local target="user-profile"
    local workspace=""
    local requested_owner=""
    local xocortex_path=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --target)       target="$2"; shift 2 ;;
            --workspace)    workspace="$2"; shift 2 ;;
            --owner)        requested_owner="$2"; shift 2 ;;
            --xocortex)     xocortex_path="$2"; shift 2 ;;
            *) die "Unknown option: $1" ;;
        esac
    done

    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  XO System — First-Time Setup"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    echo "  This script will:"
    echo "    1. Create your private xocortex memory repository"
    echo "    2. Create a PRIVATE GitHub repo for that vault"
    echo "    3. Register the XO plugin in your $([ "$target" = "workspace" ] && echo "workspace" || echo "user profile")"
    echo ""
    echo "  Privacy: Your repo will be visible ONLY to you."
    echo ""

    preflight_init "$requested_owner"

    local gh_user="$INIT_GH_USER"
    local vault_owner="$INIT_VAULT_OWNER"
    local repo_name
    repo_name=$(vault_repo_name "$vault_owner" "$gh_user")
    local repo_slug="$vault_owner/$repo_name"
    local repo_url="git@github.com:$repo_slug.git"
    local owner_label
    owner_label=$(vault_owner_label "$vault_owner" "$gh_user")
    local xocortex_dir="${xocortex_path:-}"

    if [ -z "$xocortex_dir" ]; then
        if xo_dir_is_git_repo; then
            local parent_dir
            parent_dir="$(dirname "$XO_DIR")"
            xocortex_dir="$parent_dir/xocortex"
        else
            xocortex_dir="$HOME/xocortex"
            info "XO is not running from a cloned git repo; defaulting the vault to $xocortex_dir"
            echo ""
        fi
    fi

    if [ -d "$xocortex_dir" ]; then
        if handle_existing_xocortex_dir "$xocortex_dir" "$target" "$workspace"; then
            return
        fi
        echo ""
    fi

    if gh repo view "$repo_slug" &>/dev/null; then
        info "Found existing repo: $repo_slug"
        echo ""
        read -r -p "  Clone it instead of creating or resuming local setup? [Y/n]: " clone_existing

        if [[ "$clone_existing" != "n" && "$clone_existing" != "N" ]]; then
            echo ""
            info "Cloning existing repository..."
            git clone "$repo_url" "$xocortex_dir"
            ok "Cloned to $xocortex_dir"
            echo ""
            info "Installing XO..."
            echo ""
            cmd_install --target "$target" ${workspace:+--workspace "$workspace"} --xocortex "$xocortex_dir"
            return
        fi
    fi

    info "Preparing xocortex directory structure..."
    write_xocortex_scaffold "$xocortex_dir" "$vault_owner" "$gh_user"
    # tasks/index.md is generated by the sessionStart reindex hook; no template needed.
    # The VS Code workspace file is deprecated and no longer created.
    ok "Directory structure ready"
    echo ""

    initialize_vault_git_repo "$xocortex_dir"
    echo ""

    echo "═══════════════════════════════════════════════════════════════"
    echo "  Creating Private GitHub Repository"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    info "Owner: $owner_label"
    info "Repository: $repo_slug"
    info "Visibility: PRIVATE (only you can see it)"
    echo ""

    read -r -p "  Create and push now? [Y/n]: " create_repo

    if [[ "$create_repo" == "n" || "$create_repo" == "N" ]]; then
        echo ""
        info "Skipped. To set up GitHub later:"
        echo ""
        info "  cd $xocortex_dir"
        info "  gh repo create $repo_slug --private"
        info "  git remote add origin $repo_url"
        info "  git push -u origin $(current_git_branch "$xocortex_dir")"
        echo ""
    else
        echo ""
        if gh repo view "$repo_slug" &>/dev/null; then
            info "Repository already exists on GitHub."
            ensure_vault_origin_remote "$xocortex_dir" "$repo_url"
            if git -C "$xocortex_dir" push -u origin HEAD 2>/dev/null; then
                ok "Pushed local vault to existing GitHub repository"
            else
                fail "Failed to push to existing repo: $repo_slug"
                info "  cd $xocortex_dir"
                info "  git remote -v"
                info "  git push -u origin HEAD"
            fi
        else
            info "Creating private repository..."
            if gh repo create "$repo_slug" --private --source="$xocortex_dir" --push 2>/dev/null; then
                ok "GitHub repository created and pushed"
            else
                echo ""
                fail "Failed to create repo. Check your gh CLI auth:"
                info "  gh auth login"
                echo ""
                info "Then manually create:"
                info "  cd $xocortex_dir"
                info "  gh repo create $repo_slug --private"
                info "  git remote add origin $repo_url"
                info "  git push -u origin $(current_git_branch "$xocortex_dir")"
            fi
        fi
    fi

    echo ""
    info "Installing XO..."
    echo ""
    cmd_install --target "$target" ${workspace:+--workspace "$workspace"} --xocortex "$xocortex_dir"
}

cmd_install() {
    local target="user-profile"
    local workspace=""
    local xocortex_path=""
    local install_xocortex_path=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --target)       target="$2"; shift 2 ;;
            --workspace)    workspace="$2"; shift 2 ;;
            --xocortex)     xocortex_path="$2"; shift 2 ;;
            *) die "Unknown option: $1" ;;
        esac
    done

    if [ "$target" = "workspace" ] && [ -z "$workspace" ]; then
        workspace="$(pwd)"
    fi

    local settings_file
    settings_file=$(resolve_settings_json "$target" "$workspace")

    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  XO System — Install ($target)"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    info "Plugin: $PLUGIN_DIR"
    info "Settings: $settings_file"
    echo ""

    install_plugin "$settings_file"

    install_xocortex_path="$xocortex_path"
    if [ -z "$install_xocortex_path" ]; then
        install_xocortex_path="$SNOWFLAKE_HOME/cortex/memory/xocortex"
        mkdir -p "$install_xocortex_path"
    fi

    if [ -z "$xocortex_path" ]; then
        info "XOCORTEX_HOME will default to the local fallback vault: $install_xocortex_path"
    fi
    install_shell_env "$install_xocortex_path"

    install_node_path

    echo ""
    print_tgrep_note

    echo ""

    if [ "$target" = "workspace" ]; then
        echo "  ═══════════════════════════════════════════════════════════"
        warn "IMPORTANT: Add .snowflake/ to your .gitignore if not already present"
        echo "  ═══════════════════════════════════════════════════════════"
    fi

    echo ""
    ok "XO installed to $target"
    echo ""
}

cmd_uninstall() {
    local target="user-profile"
    local workspace=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --target)       target="$2"; shift 2 ;;
            --workspace)    workspace="$2"; shift 2 ;;
            *) die "Unknown option: $1" ;;
        esac
    done

    if [ "$target" = "workspace" ] && [ -z "$workspace" ]; then
        workspace="$(pwd)"
    fi

    local settings_file
    settings_file=$(resolve_settings_json "$target" "$workspace")

    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  XO System — Uninstall ($target)"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""

    remove_plugin "$settings_file"
    remove_settings_env "$settings_file"
    uninstall_shell_env
    uninstall_node_path

    echo ""
    ok "XO uninstalled from $target"
    echo ""
}

cmd_migrate() {
    local from="" to="" workspace=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --from)         from="$2"; shift 2 ;;
            --to)           to="$2"; shift 2 ;;
            --workspace)    workspace="$2"; shift 2 ;;
            *) die "Unknown option: $1" ;;
        esac
    done

    if [ -z "$from" ] || [ -z "$to" ]; then
        die "Usage: setup.sh migrate --from <target> --to <target>"
    fi
    if [ "$from" = "$to" ]; then
        die "Cannot migrate to the same target"
    fi

    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  XO System — Migrate ($from → $to)"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""

    info "Installing to $to..."
    echo ""
    cmd_install --target "$to" ${workspace:+--workspace "$workspace"}

    info "Uninstalling from $from..."
    echo ""
    cmd_uninstall --target "$from" ${workspace:+--workspace "$workspace"}

    echo ""
    ok "Migration complete: $from → $to"
    echo ""
}

cmd_status() {
    local target="" workspace=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --target)       target="$2"; shift 2 ;;
            --workspace)    workspace="$2"; shift 2 ;;
            *) die "Unknown option: $1" ;;
        esac
    done

    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  XO System — Status"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    info "XO source: $XO_DIR"
    info "Plugin: $PLUGIN_DIR"
    echo ""

    if [ -z "$target" ] || [ "$target" = "user-profile" ]; then
        check_installation "$USER_PROFILE_DIR/settings.json" "User Profile ($USER_PROFILE_DIR)"
        echo ""
    fi

    if [ -n "$target" ] && [ "$target" = "workspace" ]; then
        if [ -z "$workspace" ]; then workspace="$(pwd)"; fi
        check_installation "${workspace}/.snowflake/cortex/settings.json" "Workspace ($workspace)"
        echo ""
    fi
}

cmd_help() {
    echo ""
    echo "Usage: setup.sh <command> [options]"
    echo ""
    echo "Commands:"
    echo "  init        First-time setup: create xocortex repo and register XO plugin"
    echo "  install     Register XO plugin and set XOCORTEX_HOME"
    echo "  uninstall   Remove XO from a target"
    echo "  migrate     Move XO between user-profile and workspace"
    echo "  status      Check XO installation health"
    echo "  help        Show this help"
    echo ""
    echo "Options:"
    echo "  --target <user-profile|workspace>   Install target (default: user-profile)"
    echo "  --workspace <path>                  Workspace path (default: current directory)"
    echo "  --owner <github-owner>              Vault owner for init only (default: your gh user)"
    echo "  --xocortex <path>                   Path to xocortex memory repo"
    echo ""
    echo "Examples:"
    echo "  setup.sh init                                  # First-time setup"
    echo "  setup.sh init --target workspace               # First-time setup, install to workspace"
    echo "  setup.sh install                               # Register plugin in user profile"
    echo "  setup.sh install --xocortex ~/xocortex          # Install + set memory repo path"
    echo "  setup.sh install --target workspace             # Register plugin in current workspace"
    echo "  setup.sh uninstall --target workspace           # Remove from workspace"
    echo "  setup.sh migrate --from workspace --to user-profile"
    echo "  setup.sh status"
    echo ""
}

# ─── Main ─────────────────────────────────────────────────────────────────────

case "${1:-help}" in
    init)       shift; cmd_init "$@" ;;
    install)    shift; cmd_install "$@" ;;
    uninstall)  shift; cmd_uninstall "$@" ;;
    migrate)    shift; cmd_migrate "$@" ;;
    status)     shift; cmd_status "$@" ;;
    help|--help|-h) cmd_help ;;
    *) die "Unknown command: $1. Run 'setup.sh help' for usage." ;;
esac
