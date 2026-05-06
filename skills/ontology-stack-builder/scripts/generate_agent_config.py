# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pyyaml>=6.0",
# ]
# ///
"""
DEPRECATED: This script is no longer used by the ontology-stack-builder workflow.

Agent creation is now handled by the native Cortex Code `cortex-agent` skill,
which uses CREATE OR REPLACE AGENT ... FROM SPECIFICATION $spec$...$spec$ DDL
with cortex_analyst_text_to_sql tools and proper tool_resources configuration.

SPCS graph analytics scaffolding has been moved to generate_spcs_scaffolding.py.

This file is retained for reference only. Do not invoke it from the skill workflow.
See SKILL.md Phase 6 for the current workflow.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml


# --- Intent-routing descriptions for each model type ---
MODEL_DESCRIPTIONS = {
    "kg": (
        "Use for CONCRETE entity queries: specific lookups, named entity searches, "
        "aggregations on physical data, direct record retrieval. "
        "Best for: 'Who scored most goals?', 'Which customer spent most?', "
        "'Show details for X', entity-specific questions."
    ),
    "ontology": (
        "Use for ABSTRACT cross-type queries: entity unification, type hierarchy traversal, "
        "polymorphic queries across entity types, 'what types of X exist?'. "
        "Best for: 'What types of entities exist?', 'Show all people regardless of role', "
        "'How are different entity types connected?', cross-entity reasoning."
    ),
    "metadata": (
        "Use for GOVERNANCE and INTROSPECTION queries: ontology structure, class definitions, "
        "relationship types, mapping catalog, schema documentation. "
        "Best for: 'What classes are defined?', 'How is the ontology structured?', "
        "'What tables map to which classes?', data catalog questions."
    ),
}

# Tool name mapping
MODEL_TOOL_NAMES = {
    "kg": "kg_analyst",
    "ontology": "ontology_analyst",
    "metadata": "metadata_analyst",
}


def detect_model_type(model_path: Path) -> str | None:
    """Detect model type from filename."""
    name = model_path.stem.lower()
    if "kg" in name and "ontology" not in name:
        return "kg"
    elif "ontology" in name:
        return "ontology"
    elif "metadata" in name or "governance" in name:
        return "metadata"
    return None


def build_agent_tools(
    model_files: list[Path],
    stage_name: str,
) -> list[dict]:
    """Build cortex_analyst_tool entries for each semantic model."""
    tools = []

    for mf in model_files:
        model_type = detect_model_type(mf)
        if model_type is None:
            # Fallback: use filename as tool name
            tool_name = mf.stem.replace("-", "_").replace(" ", "_").lower()
            description = f"Semantic model from {mf.name}. Use for queries against this model."
        else:
            tool_name = MODEL_TOOL_NAMES[model_type]
            description = MODEL_DESCRIPTIONS[model_type]

        tools.append({
            "tool_spec": {
                "type": "cortex_analyst_tool",
                "name": tool_name,
                "description": description,
                "semantic_model_file": f"{stage_name}/{mf.name}",
            }
        })

    return tools


def build_graph_tools(database: str, schema: str) -> list[dict]:
    """Build graph analytics tool entries (requires SPCS service)."""
    service_name = f"{database}.{schema}.GRAPH_ANALYTICS_SERVICE"

    return [
        {
            "tool_spec": {
                "type": "function",
                "name": "graph_centrality",
                "description": (
                    "Calculate centrality metrics for entities in the knowledge graph. "
                    "Returns top N most central/important entities. Use for: "
                    "'Who is most connected?', 'Most influential entity', 'Key players'."
                ),
                "function": {
                    "name": f"{database}.{schema}.GRAPH_CENTRALITY",
                    "description": "Compute centrality (degree, betweenness, or PageRank) for graph nodes",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "metric": {
                                "type": "string",
                                "enum": ["degree", "betweenness", "pagerank"],
                                "description": "Centrality metric to compute",
                            },
                            "entity_type": {
                                "type": "string",
                                "description": "Optional: filter to specific entity type",
                            },
                            "top_n": {
                                "type": "integer",
                                "description": "Number of top results to return (default 10)",
                            },
                        },
                        "required": ["metric"],
                    },
                },
            }
        },
        {
            "tool_spec": {
                "type": "function",
                "name": "graph_community_detection",
                "description": (
                    "Detect communities/clusters in the knowledge graph using Louvain algorithm. "
                    "Returns community assignments and sizes. Use for: "
                    "'What groups exist?', 'Find clusters', 'Community structure'."
                ),
                "function": {
                    "name": f"{database}.{schema}.GRAPH_COMMUNITY_DETECTION",
                    "description": "Detect communities using Louvain method",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "resolution": {
                                "type": "number",
                                "description": "Resolution parameter (higher = more communities, default 1.0)",
                            },
                        },
                    },
                },
            }
        },
        {
            "tool_spec": {
                "type": "function",
                "name": "graph_shortest_path",
                "description": (
                    "Find the shortest path between two entities in the knowledge graph. "
                    "Returns the path with intermediate nodes and relationship types. Use for: "
                    "'How is A connected to B?', 'Path between X and Y', 'Degrees of separation'."
                ),
                "function": {
                    "name": f"{database}.{schema}.GRAPH_SHORTEST_PATH",
                    "description": "Find shortest path between two entities",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "source_id": {
                                "type": "string",
                                "description": "Source entity ID",
                            },
                            "target_id": {
                                "type": "string",
                                "description": "Target entity ID",
                            },
                        },
                        "required": ["source_id", "target_id"],
                    },
                },
            }
        },
    ]


def build_instructions(model_types: list[str], include_graph: bool) -> str:
    """Build agent orchestration instructions."""
    lines = [
        "You are an intelligent data assistant with access to multiple semantic models organized by intent.",
        "",
        "MODEL SELECTION STRATEGY:",
    ]

    if "kg" in model_types:
        lines.append("1. CONCRETE queries (named entities, specific lookups, aggregations) → kg_analyst")
    if "ontology" in model_types:
        lines.append(f"{'2' if 'kg' in model_types else '1'}. ABSTRACT queries (cross-type, hierarchy, unification, 'what types') → ontology_analyst")
    if "metadata" in model_types:
        idx = len([t for t in ["kg", "ontology"] if t in model_types]) + 1
        lines.append(f"{idx}. GOVERNANCE queries (schema structure, class definitions, metadata) → metadata_analyst")

    if include_graph:
        lines.extend([
            "",
            "GRAPH ANALYTICS:",
            "- For importance/influence questions → graph_centrality",
            "- For grouping/clustering questions → graph_community_detection",
            "- For connection/path questions → graph_shortest_path",
        ])

    lines.extend([
        "",
        "RULES:",
        "- Select the MOST SPECIFIC model that can answer the question",
        "- If unsure, start with " + ("kg_analyst" if "kg" in model_types else "ontology_analyst") + " (most common)",
        "- For multi-part questions, you may call multiple tools",
        "- Always explain which model you chose and why",
        "- Present results in clear tables or lists",
    ])

    return "\n".join(lines)


def generate_spcs_graph_service(database: str, schema: str) -> str:
    """Generate the Python graph analytics service for SPCS."""
    return '''"""
