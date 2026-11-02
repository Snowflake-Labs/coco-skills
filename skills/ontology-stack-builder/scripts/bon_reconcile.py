"""Read Business Ontology back and reconcile it against the export.

Never trust write logs: BON can return error:false for writes that do not
persist. This script reads the domain back, classifies every source object as
MIGRATED / MISSING / NOT_MIGRATED, writes reconcile_report.md, and upserts
ONT_BON_MIGRATION_MAP in the stack schema (DDL: references/migration_map_table.sql).

Usage:
  python scripts/bon_reconcile.py --connection C --database DB --schema SC \
      --domain "Stock Market" --input-dir /tmp/bon_migration [--no-write-map]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bon_migration_common import asset_key, call_system, check_identifier, connect, dumps, norm_key  # noqa: E402

DDL_FILE = Path(__file__).resolve().parent.parent / "references" / "migration_map_table.sql"
NODE_KINDS = {"CLASS", "DERIVED", "SHARED", "METRIC"}
EDGE_KINDS = {"SUBCLASS", "RELATION", "METRIC_EDGE"}
REF_KINDS = {"BINDING", "PROPERTY", "METRIC_SV"}


# --------------------------------------------------------------------------- read BON

def read_bon_state(cur, domain: str, expected_names: list[str]) -> dict:
    """Return {"terms": {norm_name: {termId, name, itemKind}}, "edges": set, "refs": set, "source": str}."""
    g = call_system(cur, "SYSTEM$GET_GLOSSARY_GRAPH", domain)
    if not g.get("error"):
        terms = {norm_key(t["name"]): t for t in g.get("terms", []) or [] if t.get("domain", domain) == domain}
        by_id = {str(t["termId"]): t["name"] for t in terms.values()}
        edges = {(norm_key(by_id.get(str(r["sourceTermId"]), "")), norm_key(by_id.get(str(r["targetTermId"]), "")), r["relationshipType"])
                 for r in g.get("relationships", []) or []}
        refs = {asset_key(norm_key(by_id.get(str(a["termId"]), "")), a) for a in g.get("associations", []) or []}
        return {"terms": terms, "edges": edges, "refs": refs, "source": "GET_GLOSSARY_GRAPH"}

    # Fallback for large accounts: term list, then per-term reads for edges and assets.
    lst = call_system(cur, "SYSTEM$GET_GLOSSARY_TERM_LIST", domain, "TERM", "")
    terms = {norm_key(t["name"]): t for t in lst.get("terms", []) or [] if t.get("domain") == domain}
    by_id = {str(t["termId"]): t["name"] for t in terms.values()}
    edges, refs = set(), set()
    wanted = {norm_key(n) for n in expected_names}
    for key, t in terms.items():
        if key not in wanted:
            continue
        detail = call_system(cur, "SYSTEM$GET_GLOSSARY_TERM", str(t["termId"]))
        for e in (detail.get("relationships") or {}).get("edges", []) or []:
            names = {str(n["termId"]): n["name"] for n in (detail.get("relationships") or {}).get("nodes", []) or []}
            names.update(by_id)
            edges.add((norm_key(names.get(str(e["sourceTermId"]), "")), norm_key(names.get(str(e["targetTermId"]), "")), e["relationshipType"]))
        assets = call_system(cur, "SYSTEM$GET_GLOSSARY_TERM_ASSETS", str(t["termId"]), "")
        for a in assets.get("assets", assets.get("associations", [])) or []:
            refs.add(asset_key(key, a))
    return {"terms": terms, "edges": edges, "refs": refs, "source": f"term reads (graph error: {g.get('errorMessage')})"}


# --------------------------------------------------------------------------- pure reconcile

def reconcile(manifest: dict, relationships: list[dict], associations: list[dict], state: dict) -> dict:
    rows = []
    stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    missing: list[str] = []

    def edge_present(e: dict) -> bool:
        return (norm_key(e["source"]), norm_key(e["target"]), e["type"]) in state["edges"]

    def ref_present(a: dict) -> bool:
        return asset_key(norm_key(a["termName"]), a["assetRef"]) in state["refs"]

    for o in manifest["objects"]:
        kind, bon = o["source_kind"], o.get("bon")
        term_id, bon_name, bon_kind, detail = None, None, None, None
        if o["status"] == "NOT_MIGRATED":
            status = "NOT_MIGRATED"
        elif kind in NODE_KINDS:
            t = state["terms"].get(norm_key(bon["name"]))
            bon_name, bon_kind = bon["name"], bon["itemKind"]
            status = "MIGRATED" if t else "MISSING"
            term_id = str(t["termId"]) if t else None
        elif kind in EDGE_KINDS:
            bon_name, bon_kind, detail = f"{bon['source']} -> {bon['target']}", bon["type"], bon
            status = "MIGRATED" if edge_present(bon) else "MISSING"
        elif kind in REF_KINDS:
            bon_name, bon_kind, detail = bon["termName"], bon["assetRef"]["refType"], bon["assetRef"]
            status = "MIGRATED" if ref_present(bon) else "MISSING"
        else:
            status = "NOT_MIGRATED"
        stats[kind][status] += 1
        if status == "MISSING":
            missing.append(f"{kind} `{o['source_key']}` -> {bon_name}")
        rows.append({
            "ONTOLOGY_NAME": manifest["ontology"], "SOURCE_KIND": kind, "SOURCE_KEY": o["source_key"],
            "BON_DOMAIN": manifest["domain"], "BON_NAME": bon_name, "BON_KIND": bon_kind, "BON_TERM_ID": term_id,
            "BON_DETAIL": detail, "STATUS": status,
            "SOURCE_CHECKSUM": o["checksum"] if status == "MIGRATED" else None,
            "SOURCE_ROW": o.get("source_row"), "NOTE": o.get("note") or None,
        })

    # Totals over the export files themselves (they also include derived MEASURES / CLASSIFIES edges).
    rel_found = sum(1 for e in relationships if edge_present(e))
    ref_found = sum(1 for a in associations if ref_present(a))
    node_names = [o["bon"]["name"] for o in manifest["objects"] if o["source_kind"] in NODE_KINDS and o["status"] == "IN_SCOPE"]
    node_found = sum(1 for n in node_names if norm_key(n) in state["terms"])
    totals = {
        "nodes": (node_found, len(node_names)),
        "relationships": (rel_found, len(relationships)),
        "representations": (ref_found, len(associations)),
    }
    parity = all(found == expected for found, expected in totals.values())
    return {"rows": rows, "stats": {k: dict(v) for k, v in stats.items()}, "totals": totals,
            "missing": missing, "parity": parity, "source": state.get("source")}


def render_report(manifest: dict, result: dict) -> str:
    lines = [
        f"# Reconcile report: {manifest['ontology']} -> {manifest['domain']}",
        "",
        f"Read back via: {result['source']}",
        "",
        f"**Parity: {'100% - every in-scope object is in Business Ontology' if result['parity'] else 'INCOMPLETE'}**",
        "",
        "| Object | Found in BON | Expected |",
        "|---|---|---|",
    ]
    for k, (found, expected) in result["totals"].items():
        lines.append(f"| {k} | {found} | {expected} |")
    lines += ["", "| Source kind | MIGRATED | MISSING | NOT_MIGRATED |", "|---|---|---|---|"]
    for kind, s in sorted(result["stats"].items()):
        lines.append(f"| {kind} | {s.get('MIGRATED', 0)} | {s.get('MISSING', 0)} | {s.get('NOT_MIGRATED', 0)} |")
    if result["missing"]:
        lines += ["", "## Missing", "", "Re-run `bon_apply.py` once; it only writes what is missing. Report anything still missing after that.", ""]
        lines += [f"- {m}" for m in result["missing"]]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- write map table

def write_map(cur, database: str, schema: str, rows: list[dict]) -> int:
    db, sc = check_identifier(database, "database"), check_identifier(schema, "schema")
    ddl = DDL_FILE.read_text().replace("{DATABASE}", db).replace("{SCHEMA}", sc)
    ddl = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())  # comments may contain ';'
    create = ddl[ddl.index("CREATE TABLE"): ddl.index(";", ddl.index("CREATE TABLE")) + 1]
    cur.execute(create)
    cols = ["ONTOLOGY_NAME", "SOURCE_KIND", "SOURCE_KEY", "BON_DOMAIN", "BON_NAME", "BON_KIND", "BON_TERM_ID",
            "BON_DETAIL", "STATUS", "SOURCE_CHECKSUM", "SOURCE_ROW", "NOTE"]
    variant_cols = {"BON_DETAIL", "SOURCE_ROW"}
    written = 0
    for i in range(0, len(rows), 50):
        chunk = rows[i:i + 50]
        selects, params = [], []
        for r in chunk:
            parts = []
            for c in cols:
                v = r.get(c)
                if c in variant_cols:
                    parts.append(f"PARSE_JSON(%s) AS {c}")
                    params.append(dumps(v) if v is not None else "null")
                else:
                    parts.append(f"%s::STRING AS {c}")
                    params.append(v)
            selects.append("SELECT " + ", ".join(parts))
        src = " UNION ALL ".join(selects)
        set_clause = ", ".join(f"t.{c} = s.{c}" for c in cols[3:]) + ", t.MIGRATED_AT = CURRENT_TIMESTAMP()"
        cur.execute(
            f"MERGE INTO {db}.{sc}.ONT_BON_MIGRATION_MAP t USING ({src}) s "
            "ON t.ONTOLOGY_NAME = s.ONTOLOGY_NAME AND t.SOURCE_KIND = s.SOURCE_KIND AND t.SOURCE_KEY = s.SOURCE_KEY "
            f"WHEN MATCHED THEN UPDATE SET {set_clause} "
            f"WHEN NOT MATCHED THEN INSERT ({', '.join(cols)}) VALUES ({', '.join('s.' + c for c in cols)})",
            params,
        )
        written += len(chunk)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--connection", "-c", required=True)
    ap.add_argument("--database", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--input-dir", default="/tmp/bon_migration")
    ap.add_argument("--no-write-map", action="store_true", help="report only; do not create/update ONT_BON_MIGRATION_MAP")
    args = ap.parse_args(argv)

    d = Path(args.input_dir)
    manifest = json.loads((d / "export_manifest.json").read_text())
    relationships = json.loads((d / "relationships.json").read_text())
    associations = json.loads((d / "associations.json").read_text())
    if manifest["domain"] != args.domain:
        raise SystemExit(f"--domain {args.domain!r} does not match the export ({manifest['domain']!r})")

    with connect(args.connection) as conn:
        cur = conn.cursor()
        names = [o["bon"]["name"] for o in manifest["objects"] if o["source_kind"] in NODE_KINDS and o.get("bon")]
        state = read_bon_state(cur, args.domain, names)
        result = reconcile(manifest, relationships, associations, state)
        written = 0 if args.no_write_map else write_map(cur, args.database, args.schema, result["rows"])

    (d / "reconcile_report.md").write_text(render_report(manifest, result))
    print(json.dumps({"parity": result["parity"], "totals": result["totals"], "stats": result["stats"],
                      "missing": result["missing"], "map_rows_written": written, "read_via": result["source"]}, indent=2))
    return 0 if result["parity"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
