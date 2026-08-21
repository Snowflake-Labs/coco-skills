#!/usr/bin/env python3
"""
Config Manager – Configuration management for Snowpark Migrator

Handles listing, loading, creating, and saving project configurations.
Each project has its own JSON file under configurations/<project_name>.json.

Conversion-type alias normalization
-----------------------------------
Historical configs use one of: ``scos``, ``snowpark_connect``, ``snowpark-connect``
for the SCOS path and ``snowpark_api``, ``snowpark-api`` for the SMA / Snowpark
API path. ``load_configuration``/``load_global`` normalize on read and persist the
canonical hyphenated form (``snowpark-connect`` / ``snowpark-api``) immediately,
so aliases do not linger across runs.

Namespaced view
---------------
Sub-skills consume their own slice of keys via ``view_section(cfg, namespace)``.
Currently defined namespaces:

- ``snowpark_api`` — SMA-only keys: ``sql_flavor``, ``enable_jupyter_conversion``,
  ``generate_checkpoints``, ``run_ewi_fixer``, ``run_ewi_fixer.ewi_comments``,
  ``run_ewi_fixer.ewi_scope``, ``run_stage_conversion``,
  ``run_stage_conversion.stage_name``, ``run_dvp_orchestrator``, ``sma_cli_path``
- ``shared`` — path-agnostic keys: ``project_name``, ``input_folder``,
  ``output_folder``, ``email``, ``company``, ``conversion_type``,
  ``migration_status``, ``run_notebook_migration``

Usage:
    from config_manager import list_configurations, load_configuration, ...

    # List available projects
    projects = list_configurations("/path/to/skill/configurations")

    # Load (and merge defaults into) a config — aliases normalized on read
    cfg = load_configuration("/path/to/skill/configurations/my_project.json")

    # Get just the snowpark-api slice for the API sub-skill
    api_cfg = view_section(cfg, "snowpark_api")

    # Persist updates (values for conversion_type are canonicalized)
    cfg = save_configuration("/path/to/skill/configurations/my_project.json",
                             {"email": "user@co.com"})

CLI usage (from SKILL.md inline blocks):
    python3 config_manager.py list   <config_dir>
    python3 config_manager.py load   <config_path>
    python3 config_manager.py create <config_dir> <project_name>
    python3 config_manager.py save   <config_path> '<json_updates>'
    python3 config_manager.py load-global   <skill_dir>
    python3 config_manager.py save-global   <skill_dir> '<json_updates>'
    python3 config_manager.py view-section  <config_path> <namespace>
"""

import json
import os
import sys
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULTS: Dict[str, str] = {
    "conversion_type": "snowpark-connect",
    "enable_jupyter_conversion": "yes",
    "generate_checkpoints": "yes",
    "migration_status": "migrate",
    "run_dvp_orchestrator": "yes",
    "run_ewi_fixer": "yes",
    "run_ewi_fixer.ewi_comments": "mark",
    "run_ewi_fixer.ewi_scope": "only_pending",
    "run_notebook_migration": "yes",
    "run_stage_conversion": "yes",
    "run_stage_conversion.stage_name": "migration_stage",
    "sql_flavor": "SparkSql",
    # Progress UI (EXPERIMENTAL — off by default; only launch on explicit user request)
    "enable_progress_ui": "no",
    "ui_auto_open_browser": "yes",
    "ui_port": "auto",
    "ui_idle_shutdown_secs": "900",
}

GLOBAL_CONFIG_NAME = "config.json"

# Canonical conversion_type values
CT_SCOS = "snowpark-connect"
CT_API = "snowpark-api"

# Read-time aliases → canonical values. Saves always emit the canonical form.
CONVERSION_TYPE_ALIASES: Dict[str, str] = {
    "scos": CT_SCOS,
    "snowpark_connect": CT_SCOS,
    "snowpark-connect": CT_SCOS,
    "snowpark_api": CT_API,
    "snowpark-api": CT_API,
}

