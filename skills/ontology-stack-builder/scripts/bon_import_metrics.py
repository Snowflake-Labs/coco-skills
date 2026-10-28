"""Path M step M4: import metrics from the stack's data semantic view(s) into the export.

Reads metrics from live semantic views, maps each one to the migrated class it
measures, and merges METRIC nodes, `metric MEASURES class` edges and
RELATED_SEMANTIC_VIEW representations into the files written by
export_to_bon.py. bon_apply.py and bon_reconcile.py then treat them like every
other object (scoped approve, read-back, map table, delta re-runs).

Which semantic views count as "data" views: those whose base tables include an
object a migrated class is bound to, and none of the stack's own plumbing
(ONT_*, VW_ONT_*, REL_RESOLVED). On the KG path that is {ONT}_KG_MODEL; on the
direct path the base view. Override with --semantic-view.

A metric whose base table maps to no migrated class is reported, not imported:
a metric that measures nothing is an orphan the BON gate rejects.

Usage:
  python scripts/bon_import_metrics.py -c CONN --input-dir /tmp/bon_migration [--semantic-view DB.SC.SV ...]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bon_migration_common import cap_description, check_identifier, checksum, connect, humanize, norm_key  # noqa: E402

PLUMBING_PREFIXES = ("ONT_", "VW_ONT_", "REL_RESOLVED", "REL_EDGE_INFERRED", "KG_NODE", "KG_EDGE")
METRIC_KINDS = ("METRIC", "METRIC_EDGE", "METRIC_SV")
PLAIN_AGG_RE = re.compile(r"^\s*(COUNT|SUM|AVG|MIN|MAX)\s*\(\s*(DISTINCT\s+)?[\w\.\*]+\s*\)\s*$", re.I)
FILLER = {"count", "of", "distinct", "number", "total", "the", "all", "across", "a", "an", "sum", "average", "avg", "in", "per"}


def is_trivial(m: dict) -> bool:
    """BON rule B-09: a plain aggregate whose description only restates its name governs nothing;
    the semantic view already owns it. ('COUNT(DISTINCT NODE_ID)' + 'Count of distinct companies'.)"""
    if not PLAIN_AGG_RE.match(m.get("expression") or ""):
        return False
    name_words = {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", m["name"].lower())}
    extra = {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", (m.get("comment") or "").lower())} - name_words - FILLER
    return len(extra) <= 3


def describe_sv(cur, fqn: str) -> dict:
    """Parse DESCRIBE SEMANTIC VIEW into {"tables": {alias: fqn}, "metrics": [...]}."""
    db, sc, name = (check_identifier(p, "semantic view part") for p in fqn.split("."))
    cur.execute(f"DESCRIBE SEMANTIC VIEW {db}.{sc}.{name}")
    cols = [c[0].lower() for c in cur.description]
    tables: dict[str, dict] = defaultdict(dict)
    metrics: dict[tuple, dict] = defaultdict(dict)
    for row in cur.fetchall():
        r = dict(zip(cols, row))
        kind, obj, parent, prop, val = r["object_kind"], r["object_name"], r["parent_entity"], r["property"], r["property_value"]
        if kind == "TABLE":
            tables[obj][prop] = val
        elif kind == "METRIC":
            metrics[(parent, obj)][prop] = val
    t_fqn = {alias: f"{p.get('BASE_TABLE_DATABASE_NAME')}.{p.get('BASE_TABLE_SCHEMA_NAME')}.{p.get('BASE_TABLE_NAME')}".upper()
             for alias, p in tables.items()}
    out = []
    for (parent, name), p in metrics.items():
        syn = p.get("SYNONYMS")
        out.append({
            "name": name, "entity": parent, "base_table": t_fqn.get(parent),
            "expression": p.get("EXPRESSION"), "comment": p.get("COMMENT") or "",
            "synonyms": json.loads(syn) if syn else [],
        })
    return {"fqn": fqn.upper(), "tables": t_fqn, "metrics": out}


def is_data_view(sv: dict, bound_fqns: set[str]) -> bool:
    names = [t.split(".")[-1] for t in sv["tables"].values()]
    return bool(set(sv["tables"].values()) & bound_fqns) and not any(n.startswith(PLUMBING_PREFIXES) for n in names)


def build_metrics(svs: list[dict], manifest: dict, associations: list[dict], domain: str, existing_names: set[str],
                  include_trivial: bool = False) -> dict:
    """Pure mapping: semantic view metrics -> candidates, edges, representations, manifest objects, report."""
    db, sc = manifest["database"], manifest["schema"]
    # base object FQN -> class node name (from the class bindings already exported)
    by_table = {a["assetRef"]["fqn"].upper(): a["termName"] for a in associations
                if a["assetRef"]["refType"] in ("TABLE", "VIEW")}
    # relation view V_{REL} -> node name of the relation's source class. Uses the source row, so
    # relations that were not migrated as edges (self-referential, inverse) still resolve.
    class_names = {norm_key(str(o["source_key"])): o["bon"]["name"] for o in manifest["objects"]
                   if o["source_kind"] == "CLASS" and o["status"] == "IN_SCOPE" and o.get("bon")}
    for o in manifest["objects"]:
        if o["source_kind"] == "CLASS" and o.get("bon") and isinstance(o.get("source_row"), dict):
            class_names[norm_key(str(o["source_row"].get("CLASS_NAME", "")))] = o["bon"]["name"]
    rel_src = {}
    for o in manifest["objects"]:
        if o["source_kind"] != "RELATION":
            continue
        if o["status"] == "IN_SCOPE" and o.get("bon") and o["bon"].get("type") != "HAS_PART":
            src = o["bon"]["source"]
        else:
            row = o.get("source_row") or {}
            src = class_names.get(norm_key(str(row.get("DOMAIN_CLASS") or row.get("SOURCE_CLASS_ID") or "")))
        if src:
            rel_src[f"{db}.{sc}.V_{str(o['source_key']).upper()}"] = src
    candidates, edges, refs, objects, skipped = [], [], [], [], []
    used = {norm_key(n) for n in existing_names}
    for sv in svs:
        for m in sv["metrics"]:
            key = f"{sv['fqn']}.{m['entity']}.{m['name']}"
            if not include_trivial and is_trivial(m):
                skipped.append(f"`{key}`: `{m['expression']}` only restates its name (B-09); stays in the semantic view")
                objects.append({"source_kind": "METRIC", "source_key": key, "status": "NOT_MIGRATED", "bon": None,
                                "checksum": checksum(m), "action": "none",
                                "note": "trivial aggregate; semantic view owns it (B-09)", "source_row": m})
                continue
            target = by_table.get((m["base_table"] or "").upper()) or rel_src.get((m["base_table"] or "").upper())
            if not target:
                skipped.append(f"`{key}`: base table `{m['base_table']}` is not bound to a migrated class")
                objects.append({"source_kind": "METRIC", "source_key": key, "status": "NOT_MIGRATED", "bon": None,
                                "checksum": checksum(m), "action": "none", "note": "base table not bound to a migrated class",
                                "source_row": m})
                continue
            name = humanize(m["name"])
            if norm_key(name) in used:
                name = f"{name} ({humanize(m['entity'])})"
            if norm_key(name) in used:
                skipped.append(f"`{key}`: name `{name}` collides with an existing node")
                continue
            used.add(norm_key(name))
            syn = [s for s in ([m["name"]] + [s.replace("_", " ") for s in m["synonyms"]]) if s.lower() != name.lower()][:5]
            cand = {
                "name": name, "itemKind": "METRIC",
                "description": cap_description(m["comment"] or f"{name} from semantic view {sv['fqn']}."),
                "domainName": domain, "domainSource": "USER_DECLARED", "realisations": 1,
                "realisation_sources": [sv["fqn"]], "definition": m["expression"], "definition_kind": "SV_EXPRESSION",
                "definitions_observed": [m["expression"]], "descriptions_observed": [m["comment"]] if m["comment"] else [],
                "source_path": "SV", "synonyms": syn, "tags": [], "formula": m["expression"],
                "formulaSource": m["base_table"], "exclusions": [],
            }
            edge = {"source": name, "target": target, "type": "MEASURES", "label": None}
            ref = {"termName": name, "role": "RELATED_SEMANTIC_VIEW",
                   "assetRef": {"refType": "SEMANTIC_VIEW", "fqn": sv["fqn"], "subObjectType": "METRIC",
                                "subObjectName": m["name"], "entityQualifier": m["entity"]}}
            candidates.append(cand)
            edges.append(edge)
            refs.append(ref)
            cs = checksum({"m": m, "name": name, "target": target})
            for kind, bon in (("METRIC", {"name": name, "itemKind": "METRIC"}), ("METRIC_EDGE", edge), ("METRIC_SV", ref)):
                objects.append({"source_kind": kind, "source_key": key, "status": "IN_SCOPE", "bon": bon,
                                "checksum": cs, "action": "create", "note": f"from {sv['fqn']}", "source_row": m})
    return {"candidates": candidates, "relationships": edges, "associations": refs, "objects": objects, "skipped": skipped}


def merge_into_export(d: Path, result: dict, svs: list[str], prev_map: dict | None = None) -> None:
    """Replace any metrics from a previous run, then append. Keeps re-runs idempotent."""
    cands = json.loads((d / "candidates.json").read_text())
    rels = json.loads((d / "relationships.json").read_text())
    assocs = json.loads((d / "associations.json").read_text())
    man = json.loads((d / "export_manifest.json").read_text())
    old = {o["bon"]["name"] for o in man["objects"] if o["source_kind"] == "METRIC" and o.get("bon")}
    cands = [c for c in cands if c["name"] not in old] + result["candidates"]
    rels = [e for e in rels if not (e["type"] == "MEASURES" and e["source"] in old)] + result["relationships"]
    assocs = [a for a in assocs if a["termName"] not in old] + result["associations"]
    # Delta: compare with the map table (same rule as export_to_bon: unchanged MIGRATED rows are skipped,
    # changed ones become updates; bon_apply skips anything that already exists either way).
    for o in result["objects"]:
        p = (prev_map or {}).get((o["source_kind"], o["source_key"]))
        if o["status"] != "IN_SCOPE" or not p or p.get("STATUS") != "MIGRATED":
            continue
        o["action"] = "skip" if p.get("SOURCE_CHECKSUM") == o["checksum"] else "update"
    man["objects"] = [o for o in man["objects"] if o["source_kind"] not in METRIC_KINDS] + result["objects"]
    man["counts"]["nodes"] = len(cands)
    man["counts"]["relationships"] = len(rels)
    man["counts"]["representations"] = len(assocs)
    man["metrics"] = {"semantic_views": svs, "imported": len(result["candidates"]), "skipped": result["skipped"]}
    (d / "candidates.json").write_text(json.dumps(cands, indent=2, ensure_ascii=False))
    (d / "relationships.json").write_text(json.dumps(rels, indent=2, ensure_ascii=False))
    (d / "associations.json").write_text(json.dumps(assocs, indent=2, ensure_ascii=False))
    (d / "export_manifest.json").write_text(json.dumps(man, indent=2, ensure_ascii=False, default=str))
    report = d / "migration_loss_report.md"
    if result["skipped"]:
        txt = report.read_text().split("\n## Metrics not imported")[0].rstrip()
        report.write_text(txt + "\n\n## Metrics not imported (no migrated class to measure)\n\n" + "\n".join(f"- {s}" for s in result["skipped"]) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--connection", "-c", required=True)
    ap.add_argument("--input-dir", default="/tmp/bon_migration")
    ap.add_argument("--semantic-view", action="append", default=[], help="DB.SCHEMA.SV; default: auto-detect data views in the stack schema")
    ap.add_argument("--include-trivial", action="store_true",
                    help="also import plain aggregates whose description only restates the name (BON gate B-09 will flag them)")
    args = ap.parse_args(argv)
    d = Path(args.input_dir)
    man = json.loads((d / "export_manifest.json").read_text())
    assocs = json.loads((d / "associations.json").read_text())
    bound = {a["assetRef"]["fqn"].upper() for a in assocs if a["assetRef"]["refType"] in ("TABLE", "VIEW")}
    cand_names = {c["name"] for c in json.loads((d / "candidates.json").read_text())}
    old_metrics = {o["bon"]["name"] for o in man["objects"] if o["source_kind"] == "METRIC" and o.get("bon")}

    with connect(args.connection) as conn:
        cur = conn.cursor()
        if args.semantic_view:
            fqns = [s.upper() for s in args.semantic_view]
        else:
            db, sc = check_identifier(man["database"], "database"), check_identifier(man["schema"], "schema")
            cur.execute(f"SHOW SEMANTIC VIEWS IN SCHEMA {db}.{sc}")
            cols = [c[0].lower() for c in cur.description]
            fqns = [f"{db}.{sc}.{dict(zip(cols, r))['name']}".upper() for r in cur.fetchall()]
        svs = [describe_sv(cur, f) for f in fqns]
        prev_map = {}
        db, sc = check_identifier(man["database"], "database"), check_identifier(man["schema"], "schema")
        cur.execute(f"SHOW TABLES LIKE 'ONT_BON_MIGRATION_MAP' IN SCHEMA {db}.{sc}")
        if cur.fetchall():
            cur.execute(f"SELECT SOURCE_KIND, SOURCE_KEY, STATUS, SOURCE_CHECKSUM FROM {db}.{sc}.ONT_BON_MIGRATION_MAP "
                        "WHERE ONTOLOGY_NAME = %s AND SOURCE_KIND IN ('METRIC', 'METRIC_EDGE', 'METRIC_SV')", (man["ontology"],))
            prev_map = {(k, key): {"STATUS": s, "SOURCE_CHECKSUM": c} for k, key, s, c in cur.fetchall()}

    chosen = svs if args.semantic_view else [s for s in svs if is_data_view(s, bound)]
    result = build_metrics(chosen, man, assocs, man["domain"], cand_names - old_metrics, args.include_trivial)
    merge_into_export(d, result, [s["fqn"] for s in chosen], prev_map)
    print(json.dumps({
        "semantic_views_scanned": [s["fqn"] for s in svs],
        "data_views_used": [s["fqn"] for s in chosen],
        "metrics_imported": [(c["name"], e["target"]) for c, e in zip(result["candidates"], result["relationships"])],
        "metrics_skipped": result["skipped"],
        "next": "run bon_apply.py, then bon_reconcile.py",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
