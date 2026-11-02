#!/usr/bin/env python3
"""Business-context store: consent, redaction, citations, conflicts. Stdlib only.

``context.json`` is the single source of business context for v2. Items come
from connected MCP sources (extracted by the agent per subskills/discover_context.md)
or from exported docs (extract_context.py). Every item is:
  * tied to a consented source scope,
  * redacted of person-level details before it is written,
  * cited (connector + ref),
  * ``proposed`` until the analyst confirms it in the wizard.

CLI:
  context_store.py consent  --context ctx.json --connector slack --scope "#people-analytics" --by analyst
  context_store.py ingest   --context ctx.json --items raw_items.json [--org-allowlist index.json]
  context_store.py set-status --context ctx.json --id <item_id> --status confirmed|rejected [--value JSON]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

CATALOG_DIR = Path(__file__).resolve().parents[2] / "catalog"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
MENTION = re.compile(r"(?<![\w])@[\w.-]+")
PERSON_ID = re.compile(r"\b(?:EMP|EID|E|WD|ID)[-_]?\d{3,}\b", re.I)
TITLE_SEQ = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z'-]+){1,2})\b")
BUSINESS_WORDS = {
    "management", "research", "risk", "technology", "operations", "department", "team",
    "group", "services", "accounting", "compliance", "relations", "resources", "trading",
    "engineering", "fiscal", "quarter", "policy", "headcount", "finance", "legal", "human",
    "people", "analytics", "investor", "portfolio", "quantitative", "talent", "acquisition",
    "data", "dictionary", "handbook", "year", "month", "monday", "january", "february",
    "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "full", "time", "part", "regular", "contractor", "voluntary", "involuntary",
    "office", "york", "london", "chicago", "greenwich", "singapore", "hong", "kong", "boston",
    "americas", "emea", "apac", "wealth", "retail", "banking", "markets", "capital", "strategy",
    "the", "this", "our", "all", "each", "every", "new", "open", "active", "total", "q1", "q2",
    "q3", "q4", "hr", "it", "chief", "officer", "vice", "president", "director", "manager",
    "senior", "head", "analyst", "associate", "partner", "managing", "global", "north", "south",
}
STATUSES = {"proposed", "confirmed", "rejected"}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def load_registry() -> dict[str, Any]:
    return json.loads((CATALOG_DIR / "connectors.json").read_text(encoding="utf-8"))


def load(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"version": 1, "sources": [], "items": []}


def save(path: Path, ctx: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ctx, indent=2) + "\n", encoding="utf-8")


def redact(text: str, allow_phrases: set[str] | None = None) -> str:
    """Strip person-level details: emails, phones, @mentions, person IDs, and
    title-case name sequences that are not business phrases.

    Conservative by design: a false positive only loses a word of a definition,
    while a false negative would persist a person's name.
    """
    if not text:
        return text
    allow = {p.lower() for p in (allow_phrases or set())}
    out = EMAIL.sub("[redacted-email]", text)
    out = MENTION.sub("[redacted-mention]", out)
    out = PERSON_ID.sub("[redacted-id]", out)
    out = PHONE.sub("[redacted-phone]", out)

    def name_sub(match: re.Match[str]) -> str:
        phrase = match.group(1)
        if phrase.lower() in allow:
            return phrase
        words = [w.lower().strip("'-") for w in phrase.split()]
        if any(w in BUSINESS_WORDS for w in words):
            return phrase
        return "[redacted-name]"

    return TITLE_SEQ.sub(name_sub, out)


def item_id(item: dict[str, Any]) -> str:
    basis = json.dumps([item.get("kind"), item.get("term"), item.get("value"),
                        item.get("source", {}).get("ref")], sort_keys=True)
    return hashlib.sha1(basis.encode()).hexdigest()[:12]


def normalize_value(value: Any) -> str:
    if isinstance(value, list):
        return json.dumps(sorted(str(v).strip().lower() for v in value))
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True).lower()
    return re.sub(r"\s+", " ", str(value).strip().lower())


def consent(ctx: dict[str, Any], connector: str, scopes: list[str], by: str) -> dict[str, Any]:
    registry = {c["id"]: c for c in load_registry()["connectors"]}
    if connector not in registry:
        raise SystemExit(f"unknown connector {connector}")
    spec = registry[connector]
    forbidden = {f.lower() for f in spec.get("forbid_scopes", [])}
    for scope in scopes:
        if scope.strip().lower() in forbidden or scope.strip() in {"", "*"}:
            raise SystemExit(f"scope '{scope}' is not allowed for {connector}")
    entry = {"connector": connector, "scopes": scopes, "risk": spec["risk"],
             "consented_by": by, "consented_at": now()}
    ctx["sources"] = [s for s in ctx["sources"] if s["connector"] != connector] + [entry]
    return entry


def consented_scopes(ctx: dict[str, Any]) -> dict[str, set[str]]:
    return {s["connector"]: {x.lower() for x in s["scopes"]} for s in ctx["sources"]}


def ingest(ctx: dict[str, Any], raw_items: list[dict[str, Any]],
           allow_phrases: set[str] | None = None) -> dict[str, int]:
    """Validate, redact, cite, de-dupe and conflict-mark incoming items."""
    kinds = set(load_registry()["allowed_item_kinds"])
    scopes = consented_scopes(ctx)
    stats = {"accepted": 0, "rejected_no_consent": 0, "rejected_no_citation": 0,
             "rejected_kind": 0, "duplicates": 0}
    existing = {i["id"] for i in ctx["items"]}
    for raw in raw_items:
        src = raw.get("source") or {}
        connector, scope = src.get("connector"), str(src.get("scope", "")).lower()
        if raw.get("kind") not in kinds:
            stats["rejected_kind"] += 1
            continue
        if not src.get("ref"):
            stats["rejected_no_citation"] += 1
            continue
        if connector not in scopes or (scope and scope not in scopes[connector]):
            stats["rejected_no_consent"] += 1
            continue
        item = {
            "kind": raw["kind"],
            "term": raw.get("term"),
            "value": raw.get("value"),
            "text": redact(str(raw.get("text", ""))[:600], allow_phrases),
            "source": {"connector": connector, "scope": src.get("scope"), "ref": src["ref"],
                       "title": redact(str(src.get("title", ""))[:160], allow_phrases),
                       "updated": src.get("updated")},
            "confidence": raw.get("confidence", "MEDIUM"),
            "status": "proposed",
            "conflicts_with": [],
        }
        if isinstance(item["value"], str):
            item["value"] = redact(item["value"], allow_phrases)
        item["id"] = item_id(item)
        if item["id"] in existing:
            stats["duplicates"] += 1
            continue
        existing.add(item["id"])
        ctx["items"].append(item)
        stats["accepted"] += 1
    mark_conflicts(ctx)
    return stats


def mark_conflicts(ctx: dict[str, Any]) -> None:
    """Structured items (calendar, reference numbers, org units, thresholds) can
    genuinely conflict. Free-text definitions are linked as ``related`` instead;
    the analyst picks the authoritative wording in the wizard."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in ctx["items"]:
        if item["status"] == "rejected" or item["kind"] in {"caveat", "owner"}:
            continue
        value = item.get("value")
        topic = item.get("term") or (value.get("metric") if isinstance(value, dict) else None)
        key = (item["kind"], str(topic))
        groups.setdefault(key, []).append(item)
    for items in groups.values():
        for item in items:
            peers = sorted(other["id"] for other in items if other["id"] != item["id"])
            if item["kind"] == "definition":
                item["conflicts_with"], item["related"] = [], peers
                continue
            item["conflicts_with"] = sorted(
                other["id"] for other in items
                if other["id"] != item["id"]
                and normalize_value(other["value"]) != normalize_value(item["value"])
            )


