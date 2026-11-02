#!/usr/bin/env python3
"""Skill-owned business questions with the engine's answer preselected. Stdlib only.

Questions come from the business definitions the chosen skills rely on
(catalog/definitions.json), with options built from the customer's data
(definitions.py), plus cached assistant suggestions. For the chosen skills, each is:
  ask        the engine can't settle it (default guess, several plausible values,
             or your documents disagree); shown with a preselected answer + evidence
  decided    settled from your documents or answered before; listed so you can reopen it
  hidden     applies_when doesn't hold (e.g. only one employee type in the data)
  cannot_compute  a skill needs it but the data isn't there (plain note, never a question)

Flexible count: everything that matters, ranked by how much the answer moves the numbers, never more
than MAX_QUESTIONS for one skill. Labels the data settles and conventions where every option gives the
same number are stated, not asked; policies (thresholds, business rules) are asked. "Not sure" defers.
Answers write to the tunable in every skill that has it (one business decision).

  questions.py plan   --workspace W --skill S [--skill S2] [--connection C] [--ai]
  questions.py answer --workspace W --id Q --value '<json>'   (or --skip)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
CATALOG = SCRIPTS.parents[1] / "catalog" / "definitions.json"
import definitions as D  # noqa: E402
MAX_QUESTIONS = 5  # per skill; everything else is shown as a stated default
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def tunable(ws: Path, tid: str, skills: list[str]) -> tuple[dict[str, Any] | None, list[str]]:
    found, users = None, []
    for path in sorted((ws / "tuning").glob("*.json")):
        t = (load(path, {}) or {}).get("tunables", {}).get(tid)
        if t is not None:
            users.append(path.stem)
            mine = path.stem in skills and (found is None or found[0] not in skills)
            answered = t.get("source") == "analyst" and (found is None or found[1].get("source") != "analyst")
            if found is None or answered or (mine and found[1].get("source") != "analyst"):
                found = (path.stem, t)
    return (found[1] if found else None), users


def observed_values(ws: Path, spec: dict[str, Any] | None) -> tuple[list[str], str | None]:
    """Code values seen in the bound column (from aggregate profiling), most frequent first."""
    if not spec:
        return [], None
    bindings = load(ws / "bindings.json", {}).get("needs", {})
    index = load(ws / "data_index.json", {"objects": {}})
    for key in spec["needs"]:
        b = bindings.get(key) or {}
        if not D.trusted(b, spec["field"]):  # a guessed table/column would ask about the wrong values
            continue
        col = b["fields"][spec["field"]]["column"]
        meta = index["objects"].get(b.get("fqn"), {}).get("columns", {}).get(col or "", {})
        vals = [str(v) for v, _n in meta.get("top_values") or [] if v is not None]
        if vals:
            return vals, f"{b['fqn'].rsplit('.', 1)[1]}.{col}"
    return [], None


def glossary_support(ws: Path, values: list[str]) -> str | None:
    """A confirmed-or-proposed document definition that names every chosen value."""
    for i in load(ws / "context.json", {"items": []}).get("items", []):
        if i["kind"] != "definition" or i["status"] == "rejected" or i.get("conflicts_with"):
            continue
        text = f"{i.get('text') or ''} {i['value'] if isinstance(i['value'], str) else ''}".lower()
        if values and all(str(v).lower() in text for v in values):
            return i["source"].get("title") or i["source"]["connector"]
    return None


def conflict_options(ws: Path, tid: str) -> list[dict[str, Any]]:
    """Documents that disagree about this setting, as labelled options."""
    key = {"fiscal_year_start_month": "fiscal_year_start_month"}.get(tid)
    if not key:
        return []
    out = []
    for i in load(ws / "context.json", {"items": []}).get("items", []):
        if i["status"] == "rejected" or not isinstance(i["value"], dict) or key not in i["value"]:
            continue
        src = i["source"].get("title") or i["source"].get("scope") or i["source"]["connector"]
        where = {"slack": "#" + str(i["source"].get("scope", "")).lstrip("#")}.get(i["source"]["connector"], src)
        out.append({"value": i["value"][key], "label": f"{MONTHS[int(i['value'][key]) - 1]} (from {where})",
                    "item": i["id"], "conflict": bool(i.get("conflicts_with"))})
    return out if any(o["conflict"] for o in out) and len({o["value"] for o in out}) > 1 else []


def effect_now(ws: Path, q: dict[str, Any], skills: list[str]) -> Any:
    """The question's headline number as the preview shows it today (no query)."""
    mid = (q.get("effect") or {}).get("metric")
    for s in list(skills) + list(q.get("skills", [])):
        for m in (load(ws / "previews" / f"{s}.json", {}) or {}).get("metrics", []):
            if m["id"] == mid and isinstance(m.get("value"), (int, float)):
                return m["value"]
    return None


