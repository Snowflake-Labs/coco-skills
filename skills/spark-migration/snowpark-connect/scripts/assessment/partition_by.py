"""Partition-by strategy engine for the migration-plan section.

Computes file→group assignments for each strategy and returns a single
JSON-serializable payload that the HTML report's client-side JavaScript
renders into an interactive table.

Design goals
------------
* Works on **static IR only** by default — every strategy degrades
  gracefully when a data source is absent (empty data DAG, no file_info,
  no import graph, etc.).
* When ``llm_resolved_data_edges`` is present on the IR, strategies that
  depend on data flow automatically use the enriched graph, and an
  additional "By LLM Pipeline" strategy becomes available.
* The payload is intentionally flat and JSON-safe — no Pydantic models,
  no datetime objects, just dicts/lists/strings so the caller can pass it
  straight to ``json.dumps``.

Payload shape
-------------
::

    {
      "strategies": [
        {
          "id": "migration_wave",
          "label": "Migration Wave",
          "description": "...",
          "available": true,
          "requires_llm": false,
          "default": true,
          "groups": [
            {"label": "Wave 1", "count": 5, "badge": "blue"},
            ...
          ]
        }, ...
      ],
      "partition_map": {
        "migration_wave": {"Wave 1": ["a.py", "b.py"], ...},
        ...
      },
      "file_rows": {
        "a.py": {
          "name": "a.py", "path": "src/a.py",
          "technology": "Python",
          "readiness": "High", "readiness_label": "Ready",
          "source_systems": ["S3"],
          "target_types": ["Snowflake Table"],
          "target_location": "DB.SCH.TBL",
          "eai_required": "No",
          "ar_required": "No",
          "lines": 250,
          "wave": "Wave 2",
          "blast_radius": 3,
          "in_degree": 2
        }, ...
      }
    }
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


# ---------------------------------------------------------------------------
# Readiness helpers
# ---------------------------------------------------------------------------

_READINESS_LABELS = {
    "High": "Ready",
    "Medium": "Light Refactor",
    "Low": "Active Refactor",
}

_READINESS_ORDER = {"High": 0, "Medium": 1, "Low": 2}  # ascending → easiest first


def _readiness_label(level: str) -> str:
    return _READINESS_LABELS.get(level, level)


# ---------------------------------------------------------------------------
# Step 1 — build the per-file row dict (joins files, file_info, waves, graph)
# ---------------------------------------------------------------------------


def _build_file_rows(assessment: Any) -> dict[str, dict[str, Any]]:
    """Join all per-file data from the Assessment into a flat dict.

    Key is the canonical file path (``FileCompatibilityRow.path``).
    All fields are guaranteed to be present (with sensible defaults) so
    the JavaScript table renderer never needs to guard against missing keys.
    """
    rows: dict[str, dict[str, Any]] = {}

    # Seed from FileCompatibilityRow (always present when any scan ran)
    for f in getattr(assessment, "files", []) or []:
        path = getattr(f, "path", None) or ""
        if not path:
            continue
        rows[path] = {
            "name": getattr(f, "name", path.split("/")[-1]),
            "path": path,
            "technology": getattr(f, "technology", "Unknown") or "Unknown",
            "lines": getattr(f, "lines", 0) or 0,
            "spark_usages": getattr(f, "spark_usages", 0) or 0,
            "issues": getattr(f, "issues", 0) or 0,
            "readiness": getattr(f, "status", "High") or "High",
            "readiness_label": _readiness_label(getattr(f, "status", "High") or "High"),
            "source_systems": ["N/A"],
            "target_types": ["N/A"],
            "target_location": "",
            "eai_required": "No",
            "ar_required": "No",
            "eai_packages": [],
            "ar_packages": [],
            "wave": None,
            "blast_radius": 0,
            "in_degree": 0,
        }

    # Overlay FileInfoRow (codebase scanner output — richer I/O metadata)
    for fi in getattr(assessment, "file_info", []) or []:
        path = getattr(fi, "path", None) or ""
        if not path:
            continue
        if path not in rows:
            rows[path] = {
                "name": getattr(fi, "name", path.split("/")[-1]),
                "path": path,
                "technology": "Unknown",
                "lines": getattr(fi, "lines", 0) or 0,
                "spark_usages": 0,
                "issues": 0,
                "readiness": "High",
                "readiness_label": _readiness_label("High"),
                "source_systems": ["N/A"],
                "target_types": ["N/A"],
                "target_location": "",
                "eai_required": "No",
                "ar_required": "No",
                "eai_packages": [],
                "ar_packages": [],
                "wave": None,
                "blast_radius": 0,
                "in_degree": 0,
            }
        r = rows[path]
        # source_system and target_type are list[str] on the IR model
        src = getattr(fi, "source_system", None) or ["N/A"]
        if isinstance(src, str):
            src = [src] if src else ["N/A"]
        tgt = getattr(fi, "target_type", None) or ["N/A"]
        if isinstance(tgt, str):
            tgt = [tgt] if tgt else ["N/A"]
        r["source_systems"] = src
        r["target_types"] = tgt
        r["target_location"] = getattr(fi, "target_location", "") or ""
        r["eai_required"] = getattr(fi, "eai_required", "No") or "No"
        r["ar_required"] = getattr(fi, "ar_required", "No") or "No"
        r["eai_packages"] = list(getattr(fi, "eai_packages", []) or [])
        r["ar_packages"] = list(getattr(fi, "ar_packages", []) or [])
        # Take the larger lines count (scanner vs analyzer may differ slightly)
        fi_lines = getattr(fi, "lines", 0) or 0
        r["lines"] = max(r.get("lines", 0), fi_lines)

    # Overlay migration wave membership
    for wave in getattr(assessment, "migration_waves", []) or []:
        wave_name = getattr(wave, "name", "") or ""
        for wf in getattr(wave, "files", []) or []:
            wf_path = getattr(wf, "path", None) or ""
            if wf_path and wf_path in rows:
                rows[wf_path]["wave"] = wave_name

    # Overlay dependency-graph node metrics (blast_radius, in_degree)
    dep_graph = getattr(assessment, "dependency_graph", None)
    if dep_graph:
        for node in getattr(dep_graph, "nodes", []) or []:
            nid = getattr(node, "id", None) or ""
            if nid and nid in rows:
                rows[nid]["blast_radius"] = getattr(node, "blast_radius", 0) or 0
                rows[nid]["in_degree"] = getattr(node, "in_degree", 0) or 0

    return rows


# ---------------------------------------------------------------------------
# Step 2 — individual partition strategies
# ---------------------------------------------------------------------------




def _partition_by_source_system(
    assessment: Any, file_rows: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Group files by the data source(s) they read from.

    Multi-source files appear under EACH source system they read from.
    Naming:
    - Others: the literal source label (S3, JDBC, Kafka, Snowflake, etc.)
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for path, row in file_rows.items():
        systems = row.get("source_systems") or ["N/A"]
        for s in systems:
            label = (s or "N/A").strip() or "N/A"
            groups[label].append(path)

    # Sort files within each group by readiness asc (Active Refactor first = higher urgency)
    return {k: sorted(v, key=lambda p: _READINESS_ORDER.get(file_rows[p]["readiness"], 1)) for k, v in sorted(groups.items())}


def _partition_by_target_type(
    assessment: Any, file_rows: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Group files by their write target type. Multi-target files appear under each type."""
    groups: dict[str, list[str]] = defaultdict(list)
    for path, row in file_rows.items():
        tgts = row.get("target_types") or ["N/A"]
        for t in tgts:
            label = (t or "N/A").strip() or "N/A"
            groups[label].append(path)

    return {k: sorted(v, key=lambda p: _READINESS_ORDER.get(file_rows[p]["readiness"], 1)) for k, v in sorted(groups.items())}


