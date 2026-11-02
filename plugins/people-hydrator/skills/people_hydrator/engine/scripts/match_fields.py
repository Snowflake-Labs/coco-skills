#!/usr/bin/env python3
"""Multi-signal field matcher: data_index + context graph -> bindings.json.

For every catalog data need (across ALL skills, so adding a skill later needs no
re-crawl) pick the best object, an optional row filter, and a column/expression
per field. Every choice keeps its score breakdown and evidence so the wizard can
show WHY, and only low-confidence choices go to the analyst.

Field score (weights configurable, see DEFAULT_WEIGHTS):
  name        alias / token similarity
  type        semantic_type fit vs dtype + value shape
  values      value evidence (catalog value_hints or context org units in column values)
  usage       column used in recent queries
  structure   grain / join / cadence consistency
  context     business-context mention of the column
  semview     column exposed by an existing semantic view
A zero type fit multiplies the score by 0.3 (a boolean can't bind to a 40-value column).
Learned corrections (learned.json) override everything.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_context_graph import norm_value, tokens  # noqa: E402

DEFAULT_WEIGHTS = {"name": 0.20, "type": 0.20, "values": 0.20, "usage": 0.15,
                   "structure": 0.10, "context": 0.10, "semview": 0.05}
FIELD_TERMS = {
    "regrettable_flag": "regrettable_exit", "voluntary_flag": "voluntary_exit",
    "involuntary_flag": "voluntary_exit", "termination_category": "voluntary_exit",
    "employee_type": "regular_headcount", "employment_status": "regular_headcount",
    "is_active": "regular_headcount", "org_name": "org_hierarchy", "org": "org_hierarchy",
    "org_group": "org_hierarchy", "org_label": "org_hierarchy", "department": "org_hierarchy",
    "direct_reports": "span_of_control", "manager_id": "span_of_control",
    "days_to_fill": "time_to_fill", "hire_date": "new_hire", "fiscal_year": "fiscal_calendar",
}
SNAPSHOT_WORDS = {"snapshot", "monthend", "month_end", "eom"}
EVENT_WORDS = {"transaction", "event", "change", "movement"}
EVENT_TABLE_STEMS = {"exits", "termi", "separ", "hires", "offbo", "leave"}
VALUE_SYNONYMS = {
    "exit": {"termination", "terminated", "separation", "separated", "exit", "exited"},
    "voluntary": {"voluntary", "resignation", "resigned"},
    "involuntary": {"involuntary"},
    "hire": {"hire", "hired", "newhire", "start"},
    "leader": {"leader", "manager"},
}


def load(path: Path | None, default: Any) -> Any:
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def merged_needs(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Union each need key's fields across skills (required if required anywhere)."""
    needs: dict[str, dict[str, Any]] = {}
    for skill in catalog["skills"]:
        for need in skill["data_needs"]:
            entry = needs.setdefault(need["key"], {
                "key": need["key"], "concepts": list(need.get("concepts", [])),
                "grain": list(need.get("grain", [])), "fields": {}, "skills": [],
                "row_filter_hint": need.get("row_filter_hint"), "required_in": []})
            entry["skills"].append(skill["name"])
            if need.get("required"):
                entry["required_in"].append(skill["name"])
            for c in need.get("concepts", []):
                if c not in entry["concepts"]:
                    entry["concepts"].append(c)
            for fname, spec in need["fields"].items():
                cur = entry["fields"].setdefault(fname, {"required": False, "aliases": [fname],
                                                        "semantic_type": spec.get("semantic_type"),
                                                        "value_hints": []})
                cur["required"] = cur["required"] or bool(spec.get("required"))
                for a in spec.get("aliases", []):
                    if a not in cur["aliases"]:
                        cur["aliases"].append(a)
                for h in spec.get("value_hints", []):
                    if h not in cur["value_hints"]:
                        cur["value_hints"].append(h)
    return needs


class Evidence:
    """Indexes graph edges by column for fast lookup."""

    def __init__(self, graph: dict[str, Any]):
        self.by_col: dict[str, list[dict[str, Any]]] = {}
        self.sv_members: set[str] = set()
        for e in graph.get("edges", []):
            if e["kind"] == "join":
                self.by_col.setdefault(e["left"], []).append(e)
                self.by_col.setdefault(e["right"], []).append(e)
            elif e["kind"] == "semantic_view":
                self.sv_members.add(norm_value(e["member"].split(".")[-1]))
            elif "column" in e:
                self.by_col.setdefault(e["column"], []).append(e)

    def get(self, col: str, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.by_col.get(col, []) if e["kind"] == kind]


