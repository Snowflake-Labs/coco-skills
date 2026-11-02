import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import context_store  # noqa: E402
import extract_context  # noqa: E402


def src(connector="confluence", scope="PEOPLE", ref="p1"):
    return {"connector": connector, "scope": scope, "ref": ref, "title": "doc"}


def test_redact_removes_person_details_but_keeps_business_phrases():
    text = "Thanks Priya Raman (priya.raman@x.example, @priya, EMP-12345) for Portfolio Management data"
    out = context_store.redact(text)
    assert "Priya" not in out and "x.example" not in out and "12345" not in out
    assert "Portfolio Management" in out


def test_consent_rejects_forbidden_scopes():
    ctx = {"version": 1, "sources": [], "items": []}
    with pytest.raises(SystemExit):
        context_store.consent(ctx, "slack", ["dm"], "a")
    with pytest.raises(SystemExit):
        context_store.consent(ctx, "email", ["inbox"], "a")
    context_store.consent(ctx, "slack", ["#people-analytics"], "a")
    assert ctx["sources"][0]["risk"] == "high"


def test_ingest_requires_consent_and_citation():
    ctx = {"version": 1, "sources": [], "items": []}
    context_store.consent(ctx, "confluence", ["PEOPLE"], "a")
    items = [
        {"kind": "definition", "term": "regular_headcount", "value": "FT + PT", "source": src()},
        {"kind": "definition", "term": "regular_headcount", "value": "x", "source": src(ref="")},
        {"kind": "definition", "term": "x", "value": "y", "source": src(connector="slack", scope="#x")},
        {"kind": "salary", "term": "x", "value": "y", "source": src()},
    ]
    stats = context_store.ingest(ctx, items)
    assert stats == {"accepted": 1, "rejected_no_consent": 1, "rejected_no_citation": 1,
                     "rejected_kind": 1, "duplicates": 0}
    assert ctx["items"][0]["status"] == "proposed"


def test_structured_conflicts_flagged_definitions_related():
    ctx = {"version": 1, "sources": [], "items": []}
    context_store.consent(ctx, "confluence", ["PEOPLE"], "a")
    context_store.ingest(ctx, [
        {"kind": "fiscal_calendar", "term": "fiscal_calendar", "value": {"fiscal_year_start_month": 1}, "source": src(ref="a")},
        {"kind": "fiscal_calendar", "term": "fiscal_calendar", "value": {"fiscal_year_start_month": 4}, "source": src(ref="b")},
        {"kind": "definition", "term": "regrettable_exit", "value": "flag true", "source": src(ref="c")},
        {"kind": "definition", "term": "regrettable_exit", "value": "use the flag", "source": src(ref="d")},
    ])
    fiscal = [i for i in ctx["items"] if i["kind"] == "fiscal_calendar"]
    assert all(i["conflicts_with"] for i in fiscal)
    defs = [i for i in ctx["items"] if i["kind"] == "definition"]
    assert all(not i["conflicts_with"] and i["related"] for i in defs)


def test_confirmed_item_shadows_proposed_peers():
    ctx = {"version": 1, "sources": [], "items": []}
    context_store.consent(ctx, "confluence", ["PEOPLE"], "a")
    context_store.ingest(ctx, [
        {"kind": "fiscal_calendar", "term": "fiscal_calendar", "value": {"fiscal_year_start_month": 1}, "source": src(ref="a")},
        {"kind": "fiscal_calendar", "term": "fiscal_calendar", "value": {"fiscal_year_start_month": 4}, "source": src(ref="b")},
    ])
    jan = next(i for i in ctx["items"] if i["value"]["fiscal_year_start_month"] == 1)
    context_store.set_status(ctx, jan["id"], "confirmed")
    active = context_store.active_items(ctx, "fiscal_calendar")
    assert [i["id"] for i in active] == [jan["id"]]


def test_extractor_drops_author_fields_and_finds_items(tmp_path):
    (tmp_path / "wiki.md").write_text(
        "Our fiscal year starts in February.\n\n## Departments\n- Trading\n- Operations\n\n"
        "- **Regular headcount**: active Full-Time and Part-Time employees.\n"
        "Regular headcount was 1,200 as of 2026-08-31.\n", encoding="utf-8")
    (tmp_path / "slack.json").write_text(json.dumps(
        [{"channel": "pa", "ts": "1", "user": "U1", "real_name": "Jane Doe", "text": "hello"}]),
        encoding="utf-8")
    vocab = extract_context.load_vocabulary()
    docs = extract_context.documents(tmp_path)
    assert all("Jane Doe" not in body for _r, _t, body in docs)
    items = [i for ref, title, text in docs for i in extract_context.extract_doc(ref, title, text, vocab)]
    kinds = {(i["kind"], json.dumps(i["value"])) for i in items}
    assert ("fiscal_calendar", json.dumps({"fiscal_year_start_month": 2})) in kinds
    assert any(i["kind"] == "org_units" and i["value"] == ["Trading", "Operations"] for i in items)
    assert any(i["kind"] == "reference_number" and i["value"]["value"] == 1200 for i in items)
    assert any(i["kind"] == "definition" and i["term"] == "regular_headcount" for i in items)
