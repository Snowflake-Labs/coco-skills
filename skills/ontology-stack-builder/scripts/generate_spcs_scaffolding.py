# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Generate SPCS Graph Analytics Scaffolding.

Produces the SPCS service and setup SQL for graph analytics (NetworkX-based).
Agent creation is handled by the native Cortex Code `cortex-agent` skill.

Generates:
  - spcs_graph_service.py: NetworkX-based graph analytics Flask service for SPCS
  - spcs_setup.sql: SPCS compute pool, service, and service function DDL

Usage:
    uv run --project <SKILL_DIR> python <SKILL_DIR>/scripts/generate_spcs_scaffolding.py \
      --database MYDB --schema MYSCHEMA \
      --output-dir /tmp/generated
"""

import argparse
import sys
from pathlib import Path


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
    parser = argparse.ArgumentParser(description="Generate SPCS Graph Analytics Scaffolding")
    parser.add_argument("--database", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    database = args.database
    schema = args.schema

    print(f"Generating SPCS graph analytics scaffolding for {database}.{schema}")

    service_path = output_dir / "spcs_graph_service.py"
    service_path.write_text(generate_spcs_graph_service(database, schema))
    print(f"  Wrote {service_path}")

    setup_path = output_dir / "spcs_setup.sql"
    setup_path.write_text(generate_spcs_setup_sql(database, schema))
    print(f"  Wrote {setup_path}")

    print(f"\nSPCS scaffolding generation complete")


if __name__ == "__main__":
    main()