GENERIC_TOKENS = {"id", "date", "name", "type", "flag", "code", "number", "is", "value", "key"}
DISPLAY_NAME_QUALIFIERS = {"full", "display", "employee", "candidate", "applicant", "user"}


def name_score(field: str, spec: dict[str, Any], col: str) -> float:
    """Alias similarity. Generic tokens (id, date, name, ...) never count as a
    match on their own: REQ_ID is not evidence for offer_id. A partial match must
    also share a word of the field's own name: EXIT_FLAG is not evidence for
    regrettable_flag just because an alias says "regrettable_exit_flag"."""
    col_norm = norm_value(col)
    col_tokens = set(tokens(col))
    own_tokens = set(tokens(field)) - GENERIC_TOKENS
    best = 0.0
    if set(tokens(field)) - {"name"} <= DISPLAY_NAME_QUALIFIERS and col_tokens & {"name"}:
        # display-name fields (full_name, name, display_name): a *_NAME column is a
        # candidate even when the alias list only has generic forms
        best = 0.6
    for rank, alias in enumerate(spec["aliases"]):
        if norm_value(alias) == col_norm:
            # Alias order expresses preference: canonical name beats a fallback alias.
            return round(max(0.85, 1.0 - 0.04 * rank), 3)
        a_tokens = set(tokens(alias))
        shared = (a_tokens - GENERIC_TOKENS) & (col_tokens - GENERIC_TOKENS)
        if not shared or (own_tokens and not shared & own_tokens):
            continue
        jacc = len(a_tokens & col_tokens) / len(a_tokens | col_tokens)
        ratio = difflib.SequenceMatcher(None, norm_value(alias), col_norm).ratio()
        best = max(best, 0.85 * jacc + 0.15 * ratio)
    return round(min(best, 0.84), 3)


def type_score(sem: str | None, meta: dict[str, Any], rows: int, field: str = "") -> float:
    nd = meta.get("distinct") or 0
    if sem == "boolean" and meta.get("is_code") and not meta.get("is_boolean") and field \
            and boolean_expression(field, meta, "_"):
        return 0.9   # a code value carries the flag, e.g. TRANSACTION_TYPE = 'TERMINATION'
    if sem == "category" and field.endswith("name") and not field.startswith("org") \
            and meta.get("is_text") and not meta.get("is_code") and rows and nd / rows > 0.05:
        return 1.0   # person / entity display names are high-cardinality text
    if sem == "identifier":
        if meta.get("is_id"):
            return 1.0
        if not (meta.get("is_text") or meta.get("is_number")) or not rows:
            return 0.0
        ratio = nd / rows
        return 0.85 if ratio > 0.5 else (0.5 if ratio > 0.05 else 0.0)
    if sem == "date_or_period":
        return 1.0 if meta.get("is_date") else (0.4 if meta.get("is_number") and nd < 50 else 0.0)
    if sem == "boolean":
        if meta.get("is_boolean"):
            return 1.0
        return 0.7 if meta.get("is_code") and 2 <= nd <= 4 else 0.0
    if sem == "category":
        if meta.get("is_code"):
            return 1.0
        if meta.get("is_id"):
            return 0.0 if not field.endswith(("_id", "_code")) else 0.3
        if meta.get("is_text"):
            return 0.6 if not meta.get("pii_risk") else 0.5
        return 0.4 if meta.get("is_number") and nd < 30 else 0.0
    if sem in {"number", "money"}:
        return 1.0 if meta.get("is_number") else 0.0
    if sem == "text":
        return 1.0 if meta.get("is_text") and not meta.get("is_code") else (0.4 if meta.get("is_text") else 0.0)
    return 0.5


def value_score(spec: dict[str, Any], meta: dict[str, Any], evidence: Evidence,
                col_ref: str) -> tuple[float, list[str]]:
    hints = spec.get("value_hints") or []
    notes: list[str] = []
    best = 0.0
    if "@context:org_units" in hints:
        for e in evidence.get(col_ref, "value_in_column"):
            if e["weight"] > best:
                best, notes = e["weight"], [e["evidence"]]
    plain = [norm_value(h) for h in hints if not h.startswith("@")]
    values = [norm_value(v) for v, _n in meta.get("top_values") or [] if v is not None]
    if plain and values:
        matched = [v for v in values if any(h == v or (len(h) > 3 and h in v) for h in plain)]
        share = len(matched) / len(values)
        if share > best:
            best = share
            notes = [f"{len(matched)}/{len(values)} values match expected ({', '.join(matched[:4])})"]
    return round(best, 3), notes


