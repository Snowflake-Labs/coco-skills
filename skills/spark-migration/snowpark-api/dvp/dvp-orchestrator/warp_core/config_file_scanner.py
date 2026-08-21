"""
Config File Scanner — detects companion configuration files in a workload directory.

Purpose
-------
Many enterprise Spark pipelines externalise their data source paths, schemas,
and pipeline structure into JSON/YAML configuration files.  Static analysis of
the Python or Scala code alone cannot see this information because it is loaded
at runtime via ``json.load()``, ``yaml.safe_load()``, ``importlib``, etc.

This module scans the workload directory for config files and classifies them
by the kind of information they likely contain.  The results are surfaced in
the ASG and the REPORT.md to inform the user that:

  1. Config files exist and were intentionally not analysed.
  2. They may contain data source paths, schemas, or pipeline wiring.
  3. Future WARP features could parse them to improve analysis quality.

This is purely **observational** — no changes are made to the ASG's data_in /
data_out / transformations based on these files.  That upgrade path is tracked
as a feature request in ``docs/33_CONFIG_DRIVEN_PIPELINES.md``.

Supported formats
-----------------
JSON (.json), YAML (.yaml / .yml)

Excluded paths
--------------
- Python/Node packaging: pyproject.toml, package.json, package-lock.json
- Lock files: *.lock, poetry.lock, uv.lock
- CI/CD metadata: .github/, .gitlab-ci.yml, .circleci/
- Hidden directories: .*/
- Build artifacts: __pycache__/, dist/, build/, .venv/, node_modules/
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Classification signals
# ---------------------------------------------------------------------------

# Top-level keys that strongly suggest a pipeline / data orchestration config
_PIPELINE_KEYS: frozenset[str] = frozenset({
    "reader", "writer", "transforms", "transformers", "pipeline",
    "sparkApp", "spark", "jobs", "tasks", "steps", "stages",
    "source", "sink", "connections", "workflow", "dag",
    "pipelineModule", "readerModule", "writerModule", "transformModule",
})

# Top-level keys that suggest a schema / data-contract config
_SCHEMA_KEYS: frozenset[str] = frozenset({
    "schema", "schemas", "fields", "columns", "types", "struct",
    "StructType", "StructField", "dataTypes", "columnTypes",
    "prefixes", "suffixes", "column_types",
})

# Top-level keys that suggest environment / connection config
_ENV_KEYS: frozenset[str] = frozenset({
    "database", "host", "port", "jdbc", "url", "username", "password",
    "credentials", "aws", "s3", "gcs", "azure", "blob",
    "snowflake", "redshift", "bigquery",
})

# Top-level keys that suggest a Databricks-specific job definition
_DATABRICKS_KEYS: frozenset[str] = frozenset({
    "clusters", "job_clusters", "new_cluster", "existing_cluster_id",
    "libraries", "spark_version", "node_type_id",
})

# Files that are config-like in name but are NOT data-pipeline configs
_EXCLUDED_FILENAMES: frozenset[str] = frozenset({
    "package.json", "package-lock.json", "tsconfig.json",
    "jsconfig.json", ".babelrc", ".eslintrc.json",
    "composer.json", "renovate.json",
})

# Directory names to skip entirely
_EXCLUDED_DIRS: frozenset[str] = frozenset({
    "__pycache__", ".git", ".github", ".gitlab", ".circleci",
    "node_modules", "dist", "build", ".venv", "venv", ".env",
    ".tox", ".pytest_cache", ".mypy_cache",
})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DetectedConfigFile:
    """A configuration file found in the workload directory."""

    path: str
    """Relative path from the workload root."""

    format: str
    """File format: 'json' or 'yaml'."""

    size_bytes: int
    """File size in bytes."""

    classification: str
    """
    One of:
      pipeline_config     — reader/writer/transforms wiring
      schema_definition   — column names, types, or StructType definitions
      env_config          — database connections, credentials, cloud endpoints
      databricks_job      — Databricks Job JSON (cluster + task definitions)
      column_types        — WARP-compatible column_types.yaml / .json
      general_config      — unspecific application config
    """

    signals: list[str] = field(default_factory=list)
    """Keys / patterns that triggered the classification."""

    potential_value: str = ""
    """Human-readable description of what WARP could extract from this file."""

    ai_instructions: dict = field(default_factory=dict)
    """
    Structured task for an AI agent that has file-system access.

    An AI reading this ASG can follow these instructions to enrich the ASG
    with information from this config file, without any additional context.

    Schema::

        {
          "read_file": "<path relative to workload root>",
          "goal": "<one-sentence description of the enrichment goal>",
          "extract": [
            {
              "look_for": "<key name or JSONPath-like description>",
              "contains": "<what kind of value is expected>",
              "asg_action": "<what to do with the extracted value in the ASG>",
              "example": "<example key → value → ASG update>"
            }
          ]
        }
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _collect_top_keys(obj: Any, depth: int = 2) -> set[str]:
    """Recursively collect all string keys up to *depth* levels."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        keys.update(k for k in obj if isinstance(k, str))
        if depth > 1:
            for v in obj.values():
                keys |= _collect_top_keys(v, depth - 1)
    elif isinstance(obj, list) and depth > 1:
        for item in obj[:5]:  # sample first 5 items to stay fast
            keys |= _collect_top_keys(item, depth - 1)
    return keys


def _classify(
    keys: set[str], raw_text: str, rel_path: str
) -> tuple[str, list[str], str, dict]:
    """Return (classification, signals, potential_value, ai_instructions)."""
    signals: list[str] = []
    scores: dict[str, int] = {
        "pipeline_config": 0,
        "schema_definition": 0,
        "env_config": 0,
        "databricks_job": 0,
        "column_types": 0,
    }

    for k in keys:
        if k in _PIPELINE_KEYS:
            scores["pipeline_config"] += 2
            signals.append(k)
        if k in _SCHEMA_KEYS:
            scores["schema_definition"] += 2
            signals.append(k)
        if k in _ENV_KEYS:
            scores["env_config"] += 1
            signals.append(k)
        if k in _DATABRICKS_KEYS:
            scores["databricks_job"] += 2
            signals.append(k)

    # Special: WARP column_types pattern (prefixes / suffixes keys)
    if {"prefixes", "suffixes"} & keys or "column_types" in keys:
        scores["column_types"] = 10

    best = max(scores, key=lambda c: scores[c])
    best_score = scores[best]

    if best_score == 0:
        classification = "general_config"
        potential_value = "General application config — no recognised data-pipeline patterns."
    else:
        classification = best
        potential_value = _describe(classification, signals)

    ai_instructions = _build_ai_instructions(classification, rel_path)
    return classification, list(dict.fromkeys(signals))[:8], potential_value, ai_instructions


def _build_ai_instructions(classification: str, rel_path: str) -> dict:
    """
    Return a structured task block that an AI agent can follow to enrich the
    ASG from this config file.

    The instructions are intentionally concrete and self-contained so that an
    AI with access to the workload file system can execute them without
    additional context.
    """
    base: dict = {"read_file": rel_path}

    if classification == "pipeline_config":
        base["goal"] = (
            "Extract data source paths, sink paths, and transformation module "
            "names so that WARP can resolve unnamed PTH_XXX nodes and connect "
            "the data lineage graph."
        )
        base["extract"] = [
            {
                "look_for": "Any key whose value is a file path or cloud storage "
                            "URI (e.g. s3SourceDirectory, inputDir, sourcePath, "
                            "s3Bucket, outputDirectory, sinkPrefix, outputPath).",
                "contains": "A string file-system path or cloud storage URI "
                            "(e.g. 's3://bucket/prefix/', 'data/input/').",
                "asg_action": (
                    "Match the path against data_in / data_out nodes whose name "
                    "starts with 'PTH_' (placeholder). Replace the node's name "
                    "with a human-readable label derived from the path "
                    "(e.g. last path segment without extension)."
                ),
                "example": (
                    "s3SourceDirectory = 'sample_data/' → "
                    "data_in[name='PTH_001'].name = 'sample_data'"
                ),
            },
            {
                "look_for": "Any key that references a Python module or class "
                            "name for readers, writers, or transformers "
                            "(e.g. readerModule, writerModule, transformModule, "
                            "pipelineModule, className).",
                "contains": "A dotted Python module string or class name.",
                "asg_action": (
                    "Add an AnalysisWarning to the ASG noting that the actual "
                    "reader/writer/transformer class is resolved at runtime from "
                    "this config key, linking the node to the module name."
                ),
                "example": (
                    "readerModule = 'readers.parquet_reader' → "
                    "warning: data_in node resolved via config to readers.parquet_reader"
                ),
            },
            {
                "look_for": "Any key that contains a list of column names or "
                            "field definitions (e.g. extractFields, columns, "
                            "fields, outputColumns).",
                "contains": "A list of strings (column names) or objects with "
                            "'name'/'type' pairs.",
                "asg_action": (
                    "Add InferredColumn entries (source='config') to the matching "
                    "data_in / data_out node's schema list."
                ),
                "example": (
                    "extractFields = ['client_version', 'utc_timestamp'] → "
                    "data_out[name='PTH_001'].schema += [{name:'client_version'}, ...]"
                ),
            },
            {
                "look_for": "Type cast or schema change definitions "
                            "(e.g. schemasToChange, typeCasts, fieldTypes).",
                "contains": "A list of objects with 'fieldName' and 'newType'.",
                "asg_action": (
                    "Update the data_type of the matching InferredColumn to the "
                    "Spark type specified (map to WARP internal types: "
                    "LongType→LONG, DoubleType→DOUBLE, StringType→STRING, etc.)."
                ),
                "example": (
                    "schemasToChange = [{fieldName:'utc_timestamp', newType:'LongType'}] → "
                    "column utc_timestamp.data_type = 'LONG'"
                ),
            },
        ]

    elif classification == "schema_definition":
        base["goal"] = (
            "Extract column names and data types to populate the schema of "
            "data_in / data_out nodes, improving Schema Coverage and "
            "enabling Synthetic Data generation."
        )
        base["extract"] = [
            {
                "look_for": "Any key containing field/column definitions "
                            "(e.g. schema, fields, columns, StructType, "
                            "columnTypes, types).",
                "contains": "A list of {name, type} objects, or a dict "
                            "mapping column_name → type_string.",
                "asg_action": (
                    "Add InferredColumn entries (source='config') to all "
                    "data_in / data_out nodes whose name matches the context "
                    "of this schema file (try matching by file name or "
                    "surrounding keys). If no match, add to all nodes that "
                    "currently have an empty schema."
                ),
                "example": (
                    "fields = [{name:'order_id', type:'string'}, {name:'amount', type:'double'}] → "
                    "data_in[0].schema += [{name:'order_id', data_type:'STRING'}, ...]"
                ),
            },
        ]

    elif classification == "env_config":
        base["goal"] = (
            "Extract database/table names and connection endpoints to resolve "
            "currently-unnamed data source nodes."
        )
        base["extract"] = [
            {
                "look_for": "Keys containing database names, table names, "
                            "or S3/GCS/ADLS paths "
                            "(e.g. database, table, schema, bucket, "
                            "s3Path, jdbcUrl).",
                "contains": "A string identifier for a data asset.",
                "asg_action": (
                    "Match against data_in / data_out nodes with placeholder "
                    "names (PTH_XXX or 'Uninferable'). Replace the name with "
                    "the resolved identifier."
                ),
                "example": (
                    "database = 'prod_db', table = 'sales' → "
                    "data_in[name='PTH_001'].name = 'prod_db.sales'"
                ),
            },
        ]

    elif classification == "databricks_job":
        base["goal"] = (
            "Extract job entry points (task definitions, notebook paths, "
            "Python wheel entry points) to enrich the Entry Points section."
        )
        base["extract"] = [
            {
                "look_for": "Task definitions under 'tasks' or 'job_clusters'. "
                            "Look for notebook_task.notebook_path, "
                            "python_wheel_task.entry_point, "
                            "spark_python_task.python_file.",
                "contains": "A string path or module entry point.",
                "asg_action": (
                    "If the entry point is not already in the ASG's "
                    "entrypoints list, add a note in the AnalysisWarning "
                    "that an additional Databricks task entry point was "
                    "discovered in this job definition file."
                ),
                "example": (
                    "tasks[0].spark_python_task.python_file = 'src/main.py' → "
                    "entrypoints += {type:'script', source:'src/main.py'}"
                ),
            },
        ]

    elif classification == "column_types":
        base["goal"] = (
            "This looks like a WARP column_types file. "
            "Re-run the analysis with --column-types pointing to this file "
            "to activate naming-convention type inference for all nodes."
        )
        base["extract"] = [
            {
                "look_for": "prefixes or suffixes keys.",
                "contains": "A dict mapping prefix/suffix string → Spark type name.",
                "asg_action": (
                    "Pass the file path to the WARP CLI with --column-types "
                    "to activate naming-convention-based type inference. "
                    "No manual ASG update needed."
                ),
                "example": "prefixes: {ID_: STRING, MT_: DOUBLE}",
            },
        ]

    else:
        base["goal"] = "No structured enrichment instructions available for general config files."
        base["extract"] = []

    return base


def _describe(classification: str, signals: list[str]) -> str:
    descs: dict[str, str] = {
        "pipeline_config": (
            "Contains pipeline wiring (reader / writer / transformer modules). "
            "WARP could extract: data source paths, sink paths, and the ordered "
            "transformation stages — enabling full Data I/O naming and lineage reconstruction."
        ),
        "schema_definition": (
            "Contains column definitions or type mappings. "
            "WARP could extract: column names and data types to populate the schema "
            "of data_in / data_out nodes, improving Schema Coverage and Synthetic Data."
        ),
        "env_config": (
            "Contains connection strings or cloud endpoint references. "
            "WARP could extract: table/bucket names to resolve currently-unnamed data nodes."
        ),
        "databricks_job": (
            "Databricks Job definition. "
            "WARP could extract: cluster config, library dependencies, and task entry points "
            "to enrich the Entry Points section."
        ),
        "column_types": (
            "Looks like a WARP-compatible column_types file. "
            "Pass it with --column-types to activate naming-convention type inference."
        ),
        "general_config": (
            "General application config — no recognised data-pipeline patterns detected."
        ),
    }
    return descs.get(classification, "")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_config_files(
    root: Path,
    max_files: int = 50,
    max_size_bytes: int = 512 * 1024,  # 512 KB — skip large generated files
) -> list[DetectedConfigFile]:
    """
    Scan *root* for JSON / YAML configuration files.

    Parameters
    ----------
    root:
        Workload root directory.
    max_files:
        Cap on the number of config files returned (sorted by size asc to
        prefer small, hand-crafted configs over generated dumps).
    max_size_bytes:
        Files larger than this are skipped (likely data files or lock files).

    Returns
    -------
    List of :class:`DetectedConfigFile`, sorted by classification priority then path.
    """
    found: list[DetectedConfigFile] = []

    for path in sorted(root.rglob("*")):
        # Skip excluded directories
        if any(part in _EXCLUDED_DIRS for part in path.parts):
            continue
        if any(part.startswith(".") for part in path.parts[len(root.parts):]):
            continue

        if path.suffix not in (".json", ".yaml", ".yml"):
            continue
        if path.name in _EXCLUDED_FILENAMES:
            continue

        size = path.stat().st_size if path.exists() else 0
        if size == 0 or size > max_size_bytes:
            continue

        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        parsed: Any = None
        fmt = "json" if path.suffix == ".json" else "yaml"

        if fmt == "json":
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
        else:
            try:
                import yaml  # type: ignore[import]
                parsed = yaml.safe_load(raw)
            except Exception:
                # yaml not installed or parse error — skip
                continue

        if not isinstance(parsed, (dict, list)):
            continue

        rel_path = str(path.relative_to(root))
        keys = _collect_top_keys(parsed)
        classification, signals, potential_value, ai_instructions = _classify(keys, raw, rel_path)

        found.append(DetectedConfigFile(
            path=rel_path,
            format=fmt,
            size_bytes=size,
            classification=classification,
            signals=signals,
            potential_value=potential_value,
            ai_instructions=ai_instructions,
        ))

        if len(found) >= max_files:
            break

    # Sort: pipeline_config and schema_definition first, then by path
    priority = {
        "pipeline_config": 0, "databricks_job": 1, "schema_definition": 2,
        "env_config": 3, "column_types": 4, "general_config": 5,
    }
    found.sort(key=lambda f: (priority.get(f.classification, 9), f.path))
    return found


def config_files_to_dicts(files: list[DetectedConfigFile]) -> list[dict]:
    """Serialise to plain dicts for embedding in the ASG JSON."""
    return [
        {
            "path": f.path,
            "format": f.format,
            "size_bytes": f.size_bytes,
            "classification": f.classification,
            "signals": f.signals,
            "potential_value": f.potential_value,
            "ai_instructions": f.ai_instructions,
        }
        for f in files
    ]


def build_warp_ai_hint(files: list[DetectedConfigFile]) -> dict | None:
    """
    Build a top-level ``warp_ai_hint`` block for the ASG.

    This block is a self-contained task description for an AI agent that reads
    the ASG and has access to the workload file system.  It summarises *what
    to do* at a high level, then points to the per-file ``ai_instructions``
    for the detailed extraction steps.

    Returns ``None`` when there are no actionable config files (only
    general_config entries).
    """
    actionable = [
        f for f in files if f.classification != "general_config"
    ]
    if not actionable:
        return None

    pipeline_files  = [f for f in actionable if f.classification == "pipeline_config"]
    schema_files    = [f for f in actionable if f.classification == "schema_definition"]
    env_files       = [f for f in actionable if f.classification == "env_config"]
    databricks_files = [f for f in actionable if f.classification == "databricks_job"]
    col_type_files  = [f for f in actionable if f.classification == "column_types"]

    enrichment_opportunities: list[str] = []
    if pipeline_files:
        enrichment_opportunities.append(
            "Resolve PTH_XXX placeholder node names using paths found in pipeline config files."
        )
        enrichment_opportunities.append(
            "Populate schema for data_in / data_out nodes using 'extractFields' or 'columns' lists."
        )
        enrichment_opportunities.append(
            "Identify transformation modules from 'readerModule' / 'writerModule' / 'transformModule' keys."
        )
    if schema_files:
        enrichment_opportunities.append(
            "Add InferredColumn entries (source='config') to nodes with empty schemas."
        )
    if env_files:
        enrichment_opportunities.append(
            "Replace Uninferable / PTH_XXX node names with actual database or table identifiers."
        )
    if databricks_files:
        enrichment_opportunities.append(
            "Discover additional task entry points from Databricks Job task definitions."
        )
    if col_type_files:
        enrichment_opportunities.append(
            f"Re-run WARP with --column-types '{col_type_files[0].path}' to activate "
            "naming-convention type inference (no manual ASG editing needed)."
        )

    is_config_driven = len(pipeline_files) > 0

    hint: dict = {
        "warp_version": "observational_v1",
        "trigger": "companion_config_files_detected",
        "config_driven_architecture": is_config_driven,
        "description": (
            f"{len(actionable)} companion configuration file(s) were found in this workload "
            "but were NOT analysed by WARP's static parser. "
            "An AI agent with access to the workload file system can read these files "
            "and enrich this ASG to improve Data I/O naming, schema coverage, and lineage."
            + (
                " NOTE: This workload uses a config-driven architecture — "
                "data source paths, schemas, and pipeline wiring are defined in "
                "external config files, not in the Python/Scala source code. "
                "This is why Data I/O names show as PTH_XXX and Schema Coverage is low."
                if is_config_driven else ""
            )
        ),
        "files_to_read": [f.path for f in actionable],
        "enrichment_opportunities": enrichment_opportunities,
        "asg_fields_to_update": {
            "data_in[*].name": (
                "Replace any name starting with 'PTH_' or equal to 'Uninferable' "
                "with the actual source name found in the config."
            ),
            "data_out[*].name": (
                "Replace any name starting with 'PTH_' or equal to 'Uninferable' "
                "with the actual sink name found in the config."
            ),
            "data_in[*].schema": (
                "Add InferredColumn entries for columns found in 'extractFields', "
                "'columns', 'fields', or 'schema' keys."
            ),
            "data_out[*].schema": (
                "Add InferredColumn entries for columns found in 'extractFields', "
                "'columns', 'fields', or 'schema' keys."
            ),
        },
        "per_file_instructions": (
            "See config_files[*].ai_instructions for detailed extraction steps per file."
        ),
        "important_notes": [
            "Do NOT modify source .py or .scala files — only update this ASG JSON.",
            "Use source='config' when adding InferredColumn entries.",
            "If a config value is an environment variable placeholder "
            "(e.g. ${ENV_VAR} or $VAR), mark the node as 'Uninferable' and add "
            "an AnalysisWarning explaining which env var controls the value.",
            "Preserve all existing ASG nodes — only add or update, never delete.",
        ],
    }
    return hint