Graph Analytics Service for Snowpark Container Services.

Provides NetworkX-based graph analytics exposed via Flask endpoints.
Reads KG_NODE and KG_EDGE tables from Snowflake to build the graph.
"""

import os
import json
import logging
from flask import Flask, request, jsonify

import networkx as nx
from snowflake.snowpark import Session
from community import community_louvain

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global graph cache
_graph: nx.Graph | None = None
_node_attrs: dict = {}


def get_session() -> Session:
    """Create Snowpark session from SPCS environment."""
    return Session.builder.configs({
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "host": os.environ["SNOWFLAKE_HOST"],
        "token": open("/snowflake/session/token").read().strip(),
        "authenticator": "oauth",
        "database": "''' + database + '''",
        "schema": "''' + schema + '''",
    }).create()


def load_graph() -> nx.Graph:
    """Load KG_NODE and KG_EDGE into a NetworkX graph."""
    global _graph, _node_attrs
    if _graph is not None:
        return _graph

    logger.info("Loading graph from KG_NODE/KG_EDGE...")
    session = get_session()

    nodes_df = session.sql("SELECT NODE_ID, NODE_TYPE, NAME FROM KG_NODE").collect()
    edges_df = session.sql("SELECT SRC_ID, DST_ID, EDGE_TYPE, WEIGHT FROM KG_EDGE WHERE EFFECTIVE_END IS NULL").collect()

    G = nx.Graph()
    for row in nodes_df:
        G.add_node(row["NODE_ID"], node_type=row["NODE_TYPE"], name=row["NAME"])
        _node_attrs[row["NODE_ID"]] = {"type": row["NODE_TYPE"], "name": row["NAME"]}

    for row in edges_df:
        G.add_edge(row["SRC_ID"], row["DST_ID"],
                    edge_type=row["EDGE_TYPE"],
                    weight=float(row["WEIGHT"] or 1.0))

    _graph = G
    logger.info(f"Graph loaded: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    session.close()
    return G


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"})


@app.route("/centrality", methods=["POST"])
def centrality():
    """Compute centrality metrics."""
    data = request.json
    metric = data.get("metric", "degree")
    entity_type = data.get("entity_type")
    top_n = data.get("top_n", 10)

    G = load_graph()

    if entity_type:
        nodes = [n for n, d in G.nodes(data=True) if d.get("node_type") == entity_type]
        subgraph = G.subgraph(nodes)
    else:
        subgraph = G

    if metric == "degree":
        scores = nx.degree_centrality(subgraph)
    elif metric == "betweenness":
        scores = nx.betweenness_centrality(subgraph, k=min(100, len(subgraph)))
    elif metric == "pagerank":
        scores = nx.pagerank(subgraph)
    else:
        return jsonify({"error": f"Unknown metric: {metric}"}), 400

    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
    results = []
    for node_id, score in top:
        attrs = _node_attrs.get(node_id, {})
        results.append({
            "node_id": node_id,
            "name": attrs.get("name", ""),
            "type": attrs.get("type", ""),
            "score": round(score, 6),
        })

    return jsonify({"metric": metric, "results": results})


@app.route("/community", methods=["POST"])
def community_detection():
    """Detect communities using Louvain."""
    data = request.json or {}
    resolution = data.get("resolution", 1.0)

    G = load_graph()
    partition = community_louvain.best_partition(G, resolution=resolution)

    communities = {}
    for node_id, comm_id in partition.items():
        if comm_id not in communities:
            communities[comm_id] = []
        attrs = _node_attrs.get(node_id, {})
        communities[comm_id].append({
            "node_id": node_id,
            "name": attrs.get("name", ""),
            "type": attrs.get("type", ""),
        })

    summary = [{"community_id": cid, "size": len(members), "sample": members[:5]}
               for cid, members in sorted(communities.items(), key=lambda x: -len(x[1]))]

    return jsonify({"num_communities": len(communities), "communities": summary[:20]})


@app.route("/shortest_path", methods=["POST"])
def shortest_path():
    """Find shortest path between two nodes."""
    data = request.json
    source = data.get("source_id")
    target = data.get("target_id")

    if not source or not target:
        return jsonify({"error": "source_id and target_id are required"}), 400

    G = load_graph()

    if source not in G:
        return jsonify({"error": f"Source node {source} not found"}), 404
    if target not in G:
        return jsonify({"error": f"Target node {target} not found"}), 404

    try:
        path = nx.shortest_path(G, source, target)
    except nx.NetworkXNoPath:
        return jsonify({"error": "No path exists between these nodes", "path": []})

    path_details = []
    for i, node_id in enumerate(path):
        attrs = _node_attrs.get(node_id, {})
        entry = {"step": i, "node_id": node_id, "name": attrs.get("name", ""), "type": attrs.get("type", "")}
        if i < len(path) - 1:
            edge_data = G.edges[node_id, path[i + 1]]
            entry["edge_type"] = edge_data.get("edge_type", "")
        path_details.append(entry)

    return jsonify({"length": len(path) - 1, "path": path_details})


@app.route("/reload", methods=["POST"])
def reload_graph():
    """Force reload the graph from Snowflake."""
    global _graph, _node_attrs
    _graph = None
    _node_attrs = {}
    load_graph()
    return jsonify({"status": "reloaded", "nodes": _graph.number_of_nodes(), "edges": _graph.number_of_edges()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
'''


def generate_spcs_setup_sql(database: str, schema: str) -> str:
    """Generate SPCS setup SQL for graph analytics."""
    return f"""-- =============================================================================
