# XO Setup

Post-install runbook for the XO plugin. Catalog installs copy the `plugin/` tree and register the manifest, but they do not run the bundled installer for you. Clone-based installs use the same installer. Run it as below to finish setup.

---

## After install: run the bundled setup script

Running the plugin's `setup.sh` is what gives you the full install:

- a git-backed `xocortex` vault if you choose `init`
- `XOCORTEX_HOME` exported from the shell profile the hooks actually read
- `node` added to the hook shell PATH when needed, which is what makes the JS hooks reliably fire

XO still works without this step in zero-config mode. In that mode it falls back to `~/.snowflake/cortex/memory/xocortex/`, but hooks may not fire until `node` is resolvable in the hook shell.

### If you installed XO from a git clone

```bash
git clone https://github.com/Snowflake-Labs/coco-skills.git
coco-skills/plugins/xo/setup.sh install
```

### If you installed XO from the catalog

Run the bundled installer from the installed plugin directory. If you are using the default Cortex plugin location, that is typically:

```bash
~/.snowflake/cortex/plugins/xo/setup.sh install
```

If your plugin was installed somewhere else, run the `setup.sh` at the root of that install directory.

### Git-backed vault (recommended)

```bash
git clone https://github.com/Snowflake-Labs/coco-skills.git
coco-skills/plugins/xo/setup.sh init
```

From a cloned repo, `init` creates a private `xocortex` repo under your authenticated GitHub account by default (or under an org you choose), clones it alongside the `coco-skills/` checkout, registers the plugin, writes the `XOCORTEX_HOME` export, and ensures `node` is reachable for hooks. If the repo already exists it offers to clone it.

If you run `init` from a catalog-installed plugin instead of a git clone, it avoids writing a vault into the plugin cache: it honors `--xocortex <path>` if you provide one, otherwise defaults to `$HOME/xocortex` and tells you where it is going.

> Requires `node`, `git`, `gh`, `jq`, a completed `gh auth login`, and, only if you choose an org as the repo owner, access to that org.

---

## `XOCORTEX_HOME` and hook execution

The hooks read `XOCORTEX_HOME` from their process environment. CoCo runs hooks as `$SHELL -c` — a non-interactive, non-login shell — so the variable must be set in whichever file that context sources:

| Shell | File |
|-------|------|
| zsh | `~/.zshenv` |
| fish | `~/.config/fish/config.fish` |
| bash | a system mechanism — `launchctl setenv` (macOS), `/etc/environment` (Linux) |

The plugin's `setup.sh` writes this for you. To set it manually (zsh): `echo 'export XOCORTEX_HOME="/path/to/xocortex"' >> ~/.zshenv`, then open a new terminal.

---

## tgrep search index (optional)

XO uses `tgrep` — Cortex Code's built-in local search index — to speed up vault and code search when it is available, and falls back to plain `grep` when it is not. Nothing breaks without it; it is an accelerator, not a dependency.

To get the benefit, confirm it is enabled:

- **Setting:** `tgrep.enabled` in Cortex Code. tgrep auto-indexes repositories under 1000 files in the background, so most vaults are covered with no action.
- **Model access:** tgrep embeds with the `arctic-embed` model via Cortex. If your account lacks access, the first call returns a 403 and tgrep disables itself — search silently falls back to grep.

`setup.sh` prints this reminder on install; `setup.sh status` shows the current note. XO cannot toggle the setting or grant model access for you, so this step is a prompt, not an automated change.

---

## `setup.sh` commands

```
setup.sh init        First-time setup: create xocortex repo + register plugin
setup.sh install     Register plugin (set XOCORTEX_HOME with --xocortex)
setup.sh uninstall   Remove XO plugin (does not touch your vault)
setup.sh migrate     Move XO between user-profile and workspace
setup.sh status      Check installation health
setup.sh help        Usage
```

Options: `--target <user-profile|workspace>` (default user-profile), `--workspace <path>`, `--xocortex <path>`, `--owner <account-or-org>` (`init` only; default: your GitHub account).

Run the plugin's `setup.sh status` any time to verify the plugin is registered, `XOCORTEX_HOME` resolves, and no legacy config remains.

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
