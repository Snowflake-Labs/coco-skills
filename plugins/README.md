# Plugins

Contributions in this folder are **plugins** — richer than a single skill. A plugin bundles one or more skills together with hooks, subagents, and other components, and installs through CoCo's plugin mechanism rather than the single-skill install used for `skills/`.

## Layout

A plugin lives under `plugins/<name>/`:

```
plugins/
  your-plugin-name/
    .cortex-plugin/plugin.json   # required — the plugin manifest
    hooks/  agents/  skills/     # plugin components, as needed
    LICENSE  README.md           # recommended
```

Only `.cortex-plugin/plugin.json` is structurally required; everything else is the author's choice of organisation. Where a file sits on disk is where it ships.

## Adding a plugin

1. Create `plugins/<your-plugin>/` with a `.cortex-plugin/plugin.json` manifest (`name`, `version`, `description`).
2. Include a `README.md` (what it does, prerequisites) and, if it needs host-side setup, a `SETUP.md`.
3. Add a `LICENSE` file inside the plugin folder, following the repository's contribution guidelines ([CONTRIBUTING.md](../CONTRIBUTING.md)).
4. Add a row for your plugin to the **Plugins** table in the repository [`README.md`](../README.md).
5. Open a PR — see [CONTRIBUTING.md](../CONTRIBUTING.md) for the review checklist.

## Installing a plugin

Point CoCo at the plugin's folder — for example, in the chat:

```
Install the plugin at https://github.com/Snowflake-Labs/coco-skills/tree/main/plugins/<name>
```

Then follow any additional setup instructions the plugin bundles (see its `README.md` / `SETUP.md`).
