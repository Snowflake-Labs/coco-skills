#!/usr/bin/env python3
"""Business definitions the chosen skills rely on, with options built from the customer's data.
Stdlib only.

catalog/definitions.json says which definitions each skill needs (headcount population,
exit date, time to fill, pending start, ...). This module turns each into a decision for
this customer:

  ask             the data offers a real choice and nothing has settled it
  settled         one possible answer, a document says so, or you answered before
  cannot_compute  the skill needs it but the data it needs isn't there (a plain note)
  hidden          none of the chosen skills use it

Options never come from a fixed list of columns: value lists come from the profiled bound
column, dates from the date columns of the bound tables, spans from start/end date pairs
in one table. With a runner, one batched round adds what each option changes (exit counts
per date, median days per span) and drops spans whose end is mostly before the start.

  definitions.py plan    --workspace W --skill S [--skill S2] [--connection C]
  definitions.py suggest --workspace W --skill S [--skill S2] --connection C   (assistant, cached)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
CATALOG = SCRIPTS.parents[1] / "catalog" / "definitions.json"
SPAN_MIN_VALID = 0.8  # share of rows with end >= start for a start/end pair to make sense
MAX_SPANS = 4


def load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def library() -> list[dict[str, Any]]:
    return load(CATALOG, {"definitions": []})["definitions"]


def by_id(did: str) -> dict[str, Any] | None:
    return next((d for d in library() if d["id"] == did), None)


def all_skills() -> list[str]:
    return sorted({s for d in library() for s in d["skills"]} | {p.name for p in TEMPLATES.glob("*") if p.is_dir()})


def short(fqn: str) -> str:
    return fqn.rsplit(".", 1)[-1]


LINK_WORDS = ("req", "opening", "application", "offer", "position")  # ids that tie a requisition to its candidates
MIN_TABLE_SCORE = 0.6  # below this a table match is a guess: never build questions on it
FLAG_VALUES = {"true", "false", "yes", "no", "y", "n", "1", "0", "t", "f"}


def trusted(n: dict[str, Any] | None, field: str | None = None) -> bool:
    """A binding questions may rely on: confirmed, or a confident automatic match (table and field)."""
    if not n or not n.get("fqn") or n.get("status") == "unresolved":
        return False
    if n.get("object_status") != "confirmed" and float(n.get("object_score") or 1) < MIN_TABLE_SCORE:
        return False
    if field is None:
        return True
    f = (n.get("fields") or {}).get(field) or {}
    return bool(f.get("column")) and (f.get("status") in {"confirmed", "analyst"} or f.get("confidence") != "LOW")


def is_flag(values: list[Any]) -> bool:
    return bool(values) and {str(v).strip().lower() for v in values} <= FLAG_VALUES


def bound_tables(ws: Path, needs: list[str]) -> list[tuple[str, str]]:
    """(need, table) for each listed need bound with confidence, in the listed order, one per table."""
    b = load(ws / "bindings.json", {}).get("needs", {})
    out, seen = [], set()
    for key in needs:
        n = b.get(key) or {}
        if trusted(n) and n["fqn"] not in seen:
            seen.add(n["fqn"])
            out.append((key, n["fqn"]))
    return out


def bound_column(ws: Path, need: str, field: str) -> str | None:
    n = load(ws / "bindings.json", {}).get("needs", {}).get(need) or {}
    return n["fields"][field]["column"] if trusted(n, field) else None


def columns(ws: Path, fqn: str) -> dict[str, Any]:
    """Profiled columns when the table was profiled, else the scan's column list (types only)."""
    cols = load(ws / "data_index.json", {"objects": {}})["objects"].get(fqn, {}).get("columns", {})
    return cols or load(ws / "scan_metadata.json", {"objects": {}})["objects"].get(fqn, {}).get("columns", {})


def scanned_tables(ws: Path) -> list[str]:
    """Every table the Discover scan found (all visible databases), official ones only."""
    objs = load(ws / "scan_metadata.json", {"objects": {}})["objects"]
    return sorted(f for f, o in objs.items() if o.get("official", True)) or \
        sorted(load(ws / "data_index.json", {"objects": {}})["objects"])


