"""
Config Ingestion Framework.

Parses companion configuration files found in config-driven Spark workloads
and enriches the ASG with data source names, schemas, and module information
that cannot be inferred from static Python/Scala code analysis alone.

Quick start
-----------
::

    from pathlib import Path
    from warp_core.config_ingestion import run_config_ingestion_pass

    # asg is a dict already containing asg["config_files"] from the scanner
    enriched_asg = run_config_ingestion_pass(asg, workload_root=Path("/path/to/workload"))

Adding a new strategy
---------------------
See :mod:`warp_core.config_ingestion.base` for the ABC and result models,
and :mod:`warp_core.config_ingestion.registry` for registration instructions.
"""

from warp_core.config_ingestion.pass_ import (
    apply_enrichments,
    run_config_ingestion_pass,
)
from warp_core.config_ingestion.registry import (
    DEFAULT_REGISTRY,
    StrategyRegistry,
    build_default_registry,
)

__all__ = [
    "run_config_ingestion_pass",
    "apply_enrichments",
    "StrategyRegistry",
    "DEFAULT_REGISTRY",
    "build_default_registry",
]
