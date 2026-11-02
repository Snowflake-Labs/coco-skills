#!/usr/bin/env python3
"""Deterministic business-context extractor for exported / local documents.

Handles markdown/txt docs and JSON exports (Jira issues, Slack/Teams messages,
Confluence page dumps). Produces raw context items for ``context_store.py
ingest`` — the store then enforces consent, redaction and citations.

Live MCP sources are extracted by the agent (subskills/discover_context.md) into
the SAME item schema, so both paths converge on one store.

Author / user / assignee fields in exports are dropped on read and never become
part of an item.

Usage:
  extract_context.py --path <docs_dir> --connector local_files --scope <docs_dir> --out raw.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from context_store import redact  # noqa: E402

CATALOG_DIR = Path(__file__).resolve().parents[2] / "catalog"
MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"])}
FISCAL = re.compile(r"fiscal year\s+(?:\(fy\)\s+)?(?:starts|begins|runs from)\s+(?:in\s+|on\s+)?(?:the\s+)?(?:1st\s+of\s+)?(\w+)", re.I)
HC_REF = re.compile(
    r"(?:total\s+|active\s+|regular\s+)*headcount\s+(?:was|is|of|=|:)\s*(?:about\s+|approximately\s+|~\s*)?([\d,]+)"
    r"(?:\s+(?:as of|at|on)\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}))?", re.I)
RATE_REF = re.compile(r"(voluntary\s+|regrettable\s+|annualized\s+)?attrition(?:\s+rate)?\s+(?:was|is|of|=|:)\s*(?:about\s+|~\s*)?(\d+(?:\.\d+)?)\s*%", re.I)
DEFINE_CUE = re.compile(r"\b(means|is defined as|defined as|definition|counts?|includes?|excludes?|refers to|we define|=)\b", re.I)
GLOSSARY = re.compile(r"^\s*[-*]?\s*\**([A-Za-z][\w /&()'-]{2,60}?)\**\s*[:\u2014-]\s+(.{8,})$")
LIST_HEAD = re.compile(r"(departments|business units|divisions|org units|organizations|org structure)", re.I)
BULLET = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(.+?)\s*$")
OWNER = re.compile(r"(?:owned by|owner|contact|reach out to|escalate to)\s*(?:is|:)?\s*(#[\w-]+|the [\w ]+team)", re.I)
CAVEAT_CUE = re.compile(r"\b(known issue|caveat|do not use|deprecated|incorrect|discrepancy|stale|backfill)\b", re.I)
DROP_KEYS = {"user", "author", "assignee", "reporter", "creator", "sender", "from", "to", "cc",
             "user_name", "real_name", "display_name", "email", "mentions"}


def load_vocabulary() -> dict[str, list[str]]:
    return json.loads((CATALOG_DIR / "connectors.json").read_text(encoding="utf-8"))["term_vocabulary"]


def match_term(text: str, vocab: dict[str, list[str]]) -> str | None:
    lower = text.lower()
    best = None
    for term, phrases in vocab.items():
        for phrase in phrases:
            if phrase in lower and (best is None or len(phrase) > best[1]):
                best = (term, len(phrase))
    return best[0] if best else None


def documents(path: Path) -> list[tuple[str, str, str]]:
    """Yield (ref, title, text) per document or message. Author fields dropped."""
    docs: list[tuple[str, str, str]] = []
    for file in sorted(path.rglob("*")):
        if not file.is_file():
            continue
        rel = file.relative_to(path).as_posix()
        if file.suffix.lower() in {".md", ".txt"}:
            docs.append((rel, file.stem.replace("_", " "), file.read_text(encoding="utf-8")))
        elif file.suffix.lower() == ".json":
            data = json.loads(file.read_text(encoding="utf-8"))
            records = data if isinstance(data, list) else data.get("items") or data.get("messages") or [data]
            for n, rec in enumerate(records):
                if not isinstance(rec, dict):
                    continue
                clean = {k: v for k, v in rec.items() if k.lower() not in DROP_KEYS}
                ref = str(clean.get("key") or clean.get("id") or clean.get("ts") or n)
                where = clean.get("channel") or clean.get("project") or file.stem
                title = str(clean.get("summary") or clean.get("title") or where)
                body = "\n".join(str(clean.get(k, "")) for k in ("summary", "title", "description", "text", "body"))
                docs.append((f"{rel}#{where}:{ref}", title, body))
    return docs


def extract_doc(ref: str, title: str, text: str, vocab: dict[str, list[str]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_unit_lists: list[list[str]] = []
    lines = text.splitlines()

    def add(kind: str, term: str | None, value: Any, snippet: str, line_no: int, confidence: str) -> None:
        # Redact at extraction time too, so even intermediate files hold no person-level details.
        clean_value = redact(value) if isinstance(value, str) else value
        items.append({"kind": kind, "term": term, "value": clean_value, "text": redact(snippet.strip()),
                      "confidence": confidence,
                      "source": {"ref": f"{ref}:L{line_no}" if "#" not in ref else ref, "title": redact(title)}})

    for no, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        if m := FISCAL.search(stripped):
            month = MONTHS.get(m.group(1).lower())
            if month:
                add("fiscal_calendar", "fiscal_calendar", {"fiscal_year_start_month": month},
                    stripped, no, "HIGH")
        for m in HC_REF.finditer(stripped):
            value = {"metric": "active_headcount", "value": int(m.group(1).replace(",", ""))}
            if m.group(2):
                value["as_of"] = m.group(2)
            add("reference_number", "active_headcount", value, stripped, no, "MEDIUM")
        for m in RATE_REF.finditer(stripped):
            metric = ((m.group(1) or "").strip().lower() + "_attrition_rate").lstrip("_")
            add("reference_number", metric, {"metric": metric, "value": float(m.group(2)), "unit": "pct"},
                stripped, no, "MEDIUM")
        if m := OWNER.search(stripped):
            add("owner", match_term(stripped, vocab) or "people_analytics", m.group(1), stripped, no, "MEDIUM")
        term = match_term(stripped, vocab)
        if term and term != "fiscal_calendar":
            glossary = GLOSSARY.match(stripped)
            if glossary and match_term(glossary.group(1), vocab) == term:
                add("definition", term, glossary.group(2).strip(), stripped, no, "HIGH")
            elif DEFINE_CUE.search(stripped) and len(stripped.split()) >= 7:
                add("definition", term, stripped, stripped, no, "MEDIUM")
        if CAVEAT_CUE.search(stripped):
            add("caveat", term, stripped, stripped, no, "LOW")
        if LIST_HEAD.search(stripped) and len(stripped) < 120:
            units: list[str] = []
            for follow in lines[no:no + 30]:
                bullet = BULLET.match(follow)
                if bullet:
                    units.append(re.split(r"\s[\u2014(:-]\s?", bullet.group(1))[0].strip("* "))
                elif units:
                    break
            if len(units) >= 2 and units not in seen_unit_lists:
                seen_unit_lists.append(units)
                add("org_units", "org_hierarchy", units, stripped, no, "HIGH")
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--connector", default="local_files")
    parser.add_argument("--scope", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    vocab = load_vocabulary()
    raw: list[dict[str, Any]] = []
    for ref, title, text in documents(args.path):
        for item in extract_doc(ref, title, text, vocab):
            item["source"].update({"connector": args.connector, "scope": args.scope})
            raw.append(item)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"items": raw}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"event": "context_extracted", "items": len(raw)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