-- SPCS Graph Analytics Setup
-- Generated by ontology-stack-builder
-- =============================================================================

-- 1. Create compute pool (adjust size as needed)
CREATE COMPUTE POOL IF NOT EXISTS {schema}_GRAPH_POOL
  MIN_NODES = 1
  MAX_NODES = 1
  INSTANCE_FAMILY = CPU_X64_XS
  AUTO_RESUME = TRUE
  AUTO_SUSPEND_SECS = 300;

-- 2. Create image repository
CREATE IMAGE REPOSITORY IF NOT EXISTS {database}.{schema}.GRAPH_IMAGES;

-- 3. Build and push the Docker image:
--    docker build -t graph-analytics .
--    docker tag graph-analytics <repo_url>/graph-analytics:latest
--    docker push <repo_url>/graph-analytics:latest
--
--    Get repo URL with:
--    SHOW IMAGE REPOSITORIES IN SCHEMA {database}.{schema};

-- 4. Create the service
CREATE SERVICE IF NOT EXISTS {database}.{schema}.GRAPH_ANALYTICS_SERVICE
  IN COMPUTE POOL {schema}_GRAPH_POOL
  MIN_INSTANCES = 1
  MAX_INSTANCES = 1
  FROM SPECIFICATION $$
  spec:
    containers:
      - name: graph-analytics
        image: /{{repo_url}}/graph-analytics:latest
        resources:
          requests:
            cpu: 1
            memory: 2Gi
          limits:
            cpu: 2
            memory: 4Gi
        env:
          SNOWFLAKE_ACCOUNT: {{{{SNOWFLAKE_ACCOUNT}}}}
          SNOWFLAKE_HOST: {{{{SNOWFLAKE_HOST}}}}
        readinessProbe:
          path: /health
          port: 8080
    endpoints:
      - name: graph-api
        port: 8080
        public: false
  $$;

