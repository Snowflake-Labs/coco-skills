#!/usr/bin/env python3
"""End-to-end CLI to render the migration-readiness HTML report.

Builds an :class:`Assessment` IR by merging two complementary sources:

  * ``--analysis-json`` (REQUIRED) — analyzer findings from Phase 1
    (LLM-emitted, run against the post-Phase-0.5 source). Findings are
    *rebased* back to original-source line numbers via ``difflib`` so
    every line number in the rendered report references the UNMODIFIED
    customer source.
  * Original customer source (REQUIRED for accurate per-file metrics) —
    selected automatically based on which of the two flags below is set.

Canonical Phase 1a invocation (used by the migrate skill — see
``migrate-pyspark-to-snowpark-connect/agents/reporter.md`` Section A):

  * ``--migration-state-json <CONVERSION_ROOT>/migration_state.json`` —
    The renderer derives the post-Phase-0.5 source dir
    (``<CONVERSION_ROOT>/Output/``) from the state file's parent,
    materializes the ``phase-0-source`` git tag from the conversion repo
    into a temp directory (the UNMODIFIED customer source the report
    describes), uses both trees to rebase analyzer findings, and reads
    ``recipe_edits`` to populate the standalone auto-resolved panel.

Standalone / legacy invocation (for ad-hoc re-renders outside the
skill, when there is no ``migration_state.json``):

  * ``--workload-dir <path>`` — Treat ``<path>`` as both the source to
    scan AND the snippet source for analyzer findings. No git tag is
    materialized; no rebasing happens; the auto-resolved panel is
    suppressed (no ``recipe_edits`` source available). Use this only
    when re-rendering against a directory the skill never touched.

When neither flag is given the report still renders, but several
prototype sections (file types, library imports, migration waves, the
auto-resolved panel) will be empty.

Phase 1a example::

    python render_assessment.py \\
        --project tiny-workload \\
        --analysis-json        /path/to/Conversion-SCOS-.../analysis.json \\
        --migration-state-json /path/to/Conversion-SCOS-.../migration_state.json \\
        --output-html          /path/to/Conversion-SCOS-.../Reports/MigrationReadinessReport.html \\
        --dump-ir              /path/to/Conversion-SCOS-.../Reports/AssessmentIR.json

Standalone re-render against a raw checkout::

    python render_assessment.py \\
        --project tiny-workload \\
        --analysis-json /path/to/analysis.json \\
        --workload-dir  /path/to/tiny-workload-src \\
        --output-html   /tmp/readiness.html
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import edge_reconcile  # noqa: E402

from _original_source import (  # noqa: E402
    OriginalSourceUnavailable,
    materialize_original_source,
)
from adapters import prototype_v1  # noqa: E402
from assess_ir import Assessment, DataSourceRow, GraphEdge, LLMResolvedEdge, SectionNarratives, SourceSinkInventoryRow, edge_lineage_role  # noqa: E402
from file_info import _platform_from_dag_location, _target_type_from_dag_location  # noqa: E402
from recipe_resolved_panel import build_recipe_resolved_panel  # noqa: E402
from scan_codebase import rebuild_data_flow_graph, scan as scan_codebase  # noqa: E402
from transform_analysis import transform as transform_analysis  # noqa: E402

logger = logging.getLogger(__name__)


_EMPTY_NARRATIVE_MARKERS = {
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "tbd",
    "-",
    "--",
}


def _normalize_narrative_text(value: object) -> str:
    """Normalize one narrative field to avoid low-value overrides.

    Empty/placeholder values are collapsed to ``""`` so template fallbacks fire.
    """
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if not text:
        return ""
    if text.lower() in _EMPTY_NARRATIVE_MARKERS:
        return ""
    return text


def _coerce_narratives_obj(data: object, source: str) -> SectionNarratives:
    """Validate/coerce a narratives JSON object into SectionNarratives.

    Unknown keys are ignored so stale producer payloads don't break rendering.
    """
    if not isinstance(data, dict):
        raise ValueError(
            f"Expected narratives JSON from {source} to be an object; "
            f"got {type(data).__name__}"
        )
    normalized: dict[str, str] = {}
    for key in SectionNarratives.model_fields:
        if key in data:
            normalized[key] = _normalize_narrative_text(data[key])
    return SectionNarratives(**normalized)


def _load_narratives(narratives_inline_json: str | None) -> SectionNarratives:
    """Load optional advisory narratives from inline JSON only.

    We keep this inline so report generation stays single-artifact from the
    caller's perspective; the output remains one HTML file (plus optional IR).
    """
    if narratives_inline_json:
        try:
            data = json.loads(narratives_inline_json)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Inline narratives JSON is not valid JSON: {e}"
            ) from e
        return _coerce_narratives_obj(data, "inline payload")
    return SectionNarratives()


_OUTPUT_SUBDIR = "Output"
"""Phase 0 step 6 commits the whole ``<CONVERSION>`` folder (``Output/``,
``Reports/``, ``Logs/``, ``migration_state.json``) under the ``phase-0-source``
tag. After extraction, the customer's source lives at ``<extracted>/Output/``."""

