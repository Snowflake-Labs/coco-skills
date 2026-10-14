"""
AWS Glue ETL Job Config Ingestion Strategy  (PARTIAL).

Detects AWS Glue job definition JSON files and extracts:

- Script location (entry point)
- Input/output paths from ``DefaultArguments`` matching common conventions
- Connection names (potential data sources)

AWS Glue job definition format
-------------------------------
::

    {
      "Name": "my-glue-job",
      "Role": "arn:aws:iam::123456789:role/GlueRole",
      "Command": {
        "Name": "glueetl",
        "ScriptLocation": "s3://bucket/scripts/job.py",
        "PythonVersion": "3"
      },
      "DefaultArguments": {
        "--input_path":   "s3://bucket/raw/",
        "--output_path":  "s3://bucket/processed/",
        "--TempDir":      "s3://bucket/tmp/",
        "--job-bookmark-option": "job-bookmark-disable"
      },
      "Connections": {
        "Connections": ["jdbc-connection-name"]
      },
      "MaxRetries": 3,
      "GlueVersion": "4.0"
    }

Limitations
-----------
- Schema information is not available in the job definition.
- ``DefaultArguments`` parsing is heuristic: WARP recognises common
  patterns (``--input*``, ``--output*``, ``--source*``, ``--target*``).
  Non-standard argument names will not be matched.
- Connections list provides data source *names* but not the actual
  table/schema they point to.

Extend this strategy when you encounter a Glue job with a consistent
argument naming convention not covered here.

Reference
---------
https://docs.aws.amazon.com/glue/latest/dg/aws-glue-api-jobs-job.html
"""

from __future__ import annotations

import re
from pathlib import Path

from warp_core.config_ingestion.base import (
    ConfigEnrichmentResult,
    ConfigIngestionStrategy,
    ModuleResolution,
    NodeResolution,
)

_INPUT_PREFIXES  = ("--input", "--source", "--src", "--in_", "--read_")
_OUTPUT_PREFIXES = ("--output", "--target", "--dest", "--out_", "--write_", "--sink_")
_SKIP_ARGS = frozenset({
    "--TempDir", "--job-bookmark-option", "--enable-metrics",
    "--enable-continuous-cloudwatch-log", "--enable-spark-ui",
    "--spark-event-logs-path", "--job-language", "--conf",
})

_PATH_LIKE = re.compile(r"(s3://|s3a://|gs://|abfss://|hdfs://|jdbc:)")


def _arg_is_input(key: str) -> bool:
    kl = key.lower()
    return any(kl.startswith(p) for p in _INPUT_PREFIXES)


def _arg_is_output(key: str) -> bool:
    kl = key.lower()
    return any(kl.startswith(p) for p in _OUTPUT_PREFIXES)


def _path_name(uri: str) -> str:
    return uri.rstrip("/").split("/")[-1] or uri


class AwsGlueStrategy(ConfigIngestionStrategy):
    """
    Partial strategy for AWS Glue ETL job definition JSON files.

    Detection signal: ``Command.Name == "glueetl"`` OR
    ``Command.Name == "gluestreaming"``.

    Coverage: entry point (ScriptLocation), heuristic I/O from
    DefaultArguments, connection names.
    """

    name        = "AwsGlue"
    description = "AWS Glue ETL job definition (Command.Name=glueetl)"
    priority    = 15

    def detect(self, config: dict, file_path: Path) -> bool:
        if not isinstance(config, dict):
            return False
        cmd = config.get("Command", {})
        if isinstance(cmd, dict):
            return cmd.get("Name", "").lower() in ("glueetl", "gluestreaming", "pythonshell")
        return False

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
                "job_name":   config.get("Name", ""),
                "glue_version": config.get("GlueVersion", ""),
                "python_version": config.get("Command", {}).get("PythonVersion", ""),
            },
        )

        # Entry point
        script_loc = config.get("Command", {}).get("ScriptLocation", "")
        if script_loc:
            result.module_resolutions.append(ModuleResolution(
                node_type="data_in",
                module_name=script_loc,
                source_file=rel_path,
                source_key="Command.ScriptLocation",
            ))

        # DefaultArguments → heuristic I/O
        default_args = config.get("DefaultArguments", {})
        if isinstance(default_args, dict):
            for arg_key, arg_val in default_args.items():
                if arg_key in _SKIP_ARGS or not isinstance(arg_val, str):
                    continue
                if not _PATH_LIKE.search(arg_val) and not arg_val.startswith("/"):
                    continue
                if _arg_is_input(arg_key):
                    result.node_resolutions.append(NodeResolution(
                        node_type="data_in",
                        new_name=_path_name(arg_val),
                        source_file=rel_path,
                        source_key=f"DefaultArguments.{arg_key}",
                        confidence=0.65,
                    ))
                elif _arg_is_output(arg_key):
                    result.node_resolutions.append(NodeResolution(
                        node_type="data_out",
                        new_name=_path_name(arg_val),
                        source_file=rel_path,
                        source_key=f"DefaultArguments.{arg_key}",
                        confidence=0.65,
                    ))

        # Connections → data source names (low confidence — we don't know the schema)
        conns = config.get("Connections", {}).get("Connections", [])
        for conn in conns:
            if isinstance(conn, str):
                result.node_resolutions.append(NodeResolution(
                    node_type="data_in",
                    new_name=conn,
                    source_file=rel_path,
                    source_key="Connections.Connections",
                    confidence=0.5,
                ))

        if not result.node_resolutions and not result.module_resolutions:
            result.warnings.append(
                "AwsGlue strategy detected the file but could not extract any "
                "I/O paths from DefaultArguments.  Common conventions: "
                "--input_path, --output_path, --source_bucket, --target_bucket."
            )

        return result