def set_status(ctx: dict[str, Any], iid: str, status: str, value: Any = None) -> None:
    if status not in STATUSES:
        raise SystemExit(f"status must be one of {sorted(STATUSES)}")
    for item in ctx["items"]:
        if item["id"] == iid:
            item["status"] = status
            if value is not None:
                item["value"] = value
                item["edited"] = True
            item["reviewed_at"] = now()
            break
    else:
        raise SystemExit(f"no item {iid}")
    mark_conflicts(ctx)


def active_items(ctx: dict[str, Any], kind: str | None = None) -> list[dict[str, Any]]:
    """Confirmed items win; proposed items are used only when no confirmed peer exists."""
    items = [i for i in ctx.get("items", []) if i["status"] != "rejected"
             and (kind is None or i["kind"] == kind)]
    confirmed_terms = {(i["kind"], i.get("term")) for i in items if i["status"] == "confirmed"}
    return [i for i in items if i["status"] == "confirmed"
            or (i["kind"], i.get("term")) not in confirmed_terms]


def org_allowlist(index_path: Path | None) -> set[str]:
    """Org/location values already present in the data are safe phrases."""
    if not index_path or not index_path.exists():
        return set()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    allow: set[str] = set()
    for obj in index.get("objects", {}).values():
        for meta in obj.get("columns", {}).values():
            for value, _n in meta.get("top_values") or []:
                if isinstance(value, str):
                    allow.add(value.strip())
    return allow


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("consent")
    c.add_argument("--context", type=Path, required=True)
    c.add_argument("--connector", required=True)
    c.add_argument("--scope", action="append", required=True)
    c.add_argument("--by", default="analyst")
    i = sub.add_parser("ingest")
    i.add_argument("--context", type=Path, required=True)
    i.add_argument("--items", type=Path, required=True)
    i.add_argument("--org-allowlist", type=Path)
    i.add_argument("--keep-raw", action="store_true", help="keep the raw items file after ingest")
    s = sub.add_parser("set-status")
    s.add_argument("--context", type=Path, required=True)
    s.add_argument("--id", required=True)
    s.add_argument("--status", required=True)
    s.add_argument("--value")
    args = parser.parse_args()

    ctx = load(args.context)
    if args.cmd == "consent":
        result: Any = consent(ctx, args.connector, args.scope, args.by)
    elif args.cmd == "ingest":
        raw = json.loads(args.items.read_text(encoding="utf-8"))
        result = ingest(ctx, raw.get("items", raw) if isinstance(raw, dict) else raw,
                        org_allowlist(args.org_allowlist))
        if not args.keep_raw:
            args.items.unlink()   # raw extracts are transient; only the redacted store persists
    else:
        set_status(ctx, args.id, args.status, json.loads(args.value) if args.value else None)
        result = {"ok": True}
    save(args.context, ctx)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