# scripts/assessment/render_assessment.py → scripts/recipes/
_RECIPES_DIR = _SCRIPT_DIR.parent / "recipes"
"""Static location of the LibCST recipe folders. The auto-resolved panel
reads each recipe's docstring's first paragraph to render a human-readable
description per row, so the panel stays informative as new recipes are
added (no hardcoded recipe-id-to-description table)."""


def _load_recipe_edits(migration_state_json: Path | None) -> dict | None:
    """Read ``migration_state.json[recipe_edits]`` if the file is present.

    Returns ``None`` on any IO / parse error (the caller proceeds without a
    recipe-resolved panel — never a fatal). Returns an empty dict if the
    file exists but has no recipe edits.
    """
    if migration_state_json is None:
        return None
    if not migration_state_json.is_file():
        logger.warning(
            "migration_state.json not found at %s; recipe-resolved panel "
            "will be empty.",
            migration_state_json,
        )
        return None
    try:
        state = json.loads(migration_state_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(
            "Could not parse %s as JSON (%s); recipe-resolved panel will be "
            "empty.",
            migration_state_json,
            e,
        )
        return None
    recipe_edits = state.get("recipe_edits") if isinstance(state, dict) else None
    if recipe_edits is None:
        return {}
    if not isinstance(recipe_edits, dict):
        logger.warning(
            "migration_state.json[recipe_edits] is %s, expected object; "
            "ignoring.",
            type(recipe_edits).__name__,
        )
        return {}
    return recipe_edits


def build_assessment(
    *,
    project: str,
    analysis_json: Path,
    workload_dir: Path | None = None,
    workload_root: str | None = None,
    language: str = "python",
    narratives_inline_json: str | None = None,
    original_source_dir: Path | None = None,
    migration_state_json: Path | None = None,
    session: "object | None" = None,
) -> Assessment:
    """Build an Assessment IR by merging analyzer + codebase scans.

    Raises ``FileNotFoundError`` if ``analysis_json`` does not exist — this
    is the user-facing signal that they need to run Phase 1 of the migrate
    skill first.

    ``session`` — an optional Snowpark ``Session`` forwarded to
    ``scan_codebase.scan``. When provided, the scanner uses it to fetch the
    authoritative Snowflake Anaconda-channel package list for the AR
    Required flag; when omitted, the scanner falls back to the cached /
    bundled defaults documented in ``file_info._load_anaconda_snapshot``.

    Tier-B parameters (no extra LLM cost):

    * ``original_source_dir`` — directory holding the customer's
      pre-Phase-0.5 source. When provided, the scanner reads from this dir
      (instead of ``workload_dir``) and the analyzer findings are rebased
      onto the original via :func:`transform_analysis.transform`.
    * ``migration_state_json`` — path to ``<CONVERSION>/migration_state.json``.
      When provided AND ``original_source_dir`` is None, the renderer
      materializes the ``phase-0-source`` git tag from the conversion repo
      into a temp directory for the duration of this call. Also feeds the
      ``recipe_resolved`` panel via
      :func:`recipe_resolved_panel.build_recipe_resolved_panel`.

    Backward compatibility: when both new parameters are ``None``, behaves
    exactly as before (scan ``workload_dir``, no rebasing, empty
    ``recipe_resolved``).
    """
    if not analysis_json.exists():
        raise FileNotFoundError(
            f"analysis.json not found at {analysis_json}. "
            "This file is produced by Phase 1 of the migrate skill — the "
            "reporter should not be invoked before Phase 1 completes."
        )

    try:
        findings = json.loads(analysis_json.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(
            f"analysis.json at {analysis_json} is not valid JSON: {e}. "
            "If Phase 1 was interrupted, re-run the analyzer to regenerate it."
        ) from e
    if not isinstance(findings, list):
        raise ValueError(
            f"Expected analysis.json to be a JSON array; got {type(findings).__name__}"
        )

    recipe_edits = _load_recipe_edits(migration_state_json)

    # Derive post-Phase-0.5 source dir from the conversion root when the
    # caller didn't pass --workload-dir but did pass --migration-state-json.
    # The on-disk layout is fixed by Phase 0: <CONVERSION>/Output/ holds the
    # mutated source the analyzer ran against.
    if workload_dir is None and migration_state_json is not None:
        candidate_post = migration_state_json.parent / _OUTPUT_SUBDIR
        if candidate_post.is_dir():
            workload_dir = candidate_post

    # The ExitStack scopes the optional materialized temp dir to the
    # entire scan+transform window so it lives until the IR is built.
    with contextlib.ExitStack() as stack:
        # Resolve the original-source dir, materializing from git if needed.
        if original_source_dir is None and migration_state_json is not None:
            conversion_root = migration_state_json.parent
            try:
                materialized = stack.enter_context(
                    materialize_original_source(conversion_root)
                )
                candidate = materialized / _OUTPUT_SUBDIR
                if candidate.is_dir():
                    original_source_dir = candidate
                else:
                    # Fall back to the extraction root itself if Output/
                    # isn't present (defensive — should always exist post-
                    # Phase-0).
                    original_source_dir = materialized
            except OriginalSourceUnavailable as e:
                logger.warning(
                    "Could not materialize pre-Phase-0.5 source from %s "
                    "(%s); the report will reflect post-Phase-0.5 code "
                    "(legacy behavior).",
                    conversion_root,
                    e,
                )
                original_source_dir = None

        # Scanner source: prefer the original snapshot, fall back to workload_dir.
        scanner_source = original_source_dir if original_source_dir is not None else workload_dir

        # When the caller hasn't explicitly pinned a workload_root, default
        # it to ``workload_dir`` so the analyzer's file paths line up with
        # the scanner's (both producers then emit ``src/foo.py`` rather than
        # one emitting ``src/foo.py`` and the other emitting ``foo.py``).
        # Without this the longest-common-parent heuristic can drift down
        # into a subdirectory when every finding happens to live under it
        # (e.g. all findings under ``src/``), producing basename-only paths
        # that the path-keyed merge can't align. The analyzer ran on the
        # POST-recipe code, so post-recipe paths are the right canonical
        # form regardless of what the scanner reads.
        effective_root = workload_root
        if effective_root is None and workload_dir is not None and workload_dir.exists():
            effective_root = str(workload_dir.resolve())

        analyzer_ir = transform_analysis(
            findings,
            project=project,
            workload_root=effective_root,
            analysis_json_path=str(analysis_json.resolve()),
            original_source_dir=original_source_dir,
            post_recipe_source_dir=workload_dir,
            language=language,
        )

        if scanner_source is not None and scanner_source.exists():
            codebase_ir = scan_codebase(
                scanner_source, project=project, language=language, session=session,
            )
            # Codebase IR is the structural backbone; we layer analyzer findings
            # on top so HIGH-severity rows beat the scanner's optimistic "High"
            # readiness defaults.
            merged = codebase_ir.merge(analyzer_ir)
        else:
            merged = analyzer_ir

        # Advisory narrative layer (R11). Injected after the deterministic merge so
        # the facts are fixed before the LLM-authored prose is layered on. Sections
        # left empty here fall back to deterministic text in the adapter.
        narratives = _load_narratives(narratives_inline_json=narratives_inline_json)
        if narratives.model_dump(exclude_defaults=True):
            merged.narratives = narratives

        # Recipe-Data Isolation Guarantee: the only consumer of recipe_edits
        # in the renderer. Assigned POST-merge so recipe data never enters
        # any risk/score/compatibility aggregation. The template renders this
        # field as a standalone informational table.
        #
        # The panel builder accepts BOTH source dirs to drive its
        # marker-driven path:
        #   - ``original_source_dir``: pre-Phase-0.5 tree; used for diff
        #     classification AND for snippets in the silent-rewrite fallback.
        #   - ``workload_dir`` (post-recipe tree): scanned for SCOS markers
        #     and read for per-instance snippets. Markers carry an accurate
        #     per-site message and a correct line number (unlike the
        #     occasionally-off ``recipe_edits[*].src_line``), so the panel
        #     surfaces what the recipe actually did, where it did it.
        #   - ``_RECIPES_DIR``: fallback summary source for the
        #     silent-rewrite path (recipes that don't insert markers).
        # All enrichments are best-effort; missing dirs / unparseable
        # files just degrade to the older recipe-edits-only behaviour
        # rather than blocking the panel.
        if recipe_edits:
            merged.recipe_resolved = build_recipe_resolved_panel(
                recipe_edits,
                recipes_dir=_RECIPES_DIR,
                original_source_dir=original_source_dir,
                post_recipe_source_dir=workload_dir,
            )

        merged.metadata.generated_at = datetime.now(timezone.utc)
        return merged


_GENERIC_DIR_NAMES = frozenset({
    "output", "src", "source", "code", "app", "project", "workspace",
    "workload", "input", "data", "build", "dist", "reports", "conversion",
})


def _infer_project_name(workload_dir: Path | None, analysis_json: Path) -> str:
    """Walk up the path tree to find a meaningful project name.

    Skips generic directory names (Output, src, etc.) and timestamp-like
    segments (Conversion-SCOS-...). Looks for a segment that contains a
    recognizable project identifier.
    """
    # Collect candidate paths: prefer workload_dir, fall back to analysis_json
    base = (workload_dir or analysis_json.parent).resolve()
    for part in reversed(base.parts):
        lower = part.lower()
        if lower in _GENERIC_DIR_NAMES:
            continue
        if lower.startswith("conversion") or lower.startswith("."):
            continue
        # Strip common prefixes like "00_" and suffixes like "_scos"
        name = part
        # Remove leading number prefix (e.g., "00_Kipawa_scos" -> "Kipawa_scos")
        import re as _re
        name = _re.sub(r"^\d+_", "", name)
        # Remove trailing _scos, _spark, _migration suffixes
        name = _re.sub(r"[_-](scos|spark|migration|workload)$", "", name, flags=_re.IGNORECASE)
        if name and name.lower() not in _GENERIC_DIR_NAMES:
            return name
    return "unknown-project"


def _format_from_signature(sig: str) -> str:
    """Guess a data format label from a resolved signature string.

    Used to bucket LLM-resolved edges into the existing DataSourceRow
    aggregation (which groups by (connection, format)). Only needs to
    distinguish the common cases — unknown is fine, the row will render
    with format "Unknown" which is honest.
    """
    lo = sig.lower()
    if lo.endswith(".parquet") or "parquet" in lo:
        return "Parquet"
    if lo.endswith(".csv") or "csv" in lo:
        return "Csv"
    if lo.endswith(".json") or "/json" in lo:
        return "Json"
    if lo.endswith(".delta") or "delta" in lo:
        return "Delta"
    if lo.endswith(".orc") or "orc" in lo:
        return "Orc"
    # Table-name pattern (DB.SCHEMA.TABLE, no extension, no scheme):
    if "://" not in lo and lo.count(".") >= 1 and " " not in lo:
        return "Table"
    return "Unknown"


def _update_data_sources_from_llm_edges(assessment: Assessment) -> Assessment:
    """Fold LLM-resolved read/write signatures into ``data_sources`` and
    ``sources_sinks_inventory`` so the overview chart and Additional Discovery
    tab reflect what the LLM found, not just what the static AST walker saw.

    Deduplication rules:
      * Paths already present in ``read_paths`` / ``write_paths`` of the
        matching row are not appended again.
      * Counts are incremented only for newly-added paths — an LLM resolution
        that confirms a path already in the static scan does not double-count.
      * A (connection, format) row that already exists in ``data_sources`` is
        updated in-place; a genuinely new (connection, format) pair creates a
        new row.

    Called after ``apply_llm_resolved_edges`` (which populates
    ``assessment.llm_resolved_data_edges``) so both the DAG and the
    data_sources table are enriched together.
    """
    llm = assessment.llm_resolved_data_edges
    if llm is None:
        return assessment

    # Build a lookup: (connection, format) → DataSourceRow (mutable in-place).
    ds_index: dict[tuple[str, str], DataSourceRow] = {}
    for row in assessment.data_sources:
        ds_index[(row.connection, row.format)] = row

    # Build read-side and write-side path sets per existing row for dedup.
    read_path_sets: dict[tuple[str, str], set[str]] = {
        k: set(v.read_paths) for k, v in ds_index.items()
    }
    write_path_sets: dict[tuple[str, str], set[str]] = {
        k: set(v.write_paths) for k, v in ds_index.items()
    }

    for edge in llm.edges:
        if edge.resolution_type not in ("literal_found", "traced", "inferred"):
            continue
        sig = edge.resolved_signature
        if not sig:
            continue
        from assess_ir import edge_lineage_role
        role = edge_lineage_role(edge.kind)
        if role not in ("source", "sink"):
            continue

        connection = _platform_from_dag_location(sig) or "Unknown"
        fmt = _format_from_signature(sig)
        key = (connection, fmt)

        # Ensure the row exists.
        if key not in ds_index:
            new_row = DataSourceRow(
                connection=connection, format=fmt, reads=0, writes=0
            )
            assessment.data_sources.append(new_row)
            ds_index[key] = new_row
            read_path_sets[key] = set()
            write_path_sets[key] = set()

        row = ds_index[key]

        if role == "source":
            if sig not in read_path_sets[key]:
                read_path_sets[key].add(sig)
                row.read_paths.append(sig)
                row.reads += 1
                if edge.file and edge.file not in row.read_files:
                    row.read_files.append(edge.file)
                # Legacy combined paths field — keep in sync.
                if sig not in row.paths:
                    row.paths.append(sig)
                if edge.file and edge.file not in row.files:
                    row.files.append(edge.file)
        else:  # sink
            if sig not in write_path_sets[key]:
                write_path_sets[key].add(sig)
                row.write_paths.append(sig)
                row.writes += 1
                if edge.file and edge.file not in row.write_files:
                    row.write_files.append(edge.file)
                if sig not in row.paths:
                    row.paths.append(sig)
                if edge.file and edge.file not in row.files:
                    row.files.append(edge.file)

    # Rebuild sources_sinks_inventory to reflect the enriched data_sources.
    new_inv: list[SourceSinkInventoryRow] = []
    for row in assessment.data_sources:
        label = f"{row.connection} {row.format}".strip() if row.connection else row.format
        if row.reads > 0:
            new_inv.append(SourceSinkInventoryRow(
                direction="Source", category=label, occurrences=row.reads, detected=True
            ))
        if row.writes > 0:
            new_inv.append(SourceSinkInventoryRow(
                direction="Sink", category=label, occurrences=row.writes, detected=True
            ))
    # Preserve any non-data_sources rows already in the inventory
    # (e.g. Streaming/JDBC rows added by scan_codebase from pattern hits).
    existing_labels = {r.category for r in new_inv}
    for old_row in assessment.sources_sinks_inventory:
        if old_row.category not in existing_labels:
            new_inv.append(old_row)
    assessment.sources_sinks_inventory = new_inv

    return assessment


def _backfill_file_info_target_locations(assessment: Assessment) -> None:
    """Backfill empty target_location fields in file_info rows from the (possibly
    LLM-enriched) data_dependency_graph.

    Called after apply_llm_resolved_edges so that LLM-resolved sink signatures
    (e.g. runtime config paths the static scan couldn't see) populate the
    Target Location column in the file info table.
    """
    dag = assessment.data_dependency_graph
    if dag is None or not assessment.file_info:
        return

    # File-node IDs equal the workload-relative path by scanner convention,
    # so edge.source is directly comparable to row.path.
    node_by_id = {n.id: n for n in dag.nodes}
    dag_sinks_by_file: dict[str, list[str]] = {}
    for edge in dag.edges:
        tgt_node = node_by_id.get(edge.target)
        if tgt_node is not None and tgt_node.id.startswith("ext:sink:"):
            loc = getattr(tgt_node, "full_label", None) or getattr(tgt_node, "label", "") or ""
            if loc:
                dag_sinks_by_file.setdefault(edge.source, []).append(loc)

    for row in assessment.file_info:
        if row.target_location:
            continue
        sinks = dag_sinks_by_file.get(row.path, [])
        if sinks:
            row.target_location = sinks[0]


def apply_llm_resolved_edges(assessment: Assessment, workload_dir: Path) -> Assessment:
    """Rebuild the data DAG with LLM-resolved signatures and mark enriched nodes.

    Called when ``--llm-resolved-edges`` is passed.  Reads
    ``assessment.llm_resolved_data_edges``, injects verifiable edges
    (``literal_found`` / ``traced``) into the DAG signature maps, rebuilds
    the data-flow graph layout, and marks LLM-enriched graph nodes.

    ``unresolved_data_edges`` is managed by the caller (``main()``): it uses
    the stored IR's post-agent state rather than a fresh scan, so that the
    report reflects what the agent already resolved.

    Returns the mutated Assessment.
    """
    llm = assessment.llm_resolved_data_edges
    if llm is None:
        logger.warning(
            "--llm-resolved-edges passed but llm_resolved_data_edges is absent "
            "in the IR.  Follow agents/data_edge_resolver.md to populate it."
        )
        return assessment

    # Build sig maps — only DAG-worthy resolution types enter the graph.
    llm_source_sigs: dict[str, list[str]] = {}
    llm_sink_sigs: dict[str, list[str]] = {}

    for edge in llm.edges:
        # Every confidence level is drawn — literal_found, traced AND inferred.
        # An `inferred` edge is still the LLM's resolution of a real call site;
        # reconciliation removes it from the unresolved table (it's accounted
        # for), so it MUST also appear in the DAG or it would vanish entirely.
        if edge.resolution_type not in ("literal_found", "traced", "inferred"):
            continue
        sig = edge.resolved_signature
        if not sig or not edge.file:
            continue
        role = edge_lineage_role(edge.kind)
        if role is None:
            logger.warning(
                "LLM edge in %s has unrecognised kind %r; excluding from the "
                "data DAG (treated as neutral). Add it to assess_ir lineage "
                "kind sets if it should produce/consume data.",
                edge.file, edge.kind,
            )
            continue
        if role == "neutral":
            # Destructive/teardown op (DROP/DELETE/TRUNCATE): records an
            # operation but does NOT produce or consume data — must not create
            # a writer→reader edge (else a DROP looks like a producer and
            # fabricates a backward edge to whoever reads that table).
            continue
        if role == "source":
            llm_source_sigs.setdefault(sig, [])
            if edge.file not in llm_source_sigs[sig]:
                llm_source_sigs[sig].append(edge.file)
        else:  # role == "sink"
            llm_sink_sigs.setdefault(sig, [])
            if edge.file not in llm_sink_sigs[sig]:
                llm_sink_sigs[sig].append(edge.file)

    # Build the LLM-resolved dynamic-import map, keyed by (orchestrator, line),
    # so the chain builder can resolve sites the static dispatch gave up on and
    # lay them out as proper chains instead of leaving them as blind spots.
    llm_import_targets: dict[tuple[str, int], list[str]] = {}
    for imp in llm.resolved_imports:
        if imp.resolved_targets:
            llm_import_targets[(imp.file, imp.line)] = list(imp.resolved_targets)

    if not llm_source_sigs and not llm_sink_sigs and not llm_import_targets:
        logger.info("No DAG-worthy LLM data edges/imports; skipping DAG rebuild.")
    else:
        new_dag = rebuild_data_flow_graph(
            workload_dir, llm_source_sigs, llm_sink_sigs,
            llm_import_targets=llm_import_targets,
        )
        if new_dag is not None:
            assessment.data_dependency_graph = new_dag

    # Orchestration handoffs are drawn into whatever graph we now have (the
    # rebuilt one, or the pre-existing one when there were no data edges to
    # rebuild from) — they connect stages that share no table.
    if assessment.data_dependency_graph is not None:
        _inject_orchestration_edges(
            assessment.data_dependency_graph, llm.orchestration_edges
        )

    # Enrich the overview data_sources table and sources_sinks_inventory with
    # LLM-resolved edges so the overview chart and Additional Discovery tab
    # reflect everything the LLM found, not just the static AST scan.
    _update_data_sources_from_llm_edges(assessment)

    # Surface the LLM-resolved read/write paths in each file's node detail panel
    # and recolour the rebuilt nodes to match per-file readiness. Both operate on
    # the final graph so they apply whether or not the DAG was rebuilt.
    if assessment.data_dependency_graph is not None:
        _enrich_node_endpoints(assessment.data_dependency_graph, llm)
        _recolor_dag_nodes(assessment.data_dependency_graph, assessment.files)

    return assessment


def _enrich_node_endpoints(dag, llm) -> None:
    """Surface LLM-resolved read/write paths in each file's node detail panel.

    The click-detail panel binds to ``node.external_sources`` /
    ``node.external_sinks``, which the deterministic scan fills only for paths
    the AST walker could resolve.  The LLM resolved additional paths (the whole
    point of the pass) and uses them to draw edges — but they were never written
    back onto the node, so a resolved file's detail panel showed empty.  Mirror
    the resolved signatures onto the matching node so the panel reflects what
    each file actually reads and writes.

    Shown WITHOUT any LLM attribution: the paths are merged into the same lists
    as the AST-discovered ones and the node carries no LLM badge, so the report
    stays clean.  Deduplicated against whatever the scan already recorded.
    """
    if dag is None or not getattr(llm, "edges", None):
        return
    by_id = {n.id: n for n in dag.nodes}
    by_path = {n.path: n for n in dag.nodes if n.path}
    for edge in llm.edges:
        if edge.resolution_type not in ("literal_found", "traced", "inferred"):
            continue
        sig = (edge.resolved_signature or "").strip()
        if not sig or not edge.file:
            continue
        role = edge_lineage_role(edge.kind)
        if role not in ("source", "sink"):
            continue  # neutral (drop/delete/truncate) — not a data endpoint
        node = by_id.get(edge.file) or by_path.get(edge.file)
        if node is None:
            continue
        bucket = node.external_sources if role == "source" else node.external_sinks
        if sig not in bucket:
            bucket.append(sig)


def _recolor_dag_nodes(dag, files) -> None:
    """Backfill per-file readiness onto DAG nodes so colours match the table.

    ``Assessment.merge`` recolours graph nodes from the per-file readiness
    table, but the LLM DAG rebuild happens AFTER merge — its fresh nodes carry
    the scanner default (``status="High"`` → green), which is why every
    LLM-rebuilt node rendered green regardless of compatibility.  Re-apply the
    same backfill (path match, then unique-basename fallback) so nodes show the
    real green / yellow / red readiness.
    """
    if dag is None or not files:
        return
    by_path = {f.path: f for f in files}
    by_name: dict[str, object] = {}
    for f in files:
        if f.name in by_name:
            by_name[f.name] = None  # ambiguous basename: don't use
        else:
            by_name[f.name] = f
    for node in dag.nodes:
        canonical = by_path.get(node.id) or by_path.get(node.path)
        if canonical is None:
            canonical = by_name.get(node.full_label or node.label)
        if canonical is not None:
            node.status = canonical.status


def _inject_orchestration_edges(dag, orchestration_edges) -> None:
    """Draw LLM-recorded pipeline handoffs as dashed ``orchestrates`` arrows.

    Data-signature matching cannot link stages that share no table (a
    ``dbutils.taskValues`` handoff, a ``%run`` include, a job dependency).  For
    each :class:`OrchestrationEdge` whose endpoints both resolve to real graph
    nodes, append a ``kind="orchestrates"`` :class:`GraphEdge` routed from the
    source node's bottom-centre to the target node's top-centre — reusing the
    existing orchestrates rendering (dashed blue arrow + legend + blast-radius).

    Additive and best-effort: an edge to/from a missing node is skipped, and a
    handoff already drawn (e.g. by the static orchestrator scan) is not
    duplicated.
    """
    if not orchestration_edges:
        return
    by_id = {n.id: n for n in dag.nodes}
    existing = {(e.source, e.target) for e in dag.edges}
    for oe in orchestration_edges:
        src = by_id.get(oe.from_file)
        tgt = by_id.get(oe.to_file)
        if src is None or tgt is None:
            logger.warning(
                "orchestration_edge %s -> %s skipped: endpoint not a DAG node.",
                oe.from_file, oe.to_file,
            )
            continue
        if (oe.from_file, oe.to_file) in existing:
            continue
        dag.edges.append(GraphEdge(
            x1=int(src.x + src.width / 2), y1=int(src.y + src.height),
            x2=int(tgt.x + tgt.width / 2), y2=int(tgt.y),
            source=oe.from_file, target=oe.to_file,
            kind="orchestrates",
            label=oe.mechanism or "orchestrates",
        ))
    # NB: do NOT touch dag.edge_count — it counts data (writer→reader) edges
    # only (see _build_data_flow_graph), and the header renders it as
    # "N writer→reader connections". Orchestration arrows are drawn but are not
    # writer→reader edges, so they must not inflate that number.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--project", default=None,
                        help="Project name. Inferred from --workload-dir basename if omitted.")
    parser.add_argument(
        "--analysis-json",
        type=Path,
        required=True,
        help="Path to an existing analysis.json produced by the migrate skill.",
    )
    parser.add_argument(
        "--workload-dir",
        type=Path,
        default=None,
        help="LEGACY / standalone-rerender flag. Project directory to scan "
        "deterministically (file types, imports, complex patterns, "
        "dependency graph, migration waves, …). The canonical Phase 1a path "
        "uses --migration-state-json instead, which auto-derives the "
        "post-recipe tree and materializes the phase-0-source tag for the "
        "original-source scan + rebase. Only set --workload-dir directly "
        "when re-rendering against a raw checkout with no migration_state.json. "
        "If neither flag is given the Overview/Detailed/Migration "
        "Plan/Discovery tabs will be mostly empty.",
    )
    parser.add_argument(
        "--workload-root",
        default=None,
        help="Optional path; file paths in the analyzer IR are made relative to it. "
        "Auto-detected as longest-common-parent if omitted.",
    )
    parser.add_argument(
        "--narratives-inline-json",
        default=None,
        help="Inline JSON object with advisory narratives. Use this to inject LLM-authored snippets.",
    )
    parser.add_argument(
        "--adapter",
        default="prototype_v1",
        choices=["prototype_v1"],
        help="Output adapter to use.",
    )
    parser.add_argument(
        "--original-source-dir",
        type=Path,
        default=None,
        help="Tier-B: directory holding the customer's PRE-Phase-0.5 source "
        "(e.g. a manual checkout of `phase-0-source`). When provided, the "
        "scanner reads this dir instead of --workload-dir AND analyzer findings "
        "are rebased onto these line numbers. Omit when also passing "
        "--migration-state-json — the renderer materializes the tag itself.",
    )
    parser.add_argument(
        "--migration-state-json",
        type=Path,
        default=None,
        help="Tier-B: path to <CONVERSION>/migration_state.json. Used to "
        "(a) auto-materialize `phase-0-source` from the conversion's git repo "
        "when --original-source-dir is omitted, and (b) populate the "
        "standalone 'Phase 0.5 auto-resolved' panel from `recipe_edits`.",
    )
    parser.add_argument(
        "--language",
        default="python",
        choices=["python", "scala", "java"],
        help=(
            "Source language of the workload being assessed. "
            "Switches file-extension defaults, comment prefix, and import "
            "extraction heuristics. Default: python."
        ),
    )
    parser.add_argument("--output-html", type=Path, required=True)
    parser.add_argument(
        "--dump-ir",
        type=Path,
        default=None,
        help="Also write the merged Assessment IR JSON to this path. The IR is "
        "the stable contract; the HTML is a view of it.",
    )
    parser.add_argument(
        "--llm-resolved-edges",
        action="store_true",
        default=False,
        help="Merge llm_resolved_data_edges from AssessmentIR (written by "
        "the data_edge_resolver agent) into the data DAG before rendering. "
        "Requires --dump-ir to point to an existing AssessmentIR.json.",
    )
    args = parser.parse_args(argv)

    project_name = args.project
    if not project_name:
        project_name = _infer_project_name(args.workload_dir, args.analysis_json)

    # Try to open a Snowpark session using default resolution (env vars,
    # ~/.snowsql/config, connections.toml). When it succeeds, the scanner
    # will use it to fetch the authoritative Anaconda-channel package list
    # from ``INFORMATION_SCHEMA.PACKAGES`` and refresh the local cache used
    # by the AR Required flag. Any failure (missing creds, network issue,
    # snowpark not installed) is non-fatal — the scanner falls back to the
    # cached-or-empty path documented in ``file_info._load_anaconda_snapshot``.
    session = None
    try:
        from snowflake.snowpark import Session  # type: ignore[import-not-found]
        session = Session.builder.create()
    except Exception as e:  # noqa: BLE001
        logger.debug(
            "No Snowpark session available (%s); AR flag will use cached / "
            "empty Anaconda list.", e,
        )

    try:
        assessment = build_assessment(
            project=project_name,
            analysis_json=args.analysis_json,
            workload_dir=args.workload_dir,
            workload_root=args.workload_root,
            language=args.language,
            narratives_inline_json=args.narratives_inline_json,
            original_source_dir=args.original_source_dir,
            migration_state_json=args.migration_state_json,
            session=session,
        )
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass

    # --llm-resolved-edges: load LLM results from a pre-existing AssessmentIR.json,
    # merge into the data DAG, then continue to render.
    #
    # Baseline invariant: assessment.unresolved_data_edges /
    # unresolved_dynamic_imports stay the STATIC scan baseline through the IR
    # dump below.  The display reduction (subtracting what the LLM accounted
    # for) happens AFTER the dump, via edge_reconcile — the SAME helper the gate
    # uses.  So the dumped IR always carries the full baseline the gate
    # reconciles against, and the report shows exactly the gate's leak set.  No
    # drift possible between "gate says N/N accounted" and "report shows M rows".
    llm_ran = False
    if args.llm_resolved_edges:
        if args.dump_ir and args.dump_ir.exists():
            try:
                existing_ir = Assessment.model_validate_json(args.dump_ir.read_text())
                if existing_ir.llm_resolved_data_edges is not None:
                    assessment.llm_resolved_data_edges = existing_ir.llm_resolved_data_edges
                    llm_ran = True
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Could not read llm_resolved_data_edges from %s: %s",
                    args.dump_ir, exc,
                )
        effective_workload_dir = args.workload_dir
        if effective_workload_dir is None and args.migration_state_json is not None:
            effective_workload_dir = args.migration_state_json.parent / "Output"
        if effective_workload_dir and effective_workload_dir.is_dir():
            assessment = apply_llm_resolved_edges(assessment, effective_workload_dir)
            _backfill_file_info_target_locations(assessment)
        else:
            logger.warning(
                "--llm-resolved-edges: could not determine workload dir; "
                "skipping DAG rebuild.  Pass --workload-dir explicitly."
            )

    if args.dump_ir:
        args.dump_ir.parent.mkdir(parents=True, exist_ok=True)
        args.dump_ir.write_text(assessment.model_dump_json(indent=2))
        print(f"[render_assessment] Wrote IR -> {args.dump_ir}", file=sys.stderr)

    # Display reduction — AFTER the IR dump so the stored IR keeps the full
    # static baseline (the gate's input).  edge_reconcile is the same helper the
    # gate uses, so the "still unresolved" rows shown here are exactly the gate's
    # leak set — they cannot drift.
    if llm_ran and assessment.llm_resolved_data_edges is not None:
        llm = assessment.llm_resolved_data_edges
        assessment.unresolved_data_edges = edge_reconcile.remaining_data_edges(
            assessment.unresolved_data_edges, llm
        )
        assessment.unresolved_dynamic_imports = edge_reconcile.remaining_dynamic_imports(
            assessment.unresolved_dynamic_imports, llm
        )

    adapter = prototype_v1
    out_path = adapter.render_to_file(assessment, args.output_html)
    print(f"[render_assessment] Wrote HTML -> {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
