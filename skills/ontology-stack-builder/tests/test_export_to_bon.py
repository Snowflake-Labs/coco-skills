"""Tests for the Business Ontology migration path (export_to_bon / bon_reconcile).

Fixtures are small synthetic snapshots, one per schema variant seen in real
deployments. No Snowflake connection is needed.

Run:  uv run --extra dev pytest tests/
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from bon_migration_common import BON_RELATIONSHIP_TYPES, MAX_DESCRIPTION, asset_key, humanize  # noqa: E402
from bon_reconcile import reconcile  # noqa: E402
from export_to_bon import build_export, detect_variant  # noqa: E402

DB, SC = "DEMO_DB", "ONT"


def _t(columns, rows, **extra):
    return {"columns": columns, "rows": rows, **extra}


def generator_snapshot() -> dict:
    """scripts/generate_ontology_sql.py output, KG path, plus one direct-path class."""
    cls_cols = ["CLASS_NAME", "PARENT_CLASS_NAME", "IS_ABSTRACT", "DESCRIPTION", "ONTOLOGY_NAME", "TYPE_CLASS", "CREATED_AT"]
    return {
        "database": DB, "schema": SC,
        "tables": {
            "ONT_ONTOLOGY": _t(["ONTOLOGY_NAME", "DESCRIPTION"], [{"ONTOLOGY_NAME": "STOCK", "DESCRIPTION": "Stock ontology"}]),
            "ONT_CLASS": _t(cls_cols, [
                {"CLASS_NAME": "Thing", "IS_ABSTRACT": True, "DESCRIPTION": "Root", "ONTOLOGY_NAME": "STOCK"},
                {"CLASS_NAME": "MarketParticipant", "IS_ABSTRACT": True, "DESCRIPTION": "Abstract participant", "ONTOLOGY_NAME": "STOCK"},
                {"CLASS_NAME": "Company", "PARENT_CLASS_NAME": "MarketParticipant", "IS_ABSTRACT": False,
                 "DESCRIPTION": "A publicly traded company", "ONTOLOGY_NAME": "STOCK"},
                {"CLASS_NAME": "Exchange", "PARENT_CLASS_NAME": "MarketParticipant", "IS_ABSTRACT": False,
                 "DESCRIPTION": "Entity from table EXCHANGES", "ONTOLOGY_NAME": "STOCK"},
                {"CLASS_NAME": "Dow30Component", "PARENT_CLASS_NAME": "Thing", "IS_ABSTRACT": False,
                 "DESCRIPTION": "x" * 400, "ONTOLOGY_NAME": "STOCK"},
            ]),
            "ONT_RELATION_DEF": _t(["REL_NAME", "DOMAIN_CLASS", "RANGE_CLASS", "CARDINALITY", "IS_HIERARCHICAL", "IS_TRANSITIVE", "INVERSE_REL_NAME"], [
                {"REL_NAME": "listed_on", "DOMAIN_CLASS": "Company", "RANGE_CLASS": "Exchange", "CARDINALITY": "N:1", "INVERSE_REL_NAME": "hosts_listing"},
                {"REL_NAME": "related_to", "DOMAIN_CLASS": "Company", "RANGE_CLASS": "Company", "IS_HIERARCHICAL": True, "IS_TRANSITIVE": True},
                {"REL_NAME": "part_of", "DOMAIN_CLASS": "Dow30Component", "RANGE_CLASS": "Exchange"},
                {"REL_NAME": "subClassOf", "DOMAIN_CLASS": "Company", "RANGE_CLASS": "MarketParticipant"},
                {"REL_NAME": "owns", "DOMAIN_CLASS": "Company", "RANGE_CLASS": "Thing"},
            ]),
            "ONT_CLASS_MAP": _t(["CLASS_NAME", "SOURCE_DATABASE", "SOURCE_SCHEMA", "SOURCE_TABLE", "ID_EXPR"], [
                {"CLASS_NAME": "Company", "SOURCE_DATABASE": DB, "SOURCE_SCHEMA": SC, "SOURCE_TABLE": "KG_NODE", "ID_EXPR": "NODE_ID"},
                {"CLASS_NAME": "Exchange", "SOURCE_DATABASE": "RAW", "SOURCE_SCHEMA": "MKT", "SOURCE_TABLE": "EXCHANGES", "ID_EXPR": "ID"},
            ]),
            "ONT_PROPERTY": _t(["CLASS_NAME", "PROP_NAME", "SHARED_PROP_NAME"], [
                {"CLASS_NAME": "Company", "PROP_NAME": "ticker", "SHARED_PROP_NAME": "name"},
                {"CLASS_NAME": "Company", "PROP_NAME": "name", "SHARED_PROP_NAME": "name"},  # display name: a real property
                {"CLASS_NAME": "Company", "PROP_NAME": "props"},  # KG plumbing column on V_ view
                {"CLASS_NAME": "Company", "PROP_NAME": "ghost_col"},
                {"CLASS_NAME": "Exchange", "PROP_NAME": "exchange_name", "SHARED_PROP_NAME": "name"},
            ]),
            "ONT_SHARED_PROPERTY": _t(["SHARED_PROP_NAME", "DESCRIPTION"], [{"SHARED_PROP_NAME": "name", "DESCRIPTION": "Display name"}]),
            "ONT_DERIVED_PROPERTY": _t(["CLASS_NAME", "PROP_NAME", "SQL_EXPR", "DESCRIPTION"], [
                {"CLASS_NAME": "Company", "PROP_NAME": "listing_count", "SQL_EXPR": "COUNT(DISTINCT exchange_id)", "DESCRIPTION": "Exchanges listed on"},
                {"CLASS_NAME": "Company", "PROP_NAME": "age", "SQL_EXPR": "DATEDIFF(YEAR, (SELECT MIN(d) FROM KG_EDGE), CURRENT_DATE())"},
                {"CLASS_NAME": "Exchange", "PROP_NAME": "age", "SQL_EXPR": "YEAR(CURRENT_DATE()) - founded"},
            ]),
            "KG_NODE": _t([], [], row_count=100),
            "ONT_RULE": _t([], [], row_count=2),
        },
        "objects": {
            f"{DB}.{SC}.V_COMPANY": "VIEW", f"{DB}.{SC}.V_DOW30COMPONENT": "VIEW", "RAW.MKT.EXCHANGES": "BASE TABLE",
        },
        "object_columns": {
            f"{DB}.{SC}.V_COMPANY": ["NODE_ID", "NAME", "TICKER", "PROPS"],
            "RAW.MKT.EXCHANGES": ["ID", "EXCHANGE_NAME"],
            f"{DB}.{SC}.V_DOW30COMPONENT": ["NODE_ID", "NAME"],
        },
    }


def reference_snapshot() -> dict:
    """ontology-on-snowflake reference repo: CONCRETE_VIEW mappings, explicit inverse rows."""
    return {
        "database": DB, "schema": SC,
        "tables": {
            "ONT_ONTOLOGY": _t(["ONTOLOGY_NAME"], [{"ONTOLOGY_NAME": "SOCCER_V1"}]),
            "ONT_CLASS": _t(["CLASS_NAME", "PARENT_CLASS_NAME", "IS_ABSTRACT", "DESCRIPTION", "STATUS"], [
                {"CLASS_NAME": "Entity", "IS_ABSTRACT": True, "DESCRIPTION": "Top-level abstract entity", "STATUS": "ACTIVE"},
                {"CLASS_NAME": "Person", "PARENT_CLASS_NAME": "Entity", "IS_ABSTRACT": True, "DESCRIPTION": "Any person", "STATUS": "ACTIVE"},
                {"CLASS_NAME": "Player", "PARENT_CLASS_NAME": "Person", "IS_ABSTRACT": False, "DESCRIPTION": "Soccer player", "STATUS": "ACTIVE"},
                {"CLASS_NAME": "Club", "PARENT_CLASS_NAME": "Entity", "IS_ABSTRACT": False, "DESCRIPTION": "Soccer club", "STATUS": "ACTIVE"},
                {"CLASS_NAME": "Retired", "IS_ABSTRACT": False, "DESCRIPTION": "Old class", "STATUS": "DEPRECATED"},
            ]),
            "ONT_RELATION_DEF": _t(["REL_NAME", "DOMAIN_CLASS", "RANGE_CLASS", "INVERSE_REL_NAME", "RENDER_HINT", "STATUS"], [
                {"REL_NAME": "plays_for", "DOMAIN_CLASS": "Player", "RANGE_CLASS": "Club", "INVERSE_REL_NAME": "has_player", "RENDER_HINT": "directed", "STATUS": "ACTIVE"},
                {"REL_NAME": "has_player", "DOMAIN_CLASS": "Club", "RANGE_CLASS": "Player", "INVERSE_REL_NAME": "plays_for", "RENDER_HINT": "inverse", "STATUS": "ACTIVE"},
            ]),
            "ONT_CLASS_MAP": _t(["CLASS_NAME", "CONCRETE_VIEW", "SUBTYPE_LABEL", "COLS"], [
                {"CLASS_NAME": "Person", "CONCRETE_VIEW": "V_PLAYER", "SUBTYPE_LABEL": "Player", "COLS": ["NAME"]},
                {"CLASS_NAME": "Player", "CONCRETE_VIEW": "V_PLAYER", "SUBTYPE_LABEL": "Player", "COLS": ["NAME", "POSITION"]},
                {"CLASS_NAME": "Club", "CONCRETE_VIEW": "V_CLUB", "SUBTYPE_LABEL": "Club", "COLS": ["NAME"]},
            ]),
            "ONT_PROPERTY": _t(["CLASS_NAME", "PROP_NAME"], [{"CLASS_NAME": "Player", "PROP_NAME": "position"}]),
        },
        "objects": {f"{DB}.{SC}.V_PLAYER": "VIEW", f"{DB}.{SC}.V_CLUB": "VIEW"},
        "object_columns": {},
    }


def id_keyed_snapshot() -> dict:
    """Hand-built stacks keyed by CLASS_ID with labels and SOURCE_TABLE on the class row."""
    return {
        "database": DB, "schema": SC,
        "tables": {
            "ONT_CLASS": _t(["CLASS_ID", "CLASS_NAME", "CLASS_LABEL", "DESCRIPTION", "PARENT_CLASS_ID", "SOURCE_TABLE"], [
                {"CLASS_ID": 1, "CLASS_NAME": "Organization", "CLASS_LABEL": "Organization", "DESCRIPTION": "Any organization"},
                {"CLASS_ID": 2, "CLASS_NAME": "Agency", "CLASS_LABEL": "Media Agency", "DESCRIPTION": "An agency", "PARENT_CLASS_ID": 1,
                 "SOURCE_TABLE": "SALES.CRM.AGENCIES"},
                {"CLASS_ID": 3, "CLASS_NAME": "Campaign", "CLASS_LABEL": "Advertising Campaign", "DESCRIPTION": "A campaign",
                 "SOURCE_TABLE": "SALES.CRM.CAMPAIGNS"},
            ]),
            "ONT_RELATION_DEF": _t(["RELATION_ID", "RELATION_NAME", "RELATION_LABEL", "SOURCE_CLASS_ID", "TARGET_CLASS_ID", "CARDINALITY"], [
                {"RELATION_ID": 1, "RELATION_NAME": "executes", "RELATION_LABEL": "Executes Campaign", "SOURCE_CLASS_ID": 2, "TARGET_CLASS_ID": 3,
                 "CARDINALITY": "ONE_TO_MANY"},
            ]),
            "ONT_PROPERTY": _t(["PROPERTY_ID", "CLASS_ID", "PROPERTY_NAME", "SOURCE_COLUMN"], [
                {"PROPERTY_ID": 1, "CLASS_ID": 2, "PROPERTY_NAME": "name", "SOURCE_COLUMN": "AGENCY_NAME"},
            ]),
        },
        "objects": {"SALES.CRM.AGENCIES": "BASE TABLE", "SALES.CRM.CAMPAIGNS": "BASE TABLE"},
        "object_columns": {"SALES.CRM.AGENCIES": ["AGENCY_ID", "AGENCY_NAME"]},
    }


def template_snapshot() -> dict:
    snap = generator_snapshot()
    snap["tables"]["ONT_PROPERTY"] = _t(["CLASS_NAME", "PROPERTY_NAME", "SOURCE_EXPR"], [
        {"CLASS_NAME": "Exchange", "PROPERTY_NAME": "exchange_name"}])
    return snap


# --------------------------------------------------------------------------- helpers

def edges(result) -> set[tuple]:
    return {(e["source"], e["target"], e["type"], e["label"]) for e in result["relationships"]}


def nodes(result) -> dict[str, dict]:
    return {c["name"]: c for c in result["candidates"]}


def refs(result) -> set[tuple]:
    return {(a["termName"], a["assetRef"]["refType"], a["assetRef"]["fqn"], a["assetRef"].get("dimensionName")) for a in result["associations"]}


# --------------------------------------------------------------------------- variants

@pytest.mark.parametrize("snap,variant", [
    (generator_snapshot, "GENERATOR"), (reference_snapshot, "REFERENCE"),
    (id_keyed_snapshot, "ID_KEYED"), (template_snapshot, "TEMPLATE"),
])
def test_detect_variant(snap, variant):
    assert detect_variant(snap()["tables"]) == variant


@pytest.mark.parametrize("snap", [generator_snapshot, reference_snapshot, id_keyed_snapshot, template_snapshot])
def test_contract_invariants(snap):
    r = build_export(snap(), "Test Domain")
    for c in r["candidates"]:
        assert c["domainName"] == "Test Domain" and c["domainSource"] == "USER_DECLARED"
        assert len(c["description"]) <= MAX_DESCRIPTION
        if c["itemKind"] != "METRIC":
            assert c["formula"] is None and c["formulaSource"] is None and not c["exclusions"]
    names = set(nodes(r))
    for e in r["relationships"]:
        assert e["type"] in BON_RELATIONSHIP_TYPES
        assert (e["type"] == "CUSTOM") == bool(e["label"])
        assert e["source"] in names and e["target"] in names
        assert e["source"] != e["target"]
    for a in r["associations"]:
        ref = a["assetRef"]
        assert a["termName"] in names and ref["fqn"].count(".") == 2
        if ref["refType"] == "COLUMN":
            assert set(ref) == {"refType", "fqn", "dimensionName"}
        else:
            assert set(ref) == {"refType", "fqn", "objectName"}


# --------------------------------------------------------------------------- generator variant

def test_generator_classes_and_hierarchy():
    r = build_export(generator_snapshot(), "Stock")
    n = nodes(r)
    assert set(n) == {"Market Participant", "Company", "Exchange", "Dow30 Component", "Company Listing Count", "Company Age", "Exchange Age"}
    assert "Thing" not in n
    assert n["Company"]["synonyms"] == [] and n["Market Participant"]["synonyms"] == ["MarketParticipant"]
    assert n["Dow30 Component"]["description"].endswith("...")
    e = edges(r)
    assert ("Market Participant", "Company", "HAS_VARIANT", None) in e
    assert ("Market Participant", "Exchange", "HAS_VARIANT", None) in e
    assert not any(x[0] == "Thing" or x[1] == "Thing" for x in e)
    # abstract class evidence comes from its descendants' bindings
    assert set(n["Market Participant"]["realisation_sources"]) == {f"{DB}.{SC}.V_COMPANY", "RAW.MKT.EXCHANGES"}


def test_generator_relation_types():
    r = build_export(generator_snapshot(), "Stock")
    e = edges(r)
    assert ("Company", "Exchange", "CUSTOM", "listed on") in e
    assert ("Exchange", "Dow30 Component", "HAS_PART", None) in e  # part_of reversed: whole -> part
    assert not any(x[0] == "Company" and x[1] == "Company" for x in e)  # self edge dropped
    assert not any(x[1] == "Market Participant" and x[2] == "CUSTOM" for x in e)  # subClassOf relation skipped
    statuses = {o["source_key"]: (o["status"], o["note"]) for o in r["manifest"]["objects"] if o["source_kind"] == "RELATION"}
    assert statuses["related_to"][0] == "NOT_MIGRATED" and "self-referential" in statuses["related_to"][1]
    assert statuses["owns"][0] == "NOT_MIGRATED"  # range is the skipped Thing root
    assert "cardinality=N:1" in r["loss_report"] and "inverse_rel_name=hosts_listing" in r["loss_report"]


def test_rel_type_override():
    r = build_export(generator_snapshot(), "Stock", rel_type_overrides={"listed_on": "RELATED_TO"})
    assert ("Company", "Exchange", "RELATED_TO", None) in edges(r)
    with pytest.raises(ValueError):
        build_export(generator_snapshot(), "Stock", rel_type_overrides={"listed_on": "RELATED"})


def test_generator_bindings_and_columns():
    r = build_export(generator_snapshot(), "Stock")
    x = refs(r)
    assert ("Company", "VIEW", f"{DB}.{SC}.V_COMPANY", None) in x  # KG path -> typed view
    assert ("Exchange", "TABLE", "RAW.MKT.EXCHANGES", None) in x  # direct path -> source table
    assert ("Company", "COLUMN", f"{DB}.{SC}.V_COMPANY", "TICKER") in x
    assert ("Exchange", "COLUMN", "RAW.MKT.EXCHANGES", "EXCHANGE_NAME") in x
    assert ("Company", "COLUMN", f"{DB}.{SC}.V_COMPANY", "NAME") in x  # display name kept
    assert not any(d in ("PROPS", "GHOST_COL") for *_, d in x)  # plumbing + missing columns dropped
    assert "ghost_col" in r["loss_report"].lower()


def test_derived_properties():
    r = build_export(generator_snapshot(), "Stock")
    n = nodes(r)
    assert n["Company Listing Count"]["itemKind"] == "METRIC"
    assert n["Company Listing Count"]["formula"] == "COUNT(DISTINCT exchange_id)"
    assert n["Company Age"]["itemKind"] == "TERM"  # correlated subquery is not a metric
    assert n["Exchange Age"]["itemKind"] == "TERM"  # class-qualified: no collision with Company Age
    assert n["Company Age"]["synonyms"] == ["Age"]  # unqualified name stays searchable
    e = edges(r)
    assert ("Company Listing Count", "Company", "MEASURES", None) in e
    assert ("Company", "Company Age", "RELATED_TO", None) in e


def test_shared_properties_opt_in():
    off = build_export(generator_snapshot(), "Stock")
    assert "Name" not in nodes(off)
    assert "--include-shared-properties" in off["loss_report"]
    on = build_export(generator_snapshot(), "Stock", include_shared_properties=True)
    assert nodes(on)["Name"]["itemKind"] == "DIMENSION_CONCEPT"
    assert nodes(on)["Name"]["realisations"] == 2
    assert ("Name", "Company", "CLASSIFIES", None) in edges(on)


def test_review_flags_and_loss_tables():
    r = build_export(generator_snapshot(), "Stock")
    assert r["manifest"]["review"]["needs_description"] == ["Exchange"]
    assert "`KG_NODE` (100 rows)" in r["loss_report"] and "`ONT_RULE` (2 rows)" in r["loss_report"]


# --------------------------------------------------------------------------- reference variant

def test_reference_inverse_folded_and_bindings():
    r = build_export(reference_snapshot(), "Soccer")
    e = edges(r)
    assert ("Player", "Club", "CUSTOM", "plays for") in e
    assert not any(x[0] == "Club" and x[1] == "Player" for x in e)
    assert "has_player" in r["loss_report"]
    x = refs(r)
    assert ("Player", "VIEW", f"{DB}.{SC}.V_PLAYER", None) in x
    assert ("Person", "VIEW", f"{DB}.{SC}.V_PLAYER", None) not in x  # abstract class is not bound to a subclass view
    assert ("Player", "COLUMN", f"{DB}.{SC}.V_PLAYER", "POSITION") in x  # columns from ONT_CLASS_MAP.COLS
    assert "Retired" not in nodes(r)
    assert r["manifest"]["review"]["generic_root"] == ["Entity"]


# --------------------------------------------------------------------------- id-keyed variant

def test_id_keyed_labels_and_sources():
    r = build_export(id_keyed_snapshot(), "Ads")
    n = nodes(r)
    assert set(n) == {"Organization", "Media Agency", "Advertising Campaign"}
    assert n["Media Agency"]["synonyms"] == ["Agency"]
    assert ("Organization", "Media Agency", "HAS_VARIANT", None) in edges(r)
    assert ("Media Agency", "Advertising Campaign", "CUSTOM", "executes campaign") in edges(r)
    assert ("Media Agency", "COLUMN", "SALES.CRM.AGENCIES", "AGENCY_NAME") in refs(r)  # SOURCE_COLUMN wins over property name


# --------------------------------------------------------------------------- delta + reconcile

def _with_map(snap: dict, export: dict) -> dict:
    rows = [{"SOURCE_KIND": o["source_kind"], "SOURCE_KEY": o["source_key"], "STATUS": "MIGRATED",
             "SOURCE_CHECKSUM": o["checksum"]} for o in export["manifest"]["objects"] if o["status"] == "IN_SCOPE"]
    snap["tables"]["ONT_BON_MIGRATION_MAP"] = _t(["SOURCE_KIND", "SOURCE_KEY", "STATUS", "SOURCE_CHECKSUM"], rows)
    return snap


def test_delta_detects_only_changes():
    first = build_export(generator_snapshot(), "Stock")
    assert set(first["manifest"]["counts"]["actions"]) <= {"create", "none"}
    snap = _with_map(generator_snapshot(), first)
    assert set(build_export(snap, "Stock")["manifest"]["counts"]["actions"]) <= {"skip", "none"}
    snap["tables"]["ONT_CLASS"]["rows"][2]["DESCRIPTION"] = "A listed company (changed)"
    again = build_export(snap, "Stock")
    assert again["manifest"]["counts"]["actions"].get("update") == 1
    assert again["manifest"]["review"]["updates"][0]["source_key"] == "Company"


def _state_from(export: dict, drop_edge: tuple | None = None) -> dict:
    """Simulate a BON read-back containing everything that was exported."""
    from bon_migration_common import norm_key
    terms = {norm_key(c["name"]): {"termId": str(i), "name": c["name"]} for i, c in enumerate(export["candidates"])}
    es = {(norm_key(e["source"]), norm_key(e["target"]), e["type"]) for e in export["relationships"]}
    if drop_edge:
        es.discard(drop_edge)
    # read side reports objectType instead of refType
    rs = {asset_key(norm_key(a["termName"]), {**{k: v for k, v in a["assetRef"].items() if k != "refType"},
                                               "objectType": a["assetRef"]["refType"].title()})
          for a in export["associations"]}
    return {"terms": terms, "edges": es, "refs": rs, "source": "test"}


def test_reconcile_parity_and_missing():
    ex = build_export(generator_snapshot(), "Stock")
    ok = reconcile(ex["manifest"], ex["relationships"], ex["associations"], _state_from(ex))
    assert ok["parity"] and not ok["missing"]
    assert {r["STATUS"] for r in ok["rows"]} <= {"MIGRATED", "NOT_MIGRATED"}
    assert all(r["SOURCE_CHECKSUM"] for r in ok["rows"] if r["STATUS"] == "MIGRATED")

    bad = reconcile(ex["manifest"], ex["relationships"], ex["associations"],
                    _state_from(ex, drop_edge=("companylistingcount", "company", "MEASURES")))
    assert not bad["parity"]
    assert bad["totals"]["relationships"][0] == bad["totals"]["relationships"][1] - 1


def test_humanize():
    assert humanize("PurchaseOrder") == "Purchase Order"
    assert humanize("PURCHASE_ORDER") == "Purchase Order"
    assert humanize("Dow30Component") == "Dow30 Component"
    assert humanize("OpenFIGI") == "Open FIGI"
    assert humanize("is_dow30_member") == "Is Dow30 Member"