-- 5. Create service functions
CREATE OR REPLACE FUNCTION {database}.{schema}.GRAPH_CENTRALITY(
    metric VARCHAR,
    entity_type VARCHAR DEFAULT NULL,
    top_n INTEGER DEFAULT 10
)
RETURNS VARIANT
SERVICE = {database}.{schema}.GRAPH_ANALYTICS_SERVICE
ENDPOINT = 'graph-api'
AS '/centrality';

CREATE OR REPLACE FUNCTION {database}.{schema}.GRAPH_COMMUNITY_DETECTION(
    resolution FLOAT DEFAULT 1.0
)
RETURNS VARIANT
SERVICE = {database}.{schema}.GRAPH_ANALYTICS_SERVICE
ENDPOINT = 'graph-api'
AS '/community';

CREATE OR REPLACE FUNCTION {database}.{schema}.GRAPH_SHORTEST_PATH(
    source_id VARCHAR,
    target_id VARCHAR
)
RETURNS VARIANT
SERVICE = {database}.{schema}.GRAPH_ANALYTICS_SERVICE
ENDPOINT = 'graph-api'
AS '/shortest_path';

-- 6. Grant usage
-- GRANT USAGE ON FUNCTION {database}.{schema}.GRAPH_CENTRALITY(VARCHAR, VARCHAR, INTEGER) TO ROLE <your_role>;
-- GRANT USAGE ON FUNCTION {database}.{schema}.GRAPH_COMMUNITY_DETECTION(FLOAT) TO ROLE <your_role>;
-- GRANT USAGE ON FUNCTION {database}.{schema}.GRAPH_SHORTEST_PATH(VARCHAR, VARCHAR) TO ROLE <your_role>;