# Per-namespace key lists for ``view_section``.
NAMESPACE_KEYS: Dict[str, List[str]] = {
    "shared": [
        "project_name",
        "input_folder",
        "output_folder",
        "email",
        "company",
        "conversion_type",
        "migration_status",
        "run_notebook_migration",
    ],
    "snowpark_api": [
        "sql_flavor",
        "enable_jupyter_conversion",
        "generate_checkpoints",
        "run_ewi_fixer",
        "run_ewi_fixer.ewi_comments",
        "run_ewi_fixer.ewi_scope",
        "run_stage_conversion",
        "run_stage_conversion.stage_name",
        "run_dvp_orchestrator",
        # Global key surfaced through view_section_with_global below
        "sma_cli_path",
    ],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_config_path(config_dir: str, project_name: str) -> str:
    """Return the full path for a project's configuration file."""
    return os.path.join(config_dir, f"{project_name}.json")


def list_configurations(config_dir: str) -> List[str]:
    """Return sorted project names from the configurations directory."""
    if not os.path.isdir(config_dir):
        return []
    names = []
    for entry in os.listdir(config_dir):
        if entry.endswith(".json"):
            names.append(entry[: -len(".json")])
    return sorted(names)


def load_configuration(config_path: str) -> Dict[str, str]:
    """Read a configuration file, merge missing defaults, normalize aliases, persist.

    If any default keys are missing OR any alias values were normalized, the
    file is written back (deterministic: ``sort_keys=True``).
    """
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as fh:
        cfg: Dict[str, str] = json.load(fh)

    changed_defaults = _merge_defaults(cfg)
    changed_aliases = _normalize_aliases(cfg)
    if changed_defaults or changed_aliases:
        _write(config_path, cfg)

    return cfg


def create_configuration(
    config_dir: str, project_name: str
) -> Tuple[str, Dict[str, str]]:
    """Create a new configuration with all defaults pre-populated."""
    os.makedirs(config_dir, exist_ok=True)
    config_path = get_config_path(config_dir, project_name)
    cfg: Dict[str, str] = dict(DEFAULTS)
    cfg["project_name"] = project_name
    _write(config_path, cfg)
    return config_path, cfg


def save_configuration(config_path: str, updates: Dict[str, str]) -> Dict[str, str]:
    """Merge *updates* into the existing configuration and persist.

    Aliases in either the existing file or in *updates* (for ``conversion_type``)
    are normalized to canonical values before the file is written.
    """
    cfg: Dict[str, str] = {}
    if os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    cfg.update(updates)
    _normalize_aliases(cfg)
    _write(config_path, cfg)
    return cfg


def get_global_config_path(skill_dir: str) -> str:
    """Return the path to the global config file: ``<skill_dir>/config.json``."""
    return os.path.join(skill_dir, GLOBAL_CONFIG_NAME)


def load_global(skill_dir: str) -> Dict[str, str]:
    """Load the global configuration (``config.json`` at the skill root)."""
    path = get_global_config_path(skill_dir)
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_global(skill_dir: str, updates: Dict[str, str]) -> Dict[str, str]:
    """Merge *updates* into the global config and persist."""
    path = get_global_config_path(skill_dir)
    cfg: Dict[str, str] = {}
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    cfg.update(updates)
    _write(path, cfg)
    return cfg


def view_section(cfg: Dict[str, str], namespace: str) -> Dict[str, str]:
    """Return the subset of *cfg* keys belonging to *namespace*.

    The ``snowpark_api`` namespace includes all SMA-only keys.
    Missing keys are simply omitted; this is a read-only projection.
    """
    keys = NAMESPACE_KEYS.get(namespace)
    if keys is None:
        raise ValueError(
            f"Unknown namespace '{namespace}'. "
            f"Known: {sorted(NAMESPACE_KEYS.keys())}"
        )
    return {k: cfg[k] for k in keys if k in cfg}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _merge_defaults(cfg: Dict[str, str]) -> bool:
    """Add missing default keys to *cfg* in-place.  Returns True if changed."""
    changed = False
    for key, value in DEFAULTS.items():
        if key not in cfg:
            cfg[key] = value
            changed = True
    return changed


def _normalize_aliases(cfg: Dict[str, str]) -> bool:
    """Canonicalize ``conversion_type`` in-place. Returns True if changed."""
    if "conversion_type" not in cfg:
        return False
    current = cfg["conversion_type"]
    canonical = CONVERSION_TYPE_ALIASES.get(current, current)
    if canonical != current:
        cfg["conversion_type"] = canonical
        return True
    return False


def _write(path: str, cfg: Dict[str, str]) -> None:
    """Write *cfg* to *path* with deterministic formatting."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, sort_keys=True)
        fh.write("\n")


# ---------------------------------------------------------------------------
# CLI entry-point  (used by SKILL.md inline blocks)
# ---------------------------------------------------------------------------


def _cli() -> None:
    """Minimal CLI so SKILL.md can call ``python3 config_manager.py <cmd> ...``."""
    if len(sys.argv) < 2:
        print(
            "Usage: config_manager.py <list|load|create|save|"
            "load-global|save-global|view-section> ...",
            file=sys.stderr,
        )
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list":
        config_dir = sys.argv[2]
        names = list_configurations(config_dir)
        print(json.dumps(names))

    elif cmd == "load":
        config_path = sys.argv[2]
        cfg = load_configuration(config_path)
        print(json.dumps(cfg, indent=2, sort_keys=True))

    elif cmd == "create":
        config_dir = sys.argv[2]
        project_name = sys.argv[3]
        _, cfg = create_configuration(config_dir, project_name)
        print(json.dumps(cfg, indent=2, sort_keys=True))

    elif cmd == "save":
        config_path = sys.argv[2]
        updates = json.loads(sys.argv[3])
        cfg = save_configuration(config_path, updates)
        print(json.dumps(cfg, indent=2, sort_keys=True))

    elif cmd == "load-global":
        skill_dir = sys.argv[2]
        cfg = load_global(skill_dir)
        print(json.dumps(cfg, indent=2, sort_keys=True))

    elif cmd == "save-global":
        skill_dir = sys.argv[2]
        updates = json.loads(sys.argv[3])
        cfg = save_global(skill_dir, updates)
        print(json.dumps(cfg, indent=2, sort_keys=True))

    elif cmd == "view-section":
        config_path = sys.argv[2]
        namespace = sys.argv[3]
        cfg = load_configuration(config_path)
        section = view_section(cfg, namespace)
        print(json.dumps(section, indent=2, sort_keys=True))

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
