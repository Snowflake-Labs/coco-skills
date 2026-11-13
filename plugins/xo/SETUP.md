# XO Setup

Post-install runbook for the XO plugin. Catalog installs copy the plugin directory and register the manifest, but they do not run the bundled installer for you. Clone-based installs use the same installer. Run it as below to finish setup.

---

## After install: run the bundled setup script

The installer logic lives in `setup.js` at the plugin root (Node built-ins only). `<plugin-dir>` below means the folder that holds `setup.sh` (in a clone of the coco-skills repo, `plugins/xo/`). Run the installer through the launcher for your platform; both take the same commands and flags:

| Platform | Launcher |
|---|---|
| macOS / Linux | `<plugin-dir>/setup.sh <command>` |
| Windows | `powershell -NoProfile -ExecutionPolicy Bypass -File <plugin-dir>\setup.ps1 <command>` |

Prereqs: `node` and `git`. `gh` (with `gh auth login`) only for `init` and `vault-init`, which create a private GitHub repo. No `jq`.

The installer:

- registers a git-clone install in `settings.json` (backed up before the first change); a catalog install is registered by the catalog installer, so setup leaves it out
- removes other XO folders listed in `settings.json`
- writes `XOCORTEX_HOME` only when you choose a vault location (`--xocortex`, or `init`); the default local vault needs no env var
- checks that `node` is reachable by the hook process, and on macOS/Linux adds it to the hook shell PATH if not
- optionally creates a git-backed vault (`init`, `vault-init`)

Without `XOCORTEX_HOME`, XO records to the default local vault at `~/.snowflake/cortex/memory/xocortex/`. That is a fully supported setup, not a degraded mode; git-backing is optional. The one hard requirement is that `node` is resolvable in the hook shell, or hooks will not fire.

After any install: fully quit Cortex Code and reopen it, then open the MCP panel, click **Connect** on `xo-ops` and **Trust** it. Until you do, the `xo-ops` tools do not appear in the agent's toolset.

### If you installed XO from a git clone

```bash
git clone https://github.com/Snowflake-Labs/coco-skills.git
coco-skills/plugins/xo/setup.sh install
```

### If you installed XO from the catalog

Run the bundled installer from the folder the plugin was installed to. For a CLI install in the default location that is:

```bash
~/.snowflake/cortex/plugins/xo/setup.sh install
```

If it was installed somewhere else, run the `setup.sh` at the root of that install folder. `setup.sh status` warns if `settings.json` lists a different XO copy.

### Windows

```powershell
git clone https://github.com/Snowflake-Labs/coco-skills.git C:\src\coco-skills
powershell -NoProfile -ExecutionPolicy Bypass -File C:\src\coco-skills\plugins\xo\setup.ps1 install
```

