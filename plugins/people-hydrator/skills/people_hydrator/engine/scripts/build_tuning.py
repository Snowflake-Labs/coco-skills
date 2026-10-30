#!/usr/bin/env python3
"""Pre-fill per-skill tuning from business context + data. Stdlib only.

Writes ``tuning/<skill>.json`` for every catalog skill. Each tunable records
``value``, ``default``, ``source`` (context | data | default | analyst),
``evidence`` and ``status`` (auto | needs_review | confirmed). Analyst
confirmations already present in an existing file are preserved.

Resolution order per tunable:
  1. analyst-confirmed value (never overwritten)
  2. confirmed / proposed business context (definition, fiscal calendar, threshold)
  3. data evidence (bound column values, fiscal calendar table)
  4. catalog default
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context_store  # noqa: E402
from build_context_graph import norm_value, tokens  # noqa: E402

NUM_AFTER = {
    "span_over_threshold": re.compile(r"(?:more than|over|above|exceed\w*)\s+(\d+)\s+direct reports", re.I),
    "span_under_threshold": re.compile(r"(?:fewer than|less than|under|below)\s+(\d+)", re.I),
    "new_hire_window_months": re.compile(r"(\d+)\s+months?\s+or\s+less|(?:within|first)\s+(\d+)\s+months", re.I),
    "attrition_window_months": re.compile(r"(?:trailing|rolling|last)\s+(\d+)\s+months", re.I),
    "stale_candidate_days": re.compile(r"(\d+)\s+days?\s+in\s+(?:a\s+)?stage", re.I),
    "org_concentration_pct": re.compile(r"(\d+)\s*%\s+of\s+(?:total\s+)?headcount", re.I),
}
TERM_FOR = {
    "span_over_threshold": "span_of_control", "span_under_threshold": "span_of_control",
    "new_hire_window_months": "new_hire", "attrition_window_months": "attrition_rate",
    "regular_employee_types": "regular_headcount", "regrettable_definition": "regrettable_exit",
    "voluntary_values": "voluntary_exit", "fiscal_year_start_month": "fiscal_calendar",
    "open_req_status_values": "open_requisition", "offer_stage_values": "offer_stage",
}


def find_binding_values(bindings: dict[str, Any], index: dict[str, Any], field: str,
                        need_prefix: str | None = None) -> tuple[list[str], str | None]:
    """Distinct values of the column bound to `field` (first matching need)."""
    for key, b in bindings.get("needs", {}).items():
        if need_prefix and not key.startswith(need_prefix):
            continue
        f = b.get("fields", {}).get(field)
        if not f or not f.get("column") or not b.get("fqn"):
            continue
        meta = index["objects"].get(b["fqn"], {}).get("columns", {}).get(f["column"], {})
        values = [str(v) for v, _n in meta.get("top_values") or [] if v is not None]
        if values:
            return values, f"{b['fqn'].split('.')[-1]}.{f['column']}"
    return [], None


def context_items(ctx: dict[str, Any], term: str | None, kinds: set[str]) -> list[dict[str, Any]]:
    return [i for i in context_store.active_items(ctx)
            if i["kind"] in kinds and (term is None or i.get("term") == term)]


EXCLUSION = re.compile(
    r"([A-Za-z][\w\- ]{1,40}?)\s+(?:exits\s+|employees\s+|workers\s+)?(?:are|is)\s+"
    r"(?:excluded|reported separately|not counted|not included|out of scope)", re.I)


def whole_word(value: str, text: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(value)}s?(?![\w-])", text, re.I) is not None


def resolve_enum(tunable: dict[str, Any], values: list[str], col_ref: str | None,
                 ctx: dict[str, Any]) -> dict[str, Any] | None:
    if not values:
        return None
    term = TERM_FOR.get(tunable["id"]) or tunable.get("context_term")
    for item in context_items(ctx, term, {"definition"}):
        text = f"{item.get('value')} {item.get('text')}"
        excluded_phrases = " ".join(EXCLUSION.findall(text))
        picked = [v for v in values if whole_word(v, text) and not whole_word(v, excluded_phrases)]
        if picked:
            return {"value": picked, "source": "context", "status": "auto" if item["status"] == "confirmed" else "needs_review",
                    "evidence": [f"{item['source']['connector']} definition of {term} names {', '.join(picked)} "
                                 f"(values of {col_ref})"], "context_ids": [item["id"]]}
    default = [norm_value(d) for d in tunable["default"]]
    # whole words only: "voluntary" must not pick "Involuntary"
    picked = [v for v in values if any(norm_value(v) == d or (len(d) > 3 and re.search(rf"(?<![a-z]){re.escape(d)}(?![a-z])",
                                                                                         norm_value(v))) for d in default)]
    if picked:
        return {"value": picked, "source": "data", "status": "needs_review",
                "evidence": [f"{col_ref} values matching default {tunable['default']}: {', '.join(picked)}"],
                "options": values}
    return {"value": [], "source": "data", "status": "needs_review", "options": values,
            "evidence": [f"no value of {col_ref} matched the default; analyst must choose"]}


def resolve_fiscal(ctx: dict[str, Any], index: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any] | None:
    items = context_items(ctx, "fiscal_calendar", {"fiscal_calendar"})
    data_month = None
    cal = bindings.get("needs", {}).get("people.calendar.fiscal", {})
    start_col = (cal.get("fields", {}).get("fiscal_year_start_date") or {}).get("column")
    if cal.get("fqn") and start_col:
        meta = index["objects"].get(cal["fqn"], {}).get("columns", {}).get(start_col, {})
        if meta.get("min"):
            data_month = int(str(meta["min"])[5:7])
    if items:
        values = {i["value"]["fiscal_year_start_month"] for i in items}
        confirmed = [i for i in items if i["status"] == "confirmed"]
        chosen = confirmed[0] if confirmed else next(
            (i for i in items if data_month and i["value"]["fiscal_year_start_month"] == data_month), items[0])
        conflict = len(values) > 1 and not confirmed
        evidence = [f"{i['source']['connector']}: fiscal year starts month {i['value']['fiscal_year_start_month']}"
                    for i in items]
        if data_month:
            evidence.append(f"fiscal calendar table: fiscal year starts month {data_month}")
        chosen_month = chosen["value"]["fiscal_year_start_month"]
        agrees_with_data = data_month is None or data_month == chosen_month
        status = "auto" if confirmed or (not conflict and agrees_with_data) else "needs_review"
        return {"value": chosen_month, "source": "context", "status": status,
                "evidence": evidence, "conflict": conflict, "context_ids": [i["id"] for i in items]}
    if data_month:
        return {"value": data_month, "source": "data", "status": "auto",
                "evidence": [f"fiscal calendar table: fiscal year starts month {data_month}"]}
    return None


def resolve_threshold(tunable: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any] | None:
    pattern = NUM_AFTER.get(tunable["id"])
    term = TERM_FOR.get(tunable["id"])
    for item in context_items(ctx, None, {"threshold"}):
        val = item.get("value") or {}
        if isinstance(val, dict) and val.get("tunable") == tunable["id"]:
            return {"value": val["value"], "source": "context", "status": "needs_review",
                    "evidence": [f"{item['source']['connector']} threshold"], "context_ids": [item["id"]]}
    if not pattern:
        return None
    for item in context_items(ctx, term, {"definition"}):
        m = pattern.search(f"{item.get('value')} {item.get('text')}")
        if m:
            number = int(next(g for g in m.groups() if g))
            return {"value": number, "source": "context",
                    "status": "auto" if item["status"] == "confirmed" or number == tunable["default"] else "needs_review",
                    "evidence": [f"{item['source']['connector']} definition of {term}: '{m.group(0)}'"],
                    "context_ids": [item["id"]]}
    return None


def resolve_definition(tunable: dict[str, Any], ctx: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any] | None:
    term = TERM_FOR.get(tunable["id"]) or tunable.get("context_term")
    items = context_items(ctx, term, {"definition"})
    if not items:
        return None
    item = sorted(items, key=lambda i: (i["status"] != "confirmed", i["confidence"] != "HIGH"))[0]
    evidence = [f"{i['source']['connector']}: {str(i.get('value'))[:120]}" for i in items[:3]]
    if tunable["id"] == "regrettable_definition":
        exits = bindings.get("needs", {}).get("people.exits.event_records", {})
        col = (exits.get("fields", {}).get("regrettable_flag") or {}).get("column")
        if col and "flag" in norm_value(item.get("value") or ""):
            evidence.append(f"bound to {exits['fqn'].split('.')[-1]}.{col}")
            return {"value": f"{col} = TRUE", "source": "context", "status": "auto" if item["status"] == "confirmed" else "needs_review",
                    "evidence": evidence, "context_ids": [i["id"] for i in items]}
    return {"value": str(item.get("value")), "source": "context", "status": "needs_review",
            "evidence": evidence, "context_ids": [i["id"] for i in items]}


def build(catalog: dict[str, Any], index: dict[str, Any], bindings: dict[str, Any],
          ctx: dict[str, Any], existing: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    shared: dict[str, Any] = {}  # an answer given once applies to every skill with the same setting
    for prev in existing.values():
        for tid, t in (prev or {}).get("tunables", {}).items():
            if t.get("status") == "confirmed" and (tid not in shared or t.get("source") == "analyst"):
                shared[tid] = t
    for skill in catalog["skills"]:
        prior = existing.get(skill["name"], {}).get("tunables", {})
        tunables: dict[str, Any] = {}
        for t in skill.get("tunables", []):
            if prior.get(t["id"], {}).get("status") == "confirmed":
                tunables[t["id"]] = prior[t["id"]]
                continue
            if t["id"] in shared:
                tunables[t["id"]] = {**shared[t["id"]], "label": t["label"], "type": t["type"], "default": t["default"]}
                continue
            resolved = None
            if t["type"] in {"enum_multi", "enum"}:
                field = (t.get("infer_from") or {}).get("field")
                values, col_ref = find_binding_values(bindings, index, field,
                                                      (t.get("infer_from") or {}).get("need_prefix")) if field else ([], None)
                resolved = resolve_enum(t, values, col_ref, ctx)
            elif t["type"] == "calendar":
                resolved = resolve_fiscal(ctx, index, bindings)
            elif t["type"] == "threshold":
                resolved = resolve_threshold(t, ctx)
            elif t["type"] == "definition":
                resolved = resolve_definition(t, ctx, bindings)
            entry = {"label": t["label"], "type": t["type"], "default": t["default"]}
            entry.update(resolved or {"value": t["default"], "source": "default", "status": "auto",
                                      "evidence": ["catalog default (no context or data evidence)"]})
            tunables[t["id"]] = entry
        for d in definitions_for(skill["name"]):  # business definitions with no catalog setting yet (def_*)
            tid = d["writes_to"]
            if tid in tunables:
                continue
            if prior.get(tid, {}).get("status") == "confirmed":
                tunables[tid] = prior[tid]
            elif tid in shared:
                tunables[tid] = {**shared[tid], "label": d.get("label") or tid, "type": d["kind"]}
            else:
                tunables[tid] = {"label": d.get("label") or d["prompt"].rstrip("?"), "type": d["kind"],
                                 "default": d.get("default"), "value": d.get("default"), "source": "default",
                                 "status": "auto", "evidence": ["not decided yet"]}
        for tid, t in prior.items():  # analyst answers to assistant suggestions are kept too
            if tid not in tunables and t.get("status") == "confirmed":
                tunables[tid] = t
        out[skill["name"]] = {"skill": skill["name"], "tunables": tunables}
    return out


def definitions_for(skill: str) -> list[dict[str, Any]]:
    path = Path(__file__).resolve().parents[2] / "catalog" / "definitions.json"
    try:
        defs = json.loads(path.read_text(encoding="utf-8"))["definitions"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return []
    return [d for d in defs if skill in d["skills"] and d["writes_to"].startswith("def_")]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    index = json.loads(args.index.read_text(encoding="utf-8"))
    bindings = json.loads(args.bindings.read_text(encoding="utf-8"))
    ctx = context_store.load(args.context)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    existing = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in args.out_dir.glob("*.json")}
    result = build(catalog, index, bindings, ctx, existing)
    review = 0
    for name, data in result.items():
        (args.out_dir / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        review += sum(1 for t in data["tunables"].values() if t["status"] == "needs_review")
    print(json.dumps({"event": "tuning_built", "skills": len(result), "needs_review": review}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
