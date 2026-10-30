#!/usr/bin/env python3
"""Build context_graph.json: evidence edges linking business context to data.

Edge kinds (each carries evidence + a 0..1 weight):
  value_in_column   context value set (e.g. org units) found in a column's values
  term_mentions     a definition / caveat mentions a column (by tokens)
  join              two columns join (profile overlap and/or observed in query history)
  usage             column used in filters / group-bys / aggregates
  semantic_view     column exposed by an existing semantic view

The graph is derived (re-buildable) and contains no person-level data: it only
references column names, code values already in the index, and context item ids.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context_store  # noqa: E402

ABBREVIATIONS = {
    "term": "termination", "mgmt": "management", "emp": "employee", "num": "number",
    "dept": "department", "hc": "headcount", "req": "requisition", "reqs": "requisition",
    "yrs": "years", "cc": "cost_center", "dt": "date", "pct": "percent", "org": "organization",
    "mgr": "manager", "hm": "hiring_manager", "qtr": "quarter", "fy": "fiscal_year",
}
STOP = {"the", "a", "an", "of", "on", "to", "and", "or", "for", "by", "is", "in", "with", "per", "as"}


def tokens(name: str) -> list[str]:
    raw = re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", raw.lower()) if p]
    out: list[str] = []
    for part in parts:
        out.extend(ABBREVIATIONS.get(part, part).split("_"))
    return [t for t in out if t not in STOP]


def norm_value(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def columns_iter(index: dict[str, Any]):
    for fqn, obj in index.get("objects", {}).items():
        if not obj.get("official", True):
            continue
        for col, meta in obj.get("columns", {}).items():
            yield fqn, col, meta


def value_edges(index: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
    edges = []
    for item in context_store.active_items(ctx, "org_units"):
        units = {norm_value(u) for u in item["value"] if str(u).strip()}
        if not units:
            continue
        for fqn, col, meta in columns_iter(index):
            vals = {norm_value(v) for v, _n in meta.get("top_values") or [] if v is not None}
            if not vals:
                continue
            hit = units & vals
            if not hit:
                continue
            recall = len(hit) / len(units)          # share of context units found
            precision = len(hit) / len(vals)        # share of column values explained
            weight = round(2 * recall * precision / (recall + precision), 3)
            edges.append({"kind": "value_in_column", "context_id": item["id"],
                          "term": item.get("term"), "column": f"{fqn}.{col}", "weight": weight,
                          "evidence": f"{len(hit)}/{len(units)} org units from "
                                      f"{item['source']['connector']} found in {col} "
                                      f"({len(hit)}/{len(vals)} of its values)"})
    return edges


def mention_edges(index: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
    edges = []
    texts = [(i, set(tokens(str(i.get("value") or "") + " " + i.get("text", ""))))
             for i in context_store.active_items(ctx) if i["kind"] in {"definition", "caveat"}]
    for fqn, col, meta in columns_iter(index):
        col_tokens = set(tokens(col))
        if not col_tokens or col_tokens <= {"id", "date", "name", "type", "flag"}:
            continue
        value_tokens = {norm_value(v) for v, _n in meta.get("top_values") or [] if v}
        for item, words in texts:
            name_hit = col_tokens <= words
            value_hit = bool(value_tokens & {norm_value(w) for w in words}) and meta.get("is_code")
            if name_hit or value_hit:
                edges.append({"kind": "term_mentions", "context_id": item["id"],
                              "term": item.get("term"), "item_kind": item["kind"],
                              "column": f"{fqn}.{col}", "weight": 1.0 if name_hit else 0.6,
                              "evidence": f"{item['kind']} from {item['source']['connector']} "
                                          f"mentions {'column ' + col if name_hit else 'values of ' + col}"})
    return edges


def join_edges(index: dict[str, Any]) -> list[dict[str, Any]]:
    edges = []
    observed = index.get("usage", {}).get("join_pairs", {})
    for j in index.get("joins", []):
        pair = "=".join(sorted([j["left"], j["right"]]))
        seen = observed.get(pair, 0)
        weight = min(1.0, 0.5 * min(j["overlap_left"], j["overlap_right"]) + 0.5 + 0.1 * seen)
        edges.append({"kind": "join", "left": j["left"], "right": j["right"],
                      "cardinality": j["cardinality"], "weight": round(min(weight, 1.0), 3),
                      "evidence": f"key overlap {j['overlap_left']:.0%}/{j['overlap_right']:.0%}"
                                  + (f", joined in {seen} queries" if seen else "")})
    for pair, count in observed.items():
        if not any(e for e in edges if "=".join(sorted([e["left"], e["right"]])) == pair):
            left, right = pair.split("=")
            edges.append({"kind": "join", "left": left, "right": right, "cardinality": "?",
                          "weight": round(min(1.0, 0.4 + 0.1 * count), 3),
                          "evidence": f"joined in {count} queries"})
    return edges


def usage_edges(index: dict[str, Any]) -> list[dict[str, Any]]:
    usage = index.get("usage", {})
    counts: dict[str, int] = {}
    for bucket in ("filter_columns", "group_by_columns"):
        for col, n in usage.get(bucket, {}).items():
            counts[col] = counts.get(col, 0) + n
    for agg, n in usage.get("aggregates", {}).items():
        col = agg[agg.index("(") + 1:-1]
        counts[col] = counts.get(col, 0) + n
    top = max(counts.values(), default=1)
    return [{"kind": "usage", "column": col, "count": n,
             "weight": round(math.log1p(n) / math.log1p(top), 3),
             "evidence": f"used in {n} recent queries"} for col, n in counts.items()]


def semantic_view_edges(index: dict[str, Any]) -> list[dict[str, Any]]:
    edges = []
    for sv in index.get("semantic_views", []):
        for name in sv.get("dimensions", []) + sv.get("metrics", []):
            edges.append({"kind": "semantic_view", "semantic_view": sv["fqn"], "member": name,
                          "weight": 1.0, "evidence": f"exposed by semantic view {sv['fqn']}"})
    return edges


def build(index: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    edges = (value_edges(index, ctx) + mention_edges(index, ctx) + join_edges(index)
             + usage_edges(index) + semantic_view_edges(index))
    return {"version": 1,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "edges": edges,
            "counts": {k: sum(1 for e in edges if e["kind"] == k)
                       for k in ("value_in_column", "term_mentions", "join", "usage", "semantic_view")}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    index = json.loads(args.index.read_text(encoding="utf-8"))
    ctx = context_store.load(args.context)
    graph = build(index, ctx)
    args.out.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"event": "graph_built", **graph["counts"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
