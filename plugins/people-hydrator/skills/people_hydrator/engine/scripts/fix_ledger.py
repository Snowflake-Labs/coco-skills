#!/usr/bin/env python3
"""Fix ledger: every complaint, failed check or number mismatch becomes one item with a
proposed, validated fix the analyst approves. Stdlib only.

Sources of items
  feedback   free text from Tune ("include people on leave, exclude interns")
  check      a failing validation gate for a skill (check_bindings.py report)
  number     a known-number mismatch (numbers.py), reconciled against other definitions

Proposals are a short list of ALLOWED operations, never SQL:
  set_values   {tunable, value: [..]}        values must exist in the bound column
  set_method   {value: {denominator, annualized}}
  set_number   {tunable, value: int}         within the setting's bounds
  set_choice   {tunable, value}              one of the answers the data supports (definitions)
  bind_column  {need, field, column}         column must exist in the need's table
Rules interpret common phrasings first; with --ai, Snowflake Cortex (AI_COMPLETE in
the customer's account, metadata-only prompt) is asked for the same JSON shape and
its answer goes through the same validator. Anything that doesn't validate stays `unresolved` with the analyst's text
kept as a note: nothing is guessed into the bindings.

  fix_ledger.py feedback --workspace W --skill S --text "..." [--ai --connection C]
  fix_ledger.py reconcile --workspace W --skill S --metric M --target N --connection C
                         (try other definitions until the metric equals the analyst's number)
  fix_ledger.py collect  --workspace W            (items from validation + numbers)
  fix_ledger.py apply    --workspace W --id I
  fix_ledger.py dismiss  --workspace W --id I
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import questions as Q  # noqa: E402

ENUM_FIELDS = {  # tunable -> where its values live
    "regular_employee_types": {"needs": ["people.headcount.employee_snapshot", "people.org_health.employee_snapshot"],
                               "field": "employee_type"},
    "active_status_values": {"needs": ["people.headcount.employee_snapshot", "people.org_health.employee_snapshot"],
                             "field": "employment_status"},
    "voluntary_values": {"needs": ["people.exits.event_records"], "field": "termination_category"},
    "open_req_status_values": {"needs": ["people.ta.requisitions", "people.reqs.open_positions"], "field": "status"},
}
NUMBER_BOUNDS = {"fiscal_year_start_month": (1, 12), "attrition_window_months": (1, 36),
                 "span_over_threshold": (2, 50), "span_under_threshold": (1, 20), "new_hire_window_months": (1, 36)}
REMOVE = re.compile(r"\b(exclude|excluding|without|remove|drop|not count|don'?t count|do not count|shouldn'?t|"
                    r"no longer|minus|except)\b", re.I)
ADD = re.compile(r"\b(include|including|add|also count|count|plus|with)\b", re.I)
STOP = {"on", "the", "of", "a", "an", "and", "or", "in", "to", "for"}
MONTHS = {m.lower(): i + 1 for i, m in enumerate(Q.MONTHS)} | {m[:3].lower(): i + 1 for i, m in enumerate(Q.MONTHS)}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def ledger(ws: Path) -> dict[str, Any]:
    return Q.load(ws / "fix_ledger.json", {"items": []}) or {"items": []}


def put(ws: Path, item: dict[str, Any]) -> dict[str, Any]:
    data = ledger(ws)
    data["items"] = [i for i in data["items"] if i["id"] != item["id"]] + [item]
    Q.save(ws / "fix_ledger.json", data)
    return item


def item_id(*parts: Any) -> str:
    return hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()[:10]


# ---------- context the validator and the AI share -----------------------------------

def facts(ws: Path) -> dict[str, Any]:
    tunables: dict[str, Any] = {}
    for path in sorted((ws / "tuning").glob("*.json")):
        for tid, t in (Q.load(path, {}) or {}).get("tunables", {}).items():
            cur = tunables.setdefault(tid, {"label": t.get("label"), "type": t.get("type"), "value": t.get("value"),
                                            "method": t.get("method"), "skills": []})
            cur["skills"].append(path.stem)
    for tid, spec in ENUM_FIELDS.items():
        if tid in tunables:
            tunables[tid]["observed"], tunables[tid]["observed_in"] = Q.observed_values(ws, spec)
    import definitions as D  # business definitions: the answers the data supports right now
    for d in D.library():
        if d["writes_to"] in tunables and (d["kind"] in {"date_choice", "date_span"} or any(
                a.get("requires") for a in d.get("common_answers", []))):
            opts, _note = D.options(ws, d)
            tunables[d["writes_to"]]["choices"] = [{"value": o["value"], "label": o["label"]} for o in opts]
    for s in D.cached_suggestions(ws, D.all_skills()):
        if s["id"] in tunables:
            tunables[s["id"]]["choices"] = s["options"]
    bindings = Q.load(ws / "bindings.json", {}).get("needs", {})
    index = Q.load(ws / "data_index.json", {"objects": {}})
    needs = {}
    for key, b in bindings.items():
        if not b.get("fqn") or b.get("status") == "unresolved":
            continue
        cols = index["objects"].get(b["fqn"], {}).get("columns", {})
        needs[key] = {"table": b["fqn"], "skills": b.get("skills", []),
                      "fields": {f: v.get("column") for f, v in (b.get("fields") or {}).items()},
                      "columns": {c: m.get("data_type") for c, m in cols.items()}}
    return {"tunables": tunables, "needs": needs, "catalog_needs": catalog_needs(ws, bindings),
            "tables": scanned_tables(ws)}


def catalog_needs(ws: Path, bindings: dict[str, Any]) -> dict[str, Any]:
    """Every data need of the chosen skills, bound or not (bind_table can fill an empty one)."""
    import discover_topics as T
    skills = set((Q.load(ws / "topics.json", {}) or {}).get("skills") or [])
    out: dict[str, Any] = {}
    for s in T.load(T.CATALOG_DIR / "people.json")["skills"]:
        for n in s["data_needs"]:
            if n["key"].endswith(".semantic_view") or (skills and s["name"] not in skills):
                continue
            e = out.setdefault(n["key"], {"label": T.need_label(n), "concepts": n.get("concepts", []),
                                          "fields": n.get("fields", {}), "skills": [],
                                          "table": (bindings.get(n["key"]) or {}).get("fqn")})
            e["skills"].append(s["name"])
    return out


def scanned_tables(ws: Path) -> dict[str, Any]:
    objs = (Q.load(ws / "scan_metadata.json", {"objects": {}}) or {}).get("objects", {})
    return {fqn.upper(): {"columns": {c.upper() for c in o.get("columns", {})}, "official": o.get("official", True)}
            for fqn, o in objs.items()}


def resolve_table(name: str, tables: dict[str, Any]) -> str | None:
    """Full or short table name -> the one scanned table it means (None when unknown or ambiguous)."""
    n = name.strip().strip("`'\"").upper()
    if n in tables:
        return n
    hits = [f for f in tables if f.rsplit(".", 1)[-1] == n or f.endswith("." + n)]
    return hits[0] if len(hits) == 1 else None


def missing_fields(need: dict[str, Any], cols: set[str]) -> list[str]:
    """Required fields the table has no column for (a field only generic names describe is lenient)."""
    import discover_topics as T
    low = {c.lower() for c in cols}
    out = []
    for fname, spec in need["fields"].items():
        if not spec.get("required"):
            continue
        alts = ({a.lower() for a in spec.get("aliases", [])} | {fname.lower()}) - T.GENERIC_ALIASES
        if alts and not any(T.col_match(a, low) for a in alts):
            out.append(fname.replace("_", " "))
    return out


TABLE_RULE = re.compile(r"\b(?:use|take|map|bind|point)\s+(?P<a>[\w.$]+)\s+(?:for|as|to)\s+(?P<b>[^.;\n]+)"
                        r"|(?P<c>[^.;\n]+?)\s+(?:is|are|lives?|comes?)\s+(?:in|from)\s+(?P<d>[\w.$]+)", re.I)


def need_from_words(text: str, f: dict[str, Any]) -> str | None:
    """The data need a phrase names: most words in common with its label and concepts."""
    w = words(text) - STOP
    best, score = None, 0
    for key, n in f.get("catalog_needs", {}).items():
        nw = words(" ".join([n["label"], *n["concepts"], key.split(".")[-1].replace("_", " ")])) - STOP
        s = len(w & nw)
        if s > score:
            best, score = key, s
    return best if score >= 2 or (score == 1 and len(w) <= 2) else None


def table_proposal(text: str, f: dict[str, Any]) -> list[dict[str, Any]]:
    """'use HM_FEEDBACK_SURVEY for hiring manager sentiment' -> bind_table."""
    ops = []
    for m in TABLE_RULE.finditer(text):
        table_txt, need_txt = (m.group("a"), m.group("b")) if m.group("a") else (m.group("d"), m.group("c"))
        fqn = resolve_table(table_txt, f.get("tables", {}))
        need = need_from_words(need_txt, f) if fqn else None
        if fqn and need:
            ops.append({"op": "bind_table", "need": need, "fqn": fqn})
    return ops


def words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        out.add(w)
        if len(w) > 3 and w.endswith("s"):
            out.add(w[:-1])
    return out


# ---------- rules ------------------------------------------------------------------------

def for_skill(f: dict[str, Any], skill: str | None) -> dict[str, Any]:
    """Only the settings and data this skill uses (feedback on headcount can't change requisitions)."""
    if not skill:
        return f
    return {"tunables": {k: v for k, v in f["tunables"].items() if skill in v["skills"]},
            "needs": {k: v for k, v in f["needs"].items() if skill in v["skills"]},
            "catalog_needs": {k: v for k, v in f.get("catalog_needs", {}).items() if skill in v["skills"]},
            "tables": f.get("tables", {})}


def rule_proposal(text: str, f: dict[str, Any]) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = table_proposal(text, f)
    clauses = re.split(r"[.;\n]|,\s*(?:but|and)\b|\bbut\b", text)
    for tid, t in f["tunables"].items():
        observed = t.get("observed") or []
        if not observed:
            continue
        current = [str(v) for v in (t.get("value") or [])]
        new = list(current)
        for clause in clauses:
            hits = [v for v in observed if (words(v) - STOP) and (words(v) - STOP) <= words(clause)]
            if not hits:
                continue
            removing = bool(REMOVE.search(clause))
            adding = bool(ADD.search(clause)) and not removing
            for v in hits:
                if removing and v in new:
                    new.remove(v)
                elif adding and v not in new:
                    new.append(v)
        if sorted(new) != sorted(current):
            ops.append({"op": "set_values", "tunable": tid, "value": new})
    low = text.lower()
    if "attrition_rate_method" in f["tunables"]:
        cur = dict(f["tunables"]["attrition_rate_method"].get("method") or {"denominator": "start_end", "annualized": False})
        m = dict(cur)
        if re.search(r"\bannuali[sz]", low):
            m["annualized"] = not re.search(r"\b(not|don'?t|no)\s+annuali", low)
        if re.search(r"\b(start|beginning)\b.{0,25}\b(and|\+|&)\s*(the\s+)?end\b", low):
            m["denominator"] = "start_end"
        elif re.search(r"\b(start|beginning) (of|headcount)", low):
            m["denominator"] = "start"
        elif re.search(r"\bend (of|headcount)", low):
            m["denominator"] = "end"
        elif re.search(r"\baverage\b", low):
            m["denominator"] = "average"
        if m != cur:
            ops.append({"op": "set_method", "value": m})
    if "fiscal" in low:
        month = next((n for w, n in MONTHS.items() if re.search(rf"\b{w}\b", low)), None)
        if month and "fiscal_year_start_month" in f["tunables"]:
            ops.append({"op": "set_number", "tunable": "fiscal_year_start_month", "value": month})
    win = re.search(r"\b(\d{1,2})[- ]?months?\b", low)
    if win:
        tid = "new_hire_window_months" if re.search(r"new hire|tenure", low) else "attrition_window_months"
        if tid in f["tunables"] and str(f["tunables"][tid].get("value")) != win.group(1):
            ops.append({"op": "set_number", "tunable": tid, "value": int(win.group(1))})
    for key, n in f["needs"].items():  # "use LAST_DAY_OF_WORK" -> the same-type field in that table
        for col, dtype in n["columns"].items():
            if re.search(rf"\b{re.escape(col.lower())}\b", low) and col not in n["fields"].values():
                same = [fl for fl, c in n["fields"].items() if c and n["columns"].get(c) == dtype]
                if len({n["fields"][fl] for fl in same}) == 1:  # one column, possibly under several field names
                    ops += [{"op": "bind_column", "need": key, "field": fl, "column": col} for fl in same]
    return dedupe(ops)


def already(text: str, f: dict[str, Any]) -> list[str]:
    """Requests that the current settings already satisfy (so the analyst hears it, not silence)."""
    notes = []
    for clause in re.split(r"[.;\n]|,\s*(?:but|and)\b|\bbut\b", text):
        removing, adding = bool(REMOVE.search(clause)), bool(ADD.search(clause))
        for t in f["tunables"].values():
            if not t.get("observed") or not isinstance(t.get("value"), list):
                continue
            current = {str(v) for v in t["value"]}
            for v in t["observed"]:
                if (words(v) - STOP) and (words(v) - STOP) <= words(clause):
                    if removing and v not in current:
                        notes.append(f"{v} is already excluded ({t['label']})")
                    elif adding and not removing and v in current:
                        notes.append(f"{v} is already included ({t['label']})")
    return notes


def dedupe(ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for o in ops:
        k = json.dumps(o, sort_keys=True)
        if k not in seen:
            seen.add(k)
            out.append(o)
    return out


# ---------- AI (bounded) ------------------------------------------------------------------

AI_MODELS = ("claude-sonnet-4-5", "llama3.3-70b")


def ai_proposal(text: str, skill: str, f: dict[str, Any], connection: str | None) -> tuple[list[dict[str, Any]], str]:
    """Ask Snowflake Cortex (AI_COMPLETE, in the customer's own account) for the same JSON shape.

    Only configuration metadata goes into the prompt: setting names, current values,
    code values seen in the data, field-to-column names. No rows, no person-level data.
    """
    from _sql import SqlError, make_runner, quote_literal
    compact = {"tunables": {k: {x: v.get(x) for x in ("label", "value", "method", "observed", "choices")} for k, v in f["tunables"].items()
                            if skill in v["skills"]},
               "needs": {k: {"table": v["table"].rsplit(".", 1)[1], "fields": v["fields"], "columns": v["columns"]}
                         for k, v in f["needs"].items() if skill in v["skills"]}}
    prompt = ("You map an HR analyst's correction to configuration changes, using only the JSON below.\n"
              f"Correction for skill {skill}: {text!r}\n"
              f"Current configuration: {json.dumps(compact, default=str)[:12000]}\n"
              "Allowed operations (use only setting ids, values and columns that appear above):\n"
              ' {"op":"set_values","tunable":ID,"value":[observed values]}\n'
              ' {"op":"set_method","value":{"denominator":"average|start|end","annualized":true|false}}\n'
              ' {"op":"set_number","tunable":ID,"value":INT}\n'
              ' {"op":"bind_column","need":KEY,"field":FIELD,"column":COLUMN}\n'
              ' {"op":"set_choice","tunable":ID,"value":one of that setting\'s choices[].value}\n'
              'Reply with ONLY JSON: {"ops":[...],"uncertain":"..."}. '
              "If part of the correction can't be expressed with these operations, leave it out. "
              "`uncertain` is shown to a non-technical HR analyst: one short sentence in plain business words, "
              "never setting ids, column names, code or words like tunable/operation/config; empty if nothing.")
    try:
        run = make_runner(connection, 120)
    except Exception as exc:  # noqa: BLE001
        return [], f"assistant unavailable ({type(exc).__name__})"
    reply, last_err = None, ""
    for model in AI_MODELS:
        try:
            rows = run(f"select ai_complete({quote_literal(model)}, {quote_literal(prompt)}) as r")
            reply = str(rows[0]["R"]) if rows else None
            break
        except SqlError as exc:
            last_err = str(exc)[:120]
    if not reply:
        return [], f"assistant unavailable ({last_err or 'no reply'})"
    try:
        reply = json.loads(reply) if reply.startswith('"') else reply  # AI_COMPLETE may return a JSON string
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", str(reply), re.S)
    if not m:
        return [], "assistant reply had no proposal"
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return [], "assistant reply was not valid JSON"
    return list(data.get("ops") or []), str(data.get("uncertain") or "")


# ---------- validation + description ------------------------------------------------------

def validate(ops: list[dict[str, Any]], f: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    ok, problems = [], []
    for o in ops:
        kind = o.get("op")
        if kind == "set_values":
            t = f["tunables"].get(o.get("tunable"))
            vals = o.get("value")
            if not t or not isinstance(vals, list) or not vals:
                problems.append(f"unknown setting or empty values: {o.get('tunable')}")
            elif t.get("observed") and not set(map(str, vals)) <= set(t["observed"]):
                problems.append(f"{t['label']}: {sorted(set(map(str, vals)) - set(t['observed']))} not in the data")
            else:
                ok.append({"op": kind, "tunable": o["tunable"], "value": [str(v) for v in vals]})
        elif kind == "set_method":
            v = o.get("value") or {}
            if v.get("denominator") in {"start_end", "average", "start", "end"} and isinstance(v.get("annualized"), bool):
                ok.append({"op": kind, "value": {"denominator": v["denominator"], "annualized": v["annualized"]}})
            else:
                problems.append("attrition method must name average/start/end and annualized true/false")
        elif kind == "set_number":
            lo, hi = NUMBER_BOUNDS.get(o.get("tunable"), (None, None))
            try:
                n = int(o.get("value"))
            except (TypeError, ValueError):
                n = None
            if lo is None or o.get("tunable") not in f["tunables"] or n is None or not lo <= n <= hi:
                problems.append(f"{o.get('tunable')}: value {o.get('value')} not allowed")
            else:
                ok.append({"op": kind, "tunable": o["tunable"], "value": n})
        elif kind == "set_choice":
            t = f["tunables"].get(o.get("tunable"))
            allowed = [c["value"] for c in (t or {}).get("choices") or []]
            if not t or o.get("value") not in allowed:
                problems.append(f"{(t or {}).get('label') or o.get('tunable')}: that answer isn't one your data supports")
            else:
                ok.append({"op": kind, "tunable": o["tunable"], "value": o["value"]})
        elif kind == "bind_column":
            n = f["needs"].get(o.get("need"))
            if not n or o.get("field") not in n["fields"] or o.get("column") not in n["columns"]:
                problems.append(f"column {o.get('column')} isn't in the table used for {o.get('need')}")
            else:
                ok.append({"op": kind, "need": o["need"], "field": o["field"], "column": o["column"]})
        elif kind == "bind_table":
            need = f.get("catalog_needs", {}).get(o.get("need"))
            fqn = resolve_table(str(o.get("fqn") or ""), f.get("tables", {}))
            if not need:
                problems.append(f"{o.get('need')} isn't something these skills use")
            elif not fqn:
                problems.append(f"we can't find a table called {o.get('fqn')} in the scanned databases")
            elif not f["tables"][fqn]["official"]:
                problems.append(f"{fqn.rsplit('.', 1)[1]} looks like a dev/test/backup copy; name the production table")
            else:
                gap = missing_fields(need, f["tables"][fqn]["columns"])
                if gap:
                    problems.append(f"{fqn.rsplit('.', 1)[1]} has no column for {', '.join(gap)}, "
                                    f"which {need['label'].lower()} needs")
                else:
                    ok.append({"op": kind, "need": o["need"], "fqn": fqn})
        else:
            problems.append(f"operation {kind!r} is not allowed")
    return [o for o in dedupe(ok) if not noop(o, f)], problems


def noop(o: dict[str, Any], f: dict[str, Any]) -> bool:
    """A proposal that changes nothing (e.g. rebinding a field to the column it already uses)."""
    if o["op"] == "bind_column":
        return f["needs"][o["need"]]["fields"].get(o["field"]) == o["column"]
    if o["op"] == "bind_table":
        return (f["catalog_needs"][o["need"]].get("table") or "").upper() == o["fqn"]
    if o["op"] == "set_method":
        return (f["tunables"].get("attrition_rate_method", {}).get("method")
                or {"denominator": "start_end", "annualized": False}) == o["value"]
    cur = f["tunables"][o["tunable"]].get("value")
    if isinstance(cur, list):
        return sorted(map(str, cur)) == sorted(map(str, o["value"]))
    return str(cur) == str(o["value"])


def describe(op: dict[str, Any], f: dict[str, Any]) -> str:
    if op["op"] == "set_values":
        t = f["tunables"][op["tunable"]]
        return f"{t['label']}: {', '.join(map(str, t.get('value') or [])) or 'none'} → {', '.join(op['value'])}"
    if op["op"] == "set_method":
        v = op["value"]
        return f"Attrition rate: exits / {v['denominator']} monthly headcount{', annualized' if v['annualized'] else ''}"
    if op["op"] == "set_choice":
        t = f["tunables"][op["tunable"]]
        lab = {json.dumps(c["value"], sort_keys=True): c["label"] for c in t.get("choices") or []}
        return f"{t['label']}: {lab.get(json.dumps(t.get('value'), sort_keys=True), t.get('value') or 'not set')} → " \
               f"{lab.get(json.dumps(op['value'], sort_keys=True), op['value'])}"
    if op["op"] == "set_number":
        t = f["tunables"][op["tunable"]]
        val = Q.MONTHS[op["value"] - 1] if op["tunable"] == "fiscal_year_start_month" else op["value"]
        return f"{t['label']}: {t.get('value')} → {val}"
    if op["op"] == "bind_table":
        n = f["catalog_needs"][op["need"]]
        cur = (n.get("table") or "").rsplit(".", 1)[-1] or "nothing"
        return f"{n['label']}: {cur} → {op['fqn'].rsplit('.', 1)[1]}"
    n = f["needs"][op["need"]]
    return f"{op['field'].replace('_', ' ')} in {n['table'].rsplit('.', 1)[1]}: {n['fields'].get(op['field'])} → {op['column']}"


def affected(ops: list[dict[str, Any]], f: dict[str, Any]) -> list[str]:
    out: set[str] = set()
    for o in ops:
        if o["op"] == "bind_column":
            out |= set(f["needs"][o["need"]]["skills"])
        elif o["op"] == "bind_table":
            out |= set(f["catalog_needs"][o["need"]]["skills"])
        else:
            out |= set(f["tunables"].get(o.get("tunable") or "attrition_rate_method", {}).get("skills", []))
    return sorted(out)


def effects(ws: Path | None, ops: list[dict[str, Any]], connection: str | None = None,
            skills: list[str] | None = None) -> list[str]:
    """Preview numbers before -> after: re-runs only the metrics this change touches (one batched round)."""
    if ws is None or not connection or not ops:
        return []
    import check_bindings as CB
    import preview as P
    from _sql import SqlError, make_runner, run_many, to_number
    bindings, tuning = Q.load(ws / "bindings.json", {}), P.tuning_of(ws)
    if not bindings:
        return []
    b, t = apply_ops_copy(ops, bindings, tuning)
    todo, seen = [], set()
    for skill in skills or sorted(tuning):
        before, after = CB.skill_metrics(skill, bindings, tuning), CB.skill_metrics(skill, b, t)
        current = {m["id"]: m for m in preview_metrics(ws, skill)}
        for mid, sql in after.items():
            if mid in current and sql != before.get(mid) and mid not in seen:
                seen.add(mid)
                todo.append((current[mid], sql))
    if not todo:
        return []
    try:
        results = run_many(make_runner(connection, 120), [sql for _, sql in todo])
    except SqlError:
        return []
    out = []
    for (m, _), rows in zip(todo, results):
        v = to_number(rows[0].get("VALUE")) if isinstance(rows, list) and rows else None
        if isinstance(v, (int, float)) and not same(float(v), float(m["value"])):
            out.append(f"{m['label']}: {m['value']:g} → {float(v):g}")
    return out


def proposal(ops: list[dict[str, Any]], f: dict[str, Any], source: str, note: str = "",
             ws: Path | None = None, connection: str | None = None) -> dict[str, Any]:
    return {"ops": ops, "changes": [describe(o, f) for o in ops], "affects": affected(ops, f), "source": source,
            "note": note, "effects": effects(ws, ops, connection, affected(ops, f))}


# ---------- actions -------------------------------------------------------------------------

# ---------- numbers: "it should be 120" ------------------------------------------------------

NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(%?)")
ASK_WHICH = ("Tell us which number looks off and what you get. We'll try other definitions on your data "
             "and show the one that matches.")


def numbers_in(text: str) -> list[float]:
    """Numbers the analyst quotes, skipping years, month windows and day counts."""
    out = []
    for m in NUM.finditer(text):
        n = float(m.group(1).replace(",", ""))
        tail = text[m.end():m.end() + 8].lower()
        if (1990 <= n <= 2100 and not m.group(2)) or re.match(r"\s*(months?|mos?|days?|years?|yrs?)\b", tail):
            continue
        out.append(n)
    return out


def preview_metrics(ws: Path, skill: str) -> list[dict[str, Any]]:
    p = Q.load(ws / "previews" / f"{skill}.json", {}) or {}
    return [m for m in p.get("metrics", []) if isinstance(m.get("value"), (int, float))]


def same(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.05, abs(b) * 0.005)


def pick_metric(text: str, n: float, metrics: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The metric the analyst means: most label words in common, then closest value."""
    if not metrics:
        return None
    w = words(text) | ({"exit", "termination", "attrition"} if words(text) & {"exit", "term", "termination", "leaver"} else set())
    pct = "%" in text or "percent" in text.lower() or "rate" in text.lower()
    def score(m: dict[str, Any]) -> tuple[int, int, float]:
        overlap = len((words(m["label"]) - STOP - {"last", "month", "12"}) & w)
        unit_ok = int((m.get("unit") == "%") == pct)
        return overlap, unit_ok, -abs(float(m["value"]) - n) / (abs(n) + 1)
    return max(metrics, key=score)


def defs_for(ws: Path, skill: str, label: str) -> list[str]:
    p = Q.load(ws / "previews" / f"{skill}.json", {}) or {}
    key = words(label) - STOP - {"last", "month", "12", "rate", "average"}
    key |= {"exit", "termination", "voluntary"} if key & {"exit", "attrition"} else set()
    return [d for d in p.get("definitions", []) if words(d) & key][:2]


def apply_ops_copy(ops: list[dict[str, Any]], bindings: dict[str, Any], tuning: dict[str, Any]) -> tuple[dict, dict]:
    """The same changes `apply` would make, on in-memory copies (nothing is saved)."""
    b, t = json.loads(json.dumps(bindings)), json.loads(json.dumps(tuning))
    for o in ops:
        if o["op"] == "bind_column":
            b["needs"][o["need"]].setdefault("fields", {}).setdefault(o["field"], {})["column"] = o["column"]
            continue
        if o["op"] == "bind_table":  # needs a re-match; its effect shows after apply
            continue
        tid = "attrition_rate_method" if o["op"] == "set_method" else o["tunable"]
        holders = [sk for sk in t.values() if tid in sk.get("tunables", {})]
        for sk in holders or t.values():  # a definition not set yet (def_*) is tried on every skill
            sk.setdefault("tunables", {}).setdefault(tid, {})["value"] = o["value"]
    return b, t


def variants(f: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """Single-change alternatives worth trying: one value in/out, all values, window, method, date column."""
    out: list[list[dict[str, Any]]] = []
    for tid, t in f["tunables"].items():
        obs, cur = t.get("observed") or [], [str(v) for v in (t.get("value") or [])] if isinstance(t.get("value"), list) else None
        if obs and cur is not None:
            for v in obs:
                new = [x for x in cur if x != v] if v in cur else cur + [v]
                if new:
                    out.append([{"op": "set_values", "tunable": tid, "value": new}])
            out.append([{"op": "set_values", "tunable": tid, "value": list(obs)}])
        if tid in {"attrition_window_months", "new_hire_window_months"}:
            out += [[{"op": "set_number", "tunable": tid, "value": n}] for n in (3, 6, 12, 24)]
    if "attrition_rate_method" in f["tunables"]:
        out += [[{"op": "set_method", "value": {"denominator": d, "annualized": a}}]
                for d in ("start_end", "average", "start", "end") for a in (False, True)]
    for key, n in f["needs"].items():
        for field, col in n["fields"].items():
            dtype = str(n["columns"].get(col) or "")
            if col and re.match(r"(DATE|TIMESTAMP)", dtype.upper()):
                out += [[{"op": "bind_column", "need": key, "field": field, "column": c}]
                        for c, d in n["columns"].items() if c != col and str(d).upper().split("(")[0] == dtype.upper().split("(")[0]]
    return out


EVENT_FOR = {"exits_window": ("people.exits.event_records", "termination_date", "exits"),
             "hires_window": ("people.hires.event_records", "hire_date", "hires")}


def after_as_of(run: Any, skill: str, metric_id: str, bindings: dict[str, Any]) -> dict[str, Any] | None:
    """Events dated after the latest month-end snapshot (the analyst may count them; we don't yet)."""
    import check_bindings as CB
    import metrics_sql as M
    if metric_id not in EVENT_FOR:
        return None
    key, field, what = EVENT_FOR[metric_id]
    ev = CB.enriched_need(key, bindings)
    snap_key = {"attrition_insights": "people.attrition.snapshot_monthly"}.get(skill, "people.headcount.employee_snapshot")
    snap = CB.enriched_need(snap_key, bindings)
    if not (ev.has(field) and snap.has("snapshot_date")):
        return None
    as_of = M.as_of_sql(snap)
    try:
        row = CB.scalar(run, f"select count(*) as n, {as_of}::string as d from {ev.table}"
                             f"{ev.where([ev.col(field) + ' > ' + as_of])}")
    except Exception:  # noqa: BLE001
        return None
    n = int(row.get("N") or 0)
    return {"after": n, "as_of": row.get("D"), "what": what} if n else None


def reconcile(ws: Path, connection: str, skill: str, metric_id: str, target: float,
              text: str = "", max_runs: int = 30) -> dict[str, Any]:
    """Try other definitions of one metric on the customer's data; propose the one that hits `target`."""
    import check_bindings as CB
    import preview as P
    from _sql import SqlError, make_runner, to_number
    full = facts(ws)
    f = for_skill(full, skill)
    bindings, tuning = Q.load(ws / "bindings.json", {}), P.tuning_of(ws)
    metric = next((m for m in preview_metrics(ws, skill) if m["id"] == metric_id), None)
    label = (metric or {}).get("label") or P.LABELS.get(metric_id, (metric_id,))[0]
    base_sql = CB.skill_metrics(skill, bindings, tuning).get(metric_id)
    item = {"id": item_id("reconcile", skill, metric_id, target), "kind": "feedback", "skills": [skill],
            "text": text or f"{label} should be {target:g}", "created_at": now(), "problems": [],
            "metric": metric_id, "target": target}
    if not base_sql:
        item.update(status="unresolved", proposal=None, why=f"We can't compute {label} for this skill yet.", ask=True)
        return put(ws, item)
    run = make_runner(connection, 120)
    seen, tried, hits = {base_sql}, [], []
    for ops in variants(f):
        ops, _ = validate(ops, f)
        if not ops:
            continue
        b, t = apply_ops_copy(ops, bindings, tuning)
        sql = CB.skill_metrics(skill, b, t).get(metric_id)
        if not sql or sql in seen:  # this change doesn't touch the metric
            continue
        seen.add(sql)
        if len(tried) >= max_runs:
            break
        try:
            val = to_number(CB.scalar(run, sql).get("VALUE"))
        except SqlError:
            continue
        if isinstance(val, (int, float)):
            tried.append((ops, float(val)))
            if same(float(val), target):
                hits.append((ops, float(val)))
    current = (metric or {}).get("value")
    gap = None if hits else after_as_of(run, skill, metric_id, bindings)
    if gap and isinstance(current, (int, float)) and same(float(current) + gap["after"], target):
        item.update(status="resolved", proposal=None,
                    why=(f"Your {target:g} includes {gap['after']} {gap['what']} dated after {gap['as_of']}, the latest "
                         f"month-end in your headcount data. We count up to that date so {gap['what']} and headcount "
                         f"cover the same months, which gives {current:g}. Once the next month's snapshot lands, "
                         f"those {gap['after']} will be counted. Nothing to change."))
        put(ws, item)
        remember(ws, item)  # explained: keep it so a later change that breaks the explanation is caught
        return item
    if hits:
        ops, val = hits[0]
        note = f"With this change, {label} becomes {val:g} (now {current})."
        item.update(status="proposed", proposal=proposal(ops, full, "number match", note),
                    others=[describe(o, full) for h in hits[1:3] for o in h[0]])
        item["proposal"]["effects"] = [f"{label}: {current} → {val:g}"]
    else:
        near = sorted([x for x in tried if not (isinstance(current, (int, float)) and same(x[1], float(current)))],
                      key=lambda x: abs(x[1] - target))[:3]
        if gap:
            near.insert(0, ([], float(current or 0) + gap["after"]))
        item.update(status="unresolved", proposal=None, ask=True,
                    why=(f"We tried {len(tried)} other definitions of {label} on your data and none gives {target:g}. "
                         f"Today it's {current}." + (" Closest: " + "; ".join(
                             f"{describe(o[0], full) if o else 'including ' + gap['what'] + ' after ' + gap['as_of']} gives {v:g}"
                             for o, v in near) + "." if near else "")
                         + " The difference may come from data that isn't in these tables, or a different date range."),
                    near=[{"changes": [describe(o, full) for o in ops] or [f"including {gap['what']} after {gap['as_of']}"],
                           "value": v} for ops, v in near])
    return put(ws, item)


# ---------- plain language -----------------------------------------------------------------

def plain(text: str, f: dict[str, Any]) -> str:
    """Replace internal setting ids / column names with labels; drop jargon."""
    if not text:
        return ""
    for tid, t in f["tunables"].items():
        text = re.sub(rf"`?\b{re.escape(tid)}\b`?", (t.get("label") or tid.replace("_", " ")).lower(), text)
    for n in f["needs"].values():
        for field, col in n["fields"].items():
            if col:
                text = re.sub(rf"`?\b{re.escape(col)}\b`?", field.replace("_", " "), text)
    text = re.sub(r"`?\b[a-z]+(?:_[a-z0-9]+)+\b`?", lambda m: m.group(0).strip("`").replace("_", " "), text)
    text = re.sub(r"\btunables?\b", "setting", text, flags=re.I)
    return re.sub(r"\b(ops?|operations?)\b", "change", text, flags=re.I)


def from_feedback(ws: Path, skill: str, text: str, use_ai: bool, connection: str | None) -> dict[str, Any]:
    full = facts(ws)
    f = for_skill(full, skill)
    table_ops = table_proposal(text, f)
    ops, problems = validate(rule_proposal(text, f), f)
    if table_ops and not ops:  # "use X for Y": answer it directly, whether it's already true or can't work
        o = table_ops[0]
        n = f["catalog_needs"][o["need"]]
        if (n.get("table") or "").upper() == o["fqn"]:
            why = f"{n['label']} already uses {o['fqn'].rsplit('.', 1)[1]}. Nothing to change."
            status = "resolved"
        else:
            why = (problems[0].rstrip(".") if problems else "That table can't be used for this") + \
                  ". Name another table, or tell us how this is recorded."
            status = "unresolved"
        item = {"id": item_id("feedback", skill, text), "kind": "feedback", "skills": [skill], "text": text,
                "created_at": now(), "problems": problems, "status": status, "proposal": None, "why": why,
                "ask": status == "unresolved"}
        return put(ws, item)
    source, note = "rules", ""
    nums, metrics = numbers_in(text), preview_metrics(ws, skill)
    if not ops and nums and metrics:  # a number: confirm it, or find the definition that gives it
        for n in nums:
            m = next((m for m in metrics if same(float(m["value"]), n)), None)
            if m:
                d = defs_for(ws, skill, m["label"])
                item = {"id": item_id("feedback", skill, text), "kind": "feedback", "skills": [skill], "text": text,
                        "created_at": now(), "problems": [], "status": "resolved", "proposal": None,
                        "why": f"That matches: {m['label']} is already {m['value']:g}. "
                               + (" ".join(d) + "." if d else "") + " Nothing to change."}
                return put(ws, item)
        n = nums[0]
        m = pick_metric(text, n, metrics)
        if m and connection:
            return reconcile(ws, connection, skill, m["id"], n, text)
    if use_ai and connection:  # the assistant also sees what the rules found, so it can add the rest
        raw, note = ai_proposal(text, skill, f, connection)
        more, more_problems = validate(raw, f)
        if more:
            ops, source = dedupe(ops + more), ("rules + assistant" if ops else "assistant")
        problems += more_problems
    note = plain(note, full)
    item = {"id": item_id("feedback", skill, text), "kind": "feedback", "skills": [skill], "text": text,
            "created_at": now(), "problems": [plain(p, full) for p in problems], "already": already(text, f)}
    if ops:
        item.update(status="proposed", proposal=proposal(ops, full, source, note, ws, connection))
    elif item["already"]:
        item.update(status="resolved", proposal=None,
                    why="; ".join(item["already"]) + ". If you meant something else, tell us more.")
    else:
        item.update(status="unresolved", proposal=None, ask=True, problems=[],
                    why=(note + " " if note else "We couldn't match this to a setting. ") + ASK_WHICH)
    return put(ws, item)


def from_gate(ws: Path, skill: str, need: str, field: str | None, value: str,
              connection: str | None = None) -> dict[str, Any]:
    """A data-connection answer ("which column holds X?") as an exact change, validated like any fix.

    field is the catalog field (or "a|b" alternatives); value is COLUMN, TABLE.COLUMN or TABLE.
    Confirming the column we already guessed is recorded as the analyst's confirmation."""
    full = facts(ws)
    text = str(value).strip()
    current = full["needs"].get(need, {})
    if field:
        names = field.split("|")
        fname = next((n for n in names if current.get("fields", {}).get(n)), names[0])
        table, _, col = text.upper().rpartition(".")
        fqn = resolve_table(table, full.get("tables", {})) if table else None
        if fqn and fqn != (current.get("table") or "").upper():
            ops = [{"op": "bind_table", "need": need, "fqn": fqn}]
        else:
            ops = [{"op": "bind_column", "need": need, "field": fname, "column": col}]
    else:
        ops = [{"op": "bind_table", "need": need, "fqn": text}]
    raw = list(ops)
    ok, problems = validate(ops, full)
    base = {"id": item_id("gate", skill, need, field, text), "kind": "gate", "skills": [skill], "text": text,
            "created_at": now(), "problems": [plain(p, full) for p in problems]}
    if not ok and not problems and raw[0]["op"] == "bind_column":  # the guess was right: the analyst confirms it
        learned = Q.load(ws / "learned.json", {}) or {}
        learned.setdefault("fields", {})[f"{need}.{raw[0]['field']}"] = raw[0]["column"]
        Q.save(ws / "learned.json", learned)
        subprocess.run([sys.executable, str(SCRIPTS / "discover.py"), "--workspace", str(ws), "--rematch"],
                       capture_output=True, text=True)
        return put(ws, {**base, "status": "confirmed", "proposal": None,
                        "why": f"{raw[0]['column']} confirmed for {raw[0]['field'].replace('_', ' ')}."})
    if not ok:
        return put(ws, {**base, "status": "unresolved", "proposal": None, "ask": True,
                        "why": (base["problems"][0] if base["problems"] else "That can't be used here")
                        + ". Name another column or table, or say we don't have it."})
    return put(ws, {**base, "status": "proposed", "proposal": proposal(ok, full, "your answer", "", ws, connection)})


def add_rule(ws: Path, skill: str, text: str, use_ai: bool, connection: str | None) -> dict[str, Any]:
    """The follow-up "any other rule or context?". A rule that maps to a setting becomes a proposal like any
    feedback; anything else is kept as a customer rule the hydrated skill follows (W/customer_rules.json)."""
    item = from_feedback(ws, skill, text, use_ai, connection)
    if item["status"] == "unresolved":
        rules = Q.load(ws / "customer_rules.json", {"rules": []}) or {"rules": []}
        if not any(r["text"] == text and r["skill"] == skill for r in rules["rules"]):
            rules["rules"].append({"id": item_id("rule", skill, text), "skill": skill, "text": text.strip(), "at": now()})
            Q.save(ws / "customer_rules.json", rules)
        item.update(status="noted", ask=False, proposal=None,
                    why="Saved as a rule for this skill: it will follow it when it runs. "
                        "Tell us if it should change a number now and we'll map it.")
        put(ws, item)
    return item


def collect(ws: Path) -> dict[str, Any]:
    """Items from the latest validation report (idempotent). Number mismatches arrive via numbers.py -> reconcile."""
    f = facts(ws)
    report = Q.load(ws / "validation_report.json", {}) or {}
    existing = {i["id"]: i for i in ledger(ws)["items"]}
    fresh = []
    for skill, entry in report.get("skills", {}).items():
        for g in entry.get("gates", []):
            if g["status"] != "FAIL":
                continue
            iid = item_id("check", skill, g["gate"], g["detail"])
            if iid in existing:
                continue
            fresh.append({"id": iid, "kind": "check", "skills": [skill], "gate": g["gate"], "text": plain_gate(g),
                          "created_at": now(), "status": "unresolved", "proposal": None,
                          "why": "tell us what's different, or go back to Tune"})
    for it in fresh:
        put(ws, it)
    return ledger(ws)


GATE_PLAIN = {"G1_parse": "Required data isn't connected", "G2_references": "A table or column no longer exists",
              "G3_compile": "A query doesn't compile", "G4_execute": "A query returns no rows",
              "A1_sanity": "A number is out of range", "A2_grain": "Rows are double counted",
              "A5_definitions": "A definition uses values that aren't in the data"}


def plain_gate(g: dict[str, Any]) -> str:
    return f"{GATE_PLAIN.get(g['gate'], g['gate'])}: {g['detail'][:200]}"


def apply(ws: Path, iid: str, ops: list[dict[str, Any]] | None = None,
          connection: str | None = None) -> dict[str, Any]:
    item = next((i for i in ledger(ws)["items"] if i["id"] == iid), None)
    if not item or not (ops or item.get("proposal")):
        raise SystemExit("nothing to apply")
    f = facts(ws)
    ops, problems = validate(ops or item["proposal"]["ops"], f)  # re-validate against the current state
    if problems or not ops:
        item.update(status="unresolved", problems=problems, why="the data changed since this was proposed")
        return put(ws, item)
    learned = Q.load(ws / "learned.json", {}) or {}
    profile_first = []
    for o in ops:
        if o["op"] == "bind_table":
            import discover_topics as T
            T.confirm(ws, o["need"], [o["fqn"]])  # the analyst's table is the only one this need may use
            learned.setdefault("objects", {})[o["need"]] = o["fqn"]
            index = Q.load(ws / "data_index.json", {"objects": {}}) or {"objects": {}}
            if o["fqn"] not in {k.upper() for k in index["objects"]}:
                profile_first.append(o["fqn"])
            continue
        if o["op"] in {"set_values", "set_number", "set_choice"}:
            Q.set_tunable(ws, o["tunable"], o["value"])
        elif o["op"] == "set_method":
            Q.set_tunable(ws, "attrition_rate_method", o["value"])
        else:
            learned.setdefault("fields", {})[f"{o['need']}.{o['field']}"] = o["column"]
    Q.save(ws / "learned.json", learned)
    if profile_first and not connection:
        item.update(status="proposed", why="profiling the new table needs a connection; apply again with --connection")
        return put(ws, item)
    cmd = [sys.executable, str(SCRIPTS / "discover.py"), "--workspace", str(ws)]
    cmd += (["--connection", connection, "--from-topics", "--keep-progress"] if profile_first else ["--rematch"])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        item.update(status="proposed", why="re-mapping failed: " + proc.stderr[-300:])
        return put(ws, item)
    item.update(status="applied", applied_at=now(), applied_ops=ops)
    put(ws, item)
    remember(ws, item)
    return item


def remember(ws: Path, item: dict[str, Any]) -> None:
    """An accepted fix becomes a saved correction (re-checked later) and asks for another look at the preview."""
    import corrections
    import review
    corrections.record(ws, item)
    for s in item.get("skills", []):
        review.mark(ws, s, "corrected", item["id"])


def dismiss(ws: Path, iid: str) -> None:
    for i in ledger(ws)["items"]:
        if i["id"] == iid:
            i["status"] = "dismissed"
            put(ws, i)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["feedback", "rule", "reconcile", "collect", "apply", "dismiss"])
    p.add_argument("--metric")
    p.add_argument("--target", type=float)
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--skill")
    p.add_argument("--text")
    p.add_argument("--id")
    p.add_argument("--ai", action="store_true")
    p.add_argument("--connection")
    a = p.parse_args()
    ws = a.workspace.resolve()
    if a.action == "feedback":
        out: Any = from_feedback(ws, a.skill, a.text or "", a.ai, a.connection)
    elif a.action == "rule":
        out = add_rule(ws, a.skill, a.text, a.ai, a.connection)
    elif a.action == "reconcile":
        out = reconcile(ws, a.connection, a.skill, a.metric, a.target, a.text or "")
    elif a.action == "collect":
        out = collect(ws)
    elif a.action == "apply":
        out = apply(ws, a.id, connection=a.connection)
    else:
        dismiss(ws, a.id)
        out = {"dismissed": a.id}
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