def _partition_by_readiness(
    assessment: Any, file_rows: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Group files by migration readiness — easiest first (Ready → Light → Active Refactor)."""
    _ORDER = [
        ("High",   "Ready"),
        ("Medium", "Light Refactor"),
        ("Low",    "Active Refactor"),
    ]
    groups: dict[str, list[str]] = {}
    for level, label in _ORDER:
        paths = sorted(
            [p for p, r in file_rows.items() if r["readiness"] == level],
            key=lambda p: file_rows[p]["lines"],  # smaller files first within tier
        )
        if paths:
            groups[label] = paths
    return groups


def _partition_by_technology(
    assessment: Any, file_rows: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Group files by programming language/technology."""
    groups: dict[str, list[str]] = defaultdict(list)
    for path, row in file_rows.items():
        tech = (row.get("technology") or "Unknown").strip()
        groups[tech].append(path)

    return {k: sorted(v, key=lambda p: _READINESS_ORDER.get(file_rows[p]["readiness"], 1)) for k, v in sorted(groups.items())}


def _partition_by_infra_need(
    assessment: Any, file_rows: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Group files by the Snowflake infrastructure they require.

    Rules (in priority order):
    - EAI + AR Required → hardest to provision, needs network policy + repo
    - EAI Required Only → needs external network access integration
    - AR Required Only  → needs Anaconda artifact repository staging
    - Standard          → no special infra, lowest deployment friction
    """
    _ORDER = [
        ("EAI + AR Required", lambda r: r["eai_required"] != "No" and r["ar_required"] == "Yes"),
        ("EAI Required Only", lambda r: r["eai_required"] != "No" and r["ar_required"] != "Yes"),
        ("AR Required Only",  lambda r: r["eai_required"] == "No" and r["ar_required"] == "Yes"),
        ("Standard", lambda r: r["eai_required"] == "No" and r["ar_required"] != "Yes"),
    ]
    groups: dict[str, list[str]] = {}
    for label, pred in _ORDER:
        paths = sorted(
            [p for p, r in file_rows.items() if pred(r)],
            key=lambda p: _READINESS_ORDER.get(file_rows[p]["readiness"], 1),
        )
        if paths:
            groups[label] = paths
    return groups


def _partition_by_blast_radius(
    assessment: Any, file_rows: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Group files by blast radius using data-driven percentile thresholds.

    Thresholds are computed from the actual distribution so the tiers are
    meaningful regardless of workload size or blast-radius scale.

    - Leaf     : blast_radius = 0 (no transitive dependents — safe to migrate any time)
    - Shared   : above zero up to and including the lower median of non-zero values
    - Foundational : above the lower median (high ripple risk — migrate first, carefully)

    The lower median is used as the split so roughly half the non-zero files
    land in each tier.  When all files have blast_radius = 0, a single "Leaf"
    group is returned.
    """
    dep_graph = getattr(assessment, "dependency_graph", None)
    if not dep_graph or not getattr(dep_graph, "nodes", None):
        return {}  # Signal: not available

    graphed: set[str] = {getattr(n, "id", "") for n in dep_graph.nodes}
    in_graph: dict[str, int] = {
        p: file_rows[p]["blast_radius"] for p in file_rows if p in graphed
    }
    not_graphed = sorted(p for p in file_rows if p not in graphed)

    if not in_graph:
        result: dict[str, list[str]] = {}
        if not_graphed:
            result["Not in Import Graph"] = not_graphed
        return result

    nonzero_paths = sorted((p for p in in_graph if in_graph[p] > 0), key=lambda p: -in_graph[p])
    zero_paths    = sorted(p for p in in_graph if in_graph[p] == 0)
    nz_vals = sorted(in_graph[p] for p in nonzero_paths)

    groups: dict[str, list[str]] = {}

    if not nz_vals:
        # All graphed files have blast_radius = 0 — single tier
        groups["Leaf — Blast Radius = 0"] = zero_paths
    else:
        n = len(nz_vals)
        # 75th-percentile split: only the top quartile of non-zero files → Foundational.
        # This means blast_radius must be a genuine outlier to land here, not just
        # "above the median of a tight low-value distribution".
        split_val = nz_vals[(3 * (n - 1)) // 4]

        foundational = [p for p in nonzero_paths if in_graph[p] > split_val]
        shared       = [p for p in nonzero_paths if in_graph[p] <= split_val]

        if foundational:
            groups[f"Foundational — Blast Radius > {split_val}"] = foundational
        if shared:
            groups[f"Shared — Blast Radius 1–{split_val}"] = shared
        if zero_paths:
            groups["Leaf — Blast Radius = 0"] = zero_paths

    if not_graphed:
        groups["Not in Import Graph"] = not_graphed

    return groups




def _build_data_adjacency(
    data_dep_graph: Any,
    llm_edges: list[Any] | None = None,
    llm_orchestration: list[Any] | None = None,
) -> tuple[dict[str, set[str]], set[str]]:
    """Build undirected adjacency from data dependency graph + optional LLM edges.

    Returns (undirected_adj, all_node_ids).
    """
    adj: dict[str, set[str]] = defaultdict(set)
    node_ids: set[str] = set()

    # Static data DAG nodes
    if data_dep_graph:
        for node in getattr(data_dep_graph, "nodes", []) or []:
            nid = getattr(node, "id", "") or ""
            if nid and not nid.startswith("ext:"):
                node_ids.add(nid)
        for edge in getattr(data_dep_graph, "edges", []) or []:
            src = getattr(edge, "source", "") or ""
            tgt = getattr(edge, "target", "") or ""
            # Skip framework/cluster-summary edges
            kind = getattr(edge, "kind", "data") or "data"
            if kind in ("framework",):
                continue
            if src and tgt and not src.startswith("ext:") and not tgt.startswith("ext:"):
                if src in node_ids or tgt in node_ids:
                    adj[src].add(tgt)
                    adj[tgt].add(src)

    # LLM-resolved data edges (newly_discovered are extra file→file edges)
    if llm_edges:
        for e in llm_edges:
            file_path = getattr(e, "file", "") or ""
            if file_path:
                node_ids.add(file_path)

    # LLM orchestration edges
    if llm_orchestration:
        for oe in llm_orchestration:
            frm = getattr(oe, "from_file", "") or ""
            to = getattr(oe, "to_file", "") or ""
            if frm and to:
                node_ids.update([frm, to])
                adj[frm].add(to)
                adj[to].add(frm)

    return adj, node_ids


def _connected_components(adj: dict[str, set[str]], all_nodes: set[str]) -> list[set[str]]:
    """Find connected components using BFS."""
    visited: set[str] = set()
    components: list[set[str]] = []
    for node in all_nodes:
        if node in visited:
            continue
        component: set[str] = set()
        queue = [node]
        while queue:
            cur = queue.pop()
            if cur in visited:
                continue
            visited.add(cur)
            component.add(cur)
            for nbr in adj.get(cur, set()):
                if nbr not in visited:
                    queue.append(nbr)
        components.append(component)
    return sorted(components, key=len, reverse=True)


def _partition_by_data_pipeline(
    assessment: Any, file_rows: dict[str, dict[str, Any]],
    use_llm: bool = False,
) -> dict[str, list[str]] | None:
    """Group files by connected component in the data dependency graph.

    Returns None when no data graph at all (signals "not computable").
    Returns {"No Data Flow Detected": [...all files...]} when the graph
    exists but has no edges (empty DAG from AST coverage gaps).

    With use_llm=True, also folds in orchestration edges from the LLM
    resolver, connecting pipeline stages that share no shared dataset.
    """
    data_dep_graph = getattr(assessment, "data_dependency_graph", None)
    llm_data = getattr(assessment, "llm_resolved_data_edges", None)
    llm_edges: list[Any] | None = None
    llm_orchestration: list[Any] | None = None

    if use_llm and llm_data:
        llm_edges = getattr(llm_data, "edges", None)
        llm_orchestration = getattr(llm_data, "orchestration_edges", None)

    if data_dep_graph is None and not (llm_edges or llm_orchestration):
        return None  # No data at all — strategy not computable

    adj, _ = _build_data_adjacency(data_dep_graph, llm_edges, llm_orchestration)

    # All workload files are nodes. Data edges connect some of them.
    # Files with no data edges become singleton components — each is its own
    # independent migration island (nothing in the graph constrains when it moves).
    # Running connected-components over the full known_paths set is the correct
    # primitive: files sharing data dependencies land in the same component and
    # must migrate as a unit; everything else is independent.
    known_paths = set(file_rows.keys())
    components = sorted(
        _connected_components(adj, known_paths),
        key=len,
        reverse=True,
    )

    groups: dict[str, list[str]] = {}
    prefix = "LLM Pipeline" if use_llm else "Pipeline"

    pipeline_num = 0
    standalone: list[str] = []

    for comp in components:
        comp_paths = sorted(comp)
        if len(comp_paths) >= 2:
            # True pipeline island — files share data connections and must
            # migrate together (or in dependency order within the component).
            pipeline_num += 1
            groups[f"{prefix} {pipeline_num}"] = comp_paths
        else:
            # No data connections to any other file — independent migration unit.
            standalone.extend(comp_paths)

    if standalone:
        groups["Standalone Files"] = sorted(standalone)

    return groups


def _partition_by_snowflake_schema(
    assessment: Any, file_rows: dict[str, dict[str, Any]],
) -> dict[str, list[str]] | None:
    """Group files that write to Snowflake by target schema.

    Files writing to Snowflake Table/Stage: grouped by database.schema (2-level).
    If there are >8 unique schemas, rolls up to database only.
    Files with no Snowflake target → "Non-Snowflake or Unknown Output".

    Returns None when no file has a non-empty target_location (no Snowflake
    target info available at all).
    """
    _SF_TARGET_TYPES = {"snowflake table", "snowflake stage"}

    def _is_sf_target(row: dict[str, Any]) -> bool:
        return any(t.lower() in _SF_TARGET_TYPES for t in (row.get("target_types") or []))

    sf_files = {p: r for p, r in file_rows.items() if _is_sf_target(r) and r.get("target_location")}

    if not sf_files:
        return None  # No Snowflake target data at all

    def _schema_key(loc: str) -> str:
        """Extract 'DB.SCHEMA' from 'DB.SCHEMA.TABLE' or 'CATALOG.DB.SCHEMA.TABLE'."""
        parts = loc.replace('"', "").split(".")
        # Handle 4-part: catalog.db.schema.table → use db.schema
        # Handle 3-part: db.schema.table → use db.schema
        # Handle 2-part: db.schema → use as-is
        if len(parts) >= 3:
            return ".".join(parts[-3:-1])  # db.schema
        if len(parts) == 2:
            return loc
        return parts[0]

    schema_groups: dict[str, list[str]] = defaultdict(list)
    for path, row in sf_files.items():
        key = _schema_key(row["target_location"])
        schema_groups[key or "Unknown Schema"].append(path)

    # Roll up to database if too many schemas
    if len(schema_groups) > 8:
        db_groups: dict[str, list[str]] = defaultdict(list)
        for schema_key, paths in schema_groups.items():
            db = schema_key.split(".")[0] if "." in schema_key else schema_key
            db_groups[db].extend(paths)
        result: dict[str, list[str]] = {k: sorted(set(v)) for k, v in sorted(db_groups.items())}
    else:
        result = {k: sorted(v) for k, v in sorted(schema_groups.items())}

    # Non-Snowflake or no-location files
    non_sf = sorted(p for p in file_rows if p not in sf_files)
    if non_sf:
        result["Non-Snowflake or Unknown Output"] = non_sf

    return result


def _partition_by_llm_pipeline(
    assessment: Any, file_rows: dict[str, dict[str, Any]],
) -> dict[str, list[str]] | None:
    """Group by LLM-discovered data + orchestration pipeline.

    Only available when llm_resolved_data_edges is present on the IR.
    Uses both data edges and orchestration edges from the LLM output to
    build richer connected components than the static AST can produce.
    """
    llm_data = getattr(assessment, "llm_resolved_data_edges", None)
    if not llm_data:
        return None  # Requires LLM enrichment

    return _partition_by_data_pipeline(assessment, file_rows, use_llm=True)


# ---------------------------------------------------------------------------
# Step 3 — assemble the full payload
# ---------------------------------------------------------------------------

_GROUP_BADGE_COLORS = {
    "Ready": "green",
    "Light Refactor": "yellow",
    "Active Refactor": "orange",
    "EAI + AR Required": "orange",
    "EAI Required Only": "yellow",
    "AR Required Only": "blue",
    "Standard": "green",
    "No Data Lineage Detected": "gray",
    "Not in Import Graph": "gray",
}


def _badge_for(label: str) -> str:
    if label in _GROUP_BADGE_COLORS:
        return _GROUP_BADGE_COLORS[label]
    low = label.lower()
    if "quick win" in low or "ready" in low or "leaf" in low:
        return "green"
    if "refactor" in low or "required" in low or "foundational" in low:
        return "orange"
    if "wave" in low or "pipeline" in low or "depth 0" in low:
        return "blue"
    if "utility" in low or "n/a" in low or "unclassified" in low or "not in" in low:
        return "gray"
    return "blue"


def build_partition_table_data(assessment: Any) -> dict[str, Any]:
    """Compute all partition strategies from the Assessment IR.

    Returns a JSON-serializable dict consumed by the HTML report's
    JavaScript table renderer.
    """
    file_rows = _build_file_rows(assessment)
    if not file_rows:
        return {"strategies": [], "partition_map": {}, "file_rows": {}}

    llm_available = getattr(assessment, "llm_resolved_data_edges", None) is not None
    dep_graph_available = (
        getattr(assessment, "dependency_graph", None) is not None
        and bool(getattr(assessment.dependency_graph, "nodes", None))
    )
    data_graph_available = (
        getattr(assessment, "data_dependency_graph", None) is not None
        or llm_available
    )
    file_info_available = bool(getattr(assessment, "file_info", None))

    # Strategy registry — order determines display order in the dropdown
    strategy_defs = [
        {
            "id": "readiness",
            "label": "By Readiness",
            "description": "Segments files by conversion effort: Ready, Light Refactor, and Active Refactor. Use this to size sprints and match work to engineer skill level.",
            "default": True,
            "requires_llm": False,
            "badge_colors": False,
            "fn": _partition_by_readiness,
            "available_check": lambda: bool(file_rows),
        },
        {
            "id": "data_pipeline",
            "label": "By Data Pipeline",
            "description": "Segments the workload by data dependency. Files sharing data connections are grouped into pipelines that must migrate as a unit. Files with no data connections to any other file are independent migration units collected in Standalone Files.",
            "default": False,
            "requires_llm": False,
            "badge_colors": False,
            "fn": lambda a, fr: _partition_by_data_pipeline(a, fr, use_llm=False),
            "available_check": lambda: data_graph_available,
        },
        {
            "id": "blast_radius",
            "label": "By Blast Radius",
            "description": "Ranks files by transitive import impact. Thresholds are computed from this workload's distribution: Foundational (top 25% by blast radius), Shared (remaining non-zero), and Leaf (nothing depends on them).",
            "default": False,
            "requires_llm": False,
            "badge_colors": False,
            "fn": _partition_by_blast_radius,
            "available_check": lambda: dep_graph_available,
        },
        {
            "id": "technology",
            "label": "By Technology",
            "description": "Groups files by language. Use this to assign work to language-specialist teams or sequence Scala vs. Python sprints.",
            "default": False,
            "requires_llm": False,
            "badge_colors": False,
            "fn": _partition_by_technology,
            "available_check": lambda: bool(file_rows),
        },
        {
            "id": "source_system",
            "label": "By Source System",
            "description": "Groups files by the data sources they read from. Use this to plan External Access Integration provisioning per source system.",
            "default": False,
            "requires_llm": False,
            "badge_colors": False,
            "fn": _partition_by_source_system,
            "available_check": lambda: file_info_available,
        },
        {
            "id": "target_type",
            "label": "By Target Type",
            "description": "Groups files by write destination type. Use this to coordinate stage creation, streaming connector setup, and IAM configuration.",
            "default": False,
            "requires_llm": False,
            "badge_colors": False,
            "fn": _partition_by_target_type,
            "available_check": lambda: file_info_available,
        },
        {
            "id": "infra_need",
            "label": "By Provisioning Requirement",
            "description": "Groups files by required Snowflake infrastructure: EAI+AR, EAI only, AR only, or Standard. Use this to sequence infrastructure provisioning in parallel with code migration.",
            "default": False,
            "requires_llm": False,
            "badge_colors": False,
            "fn": _partition_by_infra_need,
            "available_check": lambda: file_info_available,
        },
        {
            "id": "snowflake_schema",
            "label": "By Target Snowflake Schema",
            "description": "Groups files by the Snowflake database.schema they write to. Use this to assign migration ownership by data domain.",
            "default": False,
            "requires_llm": False,
            "badge_colors": False,
            "fn": _partition_by_snowflake_schema,
            "available_check": lambda: file_info_available,
        },
        {
            "id": "llm_pipeline",
            "label": "By LLM-Enriched Pipeline",
            "description": "Groups files into pipelines using LLM-resolved data and orchestration edges. Produces richer results than static analysis by capturing implicit dependencies. Requires LLM data edge resolution.",
            "default": False,
            "requires_llm": True,
            "badge_colors": False,
            "fn": _partition_by_llm_pipeline,
            "available_check": lambda: llm_available,
        },
    ]

    partition_map: dict[str, dict[str, list[str]]] = {}
    strategies_out: list[dict[str, Any]] = []

    for sdef in strategy_defs:
        available = sdef["available_check"]()
        sid = sdef["id"]

        groups: dict[str, list[str]] = {}
        if available:
            try:
                result = sdef["fn"](assessment, file_rows)
                if result is None:
                    available = False
                else:
                    groups = result
            except Exception:  # noqa: BLE001
                available = False

        partition_map[sid] = groups

        strategies_out.append({
            "id": sid,
            "label": sdef["label"],
            "description": sdef["description"],
            "available": available,
            "requires_llm": sdef["requires_llm"],
            "default": sdef.get("default", False),
            "badge_colors": sdef.get("badge_colors", False),
            "groups": [
                {
                    "label": g,
                    "count": len(paths),
                    "badge": _badge_for(g),
                }
                for g, paths in groups.items()
            ],
        })

    return {
        "strategies": strategies_out,
        "partition_map": partition_map,
        "file_rows": file_rows,
    }