def structure_score(field: str, spec: dict[str, Any], need: dict[str, Any], obj: dict[str, Any],
                    col: str, col_ref: str, evidence: Evidence) -> tuple[float, list[str]]:
    sem = spec.get("semantic_type")
    meta = obj["columns"][col]
    notes: list[str] = []
    score = 0.0
    if sem == "identifier":
        unique_cols = {c for g in obj.get("grain_candidates", []) if g["unique"] for c in g["columns"]}
        joins = evidence.get(col_ref, "join")
        if col in unique_cols:
            score = max(score, 1.0)
            notes.append(f"{col} is part of the table's unique key")
        if joins:
            score = max(score, max(e["weight"] for e in joins))
            notes.append(joins[0]["evidence"] + f" with {joins[0]['right' if joins[0]['left'] == col_ref else 'left'].split('.')[-1]}")
    elif sem == "date_or_period":
        cad = (obj.get("cadence") or {}).get(col) or {}
        wants_snapshot = any("snapshot" in g for g in need["grain"]) or "snapshot" in field
        if wants_snapshot and cad.get("month_end_share", 0) >= 0.5:
            score = 1.0
            notes.append(f"{cad['month_end_share']:.0%} of {col} values are month-ends (snapshot cadence)")
        elif cad:
            score = 0.5
    elif meta.get("null_rate") is not None:
        score = round(1 - meta["null_rate"], 3)
    return score, notes


def context_score(field: str, col_ref: str, evidence: Evidence) -> tuple[float, list[str]]:
    term = FIELD_TERMS.get(field)
    best, notes = 0.0, []
    for e in evidence.get(col_ref, "term_mentions"):
        w = e["weight"] if (term and e.get("term") == term) else 0.5 * e["weight"]
        if w > best:
            best, notes = w, [e["evidence"]]
    return round(best, 3), notes


def boolean_expression(field: str, meta: dict[str, Any], col: str) -> str | None:
    """Map a boolean field onto a code column value, e.g. voluntary_flag -> TERM_TYPE = 'Voluntary',
    exit_flag -> TRANSACTION_TYPE = 'TERMINATION'."""
    if meta.get("is_boolean") or not meta.get("is_code"):
        return None
    want = set(tokens(field)) - {"flag", "is"}
    targets = set(want)
    for w in want:
        targets |= VALUE_SYNONYMS.get(w, set())
    for value, _n in meta.get("top_values") or []:
        if value is None:
            continue
        v_norm = norm_value(value)
        if v_norm in targets or (set(tokens(str(value))) and set(tokens(str(value))) <= targets):
            if "involuntary" in v_norm and "involuntary" not in want:
                continue
            return f"{json.dumps(col)} = '{str(value).replace(chr(39), chr(39) * 2)}'"
    return None