def option_effects(ws: Path, q: dict[str, Any], options: list[dict[str, Any]], skills: list[str], run: Any) -> None:
    """Headline number for each answer: the preview metric re-run with that answer (one batched round)."""
    mid = (q.get("effect") or {}).get("metric")
    if not mid or run is None or q["kind"] not in {"choice", "month", "number", "link_policy"}:
        return
    import check_bindings as CB
    import fix_ledger as FL
    import preview as P
    from _sql import run_many, to_number
    bindings, tuning = load(ws / "bindings.json", {}), P.tuning_of(ws)
    skill = next((s for s in list(skills) + list(q.get("skills", []))
                  if mid in CB.skill_metrics(s, bindings, tuning)), None)
    if not skill:
        return
    todo = []
    for o in options:
        op = ({"op": "set_method", "value": o["value"]} if q["writes_to"] == "attrition_rate_method"
              else {"op": "set_number", "tunable": q["writes_to"], "value": o["value"]})
        b, tt = FL.apply_ops_copy([op], bindings, tuning)
        sql = CB.skill_metrics(skill, b, tt).get(mid)
        if sql:
            todo.append((o, sql))
    if not todo:
        return
    for (o, _), rows in zip(todo, run_many(run, [s for _, s in todo])):
        v = to_number(rows[0].get("VALUE")) if isinstance(rows, list) and rows else None
        if isinstance(v, (int, float)):
            o["effect"] = v


def current_value(q: dict[str, Any], t: dict[str, Any]) -> Any:
    if q["writes_to"] == "attrition_rate_method":
        return t.get("method") or {"denominator": "start_end", "annualized": False}
    return t.get("value")


def schema_question(ws: Path, skills: list[str]) -> dict[str, Any] | None:
    """Two or more near-identical copies of the HR tables (same table names in different schemas)
    is a real ambiguity the engine shouldn't settle alone. One question: which copy do you report from?"""
    topics = load(ws / "topics.json", {"topics": []}).get("topics", [])
    scan = load(ws / "scan_metadata.json", {"objects": {}, "usage": {}})
    groups: dict[str, dict[str, Any]] = {}
    undecided = False
    for t in topics:
        if t["role"] not in {"required", "optional"} or not set(t.get("required_by", []) + t.get("optional_for", [])) & set(skills):
            continue
        recs = set(t.get("chosen") or t.get("recommended") or [])
        names = {f.rsplit(".", 1)[1] for f in recs}
        for c in t.get("candidates", []):
            name = c["fqn"].rsplit(".", 1)[1]
            if name not in names:
                continue
            schema = c["fqn"].rsplit(".", 1)[0]
            g = groups.setdefault(schema, {"tables": set(), "picked": False})
            g["tables"].add(c["fqn"])
            g["picked"] |= c["fqn"] in recs
        undecided |= bool(t.get("auto")) or t["status"] == "proposed"
    twins = {s: g for s, g in groups.items() if len(g["tables"]) >= 1}
    if len(twins) < 2:
        return None
    opts = []
    for schema, g in sorted(twins.items(), key=lambda kv: (not kv[1]["picked"], kv[0])):
        objs = [scan["objects"].get(f, {}) for f in g["tables"]]
        reads = sum(int(scan["usage"].get(f.upper(), 0)) for f in g["tables"])
        latest = max((o.get("last_altered") or "")[:10] for o in objs) if objs else ""
        bits = [f"{len(g['tables'])} matching tables"] + ([f"read {reads}× recently"] if reads else []) + \
               ([f"updated {latest}"] if latest else [])
        opts.append({"value": schema, "label": f"{schema.split('.', 1)[-1]} ({', '.join(bits)})",
                     "picked": g["picked"]})
    current = next((o["value"] for o in opts if o["picked"]), opts[0]["value"])
    return {"id": "source_schema", "prompt": "We found more than one copy of your HR tables. Which one do you report from?",
            "help": "Same table names in different schemas. We picked the one that covers the most of what your skills need.",
            "kind": "choice", "writes_to": "topics", "impact": 5, "value": current, "preselected": current,
            "affects": sorted(skills), "options": [{"value": o["value"], "label": o["label"]} for o in opts],
            "observed_in": None, "effect_now": None, "undecided": undecided}


