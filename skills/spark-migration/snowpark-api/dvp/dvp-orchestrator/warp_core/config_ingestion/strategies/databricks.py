"""
Databricks Jobs API v2.1 Config Ingestion Strategy  (PARTIAL).

Detects Databricks Job definition JSON files and extracts:

- Task entry points (``spark_python_task``, ``python_wheel_task``,
  ``notebook_task``, ``spark_jar_task``)
- Potential I/O paths from task ``parameters`` matching common patterns
  (``--input``, ``--output``, ``--source``, ``--target``, etc.)
- Cluster node types and Spark versions (for migration complexity hints)

Limitations
-----------
- Full schema extraction is NOT possible from Databricks Job definitions.
  Schemas live in the Python/Scala code, not in the job config.
- ``parameters`` parsing is heuristic: WARP looks for ``--flag value``
  pairs adjacent to path-like values.  It will NOT catch positional args
  or non-standard flag names.

Extend this strategy when you have a client-specific Databricks job that
uses a known parameter convention.

Reference
---------
https://docs.databricks.com/api/workspace/jobs/create
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

_PATH_LIKE = re.compile(r"(s3://|gs://|abfss://|dbfs:/|/[a-zA-Z]|hdfs://)")
_INPUT_FLAGS  = frozenset({"--input", "--input-path", "--source", "--src", "--in"})
_OUTPUT_FLAGS = frozenset({"--output", "--output-path", "--target", "--dest", "--out"})


def _parse_params(params: list[str]) -> tuple[list[str], list[str]]:
    """Extract (input_paths, output_paths) from a parameter list."""
    inputs:  list[str] = []
    outputs: list[str] = []
    i = 0
    while i < len(params):
        p = params[i].lower()
        val = params[i + 1] if i + 1 < len(params) else ""
        if p in _INPUT_FLAGS and val:
            inputs.append(val)
            i += 2
        elif p in _OUTPUT_FLAGS and val:
            outputs.append(val)
            i += 2
        else:
            i += 1
    return inputs, outputs


class DatabricksJobsStrategy(ConfigIngestionStrategy):
    """
    Partial strategy for Databricks Jobs API v2.1 JSON files.

    Detection signal: ``tasks`` key containing at least one of
    ``spark_python_task``, ``python_wheel_task``, ``notebook_task``,
    ``spark_jar_task``.

    Coverage: entry points, heuristic I/O from parameters.
    """

    name        = "DatabricksJobs"
    description = "Databricks Jobs API v2.1 (tasks with spark_python_task / notebook_task)"
    priority    = 15

    _TASK_TYPES = frozenset({
        "spark_python_task", "python_wheel_task",
        "notebook_task", "spark_jar_task",
        "run_job_task", "pipeline_task",
    })

    def detect(self, config: dict, file_path: Path) -> bool:
        if not isinstance(config, dict):
            return False
        tasks = config.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            return False
        return any(
            isinstance(t, dict) and bool(self._TASK_TYPES & set(t.keys()))
            for t in tasks
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
                "job_name": config.get("name", ""),
                "format": config.get("format", ""),
            },
        )

        for i, task in enumerate(config.get("tasks", [])):
            if not isinstance(task, dict):
                continue
            task_key = task.get("task_key", f"task_{i}")

            # spark_python_task
            spt = task.get("spark_python_task", {})
            if spt:
                python_file = spt.get("python_file", "")
                if python_file:
                    result.module_resolutions.append(ModuleResolution(
                        node_type="data_in",
                        module_name=python_file,
                        source_file=rel_path,
                        source_key=f"tasks[{i}].spark_python_task.python_file",
                    ))
                params: list[str] = spt.get("parameters", [])
                if isinstance(params, list):
                    ins, outs = _parse_params([str(p) for p in params])
                    for path_val in ins:
                        result.node_resolutions.append(NodeResolution(
                            node_type="data_in",
                            new_name=path_val.rstrip("/").split("/")[-1] or path_val,
                            source_file=rel_path,
                            source_key=f"tasks[{i}].spark_python_task.parameters",
                            confidence=0.6,
                        ))
                    for path_val in outs:
                        result.node_resolutions.append(NodeResolution(
                            node_type="data_out",
                            new_name=path_val.rstrip("/").split("/")[-1] or path_val,
                            source_file=rel_path,
                            source_key=f"tasks[{i}].spark_python_task.parameters",
                            confidence=0.6,
                        ))

            # python_wheel_task
            pwt = task.get("python_wheel_task", {})
            if pwt:
                entry_point = pwt.get("entry_point", "")
                package_name = pwt.get("package_name", "")
                if entry_point:
                    result.module_resolutions.append(ModuleResolution(
                        node_type="data_in",
                        module_name=f"{package_name}:{entry_point}" if package_name else entry_point,
                        source_file=rel_path,
                        source_key=f"tasks[{i}].python_wheel_task.entry_point",
                    ))

            # notebook_task
            nbt = task.get("notebook_task", {})
            if nbt:
                notebook_path = nbt.get("notebook_path", "")
                if notebook_path:
                    result.module_resolutions.append(ModuleResolution(
                        node_type="data_in",
                        module_name=notebook_path,
                        source_file=rel_path,
                        source_key=f"tasks[{i}].notebook_task.notebook_path",
                    ))
                base_params = nbt.get("base_parameters", {})
                if isinstance(base_params, dict):
                    for k, v in base_params.items():
                        if isinstance(v, str) and _PATH_LIKE.search(v):
                            node_type = "data_in" if any(
                                kw in k.lower() for kw in ("input", "source", "src")
                            ) else "data_out"
                            result.node_resolutions.append(NodeResolution(
                                node_type=node_type,
                                new_name=v.rstrip("/").split("/")[-1] or v,
                                source_file=rel_path,
                                source_key=f"tasks[{i}].notebook_task.base_parameters.{k}",
                                confidence=0.55,
                            ))

            if not any([spt, pwt, nbt, task.get("spark_jar_task")]):
                result.warnings.append(
                    f"Task '{task_key}' has no recognised task type — skipped."
                )

        # Cluster metadata (for migration complexity)
        job_clusters = config.get("job_clusters", [])
        for jc in job_clusters:
            nc = jc.get("new_cluster", {})
            spark_ver = nc.get("spark_version", "")
            node_type = nc.get("node_type_id", "")
            if spark_ver:
                result.metadata.setdefault("clusters", []).append({
                    "spark_version": spark_ver, "node_type": node_type,
                })

        if not result.node_resolutions and not result.module_resolutions:
            result.warnings.append(
                "DatabricksJobs strategy detected the file but could not extract "
                "any I/O paths.  Consider adding explicit --input/--output parameters "
                "to task definitions, or extending this strategy with client-specific "
                "parameter name conventions."
            )

        return result