def score_field(field: str, spec: dict[str, Any], need: dict[str, Any], fqn: str,
                obj: dict[str, Any], evidence: Evidence, weights: dict[str, float],
                applicable_base: set[str]) -> list[dict[str, Any]]:
    rows = obj.get("row_count") or 0
    out = []
    for col, meta in obj["columns"].items():
        ref = f"{fqn}.{col}"
        parts = {"name": name_score(field, spec, col),
                 "type": type_score(spec.get("semantic_type"), meta, rows, field)}
        parts["values"], v_notes = value_score(spec, meta, evidence, ref)
        usage = evidence.get(ref, "usage")
        parts["usage"] = usage[0]["weight"] if usage else 0.0
        parts["structure"], s_notes = structure_score(field, spec, need, obj, col, ref, evidence)
        parts["context"], c_notes = context_score(field, ref, evidence)
        parts["semview"] = 1.0 if norm_value(col) in evidence.sv_members else 0.0
        expr_hit = spec.get("semantic_type") == "boolean" and boolean_expression(field, meta, col)
        if expr_hit:
            parts["values"] = max(parts["values"], 0.9)
            v_notes = v_notes + [f"value {expr_hit.split('=', 1)[1].strip()} in {col} carries {field}"]
        # Normalize over the signals that can apply to this field, so a field
        # with no value hints or context term is not penalized for lacking them.
        applicable = set(applicable_base)
        if spec.get("value_hints") or parts["values"] > 0:
            applicable.add("values")
        if FIELD_TERMS.get(field) or parts["context"] > 0:
            applicable.add("context")
        denom = sum(weights[k] for k in applicable) or 1.0
        total = sum(weights[k] * parts[k] for k in applicable) / denom
        if parts["name"] >= 0.85:
            total = min(1.0, total + 0.15 * parts["name"])   # exact catalog alias
        if parts["type"] == 0:
            total *= 0.3
        # Evidence of what the column MEANS. Usage and semantic-view membership show a
        # column matters, not which field it is, so they rank candidates but never
        # qualify one on their own (an unbound field is asked about in Tune instead).
        semantic = (parts["name"] >= 0.5 or parts["values"] >= 0.5 or parts["context"] >= 0.6
                    or bool(expr_hit))
        notes = []
        if parts["name"] >= 0.85:
            notes.append(f"name matches catalog alias for {field}")
        elif parts["name"] >= 0.5:
            notes.append(f"name similar to {field} ({parts['name']:.2f})")
        notes += v_notes + s_notes + c_notes
        if usage:
            notes.append(usage[0]["evidence"])
        out.append({"column": col, "score": round(total, 3), "breakdown": parts,
                    "semantic": semantic, "evidence": notes})
    out.sort(key=lambda c: (-c["semantic"], -c["score"]))
    return out


def confidence(best: float, margin: float) -> str:
    if best >= 0.7 and margin >= 0.1:
        return "HIGH"
    if best >= 0.5:
        return "MEDIUM"
    return "LOW"


def grain_fit(need: dict[str, Any], obj: dict[str, Any], per_field: dict[str, list[dict[str, Any]]]) -> float:
    """1.0 when the columns that carry the need's grain are unique in the object,
    0.0 when they are present but not unique, 0.5 when the grain can't be mapped."""
    grain_cols: list[str] = []
    for g in need["grain"]:
        g_tokens = set(tokens(g)) - GENERIC_TOKENS
        temporal = bool({"date", "month", "period", "week", "day"} & set(tokens(g)))
        for fname, spec in need["fields"].items():
            is_temporal = spec.get("semantic_type") == "date_or_period"
            if is_temporal != temporal or spec.get("semantic_type") not in {"identifier", "date_or_period", "category"}:
                continue
            if g_tokens and g_tokens & (set(tokens(fname)) - GENERIC_TOKENS):
                ranked = [c for c in per_field.get(fname, []) if c["semantic"]]
                if ranked:
                    grain_cols.append(ranked[0]["column"])
                break
    if not grain_cols or len(grain_cols) != len(need["grain"]):
        return 0.5
    rows = obj.get("row_count") or 0
    snapshotted = any(c.get("month_end_share", 0) >= 0.5 for c in (obj.get("cadence") or {}).values())
    if len(grain_cols) == 1:
        meta = obj["columns"].get(grain_cols[0], {})
        if rows and meta.get("distinct") == rows:
            return 1.0
        return 0.5 if snapshotted else 0.0
    for cand in obj.get("grain_candidates", []):
        if set(cand["columns"]) == set(grain_cols):
            return 1.0 if cand["unique"] else 0.0
    return 0.5


def stems(words: set[str]) -> set[str]:
    return {w[:5] for w in words if len(w) >= 4}


def object_name_score(need: dict[str, Any], fqn: str, obj: dict[str, Any]) -> float:
    """Topic fit: table name/comment vs need key + concepts, plus (weaker) column names."""
    name_tokens = stems(set(tokens(fqn.split(".")[-1] + " " + (obj.get("comment") or ""))))
    key_tokens = set(tokens(need["key"].split(".", 1)[1])) - {"records", "event"}
    concept_tokens = {t for c in need["concepts"] for t in tokens(c)}
    wanted = stems(key_tokens | concept_tokens) - {"table", "recor"}
    hit = len(name_tokens & wanted)
    table_fit = min(1.0, hit / max(1, min(3, len(name_tokens))))
    col_tokens = stems({t for c in obj.get("columns", {}) for t in tokens(c)}) - stems(GENERIC_TOKENS)
    col_fit = len(col_tokens & wanted) / max(1, len(wanted))
    return round(min(1.0, 0.75 * table_fit + 0.5 * col_fit), 3)