def plan(ws: Path, skills: list[str], run: Any = None) -> dict[str, Any]:
    state = load(ws / "questions_state.json", {}) or {}
    asked, decided, hidden = [], [], []
    sq = schema_question(ws, skills)
    if sq:
        prior = state.get("source_schema")
        if prior and prior.get("status") == "answered":
            decided.append({**sq, "value": prior["value"], "why": "you chose this copy", "source": "you"})
        elif sq.pop("undecided") or (prior and prior.get("status") == "reopened"):
            asked.append({**sq, "why": "two copies look alike"})
        else:
            decided.append({**sq, "why": "you chose the tables", "source": "you"})
    data_driven = {d["id"]: d for d in D.decide(ws, skills, run)}
    cannot = []
    for q in D.library():
        if not set(q["skills"]) & set(skills):
            continue
        t, users = tunable(ws, q["writes_to"], skills)
        if t is None and q["writes_to"].startswith("def_"):  # a definition with no setting yet: start from its default
            t, users = {"value": q.get("default"), "source": "default"}, sorted(set(q["skills"]) & set(skills))
        if t is None:
            hidden.append({"id": q["id"], "why": "not used by your skills' data"})
            continue
        dd = data_driven.get(q["id"])
        if dd is not None:
            if t.get("value") == D.NOT_TRACKED:  # the analyst told us: say so in the skill, never ask again
                cannot.append({"id": q["id"], "note": f"{D.label_of(q)} isn't tracked (you told us).",
                               "affects": dd["affects"]})
                continue
            if t.get("source") == "analyst" and not dd["options"] and t.get("value"):  # they pointed us to it
                decided.append({"id": q["id"], "prompt": q["prompt"], "value": t["value"], "writes_to": q["writes_to"],
                                "why": "you showed us where this is", "source": "you"})
                continue
            if dd["cannot_compute"]:
                cannot.append({"id": q["id"], "note": dd["cannot_compute"], "affects": dd["affects"]})
                continue
            if dd.get("locate"):  # nothing plausible found: ask where it lives instead of giving up
                loc = dd["locate"]
                opts = [{"value": o["value"], "label": f"{o['label']} (looks off: {o['warning']})",
                         "effect": o.get("effect")} for o in loc["found_but_off"]] + \
                       [{"value": o["value"], "label": o["label"]} for o in loc["other_dates"]] + \
                       [{"value": D.NOT_TRACKED, "label": "We don't track this"}]
                why = ("what we found looks off" if loc["found_but_off"] else
                       "we couldn't find it in your data")
                asked.append({"id": q["id"], "prompt": loc["prompt"], "help": q.get("help"), "kind": "locate",
                              "writes_to": q["writes_to"], "impact": q["impact"] + 1, "value": None,
                              "affects": dd["affects"], "options": opts, "free_text": "TABLE.COLUMN",
                              "observed_in": None, "effect_now": None, "why": why, "preselected": None})
                continue
            options, where = [o["value"] for o in dd["options"]], None
            if len(options) == 1:  # the data allows one way only: settled, and said so
                settle_from_data(ws, q, options[0], t)
                decided.append({"id": q["id"], "prompt": q["prompt"], "value": options[0], "writes_to": q["writes_to"],
                                "why": f"only one way in your data: {dd['options'][0]['label']}", "source": "data"})
                continue
            if len(options) < q["applies_when"].get("min_options", 2):
                hidden.append({"id": q["id"], "why": f"only {len(options)} possible answer(s) in the data"})
                continue
        else:
            options, where = observed_values(ws, q.get("options_from"))
            if q.get("options_from") and len(options) < q["applies_when"].get("min_options", 2):
                hidden.append({"id": q["id"], "why": f"only {len(options)} value(s) in the data"})
                continue
        if q["kind"] == "multi" and D.is_flag(options):  # a yes/no flag: "yes" is the obvious answer
            flag_yes = next(v for v in options if str(v).strip().lower() in {"true", "yes", "y", "1", "t"}) \
                if any(str(v).strip().lower() in {"true", "yes", "y", "1", "t"} for v in options) else options[0]
            settle_from_data(ws, q, [flag_yes], t)
            decided.append({"id": q["id"], "prompt": q["prompt"], "value": [flag_yes], "writes_to": q["writes_to"],
                            "why": f"it's a yes/no flag in {where}; counting '{flag_yes}'", "source": "data"})
            continue
        cur = current_value(q, t)
        if q.get("group") == "label" and q["kind"] == "multi" and isinstance(cur, list) and cur \
                and set(map(str, cur)) <= set(map(str, options)) and t.get("source") in {"default", "data"} \
                and not conflict_options(ws, q["writes_to"]):
            settle_from_data(ws, q, cur, t)  # the data's own labels answer it (e.g. TERM_TYPE = Voluntary)
            decided.append({"id": q["id"], "prompt": q["prompt"], "value": cur, "writes_to": q["writes_to"],
                            "why": f"your data labels it: {', '.join(map(str, cur))}" + (f" in {where}" if where else ""),
                            "source": "data"})
            continue
        if q["kind"] == "date_choice" and dd:
            cur = t.get("value") if t.get("source") == "analyst" else dd["options"][0]["value"]
        conflicts = conflict_options(ws, q["writes_to"])
        item = {"id": q["id"], "prompt": q["prompt"], "help": q.get("help"), "kind": q["kind"],
                "writes_to": q["writes_to"], "impact": q["impact"], "value": cur,
                "affects": sorted(set(users) & set(skills)) or users,
                "options": (conflicts or (dd["options"] if dd else None) or q.get("common_answers") or
                            ([{"value": i + 1, "label": m} for i, m in enumerate(MONTHS)] if q["kind"] == "month"
                             else [{"value": v, "label": v} for v in options])),
                "observed_in": where, "effect_now": effect_now(ws, q, skills)}
        option_effects(ws, q, item["options"], skills, run)
        item["spread"] = spread(item["options"])
        item["group"] = q.get("group", "convention")
        prior = state.get(q["id"])
        support = glossary_support(ws, cur) if isinstance(cur, list) else None
        if prior and prior.get("status") == "reopened":
            asked.append({**item, "why": "you asked to change this", "preselected": cur, "impact": 99})
        elif prior and prior.get("status") == "answered" and not conflicts:
            decided.append({**item, "why": "you answered this", "source": "you"})
        elif t.get("source") == "analyst" and not conflicts:
            decided.append({**item, "why": "you set this earlier", "source": "you"})
        elif conflicts:
            asked.append({**item, "why": "your documents disagree", "preselected": cur})
        elif item["group"] == "convention" and item["spread"] is not None and item["spread"] < SAME_NUMBER:
            decided.append({**item, "why": "every way gives about the same number in your data", "source": "data"})
        elif q["kind"] == "number" and item["group"] != "policy" \
                and not (t.get("source") == "context" and t.get("status") == "needs_review"):
            decided.append({**item, "why": "standard value; tell us if yours differs", "source": "default"})
        elif support:
            decided.append({**item, "why": f"your documents say so ({support})", "source": support})
        elif prior and prior.get("status") == "skipped":
            decided.append({**item, "why": "skipped for now; using our best guess", "source": "deferred"})
        else:
            why = {"default": "standard method; tell us if yours differs", "data": "based on the values in your data",
                   "context": "from your documents"}.get(t.get("source"), "our best guess")
            asked.append({**item, "why": why, "preselected": cur})
    for s in D.cached_suggestions(ws, skills):  # assistant suggestions, already validated against the data
        if not set(s["skills"]) & set(skills):
            continue
        prior = state.get(s["id"])
        item = {"id": s["id"], "prompt": s["prompt"], "help": s.get("help"), "kind": s["kind"], "writes_to": s["id"],
                "impact": 2 if s.get("group") == "policy" else 1, "value": (prior or {}).get("value", s.get("default")),
                "affects": sorted(set(s["skills"]) & set(skills)), "group": s.get("group", "convention"),
                "options": s["options"], "observed_in": None, "effect_now": None, "suggested": True}
        if prior and prior.get("status") in {"answered", "skipped"}:
            decided.append({**item, "why": "you answered this" if prior["status"] == "answered" else "skipped for now",
                            "source": "you"})
        else:
            asked.append({**item, "why": s.get("source", "suggested from your skill's instructions"),
                          "preselected": s.get("default")})
    asked += gate_questions(ws, skills, state)
    asked.sort(key=rank, reverse=True)
    keep, per_skill = [], {s: 0 for s in skills}
    for a in asked:  # flexible: everything that matters, but never more than MAX_QUESTIONS for any one skill
        mine = [s for s in a.get("affects") or skills if s in per_skill]
        if all(per_skill[s] < MAX_QUESTIONS for s in mine) or not mine:
            keep.append(a)
            for s in mine:
                per_skill[s] += 1
        else:
            guess = a.get("preselected") not in (None, [], "")
            decided.append({**a, "why": "assumed for now (tell us to change)" if guess else "asked next round",
                            "source": "deferred"})
    out = {"skills": skills, "ask": keep, "decided": decided, "hidden": hidden, "cannot_compute": cannot,
           "follow_up": {"prompt": "Is there any other rule or context we should know for these skills? "
                                   "For example, how you count something, an exclusion, or a threshold.",
                         "skills": skills},
           "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    save(ws / "questions.json", out)
    return out


SAME_NUMBER = 0.01  # options within 1% of each other don't change the answer
NOT_HAVE = "__not_available__"


def gate_questions(ws: Path, skills: list[str], state: dict[str, Any]) -> list[dict[str, Any]]:
    """One question per failed binding rule (eval_gate.py): 'where is this in your data?'."""
    import discover_topics
    gate = (load(ws / "binding_gate.json", {}) or {}).get("skills", {})
    needs = (load(ws / "bindings.json", {}) or {}).get("needs", {})
    labels = {n["key"]: discover_topics.need_label(n)
              for s in discover_topics.load(discover_topics.CATALOG_DIR / "people.json")["skills"]
              for n in s["data_needs"]}
    merged: dict[str, dict[str, Any]] = {}
    for skill in skills:
        for r in (gate.get(skill) or {}).get("rules", []):
            if r["passed"] or r["level"] not in {"G", "P"}:
                continue
            need = r.get("need") or next(iter(r.get("unbound_needs") or []), None)
            fields = (r.get("missing_fields") or []) + (r.get("weak_fields") or [])
            for fld in fields or [None]:  # one question per missing column (or per missing table)
                qid = f"gate:{need}:{fld}" if fld else f"gate:{need}"
                q = merged.setdefault(qid, {"need": need, "field": fld, "skills": [], "rules": [], "level": "P"})
                q["skills"] += [skill] if skill not in q["skills"] else []
                q["rules"].append(f"{r['title']}: {r['reason']}")
                q["level"] = "G" if "G" in (q["level"], r["level"]) else "P"
    out = []
    for qid, m in merged.items():
        if (state.get(qid) or {}).get("status") in {"answered", "skipped"}:
            continue
        need, fld = m["need"], m["field"]
        what = labels.get(need, need)
        current = ((needs.get(need) or {}).get("fields") or {})
        if fld:
            names = fld.split("|")
            label = " or ".join(n.replace("_", " ") for n in names)
            bound = next((current[n]["column"] for n in names if (current.get(n) or {}).get("column")), None)
            prompt = f"For {what}, which column holds {label}?"
            opts = [{"value": bound, "label": f"{bound} (our guess, not certain)"}] if bound else []
            free = "TABLE.COLUMN"
        else:
            label, bound = what, None
            prompt = f"Which table holds {what}?"
            opts = [{"value": a["fqn"], "label": a["fqn"]} for a in (needs.get(need) or {}).get("alternatives", [])[:3]]
            free = "TABLE"
        out.append({"id": qid, "prompt": prompt, "help": "; ".join(m["rules"]), "kind": "gate",
                    "writes_to": None, "impact": 3 if m["level"] == "G" else 2, "value": bound,
                    "affects": m["skills"], "group": "data",
                    "options": opts + [{"value": NOT_HAVE, "label": "We don't have this"}],
                    "free_text": free, "observed_in": None, "effect_now": None,
                    "why": "needed to confirm the data connection", "preselected": bound,
                    "need": need, "need_label": what, "fields": [fld] if fld else [], "field_label": label})
    return out


def spread(options: list[dict[str, Any]]) -> float | None:
    """How far apart the options' numbers are (relative); None when not computed."""
    vals = [float(v) for o in options
            for v in [o.get("effect_value", o.get("effect"))] if isinstance(v, (int, float))]
    if len(vals) < 2:
        return None
    lo, hi = min(vals), max(vals)
    if all(v.is_integer() for v in vals) and hi - lo < 1:  # counts: less than one apart
        return 0.0
    return (hi - lo) / max(abs(hi), abs(lo), 1e-9)


def rank(q: dict[str, Any]) -> tuple[float, int]:
    """Most consequential first: how much the answer moves the numbers, then why it is asked."""
    if q.get("id") == "source_schema":  # which copy of the tables: every other number depends on it
        return 100.0, 100
    boost = 2.0 if q.get("why") in {"your documents disagree", "you asked to change this"} else 0.0
    boost += 1.0 if q.get("kind") in {"locate", "gate"} else 0.0
    boost += 0.5 if q.get("group") == "policy" else 0.0
    s = q.get("spread")
    s = 0.2 if s is None else s  # no number for each option (e.g. which values count): rank by impact, not last
    return s * 4 + boost + q.get("impact", 1) / 10, q.get("impact", 1)


def settle_from_data(ws: Path, q: dict[str, Any], value: Any, t: dict[str, Any]) -> None:
    """Record a definition the data settles on its own, so the hydrated skill applies it.
    Never overrides an analyst answer or a document."""
    if t.get("source") in {"analyst", "context"} or t.get("value") == value:
        return
    for path in sorted((ws / "tuning").glob("*.json")):
        data = load(path, {}) or {}
        cur = data.get("tunables", {}).get(q["writes_to"])
        if cur is not None and cur.get("source") not in {"analyst", "context"}:
            cur.update({"value": value, "source": "data", "status": "auto",
                        "evidence": ["the only answer your data supports"]})
            save(path, data)


def set_tunable(ws: Path, tid: str, value: Any, create_for: list[str] | None = None,
                label: str | None = None, kind: str | None = None) -> list[str]:
    """Write an analyst answer into every skill's settings that has it (and, for definitions
    that have no setting yet, into the skills in `create_for`)."""
    changed = []
    (ws / "tuning").mkdir(exist_ok=True)
    paths = {p.stem: p for p in (ws / "tuning").glob("*.json")}
    for s in create_for or []:
        paths.setdefault(s, ws / "tuning" / f"{s}.json")
    for stem, path in sorted(paths.items()):
        data = load(path, {}) or {"skill": stem, "tunables": {}}
        t = data.setdefault("tunables", {}).get(tid)
        if t is None:
            if stem not in (create_for or []):
                continue
            t = data["tunables"][tid] = {"label": label or tid.replace("_", " "), "type": kind or "definition"}
        if tid == "attrition_rate_method" and isinstance(value, dict):
            t.update({"method": value, "value": ("annualized" if value.get("annualized") else "not annualized")
                      + f"; denominator = {value.get('denominator', 'average')} headcount"})
        else:
            t["value"] = value
        t.update({"source": "analyst", "status": "confirmed"})
        save(path, data)
        changed.append(path.stem)
    return changed


def use_schema(ws: Path, schema: str) -> list[str]:
    """Point every topic whose chosen table also exists in `schema` at that copy (analyst decision)."""
    import discover_topics as T
    data = load(ws / "topics.json")
    changed = []
    for t in data["topics"]:
        if t["role"] not in {"required", "optional"}:
            continue
        current = t.get("chosen") or t.get("recommended") or []
        by_name = {c["fqn"].rsplit(".", 1)[1]: c["fqn"] for c in t.get("candidates", [])
                   if c["fqn"].rsplit(".", 1)[0] == schema}
        new = sorted({by_name.get(f.rsplit(".", 1)[1], f) for f in current})
        if current and new != sorted(current):
            T.confirm(ws, t["id"], new)
            changed.append(t["id"])
    return changed


def answer(ws: Path, qid: str, value: Any, skip: bool = False, connection: str | None = None) -> dict[str, Any]:
    if qid.startswith("gate:"):
        return answer_gate(ws, qid, value, skip, connection)
    if qid == "source_schema":
        state = load(ws / "questions_state.json", {}) or {}
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        if skip:
            state[qid] = {"status": "skipped", "at": now}
            save(ws / "questions_state.json", state)
            return {"id": qid, "status": "skipped"}
        changed = use_schema(ws, str(value))
        state[qid] = {"status": "answered", "value": value, "at": now}
        save(ws / "questions_state.json", state)
        return {"id": qid, "status": "answered", "topics": changed, "rebind": bool(changed)}
    q = D.by_id(qid)
    suggestion = None if q else next((s for s in D.cached_suggestions(ws, D.all_skills()) if s["id"] == qid), None)
    if q is None and suggestion is None:
        raise SystemExit(f"unknown question {qid}")
    state = load(ws / "questions_state.json", {}) or {}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if skip:
        state[qid] = {"status": "skipped", "at": now}
        save(ws / "questions_state.json", state)
        return {"id": qid, "status": "skipped"}
    if suggestion is not None:  # assistant suggestion: the answer must be one of its data-backed options
        allowed = [o["value"] for o in suggestion["options"]]
        picked = value if isinstance(value, list) else [value]
        if suggestion["kind"] == "number":  # a threshold: any number in the suggested range
            try:
                n = float(picked[0])
            except (TypeError, ValueError, IndexError):
                raise SystemExit("give a number")
            if not min(allowed) <= n <= max(allowed):
                raise SystemExit(f"give a number between {min(allowed):g} and {max(allowed):g}")
            value = int(n) if n.is_integer() else n
        elif not picked or any(v not in allowed for v in picked) or (suggestion["kind"] != "multi" and len(picked) != 1):
            raise SystemExit("pick from the options shown")
        changed = set_tunable(ws, qid, value, create_for=suggestion["skills"], label=suggestion["label"],
                              kind=suggestion["kind"])
        state[qid] = {"status": "answered", "value": value, "at": now}
        save(ws / "questions_state.json", state)
        return {"id": qid, "status": "answered", "skills": changed}
    if q["kind"] in {"date_choice", "date_span"} or (q["kind"] == "choice" and any(
            a.get("requires") for a in q.get("common_answers", []))):
        opts, note = D.options(ws, q)
        located = None
        if value == D.NOT_TRACKED:
            located = value
        elif isinstance(value, dict) and "text" in value:  # "APPLICATIONS.HIRED_DATE"
            try:
                located = D.locate_answer(ws, q, value["text"])["value"]
            except ValueError as exc:
                raise SystemExit(str(exc))
        elif value not in [o["value"] for o in opts]:
            extra = [o["value"] for o in D.other_dates(ws, q, set(), limit=50)]
            if value not in extra:
                raise SystemExit("pick from the options shown, or name the table and column")
        if located is not None:
            value = located
    if q["kind"] == "number":
        lo, hi = q.get("bounds", [0, 10 ** 6])
        value = int(value)
        if not lo <= value <= hi:
            raise SystemExit(f"pick a number from {lo} to {hi}")
    if q["kind"] == "multi" and not (isinstance(value, list) and value):
        raise SystemExit("pick at least one value")
    if q["kind"] == "month":
        value = int(value)
        if not 1 <= value <= 12:
            raise SystemExit("month must be 1-12")
        import context_store  # the chosen document wins; the disagreeing ones are set aside
        ctx = context_store.load(ws / "context.json")
        for o in conflict_options(ws, q["writes_to"]):
            context_store.set_status(ctx, o["item"], "confirmed" if o["value"] == value else "rejected")
        context_store.save(ws / "context.json", ctx)
    changed = set_tunable(ws, q["writes_to"], value,
                          create_for=(sorted(set(q["skills"]) & set(state_skills(ws))) or q["skills"])
                          if q["writes_to"].startswith("def_") else None,
                          label=D.label_of(q), kind=q["kind"])
    rebound = False
    if q["kind"] == "date_choice" and q.get("binds") and isinstance(value, str):  # the chosen date becomes the bound column
        learned = load(ws / "learned.json", {}) or {}
        learned.setdefault("fields", {})[f"{q['binds']['need']}.{q['binds']['field']}"] = value
        save(ws / "learned.json", learned)
        import subprocess
        subprocess.run([sys.executable, str(SCRIPTS / "discover.py"), "--workspace", str(ws), "--rematch"],
                       capture_output=True, text=True)
        rebound = True
    state[qid] = {"status": "answered", "value": value, "at": now}
    save(ws / "questions_state.json", state)
    return {"id": qid, "status": "answered", "skills": changed, "rebound": rebound}


def answer_gate(ws: Path, qid: str, value: Any, skip: bool, connection: str | None = None) -> dict[str, Any]:
    """A binding-check answer becomes a validated fix proposal (fix_ledger.py), never a direct write."""
    state = load(ws / "questions_state.json", {}) or {}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    q = next((x for x in (load(ws / "questions.json", {}) or {}).get("ask", []) if x["id"] == qid), None)
    if q is None:
        raise SystemExit(f"unknown question {qid}; run questions.py plan first")
    if skip or value == NOT_HAVE:
        state[qid] = {"status": "skipped" if skip else "answered", "value": None if skip else NOT_HAVE, "at": now,
                      "skills": q["affects"], "label": q.get("field_label") or q["need_label"],
                      "need_label": q["need_label"]}
        save(ws / "questions_state.json", state)
        return {"id": qid, "status": state[qid]["status"], "note": "the skill will say this isn't available"}
    text = str(value.get("text") if isinstance(value, dict) else value).strip()
    if not text:
        raise SystemExit("name the table or TABLE.COLUMN")
    import fix_ledger  # an exact, validated change; applied only after the analyst agrees
    skill = q["affects"][0]
    item = fix_ledger.from_gate(ws, skill, q["need"], (q.get("fields") or [None])[0], text, connection)
    state[qid] = {"status": "answered" if item["status"] in {"proposed", "confirmed"} else "open",
                  "value": text, "at": now, "fix": item["id"]}
    save(ws / "questions_state.json", state)
    out = {"id": qid, "status": state[qid]["status"], "skill": skill, "fix": item["id"], "fix_status": item["status"],
           "why": item.get("why"), "changes": (item.get("proposal") or {}).get("changes")}
    if item["status"] == "proposed":
        out["next"] = f"fix_ledger.py apply --workspace W --id {item['id']} --connection C  (after the analyst agrees)"
    if item["status"] in {"proposed", "confirmed"}:
        out["then"] = f"eval_gate.py --workspace W --skill {skill}"
    return out


def state_skills(ws: Path) -> list[str]:
    return list((load(ws / "state.json", {}) or {}).get("skills") or
                (load(ws / "topics.json", {}) or {}).get("skills") or [])


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["plan", "answer"])
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--skill", action="append", default=[])
    p.add_argument("--id")
    p.add_argument("--value")
    p.add_argument("--skip", action="store_true")
    p.add_argument("--connection", help="adds what each option changes (one batched round)")
    p.add_argument("--ai", action="store_true", help="compute assistant suggestions now if not cached")
    a = p.parse_args()
    ws = a.workspace.resolve()
    if a.action == "plan":
        run = None
        if a.connection:
            from _sql import make_runner
            run = make_runner(a.connection, 120)
            if a.ai:
                D.ai_suggestions(ws, a.skill, a.connection)
        out = plan(ws, a.skill, run)
        print(json.dumps({"ask": [{k: q.get(k) for k in ("id", "prompt", "help", "kind", "group", "preselected", "why",
                                                          "options", "effect_now", "affects", "suggested", "free_text")}
                                  for q in out["ask"]],
                          "decided": [{k: q.get(k) for k in ("id", "prompt", "value", "why")} for q in out["decided"]],
                          "cannot_compute": out["cannot_compute"], "hidden": out["hidden"],
                          "follow_up": out["follow_up"]}, indent=2, default=str))
    else:
        print(json.dumps(answer(ws, a.id, json.loads(a.value) if a.value else None, a.skip, a.connection)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
