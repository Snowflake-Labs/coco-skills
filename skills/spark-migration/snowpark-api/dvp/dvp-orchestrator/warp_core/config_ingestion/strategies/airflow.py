"""
Apache Airflow DAG Config Ingestion Strategy  (PARTIAL).

Detects Airflow configuration files in JSON or YAML format and extracts:

- DAG identifier and schedule
- Connection IDs referenced in operator configurations
- S3/GCS/JDBC paths from operator params when present

Airflow DAG formats
-------------------
Airflow DAGs are most commonly written in Python (not covered here — the
PySpark parser handles ``.py`` files).  This strategy covers two
less-common but valid patterns:

**Pattern A — YAML DAG factory** (modern, used with dag-factory / Astronomer):

    .. code-block:: yaml

        dag_id: my_etl_dag
        schedule_interval: "@daily"
        tasks:
          spark_submit:
            operator: SparkSubmitOperator
            application: "s3://bucket/scripts/job.py"
            conn_id: spark_default
          upload:
            operator: S3FileTransformOperator
            source_s3_key: "s3://bucket/raw/data.csv"
            dest_s3_key: "s3://bucket/processed/data.csv"

**Pattern B — JSON variable / connection export**
(exported from Airflow via ``airflow variables export`` /
``airflow connections export``):

    .. code-block:: json

        {
          "dag_id": "my_etl_dag",
          "connections": [
            {"conn_id": "snowflake_prod", "conn_type": "snowflake", "host": "..."}
          ]
        }

Limitations
-----------
- Full lineage extraction requires Python DAG parsing, which is outside
  the scope of this strategy.
- Connection references are surfaced as potential data sources, but the
  actual tables/buckets they point to are not extractable without
  querying the live Airflow environment.
- Operator-level schema is not available.

Extend this strategy when you encounter a client that uses dag-factory
YAML with a consistent operator → I/O field mapping.

Reference
---------
https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html
https://github.com/ajbosco/dag-factory
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from warp_core.config_ingestion.base import (
    ConfigEnrichmentResult,
    ConfigIngestionStrategy,
    ModuleResolution,
    NodeResolution,
)

_PATH_LIKE = re.compile(r"(s3://|gs://|abfss://|hdfs://|s3a://|jdbc:)")

# Operator keys that contain input paths
_INPUT_FIELDS  = frozenset({
    "application", "source_s3_key", "src_path", "source_path",
    "input_path", "sql_path", "query_file", "source_dataset",
})
# Operator keys that contain output paths
_OUTPUT_FIELDS = frozenset({
    "dest_s3_key", "dest_path", "output_path", "target_path",
    "destination_dataset", "sink_path",
})


def _extract_from_task(task: dict, task_id: str, rel_path: str) -> tuple[
    list[NodeResolution], list[ModuleResolution], list[str]
]:
    """Extract I/O from a single YAML-DAG task definition."""
    resolutions: list[NodeResolution] = []
    modules: list[ModuleResolution] = []
    warnings: list[str] = []

    operator = task.get("operator", "")
    if operator:
        modules.append(ModuleResolution(
            node_type="transformation",
            module_name=operator,
            source_file=rel_path,
            source_key=f"tasks.{task_id}.operator",
        ))

    for field, val in task.items():
        if not isinstance(val, str):
            continue
        if field in _INPUT_FIELDS or (
            "source" in field.lower() and _PATH_LIKE.search(val)
        ):
            resolutions.append(NodeResolution(
                node_type="data_in",
                new_name=val.rstrip("/").split("/")[-1] or val,
                source_file=rel_path,
                source_key=f"tasks.{task_id}.{field}",
                confidence=0.6,
            ))
        elif field in _OUTPUT_FIELDS or (
            "dest" in field.lower() and _PATH_LIKE.search(val)
        ):
            resolutions.append(NodeResolution(
                node_type="data_out",
                new_name=val.rstrip("/").split("/")[-1] or val,
                source_file=rel_path,
                source_key=f"tasks.{task_id}.{field}",
                confidence=0.6,
            ))

    return resolutions, modules, warnings


class AirflowStrategy(ConfigIngestionStrategy):
    """
    Partial strategy for Airflow DAG YAML/JSON configuration files.

    Detection signal: ``dag_id`` key (both JSON and YAML).

    Coverage: DAG identity, schedule, connection IDs, heuristic I/O
    from operator field names.
    """

    name        = "Airflow"
    description = "Apache Airflow DAG config (dag_id key, YAML dag-factory or JSON export)"
    priority    = 12

    def detect(self, config: dict, file_path: Path) -> bool:
        if not isinstance(config, dict):
            return False
        return "dag_id" in config or (
            "dags" in config and isinstance(config.get("dags"), dict)
        )

    def extract(
        self,
        config: dict,
        file_path: Path,
        workload_root: Path,
        asg: dict,
    ) -> ConfigEnrichmentResult:
        rel_path = str(file_path.relative_to(workload_root))

        # Unwrap dag-factory envelope {"dags": {"dag_id": {...}}}
        if "dags" in config and "dag_id" not in config:
            dags_block = config["dags"]
            if isinstance(dags_block, dict):
                # Take the first DAG definition
                first = next(iter(dags_block.values()), {})
                if isinstance(first, dict):
                    config = {"dag_id": next(iter(dags_block.keys())), **first}

        dag_id    = config.get("dag_id", "")
        schedule  = config.get("schedule_interval") or config.get("schedule", "")
        result = ConfigEnrichmentResult(
            strategy_name=self.name,
            config_file=rel_path,
            metadata={"dag_id": dag_id, "schedule_interval": schedule},
        )

        # Tasks (YAML dag-factory style)
        tasks = config.get("tasks", {})
        if isinstance(tasks, dict):
            for task_id, task_cfg in tasks.items():
                if not isinstance(task_cfg, dict):
                    continue
                ress, mods, warns = _extract_from_task(task_cfg, task_id, rel_path)
                result.node_resolutions.extend(ress)
                result.module_resolutions.extend(mods)
                result.warnings.extend(warns)
        elif isinstance(tasks, list):
            for task_cfg in tasks:
                if not isinstance(task_cfg, dict):
                    continue
                tid = task_cfg.get("task_id", "task")
                ress, mods, warns = _extract_from_task(task_cfg, tid, rel_path)
                result.node_resolutions.extend(ress)
                result.module_resolutions.extend(mods)
                result.warnings.extend(warns)

        # Connections export
        for conn in config.get("connections", []):
            if isinstance(conn, dict):
                conn_id   = conn.get("conn_id", "")
                conn_type = conn.get("conn_type", "")
                host      = conn.get("host", "")
                schema    = conn.get("schema", "")
                if conn_id:
                    name = schema or host or conn_id
                    result.node_resolutions.append(NodeResolution(
                        node_type="data_in",
                        new_name=name,
                        source_file=rel_path,
                        source_key=f"connections[conn_id={conn_id}]",
                        confidence=0.5,
                    ))
                    result.metadata.setdefault("connection_types", {})[conn_id] = conn_type

        if not result.node_resolutions and not result.module_resolutions:
            result.warnings.append(
                f"Airflow DAG '{dag_id}' detected but no I/O paths found. "
                "Consider using dag-factory YAML with explicit source/dest fields, "
                "or parsing the Python DAG file directly."
            )

        return result