def detect_row_filter(need: dict[str, Any], fqn: str, obj: dict[str, Any],
                      evidence: Evidence) -> dict[str, Any] | None:
    """Snapshot needs on mixed tables -> keep snapshot rows; event needs on shared
    tables -> keep matching event rows."""
    hint = need.get("row_filter_hint") or {}
    # Any non-event need on a table that mixes snapshot rows with event rows must
    # keep snapshot rows only, or point-in-time counts double count.
    is_snapshot_need = not hint
    hint_words = {norm_value(w) for c in hint.get("concepts", []) for w in tokens(c)}
    hint_words = {w for w in hint_words if len(w) >= 4}

    def value_matches(normed_value: str) -> bool:
        return any(normed_value == w or normed_value.startswith(w) for w in hint_words)
    for col, meta in obj["columns"].items():
        values = [v for v, _n in meta.get("top_values") or [] if v is not None]
        if not meta.get("is_code") or len(values) < 2:
            continue
        normed = {str(v): norm_value(v) for v in values}
        caveats = [e for e in evidence.get(f"{fqn}.{col}", "term_mentions") if e.get("item_kind") == "caveat"]
        if is_snapshot_need:
            snap = [v for v, n in normed.items() if n in SNAPSHOT_WORDS]
            other = [v for v, n in normed.items() if any(w in n for w in EVENT_WORDS)]
            if snap and other:
                return {"column": col, "op": "in", "values": snap,
                        "confidence": "HIGH" if caveats else "MEDIUM",
                        "evidence": [f"{col} mixes {snap[0]} rows with {other[0]} rows; keep {snap[0]} for point-in-time counts"]
                        + [c["evidence"] for c in caveats]}
        elif hint_words and hint.get("applies_when") == "shared_table":
            dedicated = object_name_score(need, fqn, obj) >= 0.5
            match = [v for v, n in normed.items() if value_matches(n)]
            if match and not dedicated:
                return {"column": col, "op": "in", "values": match, "confidence": "MEDIUM",
                        "evidence": [f"{col} = {match[0]} marks {need['key'].split('.')[1]} events in this shared table"]}
    return None


def match_need(need: dict[str, Any], index: dict[str, Any], evidence: Evidence,
               weights: dict[str, float], learned: dict[str, Any]) -> dict[str, Any]:
    """Best table for the need; if it lacks a required field, the next candidates are tried in turn."""
    res = match_one(need, index, evidence, weights, learned)
    if res["status"] != "unresolved" or not res.get("suggestion") or learned.get("objects", {}).get(need["key"]):
        return res
    for alt in res.get("alternatives", [])[:2]:
        sub = {**index, "objects": {alt["fqn"]: index["objects"][alt["fqn"]]}}
        nxt = match_one(need, sub, evidence, weights, learned)
        if nxt["status"] != "unresolved":
            nxt["object_status"], nxt["status"] = "needs_review", "needs_review"
            nxt["alternatives"] = [{"fqn": res["suggestion"]["fqn"], "score": res["suggestion"]["score"],
                                    "missing": res["suggestion"]["missing"]}]
            return nxt
    return res


PERSON_ID_COLUMNS = {"EMPLOYEE_ID", "EMP_ID", "WORKER_ID", "PERSON_ID", "EMPLOYEE_NUMBER"}
ORG_GRAIN = {"org", "org_group", "executive_org"}
SUMMARY_GRAIN = ORG_GRAIN | {"snapshot_date", "period", "survey_date", "forecast_version"}


def summary_need(need: dict[str, Any]) -> bool:
    """An optional need pre-aggregated by org (and date): e.g. an executive headcount summary or a benchmark."""
    grain = set(need.get("grain") or [])
    return bool(grain & ORG_GRAIN) and grain <= SUMMARY_GRAIN and not need.get("required_in")


def person_level(obj: dict[str, Any]) -> bool:
    """A table with one row per person (it has an employee identifier column)."""
    return any(str(c).upper() in PERSON_ID_COLUMNS for c in obj.get("columns", {}))


