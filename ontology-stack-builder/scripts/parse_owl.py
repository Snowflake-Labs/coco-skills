# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "rdflib>=7.0.0",
# ]
# ///
"""
OWL Ontology Parser - Extracts classes, relationships, and individuals from OWL/RDF files.

Outputs structured JSON files that the ontology-semantic-modeler skill uses to generate
Snowflake metadata tables and semantic models.

Usage:
    uv run --script parse_owl.py -- --owl-file /path/to/ontology.owl --output-dir /tmp/parsed
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from rdflib import Graph, Namespace, RDF, RDFS, OWL, URIRef, BNode
from rdflib.namespace import SKOS, DC, DCTERMS


def parse_uri_to_name(uri: str) -> str:
    """Extract a human-readable name from a URI."""
    if not uri:
        return ""
    uri_str = str(uri)
    # Try fragment (#Name)
    if "#" in uri_str:
        return uri_str.split("#")[-1]
    # Try last path segment
    if "/" in uri_str:
        return uri_str.split("/")[-1]
    return uri_str


def get_label(graph: Graph, uri: URIRef) -> str:
    """Get rdfs:label or skos:prefLabel for a URI, falling back to URI parsing."""
    for pred in [RDFS.label, SKOS.prefLabel]:
        for obj in graph.objects(uri, pred):
            return str(obj)
    return parse_uri_to_name(uri)


def get_description(graph: Graph, uri: URIRef) -> str:
    """Get description from common annotation properties."""
    for pred in [RDFS.comment, DC.description, DCTERMS.description, SKOS.definition]:
        for obj in graph.objects(uri, pred):
            return str(obj)
    return ""


def extract_classes(graph: Graph) -> list[dict]:
    """Extract all OWL classes with hierarchy information."""
    classes = []
    seen = set()

    # Find all OWL classes
    class_uris = set()
    for s in graph.subjects(RDF.type, OWL.Class):
        if not isinstance(s, BNode):
            class_uris.add(s)
    for s in graph.subjects(RDF.type, RDFS.Class):
        if not isinstance(s, BNode):
            class_uris.add(s)

    # Also find classes referenced in subClassOf
    for s, _, o in graph.triples((None, RDFS.subClassOf, None)):
        if not isinstance(s, BNode):
            class_uris.add(s)
        if not isinstance(o, BNode):
            class_uris.add(o)

    for cls_uri in sorted(class_uris, key=str):
        uri_str = str(cls_uri)
        if uri_str in seen:
            continue
        seen.add(uri_str)

        name = parse_uri_to_name(cls_uri)
        label = get_label(graph, cls_uri)
        description = get_description(graph, cls_uri)

        # Find parent classes (direct subClassOf, skip blank nodes like restrictions)
        parents = []
        for parent in graph.objects(cls_uri, RDFS.subClassOf):
            if not isinstance(parent, BNode):
                parents.append(parse_uri_to_name(parent))

        # Check if class is deprecated
        deprecated = False
        for obj in graph.objects(cls_uri, OWL.deprecated):
            if str(obj).lower() in ("true", "1"):
                deprecated = True

        # Determine if abstract (has subclasses but no direct instances defined)
        has_subclasses = any(True for _ in graph.subjects(RDFS.subClassOf, cls_uri))
        has_instances = any(True for _ in graph.subjects(RDF.type, cls_uri))
        is_abstract = has_subclasses and not has_instances

        classes.append({
            "uri": uri_str,
            "name": name,
            "label": label,
            "description": description,
            "parent_names": parents,
            "parent_name": parents[0] if parents else None,
            "is_abstract": is_abstract,
            "is_deprecated": deprecated,
            "namespace": uri_str.rsplit("#", 1)[0] if "#" in uri_str else uri_str.rsplit("/", 1)[0],
        })

    return classes


def extract_relations(graph: Graph) -> list[dict]:
    """Extract OWL object properties as relationship definitions."""
    relations = []
    seen = set()

    prop_uris = set()
    for s in graph.subjects(RDF.type, OWL.ObjectProperty):
        if not isinstance(s, BNode):
            prop_uris.add(s)
    for s in graph.subjects(RDF.type, RDF.Property):
        if not isinstance(s, BNode):
            prop_uris.add(s)

    for prop_uri in sorted(prop_uris, key=str):
        uri_str = str(prop_uri)
        if uri_str in seen:
            continue
        seen.add(uri_str)

        name = parse_uri_to_name(prop_uri)
        label = get_label(graph, prop_uri)
        description = get_description(graph, prop_uri)

        # Domain and range
        domains = [parse_uri_to_name(d) for d in graph.objects(prop_uri, RDFS.domain) if not isinstance(d, BNode)]
        ranges = [parse_uri_to_name(r) for r in graph.objects(prop_uri, RDFS.range) if not isinstance(r, BNode)]

        # Property characteristics
        is_transitive = (prop_uri, RDF.type, OWL.TransitiveProperty) in graph
        is_symmetric = (prop_uri, RDF.type, OWL.SymmetricProperty) in graph
        is_functional = (prop_uri, RDF.type, OWL.FunctionalProperty) in graph

        # Inverse
        inverses = [parse_uri_to_name(inv) for inv in graph.objects(prop_uri, OWL.inverseOf) if not isinstance(inv, BNode)]

        # subClassOf is always hierarchical
        is_hierarchical = name.lower() in ("subclassof", "part_of", "has_part", "is_a")

        # Cardinality heuristic
        cardinality = "N:N"
        if is_functional:
            cardinality = "N:1"

        relations.append({
            "uri": uri_str,
            "name": name,
            "label": label,
            "description": description,
            "domain_classes": domains,
            "domain_class": domains[0] if domains else "Thing",
            "range_classes": ranges,
            "range_class": ranges[0] if ranges else "Thing",
            "is_transitive": is_transitive,
            "is_symmetric": is_symmetric,
            "is_functional": is_functional,
            "is_hierarchical": is_hierarchical,
            "inverse_name": inverses[0] if inverses else None,
            "cardinality": cardinality,
        })

    # Always ensure subClassOf is present (it's implicit in OWL)
    if not any(r["name"] == "subClassOf" for r in relations):
        relations.insert(0, {
            "uri": str(RDFS.subClassOf),
            "name": "subClassOf",
            "label": "subClassOf",
            "description": "Taxonomic subsumption (is-a hierarchy)",
            "domain_classes": ["Thing"],
            "domain_class": "Thing",
            "range_classes": ["Thing"],
            "range_class": "Thing",
            "is_transitive": True,
            "is_symmetric": False,
            "is_functional": False,
            "is_hierarchical": True,
            "inverse_name": "hasSubClass",
            "cardinality": "N:1",
        })

    return relations


def extract_individuals(graph: Graph) -> list[dict]:
    """Extract named individuals (OWL instances)."""
    individuals = []
    seen = set()

    for s in graph.subjects(RDF.type, OWL.NamedIndividual):
        if isinstance(s, BNode):
            continue
        uri_str = str(s)
        if uri_str in seen:
            continue
        seen.add(uri_str)

        name = parse_uri_to_name(s)
        label = get_label(graph, s)

        # Find types (classes this individual belongs to)
        types = []
        for t in graph.objects(s, RDF.type):
            if t != OWL.NamedIndividual and not isinstance(t, BNode):
                types.append(parse_uri_to_name(t))

        individuals.append({
            "uri": uri_str,
            "name": name,
            "label": label,
            "types": types,
        })

    return individuals


def compute_stats(classes: list, relations: list, individuals: list) -> dict:
    """Compute summary statistics about the parsed ontology."""
    # Hierarchy depth via BFS
    children_map = defaultdict(list)
    roots = []
    for cls in classes:
        if cls["parent_name"]:
            children_map[cls["parent_name"]].append(cls["name"])
        else:
            roots.append(cls["name"])

    max_depth = 0
    queue = [(r, 0) for r in roots]
    while queue:
        node, depth = queue.pop(0)
        max_depth = max(max_depth, depth)
        for child in children_map.get(node, []):
            queue.append((child, depth + 1))

    # Namespace breakdown
    namespaces = defaultdict(int)
    for cls in classes:
        namespaces[cls["namespace"]] += 1

    return {
        "total_classes": len(classes),
        "abstract_classes": sum(1 for c in classes if c["is_abstract"]),
        "concrete_classes": sum(1 for c in classes if not c["is_abstract"]),
        "deprecated_classes": sum(1 for c in classes if c["is_deprecated"]),
        "root_classes": len(roots),
        "max_hierarchy_depth": max_depth,
        "total_relations": len(relations),
        "hierarchical_relations": sum(1 for r in relations if r["is_hierarchical"]),
        "transitive_relations": sum(1 for r in relations if r["is_transitive"]),
        "total_individuals": len(individuals),
        "top_namespaces": dict(sorted(namespaces.items(), key=lambda x: -x[1])[:10]),
    }


def main():
    parser = argparse.ArgumentParser(description="Parse OWL ontology files into structured JSON")
    parser.add_argument("--owl-file", required=True, help="Path to OWL/RDF/TTL file")
    parser.add_argument("--output-dir", required=True, help="Directory for JSON output files")
    parser.add_argument("--format", default=None, help="RDF format hint (xml, turtle, n3, nt). Auto-detected if omitted.")
    parser.add_argument("--exclude-deprecated", action="store_true", help="Exclude deprecated classes")
    parser.add_argument("--namespace-filter", default=None, help="Only include classes from this namespace prefix")
    args = parser.parse_args()

    owl_path = Path(args.owl_file)
    if not owl_path.exists():
        print(f"ERROR: OWL file not found: {owl_path}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Parse the ontology
    print(f"Parsing ontology: {owl_path}")
    g = Graph()

    fmt = args.format
    if fmt is None:
        suffix = owl_path.suffix.lower()
        fmt_map = {".owl": "xml", ".rdf": "xml", ".ttl": "turtle", ".n3": "n3", ".nt": "nt"}
        fmt = fmt_map.get(suffix, "xml")

    g.parse(str(owl_path), format=fmt)
    print(f"  Loaded {len(g)} triples")

    # Extract components
    classes = extract_classes(g)
    relations = extract_relations(g)
    individuals = extract_individuals(g)

    # Apply filters
    if args.exclude_deprecated:
        classes = [c for c in classes if not c["is_deprecated"]]

    if args.namespace_filter:
        ns = args.namespace_filter
        classes = [c for c in classes if c["namespace"].startswith(ns)]

    # Compute stats
    stats = compute_stats(classes, relations, individuals)

    # Write outputs
    for name, data in [("classes", classes), ("relations", relations), ("individuals", individuals), ("stats", stats)]:
        out_path = output_dir / f"{name}.json"
        with open(out_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  Wrote {out_path} ({len(data) if isinstance(data, list) else 'summary'} items)")

    # Print summary
    print(f"\n=== Ontology Summary ===")
    print(f"  Classes:       {stats['total_classes']} ({stats['abstract_classes']} abstract, {stats['concrete_classes']} concrete)")
    print(f"  Relations:     {stats['total_relations']} ({stats['hierarchical_relations']} hierarchical)")
    print(f"  Individuals:   {stats['total_individuals']}")
    print(f"  Max depth:     {stats['max_hierarchy_depth']}")
    print(f"  Root classes:  {stats['root_classes']}")
    if stats['deprecated_classes']:
        print(f"  Deprecated:    {stats['deprecated_classes']}")


if __name__ == "__main__":
    main()
