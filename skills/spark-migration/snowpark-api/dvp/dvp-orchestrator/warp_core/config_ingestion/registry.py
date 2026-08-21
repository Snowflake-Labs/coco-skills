"""
Strategy Registry — auto-detect which strategies apply to a given config file.

Adding a new strategy
---------------------
1. Create a module under ``warp_core/config_ingestion/strategies/``.
2. Implement :class:`~warp_core.config_ingestion.base.ConfigIngestionStrategy`.
3. Import and register it here::

       from warp_core.config_ingestion.strategies.my_strategy import MyStrategy
       _REGISTRY.register(MyStrategy())

The registry will automatically call ``detect()`` on every registered
strategy for each config file, and run ``extract()`` on all that match.
Multiple strategies may match the same file (e.g. a Databricks job file
that also happens to have Airflow-like keys).

Ordering
--------
Strategies are sorted by ``priority`` (descending) before detection.
The first matching strategy with the highest priority wins if
``first_match_only=True`` (default: False — all matching strategies run).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from warp_core.config_ingestion.base import (
    ConfigEnrichmentResult,
    ConfigIngestionStrategy,
)
from warp_core.config_ingestion.strategies.airflow import AirflowStrategy
from warp_core.config_ingestion.strategies.aws_glue import AwsGlueStrategy
from warp_core.config_ingestion.strategies.databricks import DatabricksJobsStrategy
from warp_core.config_ingestion.strategies.telenav import TelenavKipawaStrategy

logger = logging.getLogger(__name__)


class StrategyRegistry:
    """Manages and runs all registered config ingestion strategies."""

    def __init__(self) -> None:
        self._strategies: list[ConfigIngestionStrategy] = []

    def register(self, strategy: ConfigIngestionStrategy) -> "StrategyRegistry":
        """Register a strategy instance.  Returns self for chaining."""
        self._strategies.append(strategy)
        self._strategies.sort(key=lambda s: s.priority, reverse=True)
        return self

    @property
    def strategies(self) -> list[ConfigIngestionStrategy]:
        return list(self._strategies)

    def run(
        self,
        config_files: list[dict],
        workload_root: Path,
        asg: dict,
        first_match_only: bool = False,
    ) -> list[ConfigEnrichmentResult]:
        """
        Run all matching strategies against each config file.

        Parameters
        ----------
        config_files:
            List of ``config_files`` dicts from the ASG (as produced by
            :func:`~warp_core.config_file_scanner.config_files_to_dicts`).
        workload_root:
            Absolute path to the workload root directory.
        asg:
            Current ASG dict (read-only).
        first_match_only:
            If True, only the highest-priority matching strategy runs per file.

        Returns
        -------
        List of :class:`ConfigEnrichmentResult` from all strategies that ran.
        """
        results: list[ConfigEnrichmentResult] = []

        for cf in config_files:
            rel_path = cf.get("path", "")
            fmt      = cf.get("format", "json")
            abs_path = workload_root / rel_path

            if not abs_path.exists():
                logger.debug("Config file not found on disk: %s", abs_path)
                continue

            try:
                raw = abs_path.read_text(encoding="utf-8", errors="replace")
                if fmt == "json":
                    config = json.loads(raw)
                else:
                    try:
                        import yaml  # type: ignore[import]
                        config = yaml.safe_load(raw)
                    except Exception as e:
                        logger.debug("yaml.safe_load failed for %s: %s", rel_path, e)
                        continue
            except Exception as e:
                logger.debug("Could not read config file %s: %s", rel_path, e)
                continue

            if not isinstance(config, (dict, list)):
                continue

            matched = False
            for strategy in self._strategies:
                try:
                    if not strategy.detect(config if isinstance(config, dict) else {}, abs_path):
                        continue
                except Exception as e:
                    logger.debug("detect() failed for %s / %s: %s",
                                 strategy.name, rel_path, e)
                    continue

                try:
                    result = strategy.extract(
                        config if isinstance(config, dict) else {},
                        abs_path,
                        workload_root,
                        asg,
                    )
                    results.append(result)
                    matched = True
                    logger.debug(
                        "[%s] extracted from %s: %d node resolutions, "
                        "%d column inferences, %d module resolutions",
                        strategy.name, rel_path,
                        len(result.node_resolutions),
                        len(result.column_inferences),
                        len(result.module_resolutions),
                    )
                except Exception as e:
                    logger.warning(
                        "extract() failed for strategy %s on %s: %s",
                        strategy.name, rel_path, e,
                        exc_info=True,
                    )

                if matched and first_match_only:
                    break

        return results


# ---------------------------------------------------------------------------
# Default registry — pre-loaded with all built-in strategies
# ---------------------------------------------------------------------------

def build_default_registry() -> StrategyRegistry:
    """
    Return a :class:`StrategyRegistry` pre-loaded with all built-in strategies.

    Strategies are registered in priority order (highest first):
    1. TelenavKipawa  (priority=20) — very specific detection signal
    2. DatabricksJobs (priority=15) — specific ``tasks`` structure
    3. AwsGlue        (priority=15) — specific ``Command.Name`` check
    4. Airflow        (priority=12) — ``dag_id`` key
    """
    registry = StrategyRegistry()
    registry.register(TelenavKipawaStrategy())
    registry.register(DatabricksJobsStrategy())
    registry.register(AwsGlueStrategy())
    registry.register(AirflowStrategy())
    return registry


# Module-level default instance
DEFAULT_REGISTRY: StrategyRegistry = build_default_registry()