def match_one(need: dict[str, Any], index: dict[str, Any], evidence: Evidence,
              weights: dict[str, float], learned: dict[str, Any]) -> dict[str, Any]:
    dropped = learned.get("drop_needs", {}).get(need["key"])
    if dropped:
        return {"fqn": None, "status": "unresolved", "fields": {}, "row_filter": None,
                "dropped_reason": dropped, "alternatives": [], "skills": need["skills"],
                "required_in": need["required_in"]}
    if need["key"].endswith(".semantic_view"):
        svs = index.get("semantic_views", [])
        return {"fqn": svs[0]["fqn"] if svs else None, "kind": "semantic_view",
                "status": "auto" if svs else "unresolved", "fields": {}, "row_filter": None,
                "object_score": 1.0 if svs else 0.0, "alternatives": [], "skills": need["skills"],
                "required_in": need["required_in"]}
    usage_q = index.get("usage", {}).get("object_queries", {})
    top_usage = max(usage_q.values(), default=1)
    applicable_base = {"name", "type", "structure"}
    if index.get("usage", {}).get("queries_parsed"):
        applicable_base.add("usage")
    if index.get("semantic_views"):
        applicable_base.add("semview")
    candidates = []
    forced_fqn = learned.get("objects", {}).get(need["key"])
    for fqn, obj in index["objects"].items():
        if not obj.get("official", True) or not obj.get("columns"):
            continue
        if summary_need(need) and person_level(obj) and fqn != forced_fqn:
            continue  # an optional summary (one row per org/date) is never read from an employee-level table
        per_field = {f: score_field(f, spec, need, fqn, obj, evidence, weights, applicable_base)
                     for f, spec in need["fields"].items()}
        req = [f for f, s in need["fields"].items() if s["required"]] or list(need["fields"])

        def best(f: str) -> float:
            ranked = [c for c in per_field[f] if c["semantic"]]
            return ranked[0]["score"] if ranked else 0.0

        coverage = sum(best(f) for f in req) / len(req)
        top_cols = [next((c["column"] for c in per_field[f] if c["semantic"]), None) for f in req]
        top_cols = [c for c in top_cols if c]
        if len(top_cols) != len(set(top_cols)):
            coverage *= 0.75   # required fields collapsing onto one column is a weak fit
        opt = [f for f, s in need["fields"].items() if not s["required"]]
        opt_cov = sum(best(f) for f in opt) / len(opt) if opt else 0
        obj_score = (0.5 * coverage + 0.1 * opt_cov + 0.2 * object_name_score(need, fqn, obj)
                     + 0.1 * grain_fit(need, obj, per_field)
                     + 0.1 * (usage_q.get(fqn, 0) / top_usage))
        table_stems = stems(set(tokens(fqn.split(".")[-1])))
        if not need.get("row_filter_hint") and table_stems & EVENT_TABLE_STEMS \
                and not stems(set(tokens(need["key"]))) & EVENT_TABLE_STEMS:
            obj_score *= 0.8   # an exits/hires event table is a poor home for a general need
        candidates.append((round(obj_score, 3), fqn, per_field))
    candidates.sort(key=lambda c: -c[0])
    forced = learned.get("objects", {}).get(need["key"])
    if forced:
        candidates.sort(key=lambda c: c[1] != forced)
    if not candidates:
        return {"fqn": None, "status": "unresolved", "fields": {}, "row_filter": None,
                "skills": need["skills"], "required_in": need["required_in"]}
    obj_score, fqn, per_field = candidates[0]
    obj = index["objects"][fqn]
    fields: dict[str, Any] = {}
    used: dict[str, str] = {}
    for fname, spec in need["fields"].items():
        ranked = per_field[fname]
        lkey = f"{need['key']}.{fname}"
        if lkey in learned.get("fields", {}):
            col = learned["fields"][lkey]
            fields[fname] = {"column": col, "expression": None, "score": 1.0, "confidence": "HIGH",
                             "status": "confirmed", "evidence": ["confirmed by analyst"],
                             "breakdown": {}, "alternatives": [c["column"] for c in ranked[:3] if c["column"] != col]}
            used[col] = fname
            continue
        rejected = set(learned.get("rejected", {}).get(lkey, []))
        ranked = [c for c in ranked if c["column"] not in rejected and c["semantic"]]
        for c in ranked:
            if c["column"] in used and used[c["column"]] != fname and c["breakdown"]["name"] < 0.85:
                c["score"] = round(c["score"] - 0.15, 3)
        ranked.sort(key=lambda c: -c["score"])
        if not ranked or ranked[0]["score"] < 0.4:
            fields[fname] = {"column": None, "status": "unresolved", "confidence": "LOW",
                             "score": ranked[0]["score"] if ranked else 0, "evidence": [],
                             "alternatives": [c["column"] for c in ranked[:3]]}
            continue
        best = ranked[0]
        margin = best["score"] - (ranked[1]["score"] if len(ranked) > 1 else 0)
        conf = confidence(best["score"], margin)
        meta = obj["columns"][best["column"]]
        expr = boolean_expression(fname, meta, best["column"]) if spec.get("semantic_type") == "boolean" else None
        fields[fname] = {"column": best["column"], "expression": expr, "score": best["score"],
                         "margin": round(margin, 3), "breakdown": best["breakdown"],
                         "evidence": best["evidence"], "confidence": conf,
                         "status": "auto" if conf == "HIGH" else "needs_review",
                         "alternatives": [{"column": c["column"], "score": c["score"]} for c in ranked[1:4]]}
        used[best["column"]] = fname
    row_filter = detect_row_filter(need, fqn, obj, evidence)
    req_ok = all(fields[f].get("column") for f, s in need["fields"].items() if s["required"])
    non_id = [f for f, s in need["fields"].items() if s.get("semantic_type") != "identifier"]
    if non_id and not any(fields[f].get("column") for f in non_id):
        req_ok = False   # only an ID matched: the object does not carry this data
    req_id_fields = [f for f, s in need["fields"].items()
                     if s["required"] and s.get("semantic_type") == "identifier" and fields[f].get("column")]
    for i, a in enumerate(req_id_fields):
        for b_ in req_id_fields[i + 1:]:
            same_col = fields[a]["column"] == fields[b_]["column"]
            aliases_a = {x.lower() for x in need["fields"][a]["aliases"]}
            aliases_b = {x.lower() for x in need["fields"][b_]["aliases"]}
            synonyms = a.lower() in aliases_b or b_.lower() in aliases_a   # e.g. job_id <-> req_id
            if same_col and not synonyms:
                req_ok = False   # unrelated required identifiers collapsed onto one column: mapping absent
    review = any(f.get("status") == "needs_review" for f in fields.values()) or (
        row_filter and row_filter["confidence"] != "HIGH")
    alt_objects = [{"fqn": c[1], "score": c[0]} for c in candidates[1:3]]
    obj_margin = obj_score - (candidates[1][0] if len(candidates) > 1 else 0)
    if not req_ok:  # never present a table that lacks the data as bound; keep it as a suggestion
        missing = [f for f, s in need["fields"].items() if s["required"] and not fields[f].get("column")]
        return {"fqn": None, "suggestion": {"fqn": fqn, "score": obj_score, "missing": missing},
                "object_score": obj_score, "alternatives": alt_objects, "row_filter": None, "fields": fields,
                "status": "unresolved", "skills": need["skills"], "required_in": need["required_in"]}
    return {"fqn": fqn, "object_score": obj_score, "object_margin": round(obj_margin, 3),
            "object_status": "confirmed" if forced == fqn else ("auto" if obj_margin >= 0.08 else "needs_review"),
            "alternatives": alt_objects, "row_filter": row_filter, "fields": fields,
            "status": "unresolved" if not req_ok else ("needs_review" if review or obj_margin < 0.08 else "auto"),
            "skills": need["skills"], "required_in": need["required_in"]}


