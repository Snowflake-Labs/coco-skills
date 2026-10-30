#!/usr/bin/env python3
"""Customer Context Profile: one versioned, auditable view of what we know. Stdlib only.

The profile is built FROM the existing workspace artifacts (no second copy of the
facts): topics.json (roles), data_index.json + scan_metadata.json (table facts),
context.json (glossary), tuning/*.json + learned.json (conventions), corrections.json
and context reference numbers (corrections), sources.json + context consent (connections).

Every entry has the same shape:
  {value, status: inferred|confirmed|deferred|stale|conflict, evidence: [..],
   sources: [{kind, ref, at}], scope: {skills: [..]}, fingerprint?: {..},
   confirmed?: {by_role, at}}

Rules:
  - Confirmed entries are never overwritten silently: if the rebuilt value differs,
    the old value is kept and the entry becomes `conflict` with the new proposal.
  - A table whose fingerprint (columns/types, code values, latest date) changed
    marks every role that uses it `stale` until the analyst re-confirms.
  - The version increases only when content changes; each change is appended to
    profile_history.jsonl. Hydrated packages pin profile.version (the lockfile).

  profile_store.py build   --workspace W
  profile_store.py summary --workspace W
  profile_store.py refresh --workspace W --connection C   (cheap freshness check of bound tables)
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

SECTIONS = ("connections", "roles", "tables", "glossary", "conventions", "corrections")
GLOSSARY_KINDS = {"definition", "fiscal_calendar", "caveat", "threshold", "org_units"}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:12]


def entry(value: Any, status: str, evidence: list[str] | None = None, sources: list[dict[str, Any]] | None = None,
          skills: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    e = {"value": value, "status": status, "evidence": list(evidence or []), "sources": list(sources or []),
         "scope": {"skills": sorted(set(skills or []))}}
    e.update({k: v for k, v in extra.items() if v is not None})
    return e


# ---------- fingerprints -------------------------------------------------------------

def fingerprint(fqn: str, scan_obj: dict[str, Any] | None, index_obj: dict[str, Any] | None) -> dict[str, Any]:
    """Schema (columns + types), code values of low-cardinality columns, latest date."""
    cols = (scan_obj or index_obj or {}).get("columns", {})
    schema = digest(sorted((c, str(m.get("data_type"))) for c, m in cols.items()))
    icols = (index_obj or {}).get("columns", {})
    values = {c: digest(sorted(str(v) for v, _n in m.get("top_values") or []))
              for c, m in icols.items() if m.get("is_code") and m.get("top_values")}
    latest = [v.get("latest") for v in ((index_obj or {}).get("cadence") or {}).values() if v.get("latest")]
    return {"schema": schema, "values": values, "max_date": max(latest) if latest else None}


def fingerprint_changes(old: dict[str, Any] | None, new: dict[str, Any]) -> list[str]:
    if not old:
        return []
    out = []
    if old.get("schema") != new.get("schema"):
        out.append("columns or types changed")
    changed = [c for c, h in new.get("values", {}).items() if c in old.get("values", {}) and old["values"][c] != h]
    if changed:
        out.append("new or removed code values in " + ", ".join(sorted(changed)[:4]))
    if old.get("max_date") and new.get("max_date") and new["max_date"] < old["max_date"]:
        out.append("latest date moved backwards")
    return out


# ---------- sections ------------------------------------------------------------------

def connections(ws: Path) -> dict[str, Any]:
    ctx = load(ws / "context.json", {"sources": []})
    consented = {s["connector"]: s for s in ctx.get("sources", [])}
    out = {}
    for s in load(ws / "sources.json", {"sources": []}).get("sources", []):
        c = consented.get(s["connector"])
        out[s["connector"]] = entry({"origin": s.get("origin"), "usable_here": s.get("usable_here"),
                                     "scopes": (c or {}).get("scopes", [])},
                                    "confirmed" if c else "inferred",
                                    ["consented by the analyst"] if c else ["detected; not connected"],
                                    [{"kind": "metadata", "ref": "sources.json", "at": (c or {}).get("consented_at")}])
    for name, c in consented.items():  # uploaded doc folders are not "detected"
        out.setdefault(name, entry({"origin": "upload", "scopes": c.get("scopes", [])}, "confirmed",
                                   ["consented by the analyst"], [{"kind": "user", "ref": "context.json"}]))
    return out


def roles(ws: Path) -> dict[str, Any]:
    out = {}
    for t in load(ws / "topics.json", {"topics": []}).get("topics", []):
        if t["role"] not in {"required", "optional"}:
            continue
        tables = t.get("chosen") or ([] if t["status"] == "none" else t.get("recommended") or [])
        by_analyst = t["status"] in {"confirmed", "none"} and not (t.get("auto") or t.get("auto_none"))
        status = ("confirmed" if by_analyst else "deferred" if not tables else "inferred")
        top = next((c for c in t.get("candidates", []) if c["fqn"] in tables), None)
        out[t["id"]] = entry({"label": t["label"], "tables": tables,
                              "candidates": [c["fqn"] for c in t.get("candidates", [])]},
                             status, (top or {}).get("reasons", [])[:4] or (["nothing matched"] if not tables else []),
                             [{"kind": "user" if by_analyst else "metadata", "ref": "topics.json"}],
                             t.get("required_by", []) + t.get("optional_for", []))
    return out


def tables(ws: Path, role_map: dict[str, Any]) -> dict[str, Any]:
    index = load(ws / "data_index.json", {"objects": {}})
    scan = load(ws / "scan_metadata.json", {"objects": {}})
    used: dict[str, list[str]] = {}
    for rid, r in role_map.items():
        for fqn in r["value"]["tables"]:
            used.setdefault(fqn, []).append(rid)
    out = {}
    for fqn, rids in used.items():
        obj = index["objects"].get(fqn) or {}
        grain = next((g["columns"] for g in obj.get("grain_candidates", []) if g.get("unique")), None)
        dates = {c: (m.get("min"), m.get("max")) for c, m in obj.get("columns", {}).items() if m.get("is_date") and m.get("max")}
        cadence = obj.get("cadence") or {}
        ev = []
        if grain:
            ev.append("one row per " + " × ".join(grain))
        if obj.get("row_count") is not None:
            ev.append(f"{obj['row_count']} rows")
        out[fqn] = entry({"grain": grain, "rows": obj.get("row_count"), "kind": obj.get("kind"),
                          "date_coverage": dates, "latest": max((v.get("latest") for v in cadence.values()
                                                                 if v.get("latest")), default=None),
                          "roles": sorted(rids)},
                         "inferred" if obj else "deferred", ev or ["not profiled yet"],
                         [{"kind": "metadata", "ref": "data_index.json", "at": index.get("generated_at")}],
                         sorted({s for rid in rids for s in role_map[rid]["scope"]["skills"]}),
                         fingerprint=fingerprint(fqn, scan["objects"].get(fqn), obj))
    return out


def glossary(ws: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for i in load(ws / "context.json", {"items": []}).get("items", []):
        if i["kind"] not in GLOSSARY_KINDS or i["status"] == "rejected":
            continue
        key = f"{i['kind']}:{i.get('term') or ''}"
        src = {"kind": "doc", "ref": i["source"].get("title") or i["source"].get("ref"),
               "connector": i["source"]["connector"], "at": i["source"].get("updated")}
        status = {"confirmed": "confirmed", "proposed": "inferred"}.get(i["status"], "inferred")
        if i.get("conflicts_with"):
            status = "conflict" if status != "confirmed" else status
        cur = out.get(key)
        if cur and digest(cur["value"]) == digest(i["value"]):
            cur["sources"].append(src)  # the same definition in several documents
            if status == "confirmed":
                cur["status"] = "confirmed"
            continue
        if cur and cur["status"] == "confirmed":
            continue
        out[key] = entry(i["value"], status, [i.get("text") or ""][:1], [src], item_id=i["id"])
    for r in (load(ws / "customer_rules.json", {"rules": []}) or {}).get("rules", []):  # the analyst's own rules
        out[f"rule:{r['id']}"] = entry(r["text"], "confirmed", [], [{"kind": "user", "ref": "customer_rules.json",
                                                                     "at": r.get("at")}], [r["skill"]])
    return out


def conventions(ws: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path in sorted((ws / "tuning").glob("*.json")):
        skill = path.stem
        for tid, t in (load(path, {}) or {}).get("tunables", {}).items():
            by_analyst = t.get("source") == "analyst" or t.get("status") == "confirmed"
            cur = out.get(tid)
            if cur:
                cur["scope"]["skills"] = sorted(set(cur["scope"]["skills"]) | {skill})
                if digest(cur["value"]) != digest(t.get("value")):  # a per-skill override
                    cur.setdefault("overrides", {})[skill] = t.get("value")
                continue
            out[tid] = entry(t.get("value"), "confirmed" if by_analyst else "inferred",
                             list(t.get("evidence", []))[:3], [{"kind": "user" if by_analyst else t.get("source", "default"),
                                                                 "ref": f"tuning/{skill}.json"}],
                             [skill], label=t.get("label"))
    return out


def corrections(ws: Path) -> dict[str, Any]:
    """Reference numbers from documents, and the analyst's accepted corrections (re-checked by the Tune checks)."""
    out: dict[str, Any] = {}
    for i in load(ws / "context.json", {"items": []}).get("items", []):
        if i["kind"] != "reference_number" or i["status"] == "rejected" or not isinstance(i["value"], dict):
            continue
        out[f"doc:{i['value'].get('metric')}"] = entry(
            {k: i["value"].get(k) for k in ("metric", "value", "as_of", "population", "unit")},
            "inferred", [i.get("text") or ""][:1],
            [{"kind": "doc", "ref": i["source"].get("title") or i["source"].get("ref"), "connector": i["source"]["connector"]}])
    for c in (load(ws / "corrections.json", {}) or {}).get("corrections", []):
        out[c["id"]] = entry({k: c.get(k) for k in ("text", "metric", "value_after", "ops")}, "confirmed",
                             [c["text"]], [{"kind": "user", "ref": "corrections.json", "at": c.get("at")}],
                             c.get("skills", []))
    return out