Then restart Cortex Code and Connect + Trust `xo-ops` as above. All commands below work the same on Windows. Differences:
- CoCo starts hooks as `powershell.exe -NoProfile -Command "node \"<plugin root>/hooks/<hook>.js\""`, with the plugin root written into the command line (it is not exported as `CORTEX_PLUGIN_ROOT`). There is no shell profile: `node` must be on the persisted Machine or User `Path` (a standard Node.js install does this). The installer checks and, if it is missing, prints the manual step; it never edits `Path`.
- `XOCORTEX_HOME`, when you choose a location, is written as a User environment variable. Restart Cortex Code to pick it up.
- The settings file is `%USERPROFILE%\.snowflake\cortex\settings.json` (or `%SNOWFLAKE_HOME%\cortex\settings.json`).
- `XOCORTEX_VAULTS_ROOT` accepts `~\` as well as `~/`.

### Git-backed vault (recommended)

```bash
git clone https://github.com/Snowflake-Labs/coco-skills.git
coco-skills/plugins/xo/setup.sh init
```

`init` creates a private `xocortex` repo under your authenticated GitHub account by default (or under an org you choose), clones it to `~/xocortex` (override with `--xocortex <path>`), registers the plugin, writes the `XOCORTEX_HOME` export, and ensures `node` is reachable for hooks. If the repo already exists it offers to clone it.

`init` defaults the vault to `$HOME/xocortex` (never into the plugin or its repo clone), whether you run it from a git clone or a catalog install, and tells you where it is going. Pass `--xocortex <path>` to put it elsewhere.

> Requires `node`, `git`, `gh`, a completed `gh auth login`, and, only if you choose an org as the repo owner, access to that org.

---

## `XOCORTEX_HOME` and hook execution

The hooks read `XOCORTEX_HOME` from their process environment. CoCo runs hooks as `$SHELL -c` — a non-interactive, non-login shell — so the variable must be set in whichever file that context sources:

| Shell | File |
|-------|------|
| zsh | `~/.zshenv` |
| fish | `~/.config/fish/config.fish` |
| bash | a system mechanism — `launchctl setenv` (macOS), `/etc/environment` (Linux) |
| Windows | User environment variable (no shell profile; hooks run under `powershell -NoProfile`) |

The installer writes this for you when you pass `--xocortex` (or run `init`). To set it manually (zsh): `echo 'export XOCORTEX_HOME="/path/to/xocortex"' >> ~/.zshenv`, then open a new terminal.

---

## tgrep search index (optional)

XO uses `tgrep` — Cortex Code's built-in local search index — to speed up vault and code search when it is available, and falls back to plain `grep` when it is not. Nothing breaks without it; it is an accelerator, not a dependency.

To get the benefit, confirm it is enabled:

- **Setting:** `tgrep.enabled` in Cortex Code. tgrep auto-indexes repositories under 1000 files in the background, so most vaults are covered with no action.
- **Model access:** tgrep embeds with the `arctic-embed` model via Cortex. If your account lacks access, the first call returns a 403 and tgrep disables itself — search silently falls back to grep.

The installer prints this reminder on install; `status` shows the current note. XO cannot toggle the setting or grant model access for you, so this step is a prompt, not an automated change.

---

## Setup commands

Same on every platform (`setup.sh` on macOS/Linux, `setup.ps1` on Windows):

```
init        First-time setup: create xocortex repo + register plugin
install     Register plugin (set XOCORTEX_HOME only with --xocortex)
uninstall   Remove XO plugin (does not touch your vault)
migrate     Move XO between user-profile and workspace
status      Check installation health (incl. the resolved project vault for the current repo)
vault-init  Create a git-backed project vault for the current repo and register it
help        Usage
```

Options (`--flag value` or `--flag=value`): `--target <user-profile|workspace>` (default user-profile), `--workspace <path>`, `--xocortex <path>`, `--owner <account-or-org>` (`init`/`vault-init`; default: your GitHub account), `--name <vault-name>` (`vault-init` only; default: the repo basename), `--from`/`--to` (`migrate`), `--yes` (accept every default without prompting), `--resume`/`--clean` (`init` on an existing partial vault directory; `--clean` refuses anything that is not a vault directory).

Run `status` any time to verify the plugin is registered, the vault resolves, and no legacy config remains.

---

## Per-project vaults (opt-in)

By default every repo records to one global vault (`XOCORTEX_HOME`, or the default local vault). A repo can instead opt into **its own** vault — a separate private git repo holding that project's diary/notes/tasks — so unrelated projects don't comingle.

### Where project vaults live

Project vaults are sibling directories under a **vaults root**, never nested inside the code repo:

- Root: `XOCORTEX_VAULTS_ROOT` (must be an absolute path) — defaults to `~/xocortex-vaults/`.
- A vault named `acme` lives at `<vaults root>/acme/`.

Opt-in always references a vault by **name**, never a path — each machine resolves the name against its own `XOCORTEX_VAULTS_ROOT`, so nothing machine-local leaks into a shared repo.

### Two ways to opt in

1. **Committed `.xocortex` marker** (portable — travels with the repo). A file at the repo root:
   - empty → the vault name is the repo directory's basename;
   - one line with a name → that vault name.
   The marker **auto-opts-in every xo-using contributor** of the repo the moment they clone it — use it when that is intended. It is **manually authored and committed by you**; XO never writes it (`vault-init` / `register_vault` do not create it), because auto-opting-in every contributor warrants explicit human review. It holds a **name, never a path**.
2. **Machine-local registry entry** in `~/.snowflake/cortex/memory/xo/vaults.json`, keyed by the resolved repo-root path. Written by `setup.sh vault-init` (or the `xo-ops` `register_vault` tool — `action: set` to register/update, `action: remove` to drop the entry and fall back to marker/env). Local to your machine; nothing is written into the code repo.

### Resolution precedence

For any working directory, XO resolves the effective vault as:

**registry entry (for this repo-root) → committed `.xocortex` marker → `XOCORTEX_HOME` → default local vault**

Registry-over-marker means a machine can **locally override** a committed marker by pointing its repo-root at a different vault. `setup.sh status` prints the resolved vault and which of these four sources won for the current directory.

### Zero / one / many

- **Zero** selected: the default vault (`XOCORTEX_HOME` or the default local vault) — unchanged behaviour.
- **One**: a repo opted into its own vault.
- **Many**: several repos each with a vault — distinct, or **sharing** one vault by registering/marking them to the **same name** (covers "several repos for the same customer/function share a knowledge base").

### Create a project vault

From inside the repo you want to opt in:

```bash
<plugin-dir>/setup.sh vault-init                 # vault name defaults to the repo basename
<plugin-dir>/setup.sh vault-init --name acme     # explicit, shareable name
```

`vault-init` scaffolds `<vaults root>/<name>/`, `git init`s it, creates a private `gh` repo `<name>-xo`, pushes, and writes the machine-local registry entry for this repo-root. Idempotent — safe to re-run. Requires `node`, `git`, `gh`, and a completed `gh auth login`.

### Gotchas

- **Registry entries are keyed by the checkout's path** — if you move or rename the checkout, re-run `vault-init` (or `register_vault`) to re-register. A committed marker avoids this by travelling with the repo — **except an empty marker**, whose name is the checkout-dir basename and so drifts on rename or clone-to-a-different-name. For portable sharing, put an explicit name in the marker.
- **Basename collisions**: an empty marker or an unset `--name` derives the name from the directory basename, so two unrelated repos with the same basename would resolve to one shared vault. `vault-init` warns if the name already maps to a different repo-root, and `buildPaths` logs a warning when a resolved vault looks absent or isn't git-backed. Pass an explicit unique `--name`; use a shared name only when sharing is intended.
- Scratch, WI numbering, and diary/notes/tasks all follow the **effective** vault automatically once opted in. WI numbers are **per-vault** — a brand-new vault starts at `wi1`.
- **The `xo-ops` MCP must be authorised in your client.** XO ships an MCP server (`xo-ops`) that the agent uses to allocate WI numbers and resolve vault paths. After installing or reloading the plugin, open the MCP panel and click **Connect** on `xo-ops` to authorise it — until you do, its tools (`allocate_wi`, `resolve_paths`, `append_diary`, `register_vault`) won't appear in the agent's toolset. Note the tools appear **namespaced**, not bare — `mcp_xo-ops_<tool>` in Desktop (e.g. `mcp_xo-ops_allocate_wi`) and `mcp__xo-ops__<tool>` in the CLI — so if a bare name is missing but a tool ending in that name under an `xo-ops` namespace exists, the MCP is already connected and the agent should call that form. During a session you may also be prompted to authorise individual tool calls; choosing **always allow** lets XO manage WI numbers and vaults without prompting each time.

---

## Vault layout

```
xocortex/
  diary/{YYYY-MM}/{YYYY-MM-DD}.md       session momentum log
  tasks/current/{project}-wi{N}-*.md    active work items
  tasks/archive/{YYYY-MM}/              completed work items
  notes/{YYYY-MM}/{project}-wi{N}-*.md  deep investigation notes
  tmp/                                  scratch space (gitignored)
```

Work items and notes carry a project prefix so they group by project. `tasks/index.md` is generated automatically by the SessionStart hook.