def bindings_to_inventory(bindings: dict[str, Any], index: dict[str, Any]) -> dict[str, Any]:
    """Emit the v1 scorer's account_inventory shape (canonical -> physical columns)."""
    objects, gaps = [], []
    usage_q = index.get("usage", {}).get("object_queries", {})
    for key, b in bindings["needs"].items():
        if not b.get("fqn") or b.get("status") == "unresolved":
            gaps.append({"data_need_key": key, "reason": b.get("dropped_reason") or "no matching object"})
            continue
        cols = {f: v["column"] for f, v in b["fields"].items() if v.get("column")}
        confs = [v.get("confidence", "LOW") for v in b["fields"].values() if v.get("column")]
        conf = "HIGH" if confs and all(c == "HIGH" for c in confs) else ("LOW" if "LOW" in confs else "MEDIUM")
        if b.get("kind") == "semantic_view":
            cols, conf = {"view_name": b["fqn"]}, "HIGH"
        objects.append({"data_need_key": key, "fqn": b["fqn"], "columns_present": cols,
                        "official": True, "confidence": conf,
                        "usage": {"queries": usage_q.get(b["fqn"], 0)}})
    return {"scope": index.get("scope", {}), "domain_access": {"people": bool(objects)},
            "objects": objects, "gaps": gaps}