# ---------- build ---------------------------------------------------------------------

def compute(ws: Path) -> dict[str, Any]:
    r = roles(ws)
    return {"connections": connections(ws), "roles": r, "tables": tables(ws, r), "glossary": glossary(ws),
            "conventions": conventions(ws), "corrections": corrections(ws)}


def merge(prior: dict[str, Any] | None, fresh: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Protect confirmed entries; flag stale tables and the roles that use them."""
    notes: list[str] = []
    if not prior:
        return fresh, notes
    for section in SECTIONS:
        old_sec = prior.get(section, {})
        for key, new in fresh.get(section, {}).items():
            old = old_sec.get(key)
            if not old:
                continue
            if old["status"] == "stale" and section in {"roles", "tables"}:
                new["status"], new["evidence"] = "stale", old["evidence"]  # until the analyst re-confirms
                continue
            if old["status"] != "confirmed":
                continue
            if digest(old["value"]) == digest(new["value"]) or section == "tables":
                new["status"] = "confirmed" if section != "tables" else new["status"]
                if old.get("confirmed") or new.get("confirmed"):
                    new["confirmed"] = old.get("confirmed") or new.get("confirmed")
            elif new["status"] != "confirmed":
                fresh[section][key] = {**old, "status": "conflict", "proposed_value": new["value"],
                                       "evidence": old["evidence"] + ["the data or documents now suggest a different value"]}
                notes.append(f"{section}.{key}: confirmed value kept; new proposal needs your review")
    for fqn, t in fresh.get("tables", {}).items():
        changes = fingerprint_changes(prior.get("tables", {}).get(fqn, {}).get("fingerprint"), t.get("fingerprint", {}))
        if changes:
            t["status"] = "stale"
            t["evidence"] = changes + t["evidence"]
            for rid in t["value"]["roles"]:
                role = fresh["roles"].get(rid)
                if role and role["status"] in {"confirmed", "inferred"}:
                    role["status"] = "stale"
                    role["evidence"] = [f"{fqn.rsplit('.', 1)[1]}: {c}" for c in changes] + role["evidence"]
            notes.append(f"{fqn}: {', '.join(changes)}")
    return fresh, notes


def build(ws: Path) -> dict[str, Any]:
    prior = load(ws / "profile.json")
    body, notes = merge(prior, compute(ws))
    stamp = {k: v for k, v in body.items()}
    content = digest(stamp)
    version = (prior or {}).get("version", 0)
    if not prior or prior.get("content_hash") != content:
        version += 1
        changed = sorted(f"{s}.{k}" for s in SECTIONS for k in body.get(s, {})
                         if digest(body[s][k]) != digest((prior or {}).get(s, {}).get(k)))
        with (ws / "profile_history.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"version": version, "at": now(), "changed": changed[:200], "notes": notes}) + "\n")
    profile = {"profile_version": 1, "version": version, "content_hash": content,
               "updated_at": now() if version != (prior or {}).get("version") else prior.get("updated_at"),
               **body, "notes": notes}
    save(ws / "profile.json", profile)
    return profile


def mark_confirmed(ws: Path, section: str, key: str, role: str | None) -> None:
    """Record who/when for an analyst confirmation (role only, never a person's name)."""
    profile = load(ws / "profile.json") or build(ws)
    e = profile.get(section, {}).get(key)
    if e:
        e["status"] = "confirmed"
        e["confirmed"] = {"by_role": role, "at": now()}
        e.pop("proposed_value", None)
        save(ws / "profile.json", profile)


# ---------- summary + freshness -------------------------------------------------------

def summary(profile: dict[str, Any], skills: list[str] | None = None) -> dict[str, Any]:
    """Tables found (confirmed by you / picked by us), settings assumed, and what's still open."""
    want = set(skills or [])

    def relevant(e: dict[str, Any]) -> bool:
        return not want or not e["scope"]["skills"] or bool(want & set(e["scope"]["skills"]))

    found, assumed, settings, open_ = [], [], [], []
    for rid, r in profile.get("roles", {}).items():
        if not relevant(r):
            continue
        label = r["value"]["label"]
        if r["status"] == "deferred":
            open_.append(f"{label}: no table found")
        elif r["status"] in {"stale", "conflict"}:
            open_.append(f"{label}: {r['evidence'][0] if r['evidence'] else r['status']}")
        else:
            tbls = ", ".join(f.rsplit(".", 1)[1] for f in r["value"]["tables"])
            (found if r["status"] == "confirmed" else assumed).append(f"{label}: {tbls or 'not available'}")
    for tid, c in profile.get("conventions", {}).items():
        if relevant(c) and c["status"] == "inferred" and c.get("label"):
            settings.append(f"{c['label']}: {plain(c['value'])}")
        elif relevant(c) and c["status"] == "conflict":
            open_.append(f"{c.get('label') or tid}: confirmed value differs from what the data shows now")
    for gid, g in profile.get("glossary", {}).items():
        if g["status"] == "conflict":
            open_.append(f"{gid.split(':', 1)[1].replace('_', ' ') or gid}: your documents disagree")
    return {"version": profile.get("version"), "found": found, "assumed": assumed, "settings": settings,
            "open": open_}


def plain(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(map(str, value)) or "none"
    if isinstance(value, dict):
        return "; ".join(f"{k.replace('_', ' ')} {v}" for k, v in value.items())
    return str(value)


def refresh(ws: Path, connection: str | None) -> dict[str, Any]:
    """Re-read columns/types of bound tables only and rebuild; changed tables become stale."""
    from _sql import SqlError, make_runner, quote_ident, quote_literal
    scan = load(ws / "scan_metadata.json", {"objects": {}})
    profile = load(ws / "profile.json") or build(ws)
    run = make_runner(connection, 60)
    checked = []
    for fqn in profile.get("tables", {}):
        db, schema, name = fqn.split(".", 2)
        try:
            rows = run(f"select column_name, data_type from {quote_ident(db)}.information_schema.columns "
                       f"where table_schema = {quote_literal(schema)} and table_name = {quote_literal(name)}")
        except SqlError:
            continue
        if fqn in scan["objects"] and rows:
            scan["objects"][fqn]["columns"] = {r["COLUMN_NAME"]: {**scan["objects"][fqn]["columns"].get(r["COLUMN_NAME"], {}),
                                                                  "data_type": r["DATA_TYPE"]} for r in rows}
            checked.append(fqn)
    if checked:
        save(ws / "scan_metadata.json", scan)
    out = build(ws)
    return {"checked": checked, "notes": out["notes"], "version": out["version"]}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["build", "summary", "refresh"])
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--connection")
    p.add_argument("--skill", action="append", default=[])
    a = p.parse_args()
    ws = a.workspace.resolve()
    if a.action == "build":
        prof = build(ws)
        print(json.dumps({"version": prof["version"], "notes": prof["notes"],
                          **{s: len(prof[s]) for s in SECTIONS}}))
    elif a.action == "summary":
        print(json.dumps(summary(load(ws / "profile.json") or build(ws), a.skill), indent=2))
    else:
        print(json.dumps(refresh(ws, a.connection), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
