"""
Telenav / Kipawa Config Ingestion Strategy.

Handles the Telenav "sparkApp + pipeline" config format used by the Kipawa
ETL framework.  Two variants are supported:

ISO (S3) variant
~~~~~~~~~~~~~~~~
::

    {
      "sparkApp": { "appName": "..." },
      "pipeline": {
        "pipelineModule": "pipeline.PipelineImpl",
        "reader": {
          "readerModule": "dataReaders.S3JsonReader",
          "config": {
            "dataReaders.S3JsonReader": {
              "s3SourceDirectory": "sample_data/"
            }
          }
        },
        "transforms": [
          {
            "transformModule": "transformers.selectExprFields.SelectSQLExpression",
            "config": {
              "extractFields": ["col1", "col2", ...],
              "schemasToChange": [{"fieldName": "col", "newType": "LongType"}]
            }
          }
        ],
        "writer": {
          "writerModule": "dataWriters.LocalParquetWriter",
          "config": {
            "dataWriters.LocalParquetWriter": {
              "outputDirectory": "output/",
              "sinkPrefix": "kipawa_raw_test"
            }
          }
        }
      }
    }

Snow (Snowflake) variant
~~~~~~~~~~~~~~~~~~~~~~~~
- reader: ``stagePath`` key instead of ``s3SourceDirectory``
- writer: ``tableName`` key (fully-qualified Snowflake table)
- transforms: same ``extractFields`` / ``schemasToChange`` patterns

``schemasToChange`` two formats
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Format 1 (object per column): ``[{"fieldName": "col", "newType": "LongType"}]``
Format 2 (flat dict): ``[{"col": "LongType", "other": "DoubleType"}]``
Both are handled.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from warp_core.config_ingestion.base import (
    ColumnInference,
    ConfigEnrichmentResult,
    ConfigIngestionStrategy,
    ModuleResolution,
    NodeResolution,
    spark_type_to_warp,
)


# ---------------------------------------------------------------------------
# Path/name helpers
# ---------------------------------------------------------------------------

def _is_env_var(value: str) -> bool:
    """
    Return True if the value is a placeholder that cannot be resolved statically.

    Covers:
    - Standard env vars: ``$VAR``, ``${VAR}``
    - Telenav-style config references: ``$JSON-PATH``, ``$SINK-PREFIX``,
      ``$CONFIG_REF``, ``$SPARK_APP``
    """
    s = value.strip()
    return s.startswith("$")


def _path_to_name(path_str: str) -> str:
    """
    Derive a human-readable node name from a file-system path or cloud URI.

    Examples
    --------
    ``"sample_data/"`` → ``"sample_data"``
    ``"s3://bucket/prefix/raw/"`` → ``"raw"``
    ``"@stage.path/raw/"`` → ``"raw"``
    ``"DEMO.SPCONN.KIPAWA_RAW_PROCESSED"`` → ``"KIPAWA_RAW_PROCESSED"``
    """
    s = path_str.rstrip("/").strip()
    # Snowflake fully-qualified name: keep last segment
    if "." in s and not ("/" in s or ":" in s):
        return s.split(".")[-1]
    # Cloud / local path: last non-empty component
    parts = [p for p in re.split(r"[/\\]", s) if p and p != "@"]
    return parts[-1] if parts else s


# ---------------------------------------------------------------------------
# Deep traversal helpers
# ---------------------------------------------------------------------------

def _find_all(obj: Any, key: str) -> list[Any]:
    """Recursively collect all values associated with *key* anywhere in *obj*."""
    results: list[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                results.append(v)
            else:
                results.extend(_find_all(v, key))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_find_all(item, key))
    return results


def _nested_get(obj: dict, *keys: str) -> Any:
    """Safe nested dict access; returns None if any key is missing."""
    cur: Any = obj
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


# ---------------------------------------------------------------------------
# Reader extraction
# ---------------------------------------------------------------------------

def _extract_reader(
    reader: dict,
    rel_path: str,
) -> tuple[NodeResolution | None, ModuleResolution | None, list[str]]:
    """
    Extract data-input node resolution and module from the ``reader`` section.

    Returns (node_resolution, module_resolution, warnings).
    """
    warnings: list[str] = []
    reader_module = reader.get("readerModule", "")
    reader_cfg    = _nested_get(reader, "config", reader_module) or {}

    # Try known path keys in priority order
    path_keys = ["s3SourceDirectory", "stagePath", "inputPath", "sourcePath",
                 "s3Path", "filePath", "inputDir", "sourceDir"]
    raw_path: str | None = None
    used_key: str | None = None
    for k in path_keys:
        v = reader_cfg.get(k)
        if v and isinstance(v, str):
            raw_path = v
            used_key = k
            break

    node_res: NodeResolution | None = None
    if raw_path:
        if _is_env_var(raw_path):
            warnings.append(
                f"Reader path key '{used_key}' contains an environment-variable "
                f"placeholder ('{raw_path}') — cannot resolve at analysis time."
            )
        else:
            name = _path_to_name(raw_path)
            node_res = NodeResolution(
                node_type="data_in",
                new_name=name,
                source_file=rel_path,
                source_key=f"pipeline.reader.config.{reader_module}.{used_key}",
            )
    else:
        warnings.append(
            "No recognisable input path key found in reader config. "
            f"Reader module: {reader_module!r}. "
            f"Available keys: {list(reader_cfg.keys())}"
        )

    mod_res: ModuleResolution | None = None
    if reader_module:
        mod_res = ModuleResolution(
            node_type="data_in",
            module_name=reader_module,
            source_file=rel_path,
            source_key="pipeline.reader.readerModule",
        )

    return node_res, mod_res, warnings


# ---------------------------------------------------------------------------
# Writer extraction
# ---------------------------------------------------------------------------

def _extract_writer(
    writer: dict,
    rel_path: str,
) -> tuple[NodeResolution | None, ModuleResolution | None, list[str]]:
    """
    Extract data-output node resolution and module from the ``writer`` section.
    """
    warnings: list[str] = []
    writer_module = writer.get("writerModule", "")
    writer_cfg    = _nested_get(writer, "config", writer_module) or {}

    # Snowflake table name takes priority (explicit asset identity)
    raw_path: str | None = None
    used_key: str | None = None

    table_keys = ["tableName", "snowflakeTable", "targetTable"]
    path_keys  = ["outputDirectory", "s3SinkDirectory", "outputPath",
                  "sinkDirectory", "outputDir", "s3OutputPath"]
    prefix_key = "sinkPrefix"

    for k in table_keys:
        v = writer_cfg.get(k)
        if v and isinstance(v, str):
            raw_path = v
            used_key = k
            break

    if not raw_path:
        for k in path_keys:
            v = writer_cfg.get(k)
            if v and isinstance(v, str):
                raw_path = v
                used_key = k
                break

    node_res: NodeResolution | None = None
    if raw_path:
        if _is_env_var(raw_path):
            warnings.append(
                f"Writer path key '{used_key}' contains an environment-variable "
                f"placeholder ('{raw_path}') — cannot resolve at analysis time."
            )
        else:
            name = _path_to_name(raw_path)
            # Optionally append sinkPrefix when present
            prefix = writer_cfg.get(prefix_key, "")
            if prefix and not _is_env_var(prefix) and used_key in path_keys:
                name = f"{name}/{prefix}"
            node_res = NodeResolution(
                node_type="data_out",
                new_name=name,
                source_file=rel_path,
                source_key=f"pipeline.writer.config.{writer_module}.{used_key}",
            )
    else:
        warnings.append(
            "No recognisable output path/table key found in writer config. "
            f"Writer module: {writer_module!r}. "
            f"Available keys: {list(writer_cfg.keys())}"
        )

    mod_res: ModuleResolution | None = None
    if writer_module:
        mod_res = ModuleResolution(
            node_type="data_out",
            module_name=writer_module,
            source_file=rel_path,
            source_key="pipeline.writer.writerModule",
        )

    return node_res, mod_res, warnings


# ---------------------------------------------------------------------------
# Transforms extraction
# ---------------------------------------------------------------------------

def _parse_schemas_to_change(raw: list) -> dict[str, str]:
    """
    Normalise ``schemasToChange`` into a ``{column_name: warp_type}`` dict.

    Handles two formats:
    - Format 1: ``[{"fieldName": "col", "newType": "LongType"}]``
    - Format 2: ``[{"col": "LongType", "other": "DoubleType"}]``
    """
    result: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        if "fieldName" in item and "newType" in item:
            # Format 1
            result[item["fieldName"]] = spark_type_to_warp(item["newType"])
        else:
            # Format 2: each key is a column name, value is the type
            for k, v in item.items():
                if isinstance(v, str):
                    result[k] = spark_type_to_warp(v)
    return result


def _extract_transforms(
    transforms: list,
    rel_path: str,
) -> tuple[list[ColumnInference], list[ModuleResolution], list[str]]:
    """
    Extract column inferences and module names from the ``transforms`` list.
    """
    col_inferences: list[ColumnInference] = []
    mod_resolutions: list[ModuleResolution] = []
    warnings: list[str] = []

    # Collect extractFields from all transforms
    all_extract_fields: list[str] = []
    extract_key_path: str = ""
    for i, t in enumerate(transforms):
        if not isinstance(t, dict):
            continue
        module = t.get("transformModule", "")
        if module:
            mod_resolutions.append(ModuleResolution(
                node_type="transformation",
                module_name=module,
                source_file=rel_path,
                source_key=f"pipeline.transforms[{i}].transformModule",
            ))
        cfg = t.get("config", {})
        fields = _find_all(cfg, "extractFields")
        if fields:
            for f_list in fields:
                if isinstance(f_list, list):
                    all_extract_fields.extend(str(x) for x in f_list)
            extract_key_path = f"pipeline.transforms[{i}].config.extractFields"

    # Collect schemasToChange from all transforms
    type_overrides: dict[str, str] = {}
    for t in transforms:
        if not isinstance(t, dict):
            continue
        raw_sc = _find_all(t.get("config", {}), "schemasToChange")
        for sc in raw_sc:
            if isinstance(sc, list):
                type_overrides.update(_parse_schemas_to_change(sc))

    if all_extract_fields:
        # Build column list, applying type overrides where available
        columns: list[dict[str, str]] = []
        for raw_col in all_extract_fields:
            # Derive short column name from dotted path (e.g. payload.log_context.client_version → client_version)
            col_name = raw_col.split(".")[-1]
            data_type = type_overrides.get(col_name, "UNKNOWN")
            columns.append({"name": col_name, "data_type": data_type, "source": "config"})

        col_inferences.append(ColumnInference(
            node_type="data_out",
            columns=columns,
            source_file=rel_path,
            source_key=extract_key_path,
        ))
    elif type_overrides:
        # schemasToChange present but no extractFields — still useful
        columns = [
            {"name": col, "data_type": dtype, "source": "config"}
            for col, dtype in type_overrides.items()
        ]
        col_inferences.append(ColumnInference(
            node_type="data_out",
            columns=columns,
            source_file=rel_path,
            source_key="pipeline.transforms[*].config.schemasToChange",
        ))
        warnings.append(
            "schemasToChange found but no extractFields. "
            "Columns derived from type-cast definitions only."
        )

    return col_inferences, mod_resolutions, warnings


# ---------------------------------------------------------------------------
# Strategy class
# ---------------------------------------------------------------------------

class TelenavKipawaStrategy(ConfigIngestionStrategy):
    """
    Full ingestion strategy for Telenav's Kipawa ETL framework.

    Detection signal: top-level ``sparkApp`` + ``pipeline`` keys.

    Extracted information
    ---------------------
    - Input path → resolves PTH_XXX ``data_in`` nodes
    - Output path / table → resolves PTH_XXX ``data_out`` nodes
    - ``extractFields`` → column list for output schema
    - ``schemasToChange`` → type annotations on those columns
    - ``readerModule`` / ``writerModule`` / ``transformModule`` → module resolutions
    """

    name        = "TelenavKipawa"
    description = "Telenav Kipawa ETL framework (sparkApp + pipeline config)"
    priority    = 20  # Higher than default — we're very specific

    def detect(self, config: dict, file_path: Path) -> bool:
        return (
            isinstance(config, dict)
            and "sparkApp" in config
            and "pipeline" in config
        )

    def extract(
        self,
        config: dict,
        file_path: Path,
        workload_root: Path,
        asg: dict,
    ) -> ConfigEnrichmentResult:
        rel_path = str(file_path.relative_to(workload_root))
        result = ConfigEnrichmentResult(
            strategy_name=self.name,
            config_file=rel_path,
            metadata={
                "app_name": _nested_get(config, "sparkApp", "appName") or "",
                "pipeline_module": _nested_get(config, "pipeline", "pipelineModule") or "",
            },
        )

        pipeline = config.get("pipeline", {})

        # --- Reader ---
        reader = pipeline.get("reader")
        if reader:
            node_res, mod_res, warns = _extract_reader(reader, rel_path)
            if node_res:
                result.node_resolutions.append(node_res)
            if mod_res:
                result.module_resolutions.append(mod_res)
            result.warnings.extend(warns)
        else:
            result.warnings.append("No 'reader' section found in pipeline config.")

        # --- Writer ---
        writer = pipeline.get("writer")
        if writer:
            node_res, mod_res, warns = _extract_writer(writer, rel_path)
            if node_res:
                result.node_resolutions.append(node_res)
            if mod_res:
                result.module_resolutions.append(mod_res)
            result.warnings.extend(warns)
        else:
            result.warnings.append("No 'writer' section found in pipeline config.")

        # --- Transforms ---
        transforms = pipeline.get("transforms", [])
        if transforms:
            col_infs, mod_ress, warns = _extract_transforms(transforms, rel_path)
            result.column_inferences.extend(col_infs)
            result.module_resolutions.extend(mod_ress)
            result.warnings.extend(warns)

        return result
