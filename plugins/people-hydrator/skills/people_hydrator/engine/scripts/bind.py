#!/usr/bin/env python3
"""Bind stage: map the chosen skills to the customer's data, no questions asked.

  bind.py --workspace W --connection C [--database DB ...] [--skill S ...]
          [--context-docs DIR --context-connector local_files] [--rescan]

Steps (progress -> W/progress.jsonl, the same file discovery uses):
  1 topics   search the named databases per topic (skipped if topics.json is current)
  2 tables   take the recommended table(s) for every topic still undecided
  3 profile  profile only the chosen tables (tables profiled before are reused)
  4 map      usage, context graph, field matching, value mappings, definitions, readiness
  5 profile  rebuild the Customer Context Profile (profile.json); report what was
             already known vs new for the chosen skills (bind_delta.json)

On a repeat run with an existing profile, bound tables get a cheap freshness check
first (columns/types); changed tables are marked stale instead of re-scanning all.

Every choice made here is a proposal; the Tune stage shows each with its evidence.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import discover_topics  # noqa: E402
import profile_store  # noqa: E402
from discover import log  # noqa: E402


def bound_needs(ws: Path) -> set[str]:
    b = discover_topics.load(ws / "bindings.json", {}) or {}
    return {k for k, v in b.get("needs", {}).items() if v.get("status") != "unresolved" and v.get("fqn")}


def delta(ws: Path, skills: list[str], before: set[str]) -> dict[str, object]:
    """What the chosen skills reuse from earlier runs vs what was found now vs still missing."""
    cat = discover_topics.load(discover_topics.CATALOG_DIR / "people.json")
    after = bound_needs(ws)
    out = {}
    for s in cat["skills"]:
        if s["name"] not in skills:
            continue
        keys = [n["key"] for n in s["data_needs"] if n.get("required")]
        out[s["name"]] = {"reused": sorted(k for k in keys if k in before),
                          "new": sorted(k for k in keys if k in after and k not in before),
                          "missing": sorted(k for k in keys if k not in after)}
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--connection", required=True)
    p.add_argument("--database", action="append", default=[])
    p.add_argument("--skill", action="append", default=[])
    p.add_argument("--context-docs", type=Path)
    p.add_argument("--context-connector", default="local_files")
    p.add_argument("--rescan", action="store_true", help="search the databases again even if already scanned")
    p.add_argument("--skip-numbers", action="store_true", help=argparse.SUPPRESS)  # no-op, kept for old commands
    a = p.parse_args()
    ws = a.workspace.resolve()
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "progress.jsonl").write_text("", encoding="utf-8")
    started = time.time()
    before = bound_needs(ws)
    if (ws / "profile.json").exists() and not a.rescan:
        log(ws, "freshness", "started")
        try:
            fresh = profile_store.refresh(ws, a.connection)
            log(ws, "freshness", "done", checked=len(fresh["checked"]), notes=fresh["notes"])
        except Exception as exc:  # noqa: BLE001 - freshness is advisory; never block binding
            log(ws, "freshness", "failed", error=str(exc)[:200])

    topics = discover_topics.load(ws / "topics.json", {})
    if a.skill:  # adding skills keeps the ones bound earlier (their choices and tables are reused)
        requested = list(a.skill)
        a.skill = sorted(set(topics.get("skills", [])) | set(requested))
    else:
        requested = []
    need_scan = a.rescan or not topics or sorted(topics.get("skills", [])) != sorted(a.skill or topics.get("skills", [])) \
        or any(d.upper() not in {x.upper() for x in topics.get("databases", [])} for d in a.database)
    if need_scan:
        if a.rescan:
            (ws / "scan_metadata.json").unlink(missing_ok=True)
        log(ws, "topics", "started")
        from _sql import make_runner
        started = time.time()
        res = discover_topics.scan(ws, make_runner(a.connection), a.database, a.skill, True,
                                   usage_run=make_runner(a.connection, discover_topics.USAGE_TIMEOUT_S))
        log(ws, "topics", "done", objects=res["objects_scanned"], seconds=round(time.time() - started, 1),
            reused=not res.get("new_databases"))
    chosen = discover_topics.accept_recommended(ws)
    log(ws, "tables", "done", confirmed_topics=chosen, tables=discover_topics.confirmed_tables(ws))
    if not discover_topics.confirmed_tables(ws):
        log(ws, "bind", "failed", error="no tables found for the chosen skills' topics")
        raise SystemExit("no candidate tables found; check the database names or pick other skills")

    cmd = [sys.executable, str(SCRIPTS / "discover.py"), "--workspace", str(ws), "--connection", a.connection,
           "--from-topics", "--keep-progress"]
    if a.context_docs:
        cmd += ["--context-docs", str(a.context_docs), "--context-connector", a.context_connector]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-1500:])
        raise SystemExit("mapping failed")
    try:  # a field that is constant on the rows a need reads (after a row filter) carries nothing
        import filtered_fields
        from _sql import make_runner
        bpath = ws / "bindings.json"
        bindings = json.loads(bpath.read_text(encoding="utf-8"))
        notes = filtered_fields.check(bindings, json.loads(filtered_fields.CATALOG.read_text(encoding="utf-8")),
                                      make_runner(a.connection, 120))
        bindings["layout_notes"] = notes
        bpath.write_text(json.dumps(bindings, indent=2) + "\n", encoding="utf-8")
        log(ws, "row_check", "done", dropped=sum(len(n.get("dropped", [])) for n in notes),
            constant=sum(len(n.get("constant", [])) for n in notes))
    except Exception as exc:  # noqa: BLE001 - advisory; never fail Bind on it
        log(ws, "row_check", "failed", error=str(exc)[:200])
    skills_now = a.skill or discover_topics.load(ws / "topics.json", {}).get("skills", [])
    log(ws, "definitions", "started")
    t0 = time.time()
    try:  # the fields each business definition needs, searched across every scanned table
        import definitions
        from _sql import make_runner
        found = definitions.decide(ws, skills_now, make_runner(a.connection, 120))
        summary = [definition_line(x) for x in found]
        profile_store.save(ws / "definition_inputs.json", {"definitions": found, "summary": summary})
        log(ws, "definitions", "done", seconds=round(time.time() - t0, 1),
            found=sum(1 for x in found if x["options"]), to_ask=sum(1 for x in found if x.get("locate")))
    except Exception as exc:  # noqa: BLE001 - Tune recomputes this; never fail Bind on it
        log(ws, "definitions", "failed", error=str(exc)[:200])
    prof = profile_store.build(ws)
    d = delta(ws, requested or a.skill or discover_topics.load(ws / "topics.json", {}).get("skills", []), before)
    profile_store.save(ws / "bind_delta.json", {"profile_version": prof["version"], "skills": d})
    log(ws, "profile", "done", version=prof["version"])
    import mapping_review
    mapping = mapping_review.review(ws, skills_now)
    log(ws, "mapping", "done", needs_input={s: v["needs_input"] for s, v in mapping["skills"].items() if v["needs_input"]})
    import eval_gate  # grade each skill's connection against the rules in its eval plan
    gate = eval_gate.run(ws, skills_now)
    log(ws, "gate", "done", grades={s: gate["skills"][s]["grade"] for s in skills_now})
    log(ws, "bind", "complete", seconds=round(time.time() - started, 1))
    print(json.dumps({"tables": discover_topics.confirmed_tables(ws), "seconds": round(time.time() - started, 1),
                      "mapping": "mapping_review.json", "binding_gate": "binding_gate.json"}))
    return 0


def definition_line(x: dict) -> str:
    """One plain line per definition for the Bind report."""
    import definitions
    label = definitions.label_of(definitions.by_id(x["id"]) or {"prompt": x["id"]})
    if x["options"]:
        best = x["options"][0]
        return f"{label}: found ({best['label']}{', ' + best['effect'] if best.get('effect') else ''})" + \
            (f" and {len(x['options']) - 1} other way(s)" if len(x["options"]) > 1 else "")
    if x.get("locate"):
        off = x["locate"]["found_but_off"]
        return f"{label}: " + (f"found {off[0]['label']} but it looks off ({off[0]['warning']}); we'll ask you"
                               if off else "not found in your tables; we'll ask you where it is")
    return f"{label}: {x['cannot_compute']}"


if __name__ == "__main__":
    raise SystemExit(main())