def hint_match(col: str, hints: list[str]) -> bool:
    low = col.lower()
    return any(h in low for h in hints)


def date_columns(ws: Path, fqn: str, hints: list[str]) -> list[str]:
    return [c for c, m in columns(ws, fqn).items() if m.get("is_date") and m.get("distinct", 1) and hint_match(c, hints)]


def requires_ok(ws: Path, req: dict[str, Any] | None) -> bool:
    if not req:
        return True
    for key in req["needs"]:
        col = bound_column(ws, key, req["field"])
        if not col:
            continue
        if not req.get("value_like"):
            return True
        fqn = load(ws / "bindings.json", {}).get("needs", {}).get(key, {}).get("fqn")
        vals = [str(v).lower() for v, _n in columns(ws, fqn).get(col, {}).get("top_values") or []]
        if any(req["value_like"] in v for v in vals):
            return True
    return False


def label_of(d: dict[str, Any]) -> str:
    return d.get("label") or d["prompt"].rstrip("?")


# ---------- options per kind ---------------------------------------------------------------

def date_choice_options(ws: Path, d: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    spec = d["dates_from"]
    tables = bound_tables(ws, spec["needs"])
    if not tables:
        return [], f"no {label_of(d).lower()} data is connected"
    need, fqn = tables[0]
    current = bound_column(ws, d["binds"]["need"], d["binds"]["field"]) if d.get("binds") else None
    cols = date_columns(ws, fqn, spec["hints"])
    if current and current not in cols:
        cols.insert(0, current)
    cols.sort(key=lambda c: c != current)
    return [{"value": c, "label": f"{c.replace('_', ' ').lower()} ({short(fqn)})", "table": fqn} for c in cols], None


def number_columns(ws: Path, fqn: str, hints: list[str]) -> list[str]:
    return [c for c, m in columns(ws, fqn).items() if m.get("is_number") and hint_match(c, hints)]


def join_key(ws: Path, left: str, right: str) -> str | None:
    """An id column both tables share (e.g. REQ_ID), so a start date in one can meet an end date in the other."""
    norm = lambda c: re.sub(r"[^a-z0-9]", "", c.lower())  # noqa: E731
    lc, rc = columns(ws, left), columns(ws, right)
    rnames = {norm(c): c for c, m in rc.items() if m.get("is_id")}
    keys = [c for c, m in lc.items() if m.get("is_id") and norm(c) in rnames and rnames[norm(c)] == c]
    order = LINK_WORDS
    keys = [c for c in keys if any(w in c.lower() for w in order)]  # a shared cost center doesn't link a req to a hire
    keys.sort(key=lambda c: (next((i for i, w in enumerate(order) if w in c.lower()), len(order)), c))
    return keys[0] if keys else None


def hint_rank(col: str, hints: list[str]) -> int:
    low = col.lower()
    return next((i for i, h in enumerate(hints) if h in low), len(hints))


def span_options(ws: Path, d: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    tables = {fqn for _k, fqn in bound_tables(ws, d["start"]["needs"])}
    if not tables:
        return [], f"{label_of(d)} can't be computed yet: no table with the {d['start'].get('label', 'start date')} is connected"
    end_tables = {fqn for _k, fqn in bound_tables(ws, d["end"]["needs"])} | tables
    for other in scanned_tables(ws):  # any scanned table that links to the start table and has an end-like date
        if other not in end_tables and date_columns(ws, other, d["end"]["hints"]) and \
                any(join_key(ws, fqn, other) for fqn in tables):
            end_tables.add(other)
    opts, end_seen = [], False
    for fqn in sorted(tables):  # 1. a column the company already calculates ("days to fill")
        for c in number_columns(ws, fqn, d.get("precomputed_hints", [])):
            opts.append({"value": {"table": fqn, "column": c},
                         "label": f"the {c.replace('_', ' ').lower()} already in {short(fqn)}"})
    for fqn in sorted(tables):  # 2. start and end date in the same table
        starts = date_columns(ws, fqn, d["start"]["hints"])
        ends = [c for c in date_columns(ws, fqn, d["end"]["hints"]) if c not in starts]
        end_seen |= bool(ends)
        for s in starts:
            for e in ends:
                opts.append({"value": {"table": fqn, "start": s, "end": e},
                             "label": f"{s.replace('_', ' ').lower()} → {e.replace('_', ' ').lower()} ({short(fqn)})"})
    for fqn in sorted(tables):  # 3. start date here, end date in a linked table (first end per key)
        for other in sorted(end_tables - {fqn}):
            key = join_key(ws, fqn, other)
            ends = date_columns(ws, other, d["end"]["hints"]) if key else []
            end_seen |= bool(ends)
            for s in date_columns(ws, fqn, d["start"]["hints"]):
                for e in ends:
                    opts.append({"value": {"table": fqn, "start": s, "end_table": other, "end": e, "key": key},
                                 "label": f"{s.replace('_', ' ').lower()} ({short(fqn)}) → first "
                                          f"{e.replace('_', ' ').lower()} ({short(other)})"})
    return opts, None


def choice_options(ws: Path, d: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"value": a["value"], "label": a["label"]} for a in d.get("common_answers", []) if requires_ok(ws, a.get("requires"))]


# ---------- effects (one batched round) ----------------------------------------------------

def effect_sqls(ws: Path, d: dict[str, Any], opts: list[dict[str, Any]], skills: list[str]) -> list[str | None]:
    """SQL per option (or None) whose single value shows what that option changes."""
    from _sql import quote_fqn, quote_ident
    out: list[str | None] = []
    if d["kind"] == "date_span":
        for o in opts:
            v = o["value"]
            if "column" in v:  # pre-calculated days: show its median, always a valid option
                c = quote_ident(v["column"])
                out.append(f"select median({c}) as value, 1 as valid from {quote_fqn(v['table'])} where {c} is not null")
                continue
            s, e = quote_ident(v["start"]), quote_ident(v["end"])
            if "end_table" in v:  # first end date per key in the linked table
                k = quote_ident(v["key"])
                out.append(f"with e as (select {k} as k, min({e}) as d from {quote_fqn(v['end_table'])} "
                           f"where {e} is not null group by 1) "
                           f"select median(datediff('day', t.{s}, e.d)) as value, "
                           f"count_if(e.d >= t.{s}) / nullif(count_if(t.{s} is not null), 0) as valid "
                           f"from {quote_fqn(v['table'])} t join e on t.{k} = e.k")
                continue
            out.append(f"select median(datediff('day', {s}, {e})) as value, "
                       f"count_if({e} >= {s}) / nullif(count_if({s} is not null and {e} is not null), 0) as valid "
                       f"from {quote_fqn(v['table'])}")
        return out
    metric = (d.get("effect") or {}).get("metric")
    if d["kind"] == "date_choice" and metric and d.get("binds"):
        import check_bindings as CB
        import preview as P
        from fix_ledger import apply_ops_copy
        bindings, tuning = load(ws / "bindings.json", {}), P.tuning_of(ws)
        skill = next((s for s in skills if metric in CB.skill_metrics(s, bindings, tuning)), None)
        for o in opts:
            if not skill:
                out.append(None)
                continue
            b, t = apply_ops_copy([{"op": "bind_column", "need": d["binds"]["need"], "field": d["binds"]["field"],
                                    "column": o["value"]}], bindings, tuning)
            out.append(CB.skill_metrics(skill, b, t).get(metric))
        return out
    return [None] * len(opts)


def add_effects(run: Any, ws: Path, d: dict[str, Any], opts: list[dict[str, Any]], skills: list[str]) -> list[dict[str, Any]]:
    from _sql import SqlError, run_many
    sqls = effect_sqls(ws, d, opts, skills)
    idx = [i for i, s in enumerate(sqls) if s]
    rows = run_many(run, [sqls[i] for i in idx]) if idx else []
    for i, r in zip(idx, rows):
        if isinstance(r, SqlError) or not r:
            continue
        val, valid = r[0].get("VALUE"), r[0].get("VALID")
        if d["kind"] == "date_span":
            opts[i]["valid_share"] = round(float(valid), 3) if valid is not None else None
            opts[i]["effect"] = f"median {float(val):g} days" if val is not None else None
            opts[i]["effect_value"] = float(val) if val is not None else None
            if val is not None and float(val) < 0:
                opts[i]["warning"] = "most values are negative (the end comes before the start)"
            elif valid is not None and float(valid) < SPAN_MIN_VALID:
                opts[i]["warning"] = f"only {float(valid):.0%} of rows have the end on or after the start"
        elif val is not None:
            opts[i]["effect"] = f"{val} {d.get('effect_unit', 'exits in 12 months')}"
            opts[i]["effect_value"] = val if isinstance(val, (int, float)) else None
    return opts


# ---------- the plan -----------------------------------------------------------------------

def options(ws: Path, d: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """Options for the data-driven kinds; (options, cannot_compute note)."""
    if d["kind"] == "date_choice":
        return date_choice_options(ws, d)
    if d["kind"] == "date_span":
        return span_options(ws, d)
    if d["kind"] == "link_policy":
        return link_policy_options(ws, d), None
    if d["kind"] == "choice" and any(a.get("requires") for a in d.get("common_answers", [])):
        opts = choice_options(ws, d)
        return opts, (None if opts else f"{label_of(d)} can't be computed yet: the data it needs isn't connected")
    return [], None


def link_policy_options(ws: Path, d: dict[str, Any]) -> list[dict[str, Any]]:
    """A policy that depends on a linked table, e.g. 'is a req still open once an offer is accepted?'.
    Offered only when some scanned table links to the bound table by an id and has the event date."""
    linked = d["linked"]
    for _k, fqn in bound_tables(ws, d["start"]["needs"]):
        for other in scanned_tables(ws):
            key = join_key(ws, fqn, other) if other != fqn else None
            dates = date_columns(ws, other, linked["hints"]) if key else []
            if dates:
                dates.sort(key=lambda c: hint_rank(c, linked["hints"]))
                return [{"value": None, "label": linked["keep_label"]},
                        {"value": {"table": other, "key": key, "date": dates[0]},
                         "label": f"{linked['close_label']} ({dates[0].replace('_', ' ').lower()} in {short(other)})"}]
    return []


NOT_TRACKED = {"not_tracked": True}


def where_prompt(d: dict[str, Any]) -> str:
    if d["kind"] == "date_span":
        return f"Where do you record the {d['end'].get('label', 'end date')} for {label_of(d).lower()}?"
    if d["kind"] == "date_choice":
        return f"Which table and date column hold the {label_of(d).lower()}?"
    return f"Which table holds the data for {label_of(d).lower()}?"


def other_dates(ws: Path, d: dict[str, Any], skip: set[str], limit: int = 6) -> list[dict[str, Any]]:
    """Date columns the analyst may mean, from tables that link to the start table (any column name)."""
    if d["kind"] != "date_span":
        return []
    starts = [fqn for _k, fqn in bound_tables(ws, d["start"]["needs"])]
    out = []
    for other in scanned_tables(ws):
        for fqn in starts:
            key = join_key(ws, fqn, other) if other != fqn else None
            s = (date_columns(ws, fqn, d["start"]["hints"]) or [None])[0]
            if not s or (other != fqn and not key):
                continue
            for c, m in columns(ws, other).items():
                v = {"table": fqn, "start": s, "end": c} if other == fqn else \
                    {"table": fqn, "start": s, "end_table": other, "end": c, "key": key}
                if m.get("is_date") and c != s and json.dumps(v, sort_keys=True) not in skip \
                        and not any(w in c.lower() for w in SNAPSHOT_WORDS):
                    out.append({"value": v, "label": f"{c.replace('_', ' ').lower()} ({short(other)})"})
    return out[:limit]


def locate_answer(ws: Path, d: dict[str, Any], text: str) -> dict[str, Any]:
    """Turn the analyst's 'TABLE.COLUMN' (or DB.SCHEMA.TABLE.COLUMN) into a checked definition value."""
    parts = [p.strip().strip('"') for p in str(text).split(".") if p.strip()]
    if len(parts) < 2:
        raise ValueError("name the table and column, e.g. APPLICATIONS.HIRED_DATE")
    col, tbl = parts[-1].upper(), ".".join(parts[:-1]).upper()
    matches = [f for f in scanned_tables(ws) if f.upper() == tbl or f.upper().endswith("." + tbl)]
    if not matches:
        raise ValueError(f"we can't see a table called {tbl}; check the name or that your role can read it")
    fqn = matches[0]
    cols = {c.upper(): c for c in columns(ws, fqn)}
    if col not in cols:
        raise ValueError(f"{short(fqn)} has no column {col}")
    meta = columns(ws, fqn)[cols[col]]
    if d["kind"] == "date_choice":
        if not meta.get("is_date"):
            raise ValueError(f"{col} isn't a date")
        return {"value": cols[col], "table": fqn}
    if d["kind"] != "date_span":
        raise ValueError("tell us in feedback instead; this definition can't be set from one column")
    starts = [f for _k, f in bound_tables(ws, d["start"]["needs"])]
    if not starts:
        raise ValueError("we haven't found the start date's table yet; run Bind again first")
    start_fqn = starts[0]
    s = (date_columns(ws, start_fqn, d["start"]["hints"]) or [None])[0]
    if meta.get("is_number") and not meta.get("is_date"):
        return {"value": {"table": fqn, "column": cols[col]}}
    if not meta.get("is_date") or not s:
        raise ValueError(f"{col} isn't a date or a number of days")
    if fqn == start_fqn:
        return {"value": {"table": fqn, "start": s, "end": cols[col]}}
    key = join_key(ws, start_fqn, fqn)
    if not key:
        raise ValueError(f"{short(fqn)} doesn't share an id with {short(start_fqn)}, so the dates can't be matched")
    return {"value": {"table": start_fqn, "start": s, "end_table": fqn, "end": cols[col], "key": key}}


def decide(ws: Path, skills: list[str], run: Any = None) -> list[dict[str, Any]]:
    """Data-driven definitions for these skills.

    options   plausible answers found in the data (asked, or settled when there's one)
    flagged   found but implausible (e.g. negative days), with the reason
    locate    nothing plausible: ask where it lives (found-but-flagged, other linked dates, or free text)
    cannot_compute only when the analyst said it isn't tracked (or the start data itself is missing)
    """
    out = []
    for d in library():
        if d["kind"] not in {"date_choice", "date_span", "link_policy"} and not (
                d["kind"] == "choice" and any(a.get("requires") for a in d.get("common_answers", []))):
            continue
        if not set(d["skills"]) & set(skills):
            continue
        opts, note = options(ws, d)
        if d["kind"] == "link_policy":  # nothing linked: the policy doesn't apply to this data
            if opts:
                out.append({"id": d["id"], "options": opts, "flagged": [], "cannot_compute": None, "locate": None,
                            "affects": sorted(set(d["skills"]) & set(skills))})
            continue
        if run is not None and opts:
            opts = add_effects(run, ws, d, opts, skills)
        end_hints = (d.get("end") or {}).get("hints", [])
        fit = lambda o: hint_rank(o["value"].get("end", o["value"].get("column", "")), end_hints) \
            if isinstance(o["value"], dict) else 0  # noqa: E731
        good = sorted([o for o in opts if not o.get("warning")], key=fit)[:MAX_SPANS]
        flagged = sorted([o for o in opts if o.get("warning")], key=fit)
        item = {"id": d["id"], "options": good, "flagged": flagged, "cannot_compute": None, "locate": None,
                "affects": sorted(set(d["skills"]) & set(skills))}
        if not good and d["kind"] in {"date_span", "date_choice"}:
            skip = {json.dumps(o["value"], sort_keys=True) for o in opts}
            item["locate"] = {"prompt": where_prompt(d), "found_but_off": flagged[:3],
                              "other_dates": other_dates(ws, d, skip), "missing": note}
        elif not good:
            item["cannot_compute"] = note or f"{label_of(d)}: the data it needs wasn't found"
        out.append(item)
    return out


# ---------- assistant suggestions (bounded, cached, validated) ----------------------------

AI_MODELS = ("claude-sonnet-4-5", "llama3.3-70b")
AI_MAX = 5
AI_VERSION = 4  # bump when the prompt or validation changes, so cached suggestions refresh
GENERIC_WORDS = {"attrition", "headcount", "employee", "employees", "counted", "measure", "calculate"}
TEMPLATES = SCRIPTS.parents[1] / "templates" / "people" / "skills"
METRIC_WORDS = re.compile(r"\b(rate|ratio|count|number of|days|time to|defin\w*|median|average|share|percent|%|"
                          r"pending|active|open|stale|regrettable|backfill|tenure|span)\b", re.I)


def skill_lines(skill: str, limit: int = 60) -> list[str]:
    """Lines of the skill's own instructions that name a metric or a definition."""
    out = []
    for name in ("SKILL.md", "queries.md"):
        p = TEMPLATES / skill / name
        if p.exists():
            out += [ln.strip()[:220] for ln in p.read_text(encoding="utf-8").splitlines()
                    if METRIC_WORDS.search(ln) and len(ln.strip()) > 20]
    return out[:limit]


def data_summary(ws: Path, skill: str) -> dict[str, Any]:
    """Metadata only: for each table the skill uses, its date columns and code columns with values."""
    b = load(ws / "bindings.json", {}).get("needs", {})
    tables = sorted({n["fqn"] for n in b.values() if skill in n.get("skills", []) and trusted(n)})
    out = {}
    for fqn in tables:
        cols = columns(ws, fqn)
        out[fqn] = {"dates": [c for c, m in cols.items() if m.get("is_date")],
                    "numbers": [c for c, m in cols.items() if m.get("is_number") and not m.get("is_id")],
                    "codes": {c: [str(v) for v, _n in (m.get("top_values") or [])[:8]]
                              for c, m in cols.items() if m.get("is_code") and not m.get("pii_risk")}}
    return out


def ai_key(skill: str, summary: dict[str, Any], covered: list[str]) -> str:
    import hashlib
    body = json.dumps({"v": AI_VERSION, "lines": skill_lines(skill), "data": summary, "covered": sorted(covered)},
                      sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


SNAPSHOT_WORDS = ("snapshot", "as_of", "asof", "report_date", "load_date", "extract")


def library_targets(ws: Path, skills: list[str]) -> set[tuple]:
    """(table, column) pairs the library already asks about, so a reworded suggestion is dropped."""
    out: set[tuple] = set()
    b = load(ws / "bindings.json", {}).get("needs", {})
    for d in library():
        if not set(d["skills"]) & set(skills):
            continue
        spec = d.get("options_from")
        if spec:
            for key in spec["needs"]:
                col = bound_column(ws, key, spec["field"])
                if col:
                    out.add((b[key]["fqn"], col))
        if d["kind"] == "date_choice":
            for o in date_choice_options(ws, d)[0]:
                out.add((o["table"], o["value"]))
    return out


def validate_ai(q: dict[str, Any], summary: dict[str, Any], covered: set[str],
                targets: set[tuple] = frozenset()) -> dict[str, Any] | None:
    """Keep a suggestion only if every option it offers exists in the customer's data."""
    label = str(q.get("label") or "").strip()
    if not label or label.lower() in covered or not q.get("prompt"):
        return None
    key_words = {w for c in covered for w in re.findall(r"[a-z]+", c) if len(w) > 6} - GENERIC_WORDS
    if any(k in w for k in key_words for w in re.findall(r"[a-z]+", label.lower())):
        return None  # the library already asks this, in other words
    table = q.get("table")
    if table not in summary:
        return None
    t = summary[table]
    if q.get("kind") == "multi":
        vals = t["codes"].get(q.get("column") or "", [])
        if len(vals) < 2 or is_flag(vals) or (table, q.get("column")) in targets:
            return None
        opts = [{"value": v, "label": v} for v in vals]
    elif q.get("kind") == "date_choice":
        cols = [c for c in q.get("columns") or [] if c in t["dates"]
                and not any(w in c.lower() for w in SNAPSHOT_WORDS)]  # a snapshot date is the table's clock, not an event
        if len(cols) < 2 or all((table, c) in targets for c in cols):
            return None
        opts = [{"value": c, "label": f"{c.replace('_', ' ').lower()} ({short(table)})"} for c in cols]
    elif q.get("kind") == "number":
        try:
            lo, hi, dflt = float(q["min"]), float(q["max"]), float(q["default"])
        except (KeyError, TypeError, ValueError):
            return None
        col = q.get("column")
        known = set(t.get("dates", [])) | set(t.get("codes", {})) | set(t.get("numbers", []) or [])
        if not (0 <= lo <= dflt <= hi <= 10000) or lo == hi or (col and known and col not in known):
            return None
        opts = [{"value": int(v) if float(v).is_integer() else v, "label": f"{v:g}"}
                for v in sorted({lo, dflt, hi})]
    else:
        return None
    group = q.get("group") if q.get("group") in {"convention", "policy", "label"} else "convention"
    if group == "label" and q.get("kind") == "multi" and len(opts) <= 4:
        return None  # a short, clear code list: the data answers it, no question needed
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40]
    return {"id": f"ai_{slug}", "label": label, "prompt": str(q["prompt"])[:200], "help": str(q.get("help") or "")[:240],
            "kind": q["kind"], "options": opts, "table": table, "column": q.get("column"), "group": group,
            "default": q.get("default") if q.get("kind") == "number" else None,
            "source": "suggested from your skill's instructions"}


def context_lines(ws: Path, limit: int = 20) -> list[str]:
    """What the customer's documents say (definitions, thresholds, calendars), short, for the prompt."""
    out = []
    for i in (load(ws / "context.json", {"items": []}) or {}).get("items", []):
        if i.get("status") == "rejected":
            continue
        out.append(f"{i.get('kind')}: {i.get('term')} = {json.dumps(i.get('value'), default=str)[:160]}")
    return out[:limit]


def ai_for_skill(ws: Path, skill: str, connection: str, covered: list[str],
                 targets: set[tuple] = frozenset()) -> list[dict[str, Any]]:
    """Up to AI_MAX extra questions for one skill; cached until the skill text or its data changes."""
    summary = data_summary(ws, skill)
    if not summary:
        return []
    key = ai_key(skill, summary, covered)
    cache = ws / "ai_definitions" / f"{skill}.json"
    hit = load(cache, {}) or {}
    if hit.get("key") == key:
        return hit.get("questions", [])
    from _sql import SqlError, make_runner, quote_literal
    prompt = ("You help configure an HR analytics skill for one company. Below are lines from the skill's instructions, "
              "the business definitions already being asked, what the company's documents say, and a metadata-only "
              "summary of the company's tables.\n"
              f"Skill: {skill}\nInstructions: {json.dumps(skill_lines(skill))}\n"
              f"Already covered: {json.dumps(sorted(covered))}\nDocuments: {json.dumps(context_lines(ws))[:2500]}\n"
              f"Data: {json.dumps(summary)[:9000]}\n"
              f"Propose at most {AI_MAX} OTHER business definitions (not variants of the covered ones) this skill relies on "
              "that the company must decide. Classify each one:\n"
              "- label: the data labels it clearly (e.g. a TERM_TYPE column with Voluntary/Involuntary). Do NOT propose "
              "these unless the labels are ambiguous codes.\n"
              "- convention: several valid industry definitions (e.g. who counts in headcount, which date ends time to fill).\n"
              "- policy: a threshold or business rule no data can decide (e.g. a healthy span of control, after how many "
              "days a requisition is aging, whether a req with an accepted offer is still open).\n"
              "These are examples, not a list: think about what THIS skill needs from THIS data. Skip anything the "
              "documents already answer. Each must use only a table and columns listed in Data:\n"
              ' {"label":"short name","group":"convention|policy|label","prompt":"question for an HR analyst",'
              '"help":"one sentence why it matters","kind":"multi","table":TABLE,"column":CODE_COLUMN}  (which values count)\n'
              ' {...,"kind":"date_choice","table":TABLE,"columns":[DATE_COLUMNS]}  (which date)\n'
              ' {...,"kind":"number","table":TABLE,"column":COLUMN_IT_APPLIES_TO,"default":N,"min":N,"max":N}  (a threshold)\n'
              'Plain business words, no column names in prompt/help. Reply ONLY JSON: {"questions":[...]} (empty list if none).')
    questions: list[dict[str, Any]] = []
    try:
        run = make_runner(connection, 120)
        reply = None
        for model in AI_MODELS:
            try:
                rows = run(f"select ai_complete({quote_literal(model)}, {quote_literal(prompt)}) as r")
                reply = str(rows[0]["R"]) if rows else None
                break
            except SqlError:
                continue
        if reply:
            try:
                reply = json.loads(reply) if reply.startswith('"') else reply
            except json.JSONDecodeError:
                pass
            m = re.search(r"\{.*\}", str(reply), re.S)
            raw = (json.loads(m.group(0)).get("questions") or []) if m else []
            low = {c.lower() for c in covered}
            for q in raw[:AI_MAX]:
                v = validate_ai(q, summary, low, targets) if isinstance(q, dict) else None
                if v:
                    v["skills"] = [skill]
                    questions.append(v)
    except Exception:  # noqa: BLE001 - suggestions are optional; the library questions still work
        return []
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps({"key": key, "questions": questions}, indent=2) + "\n", encoding="utf-8")
    return questions


def ai_suggestions(ws: Path, skills: list[str], connection: str | None) -> list[dict[str, Any]]:
    """All chosen skills at once (parallel locally; one after another on a shared session)."""
    if not connection:
        return []
    covered = [label_of(d) for d in library() if set(d["skills"]) & set(skills)] + \
        [d["id"].replace("_", " ") for d in library() if set(d["skills"]) & set(skills)]
    targets = library_targets(ws, skills)
    import os
    import shutil
    parallel = os.environ.get("ENGINE_SQL") != "snowpark" and shutil.which("snow") is not None
    if parallel and len(skills) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(skills))) as pool:
            lists = list(pool.map(lambda s: ai_for_skill(ws, s, connection, covered, targets), skills))
    else:
        lists = [ai_for_skill(ws, s, connection, covered, targets) for s in skills]
    out: list[dict[str, Any]] = []
    by_target: dict[tuple, dict[str, Any]] = {}
    for qs in lists:
        for q in qs:
            target = (q["kind"], q["table"], q.get("column") or tuple(o["value"] for o in q["options"]))
            if target in by_target:  # same decision suggested by several skills: ask once, for all of them
                by_target[target]["skills"] = sorted(set(by_target[target]["skills"]) | set(q["skills"]))
                continue
            by_target[target] = q
            out.append(q)
    return out


def cached_suggestions(ws: Path, skills: list[str]) -> list[dict[str, Any]]:
    """Suggestions already computed for these skills (no AI call), merged the same way."""
    out: list[dict[str, Any]] = []
    by_target: dict[tuple, dict[str, Any]] = {}
    for s in skills:
        for q in (load(ws / "ai_definitions" / f"{s}.json", {}) or {}).get("questions", []):
            target = (q["kind"], q["table"], q.get("column") or tuple(o["value"] for o in q["options"]))
            if target in by_target:
                by_target[target]["skills"] = sorted(set(by_target[target]["skills"]) | set(q["skills"]))
                continue
            by_target[target] = dict(q)
            out.append(by_target[target])
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["plan", "suggest"])
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--skill", action="append", default=[])
    p.add_argument("--connection")
    a = p.parse_args()
    ws = a.workspace.resolve()
    if a.action == "suggest":
        print(json.dumps(ai_suggestions(ws, a.skill, a.connection), indent=2, default=str))
        return 0
    run = None
    if a.connection:
        from _sql import make_runner
        run = make_runner(a.connection, 120)
    print(json.dumps(decide(ws, a.skill, run), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
