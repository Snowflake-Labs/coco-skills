"""Path M step M7: retire the legacy ontology layer after a verified migration.

Plan mode (default) prints what would be archived and dropped and why each kept
object is kept. Nothing changes. --execute runs it.

  1. Archive: CREATE SCHEMA {SC}_ONT_ARCHIVE CLONE {SC} (zero-copy, instant rollback).
  2. Candidates: the ontology layer BON replaces -- ONT_* metadata tables (not the
     migration map), REL_EDGE_INFERRED / ONT_CONSTRAINT_VIOLATION, VW_ONT_* and
     REL_RESOLVED views, graph-tool UDFs, inference / constraint procedures, and the
     ONTOLOGY / METADATA semantic views.
  3. Protection -- a candidate is kept if anything that stays depends on it:
       - a BON representation points at it (from the map table),
       - the DDL of a kept view / semantic view / function / procedure, or a task in the schema, names it
         (closed transitively; works on fresh clones where lineage is empty),
       - GET_LINEAGE DOWNSTREAM reaches an object outside the candidate set,
       - an agent spec still calls it (blocks --execute unless --ack-agent is given).
  4. Drop (semantic views, views, functions, procedures, tables) then verify every
     remaining view still compiles and every remaining semantic view still describes.

Usage:
  python scripts/bon_retire.py -c CONN --stack DB.SC [--target DB.SC] [--keep NAME ...] [--execute] [--ack-agent DB.SC.AGENT ...]
--stack is where the map table lives; --target defaults to it (use a clone to rehearse).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bon_migration_common import check_identifier, connect  # noqa: E402

KEEP_TABLES = {"ONT_BON_MIGRATION_MAP"}
TABLE_RE = re.compile(r"^(ONT_|REL_EDGE_INFERRED$)")
VIEW_RE = re.compile(r"^(VW_ONT_|REL_RESOLVED$)")
FUNC_RE = re.compile(r"_TOOL$|^(GET_|EXPAND_|FIND_)")
PROC_RE = re.compile(r"^SP_(INFER|CHECK|RUN_ONTOLOGY)")
SV_RE = re.compile(r"_(ONTOLOGY|METADATA)_MODEL$")
DROP_ORDER = ("SEMANTIC VIEW", "VIEW", "FUNCTION", "PROCEDURE", "TABLE")


def rows(cur, sql, params=None):
    cur.execute(sql, params)
    cols = [c[0].upper() for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def inventory(cur, db, sc):
    objs = {}
    for r in rows(cur, f"SELECT TABLE_NAME, TABLE_TYPE FROM {db}.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = %s", (sc,)):
        objs[r["TABLE_NAME"]] = {"kind": "VIEW" if r["TABLE_TYPE"] == "VIEW" else "TABLE"}
    for r in rows(cur, f"SELECT FUNCTION_NAME N, ARGUMENT_SIGNATURE S FROM {db}.INFORMATION_SCHEMA.FUNCTIONS WHERE FUNCTION_SCHEMA = %s", (sc,)):
        objs[r["N"]] = {"kind": "FUNCTION", "sig": r["S"]}
    for r in rows(cur, f"SELECT PROCEDURE_NAME N, ARGUMENT_SIGNATURE S FROM {db}.INFORMATION_SCHEMA.PROCEDURES WHERE PROCEDURE_SCHEMA = %s", (sc,)):
        objs[r["N"]] = {"kind": "PROCEDURE", "sig": r["S"]}
    for r in rows(cur, f"SHOW SEMANTIC VIEWS IN SCHEMA {db}.{sc}"):
        objs[r["NAME"]] = {"kind": "SEMANTIC VIEW"}
    return objs


def arg_types(sig: str) -> str:
    """'(A VARCHAR, B NUMBER)' -> 'VARCHAR, NUMBER' for DROP FUNCTION / GET_DDL."""
    inner = sig.strip()[1:-1].strip()
    return ", ".join(p.strip().split()[-1] for p in inner.split(",")) if inner else ""


def ref(db, sc, name, o):
    base = f"{db}.{sc}.{name}"
    return f"{base}({arg_types(o['sig'])})" if o["kind"] in ("FUNCTION", "PROCEDURE") else base


def is_candidate(name, o):
    k = o["kind"]
    if k == "TABLE":
        return bool(TABLE_RE.match(name)) and name not in KEEP_TABLES
    if k == "VIEW":
        return bool(VIEW_RE.match(name))
    if k == "FUNCTION":
        return bool(FUNC_RE.search(name))
    if k == "PROCEDURE":
        return bool(PROC_RE.match(name))
    return k == "SEMANTIC VIEW" and bool(SV_RE.search(name))


def get_ddl(cur, db, sc, name, o):
    kind = {"SEMANTIC VIEW": "SEMANTIC_VIEW"}.get(o["kind"], o["kind"])
    try:
        return rows(cur, f"SELECT GET_DDL('{kind}', %s) D", (ref(db, sc, name, o),))[0]["D"] or ""
    except Exception as e:  # noqa: BLE001
        return f"-- GET_DDL failed: {e}"


def mentions(text: str, name: str) -> bool:
    return re.search(rf"(?<![A-Z0-9_$]){re.escape(name)}(?![A-Z0-9_$])", text.upper()) is not None


def plan(cur, stack, target, user_keep=()):
    sdb, ssc = stack
    db, sc = target
    objs = inventory(cur, db, sc)
    cands = {n for n, o in objs.items() if is_candidate(n, o)}
    keep = {}
    # BON representations (bare names, so a rehearsal clone is protected the same way)
    try:
        for r in rows(cur, f"SELECT BON_DETAIL FROM {sdb}.{ssc}.ONT_BON_MIGRATION_MAP "
                           "WHERE STATUS = 'MIGRATED' AND SOURCE_KIND IN ('BINDING','PROPERTY','METRIC_SV')"):
            detail = r["BON_DETAIL"]
            detail = json.loads(detail) if isinstance(detail, str) else (detail or {})
            fqn = (detail.get("assetRef") or detail).get("fqn", "")
            n = fqn.upper().split(".")[-1] if fqn else ""
            if n in cands:
                keep[n] = "BON representation points at it"
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"cannot read {sdb}.{ssc}.ONT_BON_MIGRATION_MAP ({e}); run M5 reconcile first")
    # DDL dependencies of kept objects (incl. tasks, which load the KG from ONT_* maps), closed transitively
    ddl = {n: get_ddl(cur, db, sc, n, o) for n, o in objs.items() if o["kind"] != "TABLE"}
    for t in rows(cur, f"SHOW TASKS IN SCHEMA {db}.{sc}"):
        ddl[f"TASK {t['NAME']}"] = str(t.get("DEFINITION") or "")
    for n in user_keep:
        if n in cands:
            keep[n] = "kept on request (--keep)"
    changed = True
    while changed:
        changed = False
        kept_now = (set(objs) - cands) | set(keep) | {k for k in ddl if k.startswith("TASK ")}
        for k in kept_now:
            text = ddl.get(k, "")
            for c in cands - set(keep):
                if c != k and mentions(text, c):
                    keep[c] = f"referenced by kept object {k}"
                    changed = True
    # Lineage leaving the candidate set
    lineage_checked = 0
    for c in sorted(cands - set(keep)):
        dom = {"TABLE": "TABLE", "VIEW": "TABLE", "SEMANTIC VIEW": "SEMANTIC_VIEW"}.get(objs[c]["kind"])
        if not dom:
            continue
        try:
            out = rows(cur, "SELECT DISTINCT TARGET_OBJECT_DATABASE D, TARGET_OBJECT_SCHEMA S, TARGET_OBJECT_NAME N, TARGET_OBJECT_DOMAIN T "
                            "FROM TABLE(SNOWFLAKE.CORE.GET_LINEAGE(%s, %s, 'DOWNSTREAM', 2)) WHERE TARGET_STATUS = 'ACTIVE'",
                       (f"{db}.{sc}.{c}", dom))
            lineage_checked += 1
        except Exception:  # noqa: BLE001
            continue
        for t in out:
            inside = (t["D"], t["S"]) == (db, sc) and t["N"] in cands
            if not inside and t["T"] != "COLUMN":
                keep[c] = f"lineage: feeds {t['D']}.{t['S']}.{t['N']}"
                break
    # Agents that still call a candidate
    agents = []
    for a in rows(cur, "SHOW AGENTS IN ACCOUNT"):
        fqn = '.'.join('"' + str(a[k]).replace('"', '""') + '"' for k in ("DATABASE_NAME", "SCHEMA_NAME", "NAME"))
        try:
            spec = str(rows(cur, f"DESCRIBE AGENT {fqn}")[0].get("AGENT_SPEC") or "").upper()
        except Exception as e:  # noqa: BLE001
            agents.append({"agent": fqn, "uses": [], "error": f"could not describe: {str(e).splitlines()[0][:120]}"})
            continue
        if f"{sdb}.{ssc}." not in spec and f"{db}.{sc}." not in spec:
            continue
        hits = sorted(c for c in cands - set(keep) if mentions(spec, c))
        if hits:
            agents.append({"agent": fqn, "uses": hits})
    drop = sorted(cands - set(keep), key=lambda n: (DROP_ORDER.index(objs[n]["kind"]), n))
    return {"target": f"{db}.{sc}", "archive": f"{db}.{sc}_ONT_ARCHIVE", "drop": [(objs[n]["kind"], n) for n in drop],
            "keep_candidates": keep, "agents_using_dropped": agents, "lineage_checked": lineage_checked,
            "_objs": objs}


def verify(cur, db, sc):
    """Every remaining view compiles, every remaining semantic view describes."""
    broken = []
    for n, o in inventory(cur, db, sc).items():
        try:
            if o["kind"] == "VIEW":
                cur.execute(f"SELECT * FROM {db}.{sc}.{n} LIMIT 0")
            elif o["kind"] == "SEMANTIC VIEW":
                cur.execute(f"DESCRIBE SEMANTIC VIEW {db}.{sc}.{n}")
        except Exception as e:  # noqa: BLE001
            broken.append(f"{o['kind']} {n}: {str(e).splitlines()[0][:160]}")
    return broken


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--connection", "-c", required=True)
    ap.add_argument("--stack", required=True, help="DB.SCHEMA of the migrated stack (holds ONT_BON_MIGRATION_MAP)")
    ap.add_argument("--target", help="DB.SCHEMA to retire in (default: --stack; pass a clone to rehearse)")
    ap.add_argument("--execute", action="store_true", help="archive and drop; without it nothing changes")
    ap.add_argument("--ack-agent", action="append", default=[], help="agent FQN you accept will lose the dropped tools")
    ap.add_argument("--no-archive", action="store_true", help="skip the archive clone (only for rehearsal clones)")
    ap.add_argument("--keep", action="append", default=[], help="object name to keep even if it is a candidate (e.g. graph UDFs an agent still calls)")
    args = ap.parse_args(argv)
    stack = tuple(check_identifier(p, "stack") for p in args.stack.upper().split("."))
    target = tuple(check_identifier(p, "target") for p in (args.target or args.stack).upper().split("."))
    db, sc = target
    with connect(args.connection) as conn:
        cur = conn.cursor()
        p = plan(cur, stack, target, {k.upper() for k in args.keep})
        objs = p.pop("_objs")
        out = {**p, "executed": False}
        acked = {x.replace('"', "").upper() for x in args.ack_agent}
        unacked = [a for a in p["agents_using_dropped"]
                   if a["uses"] and a["agent"].replace('"', "").upper() not in acked]
        if args.execute:
            if unacked:
                out["error"] = "agents still call objects that would be dropped; update them or pass --ack-agent"
                out["unacked_agents"] = unacked
                print(json.dumps(out, indent=2))
                return 2
            if not args.no_archive:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {db}.{sc}_ONT_ARCHIVE CLONE {db}.{sc}")
            dropped, failed = [], []
            for kind, n in p["drop"]:
                stmt = f"DROP {kind} IF EXISTS {ref(db, sc, n, objs[n])}"
                try:
                    cur.execute(stmt)
                    dropped.append(stmt)
                except Exception as e:  # noqa: BLE001
                    failed.append(f"{stmt}: {str(e).splitlines()[0]}")
            out.update(executed=True, dropped=len(dropped), drop_failed=failed, broken_after=verify(cur, db, sc))
        else:
            out["sql"] = ([] if args.no_archive else [f"CREATE SCHEMA IF NOT EXISTS {db}.{sc}_ONT_ARCHIVE CLONE {db}.{sc};"]) + \
                         [f"DROP {k} IF EXISTS {ref(db, sc, n, objs[n])};" for k, n in p["drop"]]
            out["rollback"] = f"DROP SCHEMA {db}.{sc}; ALTER SCHEMA {db}.{sc}_ONT_ARCHIVE RENAME TO {db}.{sc};"
        print(json.dumps(out, indent=2, default=str))
        return 1 if out.get("broken_after") or out.get("drop_failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