-- 7. Test
-- SELECT {database}.{schema}.GRAPH_CENTRALITY('pagerank', NULL, 5);
-- SELECT {database}.{schema}.GRAPH_COMMUNITY_DETECTION(1.0);
-- SELECT {database}.{schema}.GRAPH_SHORTEST_PATH('node_1', 'node_2');
"""


def main():
    parser = argparse.ArgumentParser(description="Generate Cortex Agent Config")
    parser.add_argument("--semantic-models", required=True,
                        help="Comma-separated paths to semantic model YAML files")
    parser.add_argument("--database", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--stage-name", default=None,
                        help="Snowflake stage name (default: @DATABASE.SCHEMA.ONTOLOGY_STAGE)")
    parser.add_argument("--include-graph-tools", default="false",
                        help="Include graph analytics tools (true/false)")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    database = args.database
    schema = args.schema
    stage_name = args.stage_name or f"@{database}.{schema}.ONTOLOGY_STAGE"
    # Normalize: ensure stage_name starts with exactly one @
    stage_name = "@" + stage_name.lstrip("@")
    include_graph = args.include_graph_tools.lower() in ("true", "1", "yes")

    # Parse model file paths
    model_paths = []
    for p in args.semantic_models.split(","):
        p = p.strip()
        if p:
            path = Path(p)
            if path.exists():
                model_paths.append(path)
            else:
                print(f"  Warning: semantic model file not found: {p}", file=sys.stderr)

    if not model_paths:
        print("Error: No valid semantic model files found", file=sys.stderr)
        sys.exit(1)

    # Detect model types
    model_types = []
    for mp in model_paths:
        mt = detect_model_type(mp)
        if mt:
            model_types.append(mt)

    print(f"Building agent config for {len(model_paths)} semantic model(s)")
    print(f"  Models: {', '.join(mp.name for mp in model_paths)}")
    print(f"  Graph tools: {'yes' if include_graph else 'no'}")

    # Build tools list
    tools = build_agent_tools(model_paths, stage_name)

    if include_graph:
        tools.extend(build_graph_tools(database, schema))

    # Build instructions
    instructions = build_instructions(model_types, include_graph)

    # Assemble agent config
    agent_config = {
        "model_name": "claude-3-5-sonnet",
        "tool_choice": "auto",
        "max_tokens": 4096,
        "tools": tools,
        "instructions": instructions,
    }

    # Write agent config
    config_path = output_dir / "cortex_agent_config.json"
    config_path.write_text(json.dumps(agent_config, indent=2, ensure_ascii=False))
    print(f"  Wrote {config_path} ({len(tools)} tools)")

    # Generate SPCS scaffolding if graph tools requested
    if include_graph:
        service_path = output_dir / "spcs_graph_service.py"
        service_path.write_text(generate_spcs_graph_service(database, schema))
        print(f"  Wrote {service_path}")

        setup_path = output_dir / "spcs_setup.sql"
        setup_path.write_text(generate_spcs_setup_sql(database, schema))
        print(f"  Wrote {setup_path}")

    print(f"\nAgent config generation complete")


if __name__ == "__main__":
    main()