def derive_fiscal_calendar(context: dict[str, Any]) -> dict[str, Any] | None:
    """When no calendar table exists, derive one from a business-context fiscal
    year start (confirmed first, else proposed)."""
    items = [i for i in context.get("items", []) if i["kind"] == "fiscal_calendar"
             and i["status"] != "rejected"]
    if not items:
        return None
    items.sort(key=lambda i: (i["status"] != "confirmed", i["confidence"] != "HIGH"))
    chosen = items[0]
    month = int(chosen["value"]["fiscal_year_start_month"])
    conflict = len({i["value"]["fiscal_year_start_month"] for i in items}) > 1
    fields = {f: {"column": f, "expression": None, "score": 1.0, "derived": True,
                  "confidence": "HIGH" if chosen["status"] == "confirmed" else "MEDIUM",
                  "status": "auto" if chosen["status"] == "confirmed" else "needs_review",
                  "evidence": [f"derived from {chosen['source']['connector']}: fiscal year starts month {month}"]}
              for f in ("date", "fiscal_year", "fiscal_quarter", "fiscal_year_start_date")}
    return {"fqn": "DERIVED.FISCAL_CALENDAR", "kind": "derived",
            "derived": {"type": "fiscal_calendar", "fiscal_year_start_month": month,
                        "context_ids": [i["id"] for i in items], "conflict": conflict},
            "object_score": 1.0, "alternatives": [], "row_filter": None, "fields": fields,
            "status": "auto" if chosen["status"] == "confirmed" and not conflict else "needs_review"}


def topic_scope(topics: dict[str, Any] | None) -> dict[str, set[str]]:
    """need key -> allowed tables, from topics decided in topics.json ('none' -> no table)."""
    scope: dict[str, set[str]] = {}
    for t in (topics or {}).get("topics", []):
        if t.get("status") in {"confirmed", "none"}:
            allowed = list(t.get("chosen") or []) + (list(t.get("fallbacks") or []) if t.get("auto") else [])
            for key in t.get("needs", []):
                scope.setdefault(key, set()).update(f.upper() for f in allowed)
    return scope


def run_match(index: dict[str, Any], graph: dict[str, Any], catalog: dict[str, Any],
              learned: dict[str, Any], weights: dict[str, float] | None = None,
              context: dict[str, Any] | None = None, topics: dict[str, Any] | None = None) -> dict[str, Any]:
    weights = weights or DEFAULT_WEIGHTS
    scope = topic_scope(topics)
    evidence = Evidence(graph)
    needs = merged_needs(catalog)
    out = {"version": 1, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "weights": weights, "needs": {}}
    for key, need in needs.items():
        view = index
        if key in scope:  # the analyst decided which tables serve this need's topic
            view = {**index, "objects": {f: o for f, o in index["objects"].items() if f.upper() in scope[key]}}
        out["needs"][key] = match_need(need, view, evidence, weights, learned)
        if key == "people.calendar.fiscal" and out["needs"][key]["status"] == "unresolved" and context:
            derived = derive_fiscal_calendar(context)
            if derived:
                derived.update({"skills": need["skills"], "required_in": need["required_in"]})
                out["needs"][key] = derived
    statuses = [n["status"] for n in out["needs"].values()]
    out["summary"] = {s: statuses.count(s) for s in ("auto", "needs_review", "unresolved")}
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--learned", type=Path)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--weights", type=Path)
    parser.add_argument("--topics", type=Path, help="topics.json: restrict each need to the tables chosen for its topic")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--inventory-out", type=Path)
    args = parser.parse_args()
    index = load(args.index, {})
    bindings = run_match(index, load(args.graph, {}), load(args.catalog, {}),
                         load(args.learned, {}), load(args.weights, None), load(args.context, None),
                         load(args.topics, None))
    args.out.write_text(json.dumps(bindings, indent=2) + "\n", encoding="utf-8")
    if args.inventory_out:
        args.inventory_out.write_text(json.dumps(bindings_to_inventory(bindings, index), indent=2) + "\n",
                                      encoding="utf-8")
    print(json.dumps({"event": "matched", **bindings["summary"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
