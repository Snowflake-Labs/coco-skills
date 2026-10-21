"""Export a deployed ontology stack into Business Ontology migration files.

Reads the stack's ONT_* metadata tables (read-only), normalises the schema
variant, applies the mapping contract in migration/MAPPING.md and writes:

  candidates.json          node candidates (BON candidate contract v1)
  relationships.json       edges, types validated against the BON vocabulary
  associations.json        representations (assetRefJson + role)
  export_manifest.json     variant, counts, per-object provenance, checksums, actions
  migration_loss_report.md everything with no BON home

Usage:
  python scripts/export_to_bon.py --connection C --database DB --schema SC \
      --domain "Stock Market" [--ontology STOCK] [--include-shared-properties] \
      [--rel-type-override listed_on=RELATED_TO] [--out /tmp/bon_migration]

  # offline: build from a snapshot captured earlier with --dump-snapshot
  python scripts/export_to_bon.py --snapshot snap.json --domain "Stock Market"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bon_migration_common import (  # noqa: E402
    BON_RELATIONSHIP_TYPES,
    cap_description,
    check_identifier,
    checksum,
    humanize,
    norm_key,
)

# Tables the exporter reads. Everything else that exists is only counted for the loss report.
SOURCE_TABLES = [
    "ONT_ONTOLOGY", "ONT_CLASS", "ONT_RELATION_DEF", "ONT_CLASS_MAP", "ONT_OBJECT_SOURCE",
    "ONT_PROPERTY", "ONT_SHARED_PROPERTY", "ONT_DERIVED_PROPERTY", "ONT_BON_MIGRATION_MAP",
]

LOSS_TABLES = {
    "KG_NODE": "Instance data; BON stores concepts, not instances. Data stays queryable.",
    "KG_EDGE": "Instance edges; stay queryable for graph tools.",
    "REL_EDGE_INFERRED": "Inferred instance edges; no BON equivalent.",
    "ONT_RULE": "Inference rules; BON has no rule engine.",
    "ONT_CONSTRAINT_VIOLATION": "Constraint check output; no BON equivalent.",
    "ACT_TYPE": "Operational actions are not part of BON.",
    "ACT_DEF": "Operational action parameters.",
    "ACT_INVOCATION": "Action invocation log.",
    "ONT_FUNCTION": "Function registry; functions stay as agent tools.",
    "ONT_FUNCTION_BINDING": "Function bindings.",
    "ONT_INTERFACE": "Interfaces have no BON equivalent.",
    "ONT_INTERFACE_PROPERTY": "Interface properties.",
    "ONT_INTERFACE_IMPL": "Interface implementations.",
    "OBJ_VIEW_DEF": "UI rendering hints.",
    "OBJ_VIEW_FIELD": "UI rendering hints.",
    "ONT_ROLE": "BON access is governed by Snowflake privileges.",
    "ONT_ROLE_BINDING": "Ontology role bindings.",
    "ONT_PERMISSION": "Ontology permissions.",
    "ONT_REL_MAP": "Relation-to-table plumbing; BON edges are concept-level.",
    "ONT_LINK_SOURCE": "Relation-to-table plumbing; BON edges are concept-level.",
}

# Root classes that carry no business meaning. Always skipped.
ALWAYS_SKIP_CLASSES = {"thing", "owlthing"}
# Parentless abstract roots with these names are flagged for the review gate.
GENERIC_ROOT_NAMES = {"entity", "object", "root", "baseentity", "baseobject", "node", "concept"}

# Plumbing columns of the KG typed views; never real business properties.
# NAME is deliberately absent: it is the entity's display name and a real property.
KG_PLUMBING_COLUMNS = {"NODE_ID", "NODE_TYPE", "PROPS", "TS_INGESTED"}

GENERIC_DESCRIPTION_PATTERNS = [
    re.compile(r"^\s*$"),
    re.compile(r"^(entity|class|object) from table\b", re.I),
    re.compile(r"^auto[- ]?(generated|detected)\b", re.I),
    re.compile(r"^[A-Za-z0-9_]+ of [A-Za-z0-9_]+$"),  # "COL of Class" property default
]

# Relationship type selection, see MAPPING.md. Keys are norm_key(REL_NAME).
REL_SKIP = {"subclassof", "hassubclass", "isa", "typeof"}
REL_PART_OF_REVERSED = {"partof", "belongsto", "memberof", "componentof"}
REL_HAS_PART = {"haspart", "contains", "hascomponent", "hasmember", "consistsof"}
REL_IDENTIFIED_BY = {"identifiedby", "hasidentifier"}
REL_EQUIVALENT = {"equivalentto", "sameas"}
REL_RELATED = {"relatedto", "associatedwith", "relatesto"}

AGGREGATE_RE = re.compile(r"\b(SUM|COUNT|AVG|MIN|MAX|MEDIAN|STDDEV|COUNT_IF|APPROX_COUNT_DISTINCT)\s*\(", re.I)


# --------------------------------------------------------------------------- model

@dataclass
class Klass:
    key: str
    class_name: str
    label: str | None
    parent_key: str | None
    is_abstract: bool
    description: str
    raw: dict
    binding: dict | None = None  # {"fqn": "DB.SC.OBJ", "refType": "TABLE|VIEW", "columns": [...]}
    flags: list[str] = field(default_factory=list)

    @property
    def node_name(self) -> str:
        return self.label.strip() if self.label and self.label.strip() else humanize(self.class_name)


@dataclass
class Relation:
    name: str
    label: str | None
    src_key: str | None
    dst_key: str | None
    description: str
    raw: dict


def _first(row: dict, *names: str, default: Any = None) -> Any:
    for n in names:
        if n in row and row[n] is not None:
            return row[n]
    return default


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in {"true", "t", "yes", "y", "1"}
    return bool(v)


def detect_variant(tables: dict[str, dict]) -> str:
    def cols(t: str) -> set[str]:
        return {c.upper() for c in tables.get(t, {}).get("columns", [])}

    cls_cols, map_cols, prop_cols = cols("ONT_CLASS"), cols("ONT_CLASS_MAP"), cols("ONT_PROPERTY")
    if "CLASS_ID" in cls_cols:
        return "ID_KEYED"
    if "CONCRETE_VIEW" in map_cols:
        return "REFERENCE"
    if "PROPS_EXPR" in map_cols or "PROPERTY_NAME" in prop_cols:
        return "TEMPLATE"
    return "GENERATOR"


def _rows(tables: dict, name: str, ontology: str | None) -> list[dict]:
    rows = tables.get(name, {}).get("rows", []) or []
    if ontology:
        rows = [r for r in rows if str(r.get("ONTOLOGY_NAME", ontology)).upper() == ontology.upper()]
    return rows


# --------------------------------------------------------------------------- normalise

def load_classes(snapshot: dict, ontology: str | None) -> dict[str, Klass]:
    tables = snapshot["tables"]
    classes: dict[str, Klass] = {}
    for r in _rows(tables, "ONT_CLASS", ontology):
        status = str(_first(r, "STATUS", default="ACTIVE")).upper()
        if status not in ("ACTIVE", ""):
            continue
        name = str(_first(r, "CLASS_NAME", default="")).strip()
        if not name:
            continue
        key = str(_first(r, "CLASS_ID", "CLASS_NAME"))
        parent = _first(r, "PARENT_CLASS_ID", "PARENT_CLASS_NAME")
        classes[key] = Klass(
            key=key,
            class_name=name,
            label=_first(r, "CLASS_LABEL", "LABEL"),
            parent_key=str(parent) if parent not in (None, "") else None,
            is_abstract=_truthy(_first(r, "IS_ABSTRACT", default=False))
            or str(_first(r, "TYPE_CLASS", default="")).upper() == "ABSTRACT",
            description=str(_first(r, "DESCRIPTION", default="") or ""),
            raw=r,
        )
    return classes


def _object_type(snapshot: dict, fqn: str) -> str | None:
    t = (snapshot.get("objects") or {}).get(fqn.upper())
    if t is None:
        return None
    return "VIEW" if "VIEW" in t.upper() else "TABLE"


def _binding(snapshot: dict, fqn: str) -> dict | None:
    fqn = fqn.upper()
    if fqn.count(".") != 2:
        return None
    ref_type = _object_type(snapshot, fqn)
    known = snapshot.get("objects") or {}
    if known and ref_type is None:
        return None  # catalog was captured and the object is not in it
    return {
        "fqn": fqn,
        "refType": ref_type or "TABLE",
        "columns": [c.upper() for c in (snapshot.get("object_columns") or {}).get(fqn, [])],
    }


def resolve_bindings(snapshot: dict, classes: dict[str, Klass], ontology: str | None) -> None:
    """Attach each class to the Snowflake object that implements it."""
    db, sc = snapshot["database"], snapshot["schema"]
    tables = snapshot["tables"]
    by_name = {norm_key(k.class_name): k for k in classes.values()}

    def own_view(k: Klass) -> dict | None:
        return _binding(snapshot, f"{db}.{sc}.V_{k.class_name.upper()}")

    # 1. ID_KEYED: SOURCE_TABLE on the class row itself.
    for k in classes.values():
        src = _first(k.raw, "SOURCE_TABLE")
        if src and str(src).count(".") == 2:
            k.binding = _binding(snapshot, str(src))
            if k.binding is None:
                k.flags.append(f"binding_not_found:{src}")

    # 2. ONT_CLASS_MAP.
    for m in _rows(tables, "ONT_CLASS_MAP", ontology):
        k = by_name.get(norm_key(str(_first(m, "CLASS_NAME", default=""))))
        if k is None or k.binding is not None:
            continue
        concrete = _first(m, "CONCRETE_VIEW")
        if concrete:
            # REFERENCE variant: abstract classes map to their subclasses' views. Only bind a
            # class to a view that is its own (V_{CLASS} or SUBTYPE_LABEL == class).
            subtype = str(_first(m, "SUBTYPE_LABEL", "SUBTYPE_VALUE", default=""))
            if norm_key(subtype) == norm_key(k.class_name) or str(concrete).upper() == f"V_{k.class_name.upper()}":
                b = _binding(snapshot, f"{db}.{sc}.{str(concrete).upper()}")
                if b and not b["columns"] and _first(m, "COLS"):
                    cols = _first(m, "COLS")
                    b["columns"] = [c.upper() for c in (json.loads(cols) if isinstance(cols, str) else cols)]
                k.binding = b
            continue
        src_table = str(_first(m, "SOURCE_TABLE", default="")).upper()
        if src_table == "KG_NODE":
            k.binding = own_view(k)
            if k.binding is None:
                k.flags.append("kg_view_missing")
        elif src_table:
            src_db = str(_first(m, "SOURCE_DATABASE", default=db)).upper()
            src_sc = str(_first(m, "SOURCE_SCHEMA", default=sc)).upper()
            k.binding = _binding(snapshot, f"{src_db}.{src_sc}.{src_table}")
            if k.binding is None:
                k.flags.append(f"binding_not_found:{src_db}.{src_sc}.{src_table}")

    # 3. ONT_OBJECT_SOURCE reference-variant rows (CLASS_NAME + SOURCE_REF).
    for s in _rows(tables, "ONT_OBJECT_SOURCE", ontology):
        k = by_name.get(norm_key(str(_first(s, "CLASS_NAME", "OBJ_TYPE", default=""))))
        ref = _first(s, "SOURCE_REF")
        if k is not None and k.binding is None and ref:
            k.binding = _binding(snapshot, str(ref))

    # 4. Fallback: the generator's typed view, if it exists. Not when the declared source is
    # gone: V_{CLASS} selects from that source, so it exists in the catalog but cannot compile.
    for k in classes.values():
        if k.binding is None and not k.is_abstract and not any(f.startswith("binding_not_found:") for f in k.flags):
            k.binding = own_view(k)


def load_relations(snapshot: dict, classes: dict[str, Klass], ontology: str | None) -> list[Relation]:
    by_name = {norm_key(k.class_name): k.key for k in classes.values()}
    rels = []
    for r in _rows(snapshot["tables"], "ONT_RELATION_DEF", ontology):
        if str(_first(r, "STATUS", default="ACTIVE")).upper() not in ("ACTIVE", ""):
            continue
        name = str(_first(r, "REL_NAME", "RELATION_NAME", default="")).strip()
        if "SOURCE_CLASS_ID" in r or "TARGET_CLASS_ID" in r:
            src, dst = _first(r, "SOURCE_CLASS_ID"), _first(r, "TARGET_CLASS_ID")
            src, dst = (str(src) if src is not None else None), (str(dst) if dst is not None else None)
        else:
            src = by_name.get(norm_key(str(_first(r, "DOMAIN_CLASS", default=""))))
            dst = by_name.get(norm_key(str(_first(r, "RANGE_CLASS", default=""))))
        rels.append(Relation(
            name=name,
            label=_first(r, "RELATION_LABEL", "LABEL"),
            src_key=src if src in classes else None,
            dst_key=dst if dst in classes else None,
            description=str(_first(r, "DESCRIPTION", default="") or ""),
            raw=r,
        ))
    return rels


# --------------------------------------------------------------------------- mapping

def is_generic_description(desc: str, k: Klass) -> bool:
    if norm_key(desc) in (norm_key(k.class_name), norm_key(k.node_name)):
        return True
    return any(p.search(desc or "") for p in GENERIC_DESCRIPTION_PATTERNS)


def choose_rel_type(rel: Relation, overrides: dict[str, str]) -> tuple[str | None, bool, str | None]:
    """Return (bon_type, reversed, label). bon_type None means skip."""
    key = norm_key(rel.name)
    if rel.name.lower() in overrides or key in overrides:
        t = overrides.get(rel.name.lower(), overrides.get(key))
        return t, False, (_custom_label(rel) if t == "CUSTOM" else None)
    if key in REL_SKIP:
        return None, False, None
    if key in REL_PART_OF_REVERSED:
        return "HAS_PART", True, None
    if key in REL_HAS_PART:
        return "HAS_PART", False, None
    if key in REL_IDENTIFIED_BY:
        return "IDENTIFIED_BY", False, None
    if key in REL_EQUIVALENT:
        return "EQUIVALENT_TO", False, None
    if key in REL_RELATED:
        return "RELATED_TO", False, None
    return "CUSTOM", False, _custom_label(rel)


def _custom_label(rel: Relation) -> str:
    if rel.label and str(rel.label).strip():
        return str(rel.label).strip().lower()
    return humanize(rel.name).lower()


def _inverse_pairs(rels: list[Relation]) -> set[int]:
    """Indices of relations that only restate another relation in reverse."""
    drop: set[int] = set()
    by_name = {norm_key(r.name): i for i, r in enumerate(rels)}
    for i, r in enumerate(rels):
        if i in drop:
            continue
        inv = _first(r.raw, "INVERSE_REL_NAME")
        j = by_name.get(norm_key(str(inv))) if inv else None
        if j is None or j == i or j in drop:
            continue
        other = rels[j]
        if (other.src_key, other.dst_key) != (r.dst_key, r.src_key):
            continue
        # Keep the forward one: drop the row explicitly marked as the inverse, else the later row.
        def is_inverse_row(x: Relation) -> bool:
            return str(_first(x.raw, "RENDER_HINT", default="")).lower() == "inverse" or x.description.lower().startswith("inverse")
        loser = j if is_inverse_row(other) or not is_inverse_row(r) else i
        drop.add(loser)
    return drop


def build_export(
    snapshot: dict,
    domain: str,
    ontology: str | None = None,
    include_shared_properties: bool = False,
    rel_type_overrides: dict[str, str] | None = None,
) -> dict:
    overrides = {k.lower(): v.upper() for k, v in (rel_type_overrides or {}).items()}
    for t in overrides.values():
        if t not in BON_RELATIONSHIP_TYPES:
            raise ValueError(f"--rel-type-override uses invalid BON type {t!r}")

    tables = snapshot["tables"]
    variant = detect_variant(tables)
    ont_rows = _rows(tables, "ONT_ONTOLOGY", None)
    if ontology is None and len(ont_rows) == 1:
        ontology = ont_rows[0].get("ONTOLOGY_NAME")
    ontology_label = ontology or snapshot["schema"]
    ont_row = next((r for r in ont_rows if not ontology or str(r.get("ONTOLOGY_NAME", "")).upper() == str(ontology).upper()), {})

    classes = load_classes(snapshot, ontology)
    resolve_bindings(snapshot, classes, ontology)
    rels = load_relations(snapshot, classes, ontology)

    prev = {
        (str(r.get("SOURCE_KIND")), str(r.get("SOURCE_KEY"))): r
        for r in tables.get("ONT_BON_MIGRATION_MAP", {}).get("rows", []) or []
    }

    candidates: list[dict] = []
    relationships: list[dict] = []
    associations: list[dict] = []
    objects: list[dict] = []  # manifest entries, one per source object
    loss: dict[str, list[str]] = defaultdict(list)

    def record(kind: str, key: str, status: str, bon: dict | None, raw: Any, note: str = "", payload: Any = None) -> dict:
        cs = checksum(payload if payload is not None else raw)
        old = prev.get((kind, key))
        if status != "IN_SCOPE":
            action = "none"
        elif old is None or str(old.get("STATUS")) in ("MISSING", "NOT_MIGRATED", "None", ""):
            action = "create"
        elif old.get("SOURCE_CHECKSUM") == cs:
            action = "skip"
        else:
            action = "update"
        entry = {"source_kind": kind, "source_key": key, "status": status, "bon": bon,
                 "checksum": cs, "action": action, "note": note, "source_row": raw}
        if old is not None and action == "update":
            entry["previous_checksum"] = old.get("SOURCE_CHECKSUM")
        objects.append(entry)
        return entry

    # ---- classes -> ENTITY nodes
    migrated: dict[str, Klass] = {}
    children: dict[str, list[str]] = defaultdict(list)
    for k in classes.values():
        if k.parent_key:
            children[k.parent_key].append(k.key)
    used_names: dict[str, str] = {}
    name_of: dict[str, str] = {}
    for k in classes.values():
        if norm_key(k.class_name) in ALWAYS_SKIP_CLASSES:
            record("CLASS", k.key, "NOT_MIGRATED", None, k.raw, "root class Thing carries no business meaning")
            loss["Skipped classes"].append(f"`{k.class_name}`: root class")
            continue
        name = k.node_name
        if norm_key(name) in used_names:
            name = f"{name} ({k.class_name})"
        used_names[norm_key(name)] = k.key
        flags = list(k.flags)
        if is_generic_description(k.description, k):
            flags.append("needs_description")
        if not k.parent_key and k.is_abstract and norm_key(k.class_name) in GENERIC_ROOT_NAMES:
            flags.append("generic_root")
        if k.binding is None:
            flags.append("unbound")
        # Evidence: own binding, or the bindings of descendants for abstract classes.
        sources: list[str] = []
        stack = [k.key]
        seen = set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            b = classes[cur].binding if cur in classes else None
            if b and b["fqn"] not in sources:
                sources.append(b["fqn"])
            stack.extend(children.get(cur, []))
        synonyms = [k.class_name] if k.class_name.lower() != name.lower() else []
        desc = cap_description(k.description) if k.description.strip() else ""
        cand = {
            "name": name,
            "itemKind": "ENTITY",
            "description": desc,
            "domainName": domain,
            "domainSource": "USER_DECLARED",
            "realisations": len(sources),
            "realisation_sources": sources,
            "definition": None,
            "definition_kind": "NONE",
            "definitions_observed": [],
            "descriptions_observed": [k.description] if k.description else [],
            "source_path": "FILE",
            "synonyms": synonyms,
            "tags": [],
            "formula": None,
            "formulaSource": None,
            "exclusions": [],
        }
        candidates.append(cand)
        migrated[k.key] = k
        name_of[k.key] = name
        record("CLASS", k.key, "IN_SCOPE", {"name": name, "itemKind": "ENTITY"}, k.raw,
               ",".join(flags), payload={"name": name, "description": desc, "synonyms": synonyms})
        objects[-1]["flags"] = flags
        k.flags = flags


    # ---- hierarchy -> HAS_VARIANT (parent -> child)
    for k in migrated.values():
        if not k.parent_key:
            continue
        parent = classes.get(k.parent_key)
        if parent is None or k.parent_key not in migrated:
            reason = "parent is a skipped root" if parent is not None else f"parent {k.parent_key} not found"
            record("SUBCLASS", f"{k.parent_key}>{k.key}", "NOT_MIGRATED", None,
                   {"parent": k.parent_key, "child": k.key}, reason)
            if parent is None:
                loss["Hierarchy edges not migrated"].append(f"`{k.class_name}` -> parent `{k.parent_key}`: {reason}")
            continue
        edge = {"source": name_of[k.parent_key], "target": name_of[k.key], "type": "HAS_VARIANT", "label": None}
        relationships.append(edge)
        record("SUBCLASS", f"{k.parent_key}>{k.key}", "IN_SCOPE", edge, {"parent": k.parent_key, "child": k.key})

    # ---- relations
    inverse_drop = _inverse_pairs(rels)
    seen_edges: set[tuple] = {(e["source"], e["target"], e["type"], e["label"]) for e in relationships}
    for i, rel in enumerate(rels):
        raw = rel.raw
        lost_attrs = [f"{a.lower()}={raw[a]}" for a in ("CARDINALITY", "INVERSE_REL_NAME", "IS_TRANSITIVE", "SOURCE_KEY", "TARGET_KEY")
                      if raw.get(a) not in (None, "", False)]
        if i in inverse_drop:
            record("RELATION", rel.name, "NOT_MIGRATED", None, raw, f"inverse of {raw.get('INVERSE_REL_NAME')}; BON edges are one-directional")
            loss["Inverse relations folded into their forward relation"].append(f"`{rel.name}` (inverse of `{raw.get('INVERSE_REL_NAME')}`)")
            continue
        if rel.src_key not in migrated or rel.dst_key not in migrated:
            record("RELATION", rel.name, "NOT_MIGRATED", None, raw, "an endpoint class is not migrated")
            loss["Relations without both endpoints"].append(
                f"`{rel.name}`: {_first(raw, 'DOMAIN_CLASS', 'SOURCE_CLASS_ID')} -> {_first(raw, 'RANGE_CLASS', 'TARGET_CLASS_ID')}")
            continue
        rtype, rev, label = choose_rel_type(rel, overrides)
        if rtype is None:
            record("RELATION", rel.name, "NOT_MIGRATED", None, raw, "hierarchy relation; carried by HAS_VARIANT edges")
            continue
        if rel.src_key == rel.dst_key:
            # BON rejects edges whose source and target are the same node.
            record("RELATION", rel.name, "NOT_MIGRATED", None, raw, "self-referential; BON requires distinct endpoints")
            loss["Self-referential relations (BON requires distinct endpoints)"].append(
                f"`{rel.name}` on `{migrated[rel.src_key].class_name}`: {rel.description or 'no description'}")
            continue
        src, dst = (rel.dst_key, rel.src_key) if rev else (rel.src_key, rel.dst_key)
        edge = {"source": name_of[src], "target": name_of[dst], "type": rtype, "label": label}
        if (edge["source"], edge["target"], rtype, label) in seen_edges:
            record("RELATION", rel.name, "NOT_MIGRATED", None, raw, "duplicate of an edge already exported")
            continue
        seen_edges.add((edge["source"], edge["target"], rtype, label))
        relationships.append(edge)
        note = []
        if _truthy(raw.get("IS_HIERARCHICAL")) and rtype != "HAS_PART":
            note.append("source flagged IS_HIERARCHICAL; review type")
        if rtype == "CUSTOM":
            note.append("CUSTOM: confirm at review gate")
        record("RELATION", rel.name, "IN_SCOPE", edge, raw, "; ".join(note))
        if lost_attrs:
            loss["Relation attributes BON cannot store"].append(f"`{rel.name}`: " + ", ".join(lost_attrs))

    # ---- class bindings -> TABLE/VIEW representations
    for k in migrated.values():
        b = k.binding
        if not b:
            missing = [f.split(":", 1)[1] for f in k.flags if f.startswith("binding_not_found:")]
            if missing:
                loss["Class bindings pointing at objects that no longer exist (fix the source, then re-run)"].append(
                    f"`{k.class_name}` -> `{missing[0]}`")
            elif not k.is_abstract:
                loss["Classes with no Snowflake object to bind"].append(f"`{k.class_name}`")
            continue
        obj_name = b["fqn"].split(".")[-1]
        ref = {"refType": b["refType"], "fqn": b["fqn"], "objectName": obj_name}
        a = {"termName": name_of[k.key], "assetRef": ref, "role": "DESCRIBES"}
        associations.append(a)
        record("BINDING", f"{k.key}@{b['fqn']}", "IN_SCOPE", a, {"class": k.key, "fqn": b["fqn"]})

    # ---- properties -> COLUMN representations
    props_by_class: dict[str, list[dict]] = defaultdict(list)
    for p in _rows(tables, "ONT_PROPERTY", None):
        ckey = _first(p, "CLASS_ID", "CLASS_NAME")
        if ckey is None:
            continue
        ckey = str(ckey)
        if ckey not in classes:
            ckey = next((kk for kk, kv in classes.items() if norm_key(kv.class_name) == norm_key(ckey)), ckey)
        props_by_class[ckey].append(p)
    for ckey, props in props_by_class.items():
        k = migrated.get(ckey)
        for p in props:
            pname = str(_first(p, "PROP_NAME", "PROPERTY_NAME", default=""))
            pkey = f"{ckey}.{pname}"
            if k is None:
                record("PROPERTY", pkey, "NOT_MIGRATED", None, p, "class not migrated")
                continue
            if not k.binding:
                record("PROPERTY", pkey, "NOT_MIGRATED", None, p, "class has no bound object")
                loss["Properties on unbound classes"].append(f"`{k.class_name}.{pname}`")
                continue
            col = str(_first(p, "SOURCE_COLUMN", default=pname)).upper()
            if col in KG_PLUMBING_COLUMNS and b_is_kg_view(k.binding, snapshot):
                record("PROPERTY", pkey, "NOT_MIGRATED", None, p, "KG plumbing column")
                continue
            if k.binding["columns"] and col not in k.binding["columns"]:
                record("PROPERTY", pkey, "NOT_MIGRATED", None, p, f"column {col} not in {k.binding['fqn']}")
                loss["Properties whose column was not found"].append(f"`{k.class_name}.{pname}` -> `{k.binding['fqn']}.{col}`")
                continue
            ref = {"refType": "COLUMN", "fqn": k.binding["fqn"], "dimensionName": col}
            a = {"termName": name_of[ckey], "assetRef": ref, "role": "DESCRIBES"}
            associations.append(a)
            record("PROPERTY", pkey, "IN_SCOPE", a, p)

    # ---- derived properties -> METRIC / TERM
    for d in _rows(tables, "ONT_DERIVED_PROPERTY", None):
        expr = str(_first(d, "SQL_EXPR", default="") or "").strip()
        ckey = str(_first(d, "CLASS_NAME", default=""))
        k = next((kv for kv in migrated.values() if norm_key(kv.class_name) == norm_key(ckey)), None)
        pname = str(_first(d, "PROP_NAME", default=""))
        dkey = f"{ckey}.{pname}"
        if not expr or k is None:
            record("DERIVED", dkey, "NOT_MIGRATED", None, d, "no SQL_EXPR or class not migrated")
            if not expr:
                loss["Derived properties without SQL"].append(f"`{dkey}` ({_first(d, 'FUNCTION_NAME', default='')})")
            continue
        # A derived property is a per-instance attribute unless its expression is a plain
        # aggregate. Correlated subqueries (SELECT MIN(...) FROM KG_EDGE WHERE ...) are not metrics.
        is_metric = bool(AGGREGATE_RE.search(expr)) and not re.search(r"\bSELECT\b", expr, re.I)
        prop = humanize(pname)
        # Class-qualify: three classes deriving "age" must not collide on one node name.
        name = prop if norm_key(prop).startswith(norm_key(k.node_name)) else f"{k.node_name} {prop}"
        if norm_key(name) in used_names:
            record("DERIVED", dkey, "NOT_MIGRATED", None, d, f"name collides with existing node {name!r}")
            loss["Derived properties not migrated"].append(f"`{dkey}`: name collides with `{name}`")
            continue
        used_names[norm_key(name)] = dkey
        desc = cap_description(str(_first(d, "DESCRIPTION", default="") or "") or f"Derived {prop} of {k.node_name}.")
        cand = {
            "name": name, "itemKind": "METRIC" if is_metric else "TERM", "description": desc if is_metric else cap_description(f"{desc} Expression: {expr}"),
            "domainName": domain, "domainSource": "USER_DECLARED", "realisations": 1,
            "realisation_sources": [k.binding["fqn"]] if k.binding else [], "definition": expr,
            "definition_kind": "NONE", "definitions_observed": [], "descriptions_observed": [], "source_path": "FILE",
            "synonyms": [prop] if prop != name else [], "tags": [], "formula": expr if is_metric else None,
            "formulaSource": (k.binding["fqn"] if (is_metric and k.binding) else None), "exclusions": [],
        }
        candidates.append(cand)
        record("DERIVED", dkey, "IN_SCOPE", {"name": name, "itemKind": cand["itemKind"]}, d,
               payload={"row": d, "name": name, "synonyms": cand["synonyms"]})
        if is_metric:
            relationships.append({"source": name, "target": name_of[k.key], "type": "MEASURES", "label": None})
        else:
            relationships.append({"source": name_of[k.key], "target": name, "type": "RELATED_TO", "label": None})

    # ---- shared properties (opt-in) -> DIMENSION_CONCEPT + CLASSIFIES
    shared_rows = _rows(tables, "ONT_SHARED_PROPERTY", None)
    if include_shared_properties:
        for s in shared_rows:
            sname = str(_first(s, "SHARED_PROP_NAME", default=""))
            impl = [ck for ck, ps in props_by_class.items() if ck in migrated
                    and any(norm_key(str(_first(p, "SHARED_PROP_NAME", default=""))) == norm_key(sname) for p in ps)]
            if len(impl) < 2:
                record("SHARED", sname, "NOT_MIGRATED", None, s, "used by fewer than 2 migrated classes")
                continue
            name = humanize(sname)
            cand = {
                "name": name, "itemKind": "DIMENSION_CONCEPT",
                "description": cap_description(str(_first(s, "DESCRIPTION", default="") or "")),
                "domainName": domain, "domainSource": "USER_DECLARED", "realisations": len(impl),
                "realisation_sources": [migrated[c].binding["fqn"] for c in impl if migrated[c].binding],
                "definition": None, "definition_kind": "NONE", "definitions_observed": [], "descriptions_observed": [],
                "source_path": "FILE", "synonyms": [], "tags": [], "formula": None, "formulaSource": None, "exclusions": [],
            }
            candidates.append(cand)
            record("SHARED", sname, "IN_SCOPE", {"name": name, "itemKind": "DIMENSION_CONCEPT"}, s)
            for c in impl:
                relationships.append({"source": name, "target": name_of[c], "type": "CLASSIFIES", "label": None})
    elif shared_rows:
        loss["Shared properties not migrated (use --include-shared-properties)"].append(
            ", ".join(f"`{_first(s, 'SHARED_PROP_NAME')}`" for s in shared_rows))

    # ---- tables with no BON home
    for t, why in LOSS_TABLES.items():
        if t in tables:
            n = tables[t].get("row_count", len(tables[t].get("rows", []) or []))
            loss["Tables with no BON equivalent (kept in place)"].append(f"`{t}` ({n} rows): {why}")

    for e in relationships:
        if e["type"] not in BON_RELATIONSHIP_TYPES:
            raise AssertionError(f"internal error: invalid relationship type {e['type']}")
        if (e["type"] == "CUSTOM") != bool(e["label"]):
            raise AssertionError(f"internal error: label rule violated on {e}")

    manifest = {
        "contract": "ontology-stack-builder/bon-migration v1",
        "database": snapshot["database"],
        "schema": snapshot["schema"],
        "ontology": ontology_label,
        "domain": domain,
        "domain_description": cap_description(str(ont_row.get("DESCRIPTION") or "")),
        "variant": variant,
        "counts": {
            "nodes": len(candidates),
            "relationships": len(relationships),
            "relationships_by_type": dict(sorted(_count(e["type"] for e in relationships).items())),
            "representations": len(associations),
            "column_representations": sum(1 for a in associations if a["assetRef"]["refType"] == "COLUMN"),
            "not_migrated": sum(1 for o in objects if o["status"] == "NOT_MIGRATED"),
            "actions": dict(_count(o["action"] for o in objects)),
        },
        "review": {
            "needs_description": [o["bon"]["name"] for o in objects if o["source_kind"] == "CLASS" and "needs_description" in o.get("flags", [])],
            "generic_root": [o["bon"]["name"] for o in objects if o["source_kind"] == "CLASS" and "generic_root" in o.get("flags", [])],
            "custom_relationships": [{"relation": o["source_key"], **o["bon"]} for o in objects if o["source_kind"] == "RELATION" and o["bon"] and o["bon"]["type"] == "CUSTOM"],
            "one_entity_per_table": _one_entity_per_table(migrated),
            "broken_bindings": {k.node_name: f.split(":", 1)[1] for k in migrated.values()
                                for f in k.flags if f.startswith("binding_not_found:")},
            "updates": [{"source_kind": o["source_kind"], "source_key": o["source_key"], "bon": o["bon"]} for o in objects if o["action"] == "update"],
        },
        "objects": objects,
    }
    return {
        "candidates": candidates,
        "relationships": relationships,
        "associations": associations,
        "manifest": manifest,
        "loss_report": render_loss_report(manifest, loss),
    }


def b_is_kg_view(binding: dict, snapshot: dict) -> bool:
    db, sc = snapshot["database"], snapshot["schema"]
    return binding["fqn"].startswith(f"{db}.{sc}.V_")


def _count(it) -> dict:
    out: dict = defaultdict(int)
    for x in it:
        out[x] += 1
    return dict(out)


def _one_entity_per_table(migrated: dict[str, Klass]) -> bool:
    bound = [k for k in migrated.values() if not k.is_abstract]
    return bool(bound) and all(k.binding for k in bound) and len({k.binding["fqn"] for k in bound}) == len(bound) \
        and not any(k.parent_key in migrated for k in bound)


def render_loss_report(manifest: dict, loss: dict[str, list[str]]) -> str:
    c = manifest["counts"]
    lines = [
        f"# Migration loss report: {manifest['ontology']} -> {manifest['domain']}",
        "",
        f"Source: `{manifest['database']}.{manifest['schema']}` (variant `{manifest['variant']}`)",
        "",
        "| Migrated | Count |",
        "|---|---|",
        f"| Nodes | {c['nodes']} |",
        f"| Relationships | {c['relationships']} |",
        f"| Representations (object + column) | {c['representations']} |",
        f"| Source objects not migrated | {c['not_migrated']} |",
        "",
        "Every source row is kept in `ONT_BON_MIGRATION_MAP.SOURCE_ROW` after reconcile, so nothing below is lost from Snowflake; it just has no BON home.",
        "",
    ]
    if not loss:
        lines.append("Nothing was dropped.")
    for section, items in loss.items():
        lines.append(f"## {section}")
        lines.append("")
        lines.extend(f"- {i}" for i in items)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- Snowflake loader

def load_snapshot(conn, database: str, schema: str) -> dict:
    db, sc = check_identifier(database, "database"), check_identifier(schema, "schema")
    cur = conn.cursor()
    cur.execute(
        f"SELECT TABLE_NAME, TABLE_TYPE, ROW_COUNT FROM {db}.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = %s",
        (sc,),
    )
    present = {r[0].upper(): (r[1], r[2]) for r in cur.fetchall()}
    objects = {f"{db}.{sc}.{n}": t for n, (t, _) in present.items()}
    tables: dict[str, dict] = {}
    for t in SOURCE_TABLES:
        if t not in present:
            continue
        cur.execute(f"SELECT COLUMN_NAME FROM {db}.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION", (sc, t))
        cols = [r[0] for r in cur.fetchall()]
        cur.execute(f"SELECT OBJECT_CONSTRUCT_KEEP_NULL(*)::STRING FROM {db}.{sc}.{t}")
        rows = [json.loads(r[0]) for r in cur.fetchall()]
        tables[t] = {"columns": cols, "rows": rows}
    for t in LOSS_TABLES:
        if t in present:
            tables[t] = {"columns": [], "rows": [], "row_count": present[t][1]}

    snapshot = {"database": db, "schema": sc, "tables": tables, "objects": objects, "object_columns": {}}

    # Catalog for every object a class may bind to (own schema + external source tables).
    targets = {(db, sc)}
    for m in tables.get("ONT_CLASS_MAP", {}).get("rows", []):
        if m.get("SOURCE_DATABASE") and m.get("SOURCE_SCHEMA"):
            targets.add((str(m["SOURCE_DATABASE"]).upper(), str(m["SOURCE_SCHEMA"]).upper()))
    for c in tables.get("ONT_CLASS", {}).get("rows", []):
        src = c.get("SOURCE_TABLE")
        if src and str(src).count(".") == 2:
            d, s, _ = str(src).upper().split(".")
            targets.add((d, s))
    for s in tables.get("ONT_OBJECT_SOURCE", {}).get("rows", []):
        ref = s.get("SOURCE_REF")
        if ref and str(ref).count(".") == 2:
            d, s2, _ = str(ref).upper().split(".")
            targets.add((d, s2))
    for d, s in sorted(targets):
        try:
            d, s = check_identifier(d, "database"), check_identifier(s, "schema")
            cur.execute(f"SELECT TABLE_NAME, TABLE_TYPE FROM {d}.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = %s", (s,))
            for name, ttype in cur.fetchall():
                snapshot["objects"][f"{d}.{s}.{name.upper()}"] = ttype
            cur.execute(f"SELECT TABLE_NAME, COLUMN_NAME FROM {d}.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, ORDINAL_POSITION", (s,))
            for name, col in cur.fetchall():
                snapshot["object_columns"].setdefault(f"{d}.{s}.{name.upper()}", []).append(col.upper())
        except Exception as exc:  # inaccessible source schema: bindings there fall back to TABLE without column checks
            print(f"warning: cannot read catalog of {d}.{s}: {exc}", file=sys.stderr)
    return snapshot


# --------------------------------------------------------------------------- CLI

def write_outputs(result: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "candidates.json").write_text(json.dumps(result["candidates"], indent=2, ensure_ascii=False))
    (out / "relationships.json").write_text(json.dumps(result["relationships"], indent=2, ensure_ascii=False))
    (out / "associations.json").write_text(json.dumps(result["associations"], indent=2, ensure_ascii=False))
    (out / "export_manifest.json").write_text(json.dumps(result["manifest"], indent=2, ensure_ascii=False, default=str))
    (out / "migration_loss_report.md").write_text(result["loss_report"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--connection", "-c")
    ap.add_argument("--database")
    ap.add_argument("--schema")
    ap.add_argument("--snapshot", help="read a snapshot JSON instead of Snowflake")
    ap.add_argument("--dump-snapshot", help="also write the captured snapshot to this path")
    ap.add_argument("--domain", required=True, help="target BON domain, named by the user")
    ap.add_argument("--ontology", help="ONTOLOGY_NAME to export when ONT_ONTOLOGY holds several")
    ap.add_argument("--include-shared-properties", action="store_true")
    ap.add_argument("--rel-type-override", action="append", default=[], metavar="REL_NAME=TYPE")
    ap.add_argument("--out", default="/tmp/bon_migration")
    args = ap.parse_args(argv)

    if args.snapshot:
        snapshot = json.loads(Path(args.snapshot).read_text())
    else:
        if not (args.connection and args.database and args.schema):
            ap.error("--connection, --database and --schema are required unless --snapshot is given")
        from bon_migration_common import connect
        with connect(args.connection) as conn:
            snapshot = load_snapshot(conn, args.database, args.schema)
    if "ONT_CLASS" not in snapshot["tables"]:
        print(f"error: {snapshot['database']}.{snapshot['schema']} has no ONT_CLASS table; not an ontology stack", file=sys.stderr)
        return 2
    if args.dump_snapshot:
        Path(args.dump_snapshot).parent.mkdir(parents=True, exist_ok=True)
        Path(args.dump_snapshot).write_text(json.dumps(snapshot, indent=2, default=str))

    overrides = {}
    for o in args.rel_type_override:
        if "=" not in o:
            ap.error(f"bad --rel-type-override {o!r}; expected REL_NAME=TYPE")
        k, v = o.split("=", 1)
        overrides[k.strip()] = v.strip()

    result = build_export(snapshot, args.domain, args.ontology, args.include_shared_properties, overrides)
    write_outputs(result, Path(args.out))
    m = result["manifest"]
    print(json.dumps({"variant": m["variant"], "ontology": m["ontology"], "domain": m["domain"],
                      "counts": m["counts"], "review": {k: v for k, v in m["review"].items() if k != "updates"},
                      "out": args.out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
