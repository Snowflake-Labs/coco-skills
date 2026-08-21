"""
Config Ingestion Pass — applies ConfigEnrichmentResult objects to an ASG dict.

This module is the "write side" of the config ingestion framework.  It takes
the structured enrichment data produced by strategies and applies it to an
ASG dict in a non-destructive way:

- Only resolves nodes whose name starts with ``PTH_`` or equals
  ``"Uninferable"``.  Existing, non-placeholder names are never overwritten.
- Adds InferredColumn entries to nodes that currently have no schema.
  Already-populated schemas are extended (not replaced).
- Records every enrichment action in ``config_ingestion_log`` at the
  ASG root for full traceability.

Public API
----------
:func:`run_config_ingestion_pass`
    One-shot entry point: scan, detect, extract, apply.

:func:`apply_enrichments`
    Apply a pre-computed list of :class:`ConfigEnrichmentResult` objects.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from warp_core.config_ingestion.base import (
    ColumnInference,
    ConfigEnrichmentResult,
    ModuleResolution,
    NodeResolution,
)
from warp_core.config_ingestion.registry import DEFAULT_REGISTRY, StrategyRegistry

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE_PREFIX = "PTH_"
_UNINFERABLE = "Uninferable"


def _is_placeholder(name: str) -> bool:
    return name.startswith(_PLACEHOLDER_RE_PREFIX) or name == _UNINFERABLE


# ---------------------------------------------------------------------------
# Apply helpers
# ---------------------------------------------------------------------------

def _apply_node_resolutions(
    asg: dict,
    resolutions: list[NodeResolution],
    log: list[dict],
) -> None:
    """Replace placeholder node names with resolved names."""
    for res in resolutions:
        nodes = asg.get(res.node_type, [])  # "data_in" or "data_out"
        if not isinstance(nodes, list):
            continue

        if res.node_id:
            # Exact match by ID
            targets = [n for n in nodes if n.get("id") == res.node_id]
        else:
            # Match all placeholders of this node type
            targets = [n for n in nodes if _is_placeholder(n.get("name", ""))]

        if not targets:
            logger.debug(
                "NodeResolution for '%s' found no matching placeholder nodes "
                "in %s", res.new_name, res.node_type
            )
            continue

        for node in targets:
            old_name = node.get("name", "")
            node["name"] = res.new_name
            log.append({
                "action": "node_name_resolved",
                "node_type": res.node_type,
                "node_id": node.get("id"),
                "old_name": old_name,
                "new_name": res.new_name,
                "source_file": res.source_file,
                "source_key": res.source_key,
                "confidence": res.confidence,
            })


def _apply_column_inferences(
    asg: dict,
    inferences: list[ColumnInference],
    log: list[dict],
) -> None:
    """Add InferredColumn entries to node schemas."""
    for inf in inferences:
        nodes = asg.get(inf.node_type, [])
        if not isinstance(nodes, list):
            continue

        if inf.node_id:
            targets = [n for n in nodes if n.get("id") == inf.node_id]
        else:
            # Apply to nodes that have no schema yet
            targets = [n for n in nodes if not n.get("schema")]
            if not targets:
                # Fall back to ALL nodes of this type if all already have schemas
                targets = nodes

        for node in targets:
            existing_names = {c.get("name") for c in node.get("schema", [])}
            new_cols = [
                c for c in inf.columns
                if c.get("name") not in existing_names
            ]
            if not new_cols:
                continue
            node.setdefault("schema", []).extend(new_cols)
            log.append({
                "action": "columns_added",
                "node_type": inf.node_type,
                "node_id": node.get("id"),
                "node_name": node.get("name"),
                "columns_added": [c["name"] for c in new_cols],
                "source_file": inf.source_file,
                "source_key": inf.source_key,
                "confidence": inf.confidence,
            })


def _apply_module_resolutions(
    asg: dict,
    resolutions: list[ModuleResolution],
    log: list[dict],
) -> None:
    """Record module resolutions as warnings in the ASG."""
    existing_warnings = asg.setdefault("warnings", [])
    for mod in resolutions:
        msg = (
            f"[config_ingestion] {mod.node_type} node backed by runtime module "
            f"'{mod.module_name}' (from {mod.source_file} @ {mod.source_key}). "
            "Class resolved at runtime via config — WARP cannot statically analyse "
            "this module's data contract."
        )
        existing_warnings.append({
            "type": "config_module_resolution",
            "message": msg,
            "node_type": mod.node_type,
            "module_name": mod.module_name,
            "source_file": mod.source_file,
        })
        log.append({
            "action": "module_recorded",
            "node_type": mod.node_type,
            "module_name": mod.module_name,
            "source_file": mod.source_file,
            "source_key": mod.source_key,
        })


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_enrichments(
    asg: dict,
    results: list[ConfigEnrichmentResult],
) -> dict:
    """
    Apply a list of enrichment results to *asg* in place.

    Adds a ``config_ingestion_log`` key at the ASG root with the full
    trace of every change made.

    Parameters
    ----------
    asg:
        ASG dict to mutate.
    results:
        Enrichment results from one or more strategies.

    Returns
    -------
    The mutated *asg* dict (same object).
    """
    log: list[dict] = []

    for result in results:
        _apply_node_resolutions(asg, result.node_resolutions, log)
        _apply_column_inferences(asg, result.column_inferences, log)
        _apply_module_resolutions(asg, result.module_resolutions, log)

        if result.warnings:
            asg.setdefault("warnings", []).extend([
                {
                    "type": "config_ingestion_warning",
                    "strategy": result.strategy_name,
                    "config_file": result.config_file,
                    "message": w,
                }
                for w in result.warnings
            ])

        if result.metadata:
            asg.setdefault("config_ingestion_metadata", {})[result.config_file] = {
                "strategy": result.strategy_name,
                **result.metadata,
            }

    if log:
        asg["config_ingestion_log"] = log
        logger.info(
            "Config ingestion applied %d change(s): %d node resolutions, "
            "%d column inferences, %d module resolutions.",
            len(log),
            sum(1 for e in log if e["action"] == "node_name_resolved"),
            sum(1 for e in log if e["action"] == "columns_added"),
            sum(1 for e in log if e["action"] == "module_recorded"),
        )
    else:
        logger.info("Config ingestion: no changes applied to ASG.")

    return asg


def run_config_ingestion_pass(
    asg: dict,
    workload_root: Path,
    registry: StrategyRegistry | None = None,
) -> dict:
    """
    Full one-shot config ingestion: scan → detect → extract → apply.

    Uses ``asg["config_files"]`` (populated by the config file scanner)
    to know which files to parse.  If ``config_files`` is absent, returns
    *asg* unchanged.

    Parameters
    ----------
    asg:
        ASG dict to enrich (mutated in place).
    workload_root:
        Absolute path to the workload root directory.
    registry:
        Strategy registry to use.  Defaults to
        :data:`~warp_core.config_ingestion.registry.DEFAULT_REGISTRY`.

    Returns
    -------
    The enriched *asg* dict.
    """
    config_files: list[dict] = asg.get("config_files", [])
    if not config_files:
        return asg

    _registry = registry or DEFAULT_REGISTRY

    # Only run against actionable files (not general_config)
    actionable = [
        cf for cf in config_files
        if cf.get("classification", "") != "general_config"
    ]
    if not actionable:
        return asg

    results = _registry.run(actionable, workload_root, asg)
    if results:
        apply_enrichments(asg, results)

    return asg
