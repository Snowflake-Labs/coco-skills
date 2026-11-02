#!/usr/bin/env python3
"""Skill-scoped topic search across every database the role can see (or the ones named). Stdlib only.

Every data need of the chosen skills (catalog/people.json) is scored on its own against every
table: required-field coverage, name hints, comments and usage. No business-area gate, so a
table is found by what it holds; one table may serve several needs.
The search reads metadata only (information_schema tables + columns, optional
query-history counts); no row-level data is read.

  discover_topics.py scan     --workspace W --connection C [--database DB ...] [--exclude DB ...]
                              [--schema S ...] [--skill S ...] [--no-usage]

Without --database the scan covers every database listed in prewarm.json (system,
app and personal databases are already excluded there). Usage is a bounded sample:
ACCESS_HISTORY object lists for candidate tables over 7 days (30 if sparse), then
the user's own recent queries (structure only), then names and columns only.
  discover_topics.py confirm  --workspace W --topic T --table FQN [--table FQN2]   (or --none)
  discover_topics.py accept-recommended --workspace W
  discover_topics.py tables   --workspace W        (print confirmed tables, one per line)

Writes W/topics.json:
  {databases, skills, scanned_at,
   topics: [{id, label, about, role: required|optional|other|future,
             required_by, optional_for, needed_by_other, candidates: [{fqn, score, reasons}],
             chosen: [fqn] | null, status: proposed|confirmed|none}]}

Adding skills later: re-run `scan` with the larger --skill list. Confirmed topics
are kept; only topics without a decision are re-ranked.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from _sql import Runner, SqlError, make_runner  # noqa: E402
from profile_data import is_official, list_objects, list_objects_many  # noqa: E402

CATALOG_DIR = SCRIPTS.parents[1] / "catalog"
TOP_K = 3
MIN_SCORE = 0.35
SKIP_DATABASES = {"SNOWFLAKE", "SNOWFLAKE_SAMPLE_DATA"}
MAX_DATABASES = 50          # very large accounts: scan the first N and say so
USAGE_TIMEOUT_S = 25        # hard cap per usage query
USAGE_SPARSE = 20           # fewer relevant reads than this in 7 days -> widen to 30
# Aliases too generic to prove a table belongs to a topic (every HR table has them).
GENERIC_ALIASES = {"id", "date", "period", "org", "organization", "department", "status", "type",
                   "name", "value", "created_date", "updated_at", "created_at"}
# Topic words too common inside column names to signal a topic on their own.
COLUMN_WORD_STOP = {"date", "dates", "period", "type", "status", "id", "name", "job", "user", "users", "org", "cost",
                    "center", "plan", "range", "review", "activity", "tool", "internal", "move", "stage", "source",
                    "location", "manager", "department", "employee", "worker", "term", "balance"}


def load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t}


def stem(word: str) -> str:
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


# ---------- one scoring unit per data need -------------------------------------------------------

# Words in need keys and concepts that say nothing about which table holds the data.
NAME_STOP = {"people", "ta", "ats", "data", "record", "records", "event", "events", "per", "by", "of", "and",
             "the", "a", "an", "for", "in", "to", "with", "or", "list", "lists", "info", "table", "current",
             "all", "count", "counts", "level", "who", "what", "each"}


def need_label(need: dict[str, Any]) -> str:
    c = (need.get("concepts") or [need["key"].rsplit(".", 1)[-1].replace("_", " ")])[0]
    return c[:1].upper() + c[1:]


def topic_model(catalog: dict[str, Any], skills: list[str]) -> list[dict[str, Any]]:
    """One unit per data need. Tables are scored against each need's own fields and name hints,
    so a table is found by what it holds, not by which business area its name suggests."""
    chosen = set(skills)
    need_specs: dict[str, dict[str, Any]] = {}
    uses: dict[str, list[tuple[str, bool]]] = {}
    for s in catalog["skills"]:
        for n in s["data_needs"]:
            if n["key"].endswith(".semantic_view"):
                continue
            spec = need_specs.setdefault(n["key"], {**n, "name_hints": list(n.get("name_hints", []))})
            spec["name_hints"] = sorted(set(spec["name_hints"]) | set(n.get("name_hints", [])))
            uses.setdefault(n["key"], []).append((s["name"], bool(n.get("required"))))
    out = []
    for key, need in need_specs.items():
        req, opt, other = set(), set(), set()
        for skill, required in uses[key]:
            (req if required else opt).add(skill) if skill in chosen else other.add(skill)
        opt -= req
        role = "required" if req else "optional" if opt else "other"
        fields = need.get("fields", {})
        aliases = {a.lower() for f in fields.values() for a in f.get("aliases", [])} | {f.lower() for f in fields}
        words = set(need.get("name_hints", []))
        words |= tokens(" ".join(key.split(".")[1:])) | tokens(" ".join(need.get("concepts", [])))
        need_fields = [alts for fname, f in fields.items()
                       if f.get("required") and f.get("semantic_type") != "identifier"
                       for alts in [({a.lower() for a in f.get("aliases", [])} | {fname.lower()}) - GENERIC_ALIASES]
                       if alts]  # a field only generic names can describe (status) proves nothing
        out.append({"id": key, "label": need_label(need), "about": "; ".join(need.get("concepts", [])),
                    "role": role, "needs": [key], "required_by": sorted(req), "optional_for": sorted(opt),
                    "needed_by_other": sorted(other - req - opt),
                    "_name_words": {stem(w) for w in words if w not in NAME_STOP and len(w) > 1},
                    "_aliases": aliases, "_need_fields": {key: need_fields} if need_fields else {}})
    return out


# ---------- ranking ------------------------------------------------------------------

def col_match(alias: str, cols: set[str]) -> bool:
    """Exact column, or the alias as a whole word-run inside a longer column (offer_extended_date)."""
    if alias in cols:
        return True
    if len(alias) >= 8 and any(c.endswith("_" + alias) or c.startswith(alias + "_") for c in cols):
        return True
    # a short alias as a whole word of the column (response_score -> score)
    return len(alias) >= 4 and "_" not in alias and any(alias in c.split("_") for c in cols)


def need_coverage(topic: dict[str, Any], cols: set[str]) -> dict[str, float]:
    """Share of each need's required non-id fields this object has; generic aliases don't count."""
    return {key: sum(1 for alts in fields if any(col_match(a, cols) for a in alts - GENERIC_ALIASES)) / len(fields)
            for key, fields in topic["_need_fields"].items() if fields}


def need_name_fit(key: str, fqn: str) -> bool:
    """Table name echoes the need itself (USER_ORG_SCOPING for people.users.org_scoping)."""
    need_tok = {stem(w) for w in tokens(".".join(key.split(".")[1:]))} - {"event", "record", "data"}
    return len({stem(w) for w in tokens(fqn.rsplit(".", 1)[1])} & need_tok) >= 2


def score_object(topic: dict[str, Any], fqn: str, obj: dict[str, Any],
                 usage: dict[str, int]) -> tuple[float, list[str]]:
    name_parts = fqn.split(".")
    name_tok = {stem(w) for w in tokens(name_parts[-1])}
    schema_tok = {stem(w) for w in tokens(".".join(name_parts[:-1]))}
    cols = {c.lower() for c in obj["columns"]}
    words = topic["_name_words"]
    reasons: list[str] = []

    name_hits = sorted(name_tok & words)
    name = min(1.0, len(name_hits) / 2) if name_hits else (0.4 if schema_tok & words else 0.0)
    if name_hits:
        reasons.append("name mentions " + ", ".join(name_hits))

    alias_hits = sorted(a for a in topic["_aliases"] - GENERIC_ALIASES if col_match(a, cols))
    col_word_hits = sorted({c for c in cols if {stem(w) for w in tokens(c)} & (words - COLUMN_WORD_STOP)})
    coverage = max(need_coverage(topic, cols).values(), default=0.0)
    breadth = min(1.0, len(alias_hits) / 6 + len(col_word_hits) / 8)
    column = max(coverage, breadth)
    if alias_hits:
        reasons.append(f"has {', '.join(alias_hits[:4])}" + (" …" if len(alias_hits) > 4 else ""))
    if len(col_word_hits) >= 2:
        reasons.append(f"columns mention it: {', '.join(col_word_hits[:3])}")

    comment = " ".join(filter(None, [obj.get("comment") or ""] +
                              [m.get("comment") or "" for m in obj["columns"].values()]))
    comment_hit = 1.0 if {stem(w) for w in tokens(comment)} & words else 0.0
    if comment_hit:
        reasons.append("described as related in comments")

    q = usage.get(fqn.upper(), 0)
    use = min(1.0, q / 50)
    if q:
        reasons.append(f"read {q}× recently")

    score = 0.40 * name + 0.40 * column + 0.10 * comment_hit + 0.10 * use
    if not obj.get("row_count"):
        score *= 0.5
        reasons.append("empty")
    if not is_official(fqn):
        score *= 0.3
        reasons.append("looks like dev/test/backup")
    return round(score, 3), reasons


def rank(topics: list[dict[str, Any]], objects: dict[str, dict[str, Any]],
         usage: dict[str, int]) -> None:
    """Top candidates per topic, plus a recommended set that covers each need in the topic.

    When the same table exists in several schemas, the schema that covers the most
    topics wins the recommendation (keeps one coherent source); the analyst decides.
    """
    for t in topics:
        scored = []
        for fqn, obj in objects.items():
            s, why = score_object(t, fqn, obj, usage)
            if s >= MIN_SCORE:
                scored.append({"fqn": fqn, "score": s, "reasons": why, "kind": obj["kind"],
                               "_altered": obj.get("last_altered") or "",
                               "rows": obj.get("row_count"), "columns": sorted(obj["columns"])[:40],
                               "_cov": need_coverage(t, {c.lower() for c in obj["columns"]}),
                               "_named": any(r.startswith(("name mentions", "described", "columns mention")) for r in why)})
        # ties: the most recently updated copy first (a stale twin rarely is the one reported from)
        scored.sort(key=lambda c: c["_altered"], reverse=True)
        scored.sort(key=lambda c: -c["score"])
        t["_scored"] = scored
    cohesion: dict[str, int] = {}
    for t in topics:
        for c in t["_scored"][:TOP_K]:
            schema = c["fqn"].rsplit(".", 1)[0]
            cohesion[schema] = cohesion.get(schema, 0) + 1
    for t in topics:
        pool = t["_scored"]
        recommended: list[str] = []
        for key in t["_need_fields"] or {None: []}:
            def fits(c: dict[str, Any]) -> bool:
                cov = c["_cov"].get(key, 0) if key else 0
                # Every required field, or the table clearly names this need (name, comment, column
                # words). A missing field is then flagged during field matching instead of hiding the table.
                return cov >= 1.0 or (key and need_name_fit(key, c["fqn"])) or c["_named"]
            fitting = [c for c in pool if fits(c)]
            best = max(fitting, default=None, key=lambda c: (
                bool(key and need_name_fit(key, c["fqn"])),
                round(c["_cov"].get(key, 0), 1) if key else 0,
                cohesion.get(c["fqn"].rsplit(".", 1)[0], 0), round(c["score"], 1),
                objects[c["fqn"]].get("last_altered") or "", c["score"]))  # fresher table breaks ties
            table = best and best["fqn"].rsplit(".", 1)[1]
            if best and not any(r.rsplit(".", 1)[1] == table for r in recommended):
                recommended.append(best["fqn"])
            # next-best tables that also fit, in case field matching finds the first one lacks a column
            t["_fallbacks"] = [c["fqn"] for c in sorted(fitting, key=lambda c: -c["score"])
                               if c["fqn"] not in recommended
                               and c["fqn"].rsplit(".", 1)[0] == (best["fqn"].rsplit(".", 1)[0] if best else "")][:2]
        top = pool[:TOP_K] + [c for c in pool[TOP_K:] if c["fqn"] in recommended]
        names: dict[str, list[str]] = {}
        for c in pool:
            names.setdefault(c["fqn"].rsplit(".", 1)[1], []).append(c["fqn"])
        for c in top:
            c["recommended"] = c["fqn"] in recommended
            c["serves"] = sorted(k for k, v in c["_cov"].items() if v >= 0.5)
            twins = [f for f in names[c["fqn"].rsplit(".", 1)[1]] if f != c["fqn"]]
            if twins:
                c["reasons"].append("same table also in " + ", ".join(f.rsplit(".", 1)[0] for f in twins))
        t["candidates"] = [{k: v for k, v in c.items() if not k.startswith("_")} for c in top]
        t["recommended"] = recommended
        t["fallbacks"] = t.pop("_fallbacks", [])


def usage_sample(run: Runner, fqns: list[str]) -> tuple[dict[str, int], dict[str, Any]]:
    """Bounded usage signal for candidate tables only. Returns (counts, how it was measured).

    1. ACCESS_HISTORY object lists (no query text), last 7 days; 30 days if sparse.
    2. The user's own recent queries, reduced to structure (literals stripped, text discarded).
    3. Nothing readable: ranking uses names and columns only.
    """
    if not fqns:
        return {}, {"source": "none", "days": 0}
    for days in (7, 30):
        counts = access_counts(run, fqns, days)
        if counts is None:
            break  # not readable for this role; don't try the wider window
        if sum(counts.values()) >= USAGE_SPARSE or (days == 30 and counts):
            return counts, {"source": "access_history", "days": days}
    for days in (7, 30):
        try:
            import mine_usage
            texts, _src = mine_usage.fetch_history(run, sorted({f.split(".")[0] for f in fqns}), days, 2000)
            counts = {k.upper(): v for k, v in mine_usage.mine(texts, fqns).get("object_queries", {}).items()}
        except Exception:  # noqa: BLE001 - usage is a bonus signal; never block the search
            counts = {}
        if sum(counts.values()) >= USAGE_SPARSE or (days == 30 and counts):
            return counts, {"source": "query_history", "days": days}
    return {}, {"source": "none", "days": 0}


def query_counts(run: Runner, fqns: list[str]) -> dict[str, int]:
    return usage_sample(run, fqns)[0]


def access_counts(run: Runner, fqns: list[str], days: int = 7) -> dict[str, int] | None:
    """Read counts per object from ACCOUNT_USAGE.ACCESS_HISTORY (no query text). None = not readable."""
    wanted = sorted({f.upper() for f in fqns})
    if not wanted:
        return {}
    in_list = ", ".join("'" + f.replace("'", "''") + "'" for f in wanted[:500])
    try:
        rows = run(
            "select upper(f.value:\"objectName\"::string) as obj, count(*) as n "
            "from snowflake.account_usage.access_history ah, lateral flatten(ah.base_objects_accessed) f "
            f"where ah.query_start_time >= dateadd(day, -{int(days)}, current_timestamp()) "
            f"and upper(f.value:\"objectName\"::string) in ({in_list}) group by 1")
    except Exception:  # noqa: BLE001 - not every role can read ACCOUNT_USAGE
        return None
    return {r["OBJ"]: int(r["N"]) for r in rows if r.get("OBJ") in set(wanted)}


def candidate_fqns(topics: list[dict[str, Any]], objects: dict[str, dict[str, Any]]) -> list[str]:
    """Tables that match any relevant topic on names/columns alone (the usage sample targets only these)."""
    out = set()
    for t in topics:
        if t["role"] not in {"required", "optional"}:
            continue
        for fqn, obj in objects.items():
            if score_object(t, fqn, obj, {})[0] >= MIN_SCORE * 0.8:
                out.add(fqn)
    return sorted(out)


def database_summary(objects: dict[str, dict[str, Any]], topics: list[dict[str, Any]],
                     databases: list[str]) -> list[dict[str, Any]]:
    hits: dict[str, set[str]] = {}
    for t in topics:
        for c in t.get("candidates", []):
            hits.setdefault(c["fqn"].split(".")[0].upper(), set()).add(c["fqn"])
    out = []
    for db in databases:
        n = sum(1 for f in objects if f.split(".")[0].upper() == db.upper())
        out.append({"database": db, "objects": n, "hr_tables": len(hits.get(db.upper(), ())),
                    "schemas": sorted({f.split(".")[1] for f in hits.get(db.upper(), ())})})
    return sorted(out, key=lambda d: (-d["hr_tables"], d["database"]))


# ---------- workspace contract --------------------------------------------------------

def public(t: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in t.items() if not k.startswith("_")}


def visible_databases(ws: Path, exclude: list[str] | None = None) -> tuple[list[str], str | None]:
    """Every database from prewarm.json minus exclusions; capped for very large accounts."""
    names = [d["name"] for d in load(ws / "prewarm.json", {}).get("databases", [])]
    skip = {x.upper() for x in (exclude or [])} | SKIP_DATABASES
    names = [n for n in names if n.upper() not in skip and not n.upper().startswith("USER$")]
    note = None
    if len(names) > MAX_DATABASES:
        note = f"scanned the first {MAX_DATABASES} of {len(names)} databases; add others by name"
        names = names[:MAX_DATABASES]
    return names, note


def progress(ws: Path, step: str, status: str, **data: Any) -> None:
    with (ws / "progress.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"step": step, "status": status, "at": dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="seconds"), **data}) + "\n")


def scan(ws: Path, run: Runner | None, databases: list[str], skills: list[str], use_usage: bool,
         schemas: list[str] | None = None, usage_run: Runner | None = None,
         exclude: list[str] | None = None) -> dict[str, Any]:
    """run=None re-ranks from the cached metadata (adding skills later costs no queries).
    databases=[] scans every visible database (prewarm.json)."""
    catalog = load(CATALOG_DIR / "people.json")
    skills = skills or [s["name"] for s in catalog["skills"]]
    topics = topic_model(catalog, skills)
    cache = load(ws / "scan_metadata.json", {"databases": [], "objects": {}, "usage": {}, "errors": []})
    objects: dict[str, dict[str, Any]] = dict(cache["objects"])
    errors = list(cache["errors"])
    usage = dict(cache["usage"])
    note = None
    mode = "named" if databases else "all_visible"
    if not databases:
        databases, note = visible_databases(ws, exclude)
    if exclude:
        drop = {x.upper() for x in exclude}
        databases = [d for d in databases if d.upper() not in drop]
        objects = {f: o for f, o in objects.items() if f.split(".")[0].upper() not in drop}
    new_dbs = [db for db in databases if db.upper() not in {d.upper() for d in cache["databases"]}]
    if new_dbs and run is None:
        raise SystemExit("new databases need --connection")
    for db in [d for d in new_dbs if d.upper() in SKIP_DATABASES]:
        errors.append({"database": db, "error": "system or sample database; skipped"})
    todo = [d for d in new_dbs if d.upper() not in SKIP_DATABASES]
    if todo and getattr(run, "many", None):  # local CLI: every database's inventory in one round trip
        progress(ws, "inventory", "running", databases=len(todo), total=len(new_dbs))
        found, failed = list_objects_many(run, todo, schemas or [])
        objects.update(found)
        errors += [{"database": db, "error": e} for db, e in failed.items()]
    else:
        for i, db in enumerate(todo, 1):
            print(json.dumps({"event": "scan_database", "database": db}), file=sys.stderr, flush=True)
            progress(ws, "inventory", "running", database=db, done=i - 1, total=len(todo))
            try:
                objects.update(list_objects(run, db, schemas or []))
            except SqlError as exc:
                errors.append({"database": db, "error": str(exc)[:300]})
    progress(ws, "inventory", "done", total=len(new_dbs), objects=len(objects))
    usage_meta = cache.get("usage_meta") or {"source": "none", "days": 0}
    if use_usage and (new_dbs or not usage) and (usage_run or run):
        progress(ws, "usage", "running")
        cands = [f for f in candidate_fqns(topics, objects) if f.upper() not in usage]
        counts, usage_meta = usage_sample(usage_run or run, cands)
        usage.update(counts)
        progress(ws, "usage", "done", source=usage_meta["source"], days=usage_meta["days"], candidates=len(cands))
    all_dbs = sorted({*cache["databases"], *new_dbs} - {x.upper() for x in (exclude or [])}
                     - {x for x in (exclude or [])})
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "scan_metadata.json").write_text(json.dumps(
        {"databases": all_dbs, "objects": objects, "usage": usage, "usage_meta": usage_meta, "errors": errors},
        default=str) + "\n", encoding="utf-8")
    progress(ws, "rank", "running")
    rank(topics, objects, usage)
    progress(ws, "rank", "done")

    prior = {t["id"]: t for t in load(ws / "topics.json", {}).get("topics", [])}
    legacy = {k: t for t in prior.values() for k in t.get("needs", []) if t["id"] not in topics_by_id(topics)}
    for t in topics:
        old = prior.get(t["id"]) or legacy_choice(legacy.get(t["id"]), t)
        if old and old.get("status") in {"confirmed", "none"}:
            t["chosen"], t["status"] = old.get("chosen"), old["status"]
            t["auto"], t["auto_none"] = old.get("auto", False), old.get("auto_none", False)
        else:
            t["chosen"], t["status"] = None, "proposed"
    result = {"version": 1, "databases": all_dbs, "skills": skills, "mode": mode, "note": note,
              "scanned_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "objects_scanned": len(objects), "new_databases": len(new_dbs), "usage_signal": bool(usage), "usage": usage_meta,
              "errors": errors, "database_summary": database_summary(objects, topics, all_dbs),
              "topics": [public(t) for t in topics]}
    save(ws, result)
    return result


def topics_by_id(topics: list[dict[str, Any]]) -> set[str]:
    return {t["id"] for t in topics}


def legacy_choice(old: dict[str, Any] | None, t: dict[str, Any]) -> dict[str, Any] | None:
    """An analyst decision from the old per-area topics.json carries over to each of its needs;
    automatic picks are re-ranked per need."""
    if not old or old.get("auto") or old.get("auto_none") or old.get("status") not in {"confirmed", "none"}:
        return None
    return {"status": old["status"], "chosen": old.get("chosen"), "auto": False, "auto_none": False}


def save(ws: Path, data: dict[str, Any]) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "topics.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def confirm(ws: Path, topic_id: str, tables: list[str] | None) -> dict[str, Any]:
    data = load(ws / "topics.json")
    if not data:
        raise SystemExit("no topics.json yet; run scan first")
    t = next((x for x in data["topics"] if x["id"] == topic_id), None)
    if t is None:
        raise SystemExit(f"unknown topic {topic_id}")
    t["chosen"], t["status"] = (sorted(set(tables)), "confirmed") if tables else (None, "none")
    t["auto"] = t["auto_none"] = False  # decided by the analyst
    save(ws, data)
    return t


def accept_recommended(ws: Path) -> list[str]:
    """Confirm the recommended tables for every relevant topic still waiting for a decision."""
    data = load(ws / "topics.json")
    done = []
    for t in data["topics"]:
        if t["status"] != "proposed" or t["role"] not in {"required", "optional"}:
            continue
        if t.get("recommended"):
            t["chosen"], t["status"], t["auto"] = list(t["recommended"]), "confirmed", True
            done.append(t["id"])
        else:  # nothing fits: record "not found" so its needs can't bind to unrelated tables
            t["chosen"], t["status"], t["auto_none"] = None, "none", True
    save(ws, data)
    return done


def scope_tables(t: dict[str, Any]) -> list[str]:
    """Tables a need may bind to: the choice, plus fallbacks when the choice was automatic."""
    if t.get("status") != "confirmed":
        return []
    return list(t.get("chosen") or []) + (list(t.get("fallbacks") or []) if t.get("auto") else [])


def confirmed_tables(ws: Path) -> list[str]:
    data = load(ws / "topics.json", {"topics": []})
    return sorted({f for t in data["topics"] for f in scope_tables(t)})


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["scan", "confirm", "accept-recommended", "tables"])
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--connection")
    p.add_argument("--database", action="append", default=[])
    p.add_argument("--skill", action="append", default=[])
    p.add_argument("--schema", action="append", default=[], help="optional: only these schemas")
    p.add_argument("--exclude", action="append", default=[], help="databases to leave out of the scan")
    p.add_argument("--no-usage", action="store_true")
    p.add_argument("--topic")
    p.add_argument("--table", action="append", default=[])
    p.add_argument("--none", action="store_true", help="with confirm: this data does not exist")
    a = p.parse_args()
    ws = a.workspace.resolve()
    if a.action == "scan":
        if not a.database and not (ws / "prewarm.json").exists() and not (ws / "scan_metadata.json").exists():
            raise SystemExit("run prewarm.py first (lists visible databases) or pass --database")
        res = scan(ws, make_runner(a.connection) if a.connection else None, a.database, a.skill, not a.no_usage,
                   a.schema, make_runner(a.connection, USAGE_TIMEOUT_S) if a.connection else None, a.exclude)
        print(json.dumps({"objects_scanned": res["objects_scanned"], "errors": res["errors"],
                          "topics": [{"id": t["id"], "role": t["role"], "recommended": t["recommended"],
                                      "top": [c["fqn"] for c in t["candidates"]]}
                                     for t in res["topics"]]}, indent=2))
    elif a.action == "confirm":
        if not a.topic or not (a.table or a.none):
            raise SystemExit("confirm needs --topic and --table (or --none)")
        print(json.dumps(confirm(ws, a.topic, None if a.none else a.table), indent=2))
    elif a.action == "accept-recommended":
        print(json.dumps({"confirmed": accept_recommended(ws)}))
    else:
        print("\n".join(confirmed_tables(ws)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
