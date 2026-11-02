#!/usr/bin/env python3
"""Discover-once data profiler: writes data_index.json. Stdlib only.

Signals (aggregate/metadata only, no person-level reads):
  * structural profile per column: dtype, comment, null rate, distinct count,
    min/max for dates and numbers (never for text)
  * code-value profile: distinct values + counts for LOW-cardinality code
    columns that are not identifiers, names, or free text
  * grain detection: uniqueness of candidate key combinations
  * cadence: snapshot-date distribution and month-end share
  * join inference: key-overlap ratios + max multiplicity per side
  * existing semantic views (dimensions / metrics / base tables)

Usage:
  profile_data.py --connection C --database HR_DB \
      [--schema PUBLIC ...] --out <workspace>/data_index.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sql import Runner, SqlError, make_runner, quote_fqn, quote_ident, quote_literal, run_many  # noqa: E402

CODE_CARDINALITY_CAP = 40
NON_OFFICIAL = re.compile(r"(^|_)(dev|demo|test|tmp|temp|sandbox|backup|bkp|clone|scratch|old)(_|$|\d)", re.I)
PII_NAME = re.compile(
    r"(name|email|phone|address|ssn|birth|dob|salary|comment|feedback|note|text|description|answer|content)",
    re.I,
)
ID_LIKE = re.compile(r"(^id$|_id$|_key$|^key$|_uuid$|_number$|_no$)", re.I)
DATE_TYPES = {"DATE", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ", "TIMESTAMP"}
NUM_TYPES = {"NUMBER", "FLOAT", "DECIMAL", "INTEGER", "FIXED", "REAL", "DOUBLE"}


def progress(event: str, **detail: Any) -> None:
    """Emit a one-line JSON progress event on stderr (the wizard tails it)."""
    print(json.dumps({"event": event, **detail}), file=sys.stderr, flush=True)


def is_official(fqn: str) -> bool:
    return not any(NON_OFFICIAL.search(part) for part in fqn.split("."))


def classify_column(name: str, data_type: str) -> dict[str, bool]:
    dtype = data_type.upper()
    return {
        "is_id": bool(ID_LIKE.search(name)),
        "is_date": dtype in DATE_TYPES,
        "is_number": dtype in NUM_TYPES,
        "is_text": dtype in {"TEXT", "VARCHAR", "STRING", "CHAR"},
        "is_boolean": dtype == "BOOLEAN",
        "pii_risk": bool(PII_NAME.search(name)),
    }


def list_objects_sql(database: str, schemas: list[str]) -> tuple[str, str]:
    db = quote_ident(database)
    schema_filter = ""
    if schemas:
        schema_filter = "and table_schema in (" + ",".join(quote_literal(s) for s in schemas) + ")"
    return (f"select table_schema, table_name, table_type, row_count, comment, last_altered "
            f"from {db}.information_schema.tables "
            f"where table_schema <> 'INFORMATION_SCHEMA' {schema_filter}",
            f"select table_schema, table_name, column_name, data_type, comment, ordinal_position "
            f"from {db}.information_schema.columns "
            f"where table_schema <> 'INFORMATION_SCHEMA' {schema_filter} "
            f"order by table_schema, table_name, ordinal_position")


def objects_from(database: str, tables: list[dict[str, Any]], columns: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    objects: dict[str, dict[str, Any]] = {}
    for t in tables:
        fqn = f"{database}.{t['TABLE_SCHEMA']}.{t['TABLE_NAME']}"
        objects[fqn] = {
            "kind": "view" if "VIEW" in str(t["TABLE_TYPE"]) else "table",
            "row_count": t.get("ROW_COUNT"),
            "comment": t.get("COMMENT"),
            "last_altered": str(t.get("LAST_ALTERED") or ""),
            "official": is_official(fqn),
            "columns": {},
        }
    for c in columns:
        fqn = f"{database}.{c['TABLE_SCHEMA']}.{c['TABLE_NAME']}"
        if fqn not in objects:
            continue
        name = c["COLUMN_NAME"]
        objects[fqn]["columns"][name] = {
            "data_type": c["DATA_TYPE"],
            "comment": c.get("COMMENT"),
            **classify_column(name, c["DATA_TYPE"]),
        }
    return objects


def list_objects(run: Runner, database: str, schemas: list[str]) -> dict[str, dict[str, Any]]:
    tables_sql, columns_sql_ = list_objects_sql(database, schemas)
    return objects_from(database, run(tables_sql), run(columns_sql_))


def list_objects_many(run: Runner, databases: list[str], schemas: list[str]) -> tuple[dict[str, Any], dict[str, str]]:
    """Inventory of several databases in one round trip. Returns (objects, {database: error})."""
    sqls = [s for db in databases for s in list_objects_sql(db, schemas)]
    res = run_many(run, sqls)
    objects: dict[str, Any] = {}
    failed: dict[str, str] = {}
    for i, db in enumerate(databases):
        tables, cols = res[2 * i], res[2 * i + 1]
        err = next((r for r in (tables, cols) if isinstance(r, SqlError)), None)
        if err:
            failed[db] = str(err)[:300]
        else:
            objects.update(objects_from(db, tables, cols))
    return objects, failed


def columns_sql(fqn: str, obj: dict[str, Any]) -> str | None:
    cols = obj["columns"]
    if not cols:
        return None
    selects = ["count(*) as \"__ROWS\""]
    for i, (name, meta) in enumerate(cols.items()):
        q = quote_ident(name)
        selects.append(f"count({q}) as \"C{i}_NN\"")
        selects.append(f"approx_count_distinct({q}) as \"C{i}_ND\"")
        if meta["is_date"] or meta["is_number"]:
            selects.append(f"min({q})::string as \"C{i}_MIN\"")
            selects.append(f"max({q})::string as \"C{i}_MAX\"")
    return f"select {', '.join(selects)} from {quote_fqn(fqn)}"


def apply_columns(obj: dict[str, Any], row: dict[str, Any]) -> None:
    cols = obj["columns"]
    total = int(row["__ROWS"] or 0)
    obj["row_count"] = total
    for i, (name, meta) in enumerate(cols.items()):
        nn = int(row[f"C{i}_NN"] or 0)
        meta["non_null"] = nn
        meta["null_rate"] = round(1 - nn / total, 4) if total else None
        meta["distinct"] = int(row[f"C{i}_ND"] or 0)
        if meta["is_date"] or meta["is_number"]:
            meta["min"], meta["max"] = row.get(f"C{i}_MIN"), row.get(f"C{i}_MAX")
        meta["is_code"] = (
            (meta["is_text"] or meta["is_boolean"])
            and 0 < meta["distinct"] <= CODE_CARDINALITY_CAP
            and not meta["is_id"]
            and not meta["pii_risk"]
        )


def profile_columns(run: Runner, fqn: str, obj: dict[str, Any]) -> None:
    """One aggregate query per table: non-null count, approx distinct, min/max."""
    sql = columns_sql(fqn, obj)
    if sql:
        apply_columns(obj, run(sql)[0])


def code_values_sql(fqn: str, obj: dict[str, Any]) -> str | None:
    code_cols = [n for n, m in obj["columns"].items() if m.get("is_code")]
    if not code_cols:
        return None
    return " union all ".join(
        f"select {quote_literal(n)} as col, {quote_ident(n)}::string as v, count(*) as n "
        f"from {quote_fqn(fqn)} group by 1, 2"
        for n in code_cols
    )


def apply_code_values(obj: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    code_cols = [n for n, m in obj["columns"].items() if m.get("is_code")]
    values: dict[str, list[list[Any]]] = {n: [] for n in code_cols}
    for r in rows:
        values[r["COL"]].append([r["V"], int(r["N"])])
    for name, vals in values.items():
        vals.sort(key=lambda v: (-v[1], str(v[0])))  # ties in a fixed order, so every run agrees
        obj["columns"][name]["top_values"] = vals[:CODE_CARDINALITY_CAP]


def profile_code_values(run: Runner, fqn: str, obj: dict[str, Any]) -> None:
    sql = code_values_sql(fqn, obj)
    if sql:
        apply_code_values(obj, run(sql))


def grain_combos(obj: dict[str, Any]) -> list[tuple[str, ...]]:
    cols = obj["columns"]
    ids = [n for n, m in cols.items() if m["is_id"]][:4]
    dates = [n for n, m in cols.items() if m["is_date"]][:3]
    combos: list[tuple[str, ...]] = [(i,) for i in ids]
    combos += [(i, d) for i in ids[:2] for d in dates]
    combos += list(itertools.combinations(ids[:3], 2))
    return combos[:8]


def grain_sql(fqn: str, obj: dict[str, Any]) -> str | None:
    combos = grain_combos(obj)
    if not combos:
        return None
    selects = ["count(*) as \"__ROWS\""]
    for i, combo in enumerate(combos):
        selects.append(f"count(distinct {', '.join(quote_ident(c) for c in combo)}) as \"G{i}\"")
    return f"select {', '.join(selects)} from {quote_fqn(fqn)}"


def apply_grain(obj: dict[str, Any], row: dict[str, Any] | None) -> None:
    combos = grain_combos(obj)
    if not combos or row is None:
        obj["grain_candidates"] = []
        return
    total = int(row["__ROWS"] or 0)
    obj["grain_candidates"] = [
        {"columns": list(combo), "distinct": int(row[f"G{i}"] or 0),
         "unique": total > 0 and int(row[f"G{i}"] or 0) == total}
        for i, combo in enumerate(combos)
    ]


def detect_grain(run: Runner, fqn: str, obj: dict[str, Any]) -> None:
    sql = grain_sql(fqn, obj)
    apply_grain(obj, run(sql)[0] if sql else None)


def cadence_sql(fqn: str, name: str) -> str:
    q = quote_ident(name)
    return (f"select count(distinct {q}) as nd, "
            f"count_if({q} = last_day({q})) / nullif(count({q}), 0) as month_end_share, "
            f"max({q})::string as latest, "
            f"datediff('day', max({q}), current_date()) as days_since_latest "
            f"from {quote_fqn(fqn)}")


def cadence_entry(row: dict[str, Any]) -> dict[str, Any]:
    return {"distinct_dates": int(row["ND"] or 0),
            "month_end_share": round(float(row["MONTH_END_SHARE"] or 0), 3),
            "latest": row["LATEST"], "days_since_latest": row["DAYS_SINCE_LATEST"]}


def detect_cadence(run: Runner, fqn: str, obj: dict[str, Any]) -> None:
    cadence: dict[str, Any] = {}
    for name, meta in obj["columns"].items():
        if not meta["is_date"] or not meta.get("distinct"):
            continue
        cadence[name] = cadence_entry(run(cadence_sql(fqn, name))[0])
    obj["cadence"] = cadence


def profile_many(run: Runner, items: list[tuple[str, dict[str, Any]]]) -> dict[str, str]:
    """Profile many tables in three round trips; same SQL and same results as the per-table
    path. Returns {fqn: error} for tables that failed (the per-table path's `gaps`).

    Round 1: column stats, grain and every date column's cadence (none depend on each other).
    Round 2: code values (needs round 1 to know which columns are codes).
    """
    failed: dict[str, str] = {}
    plan: list[tuple[str, str, str | None]] = []  # (fqn, step, column)
    sqls: list[str] = []
    for fqn, obj in items:
        for step, sql, col in ([("columns", columns_sql(fqn, obj), None), ("grain", grain_sql(fqn, obj), None)]
                               + [("cadence", cadence_sql(fqn, n), n) for n, m in obj["columns"].items() if m["is_date"]]):
            if sql:
                plan.append((fqn, step, col))
                sqls.append(sql)
    res = dict(zip(plan, run_many(run, sqls)))
    second: list[tuple[str, dict[str, Any], str]] = []
    for fqn, obj in items:
        def got(step: str, col: str | None = None) -> Any:
            return res.get((fqn, step, col))
        r = got("columns")
        if isinstance(r, SqlError):
            failed[fqn] = str(r)[:300]
            continue
        if r is not None:
            apply_columns(obj, r[0])
        sql = code_values_sql(fqn, obj)
        if sql:
            second.append((fqn, obj, sql))
    for (fqn, obj, _), r in zip(second, run_many(run, [s for _, _, s in second])):
        if isinstance(r, SqlError):
            failed[fqn] = str(r)[:300]
        else:
            apply_code_values(obj, r)
    for fqn, obj in items:  # same order of steps as the per-table path, so a failure leaves the same state
        if fqn in failed:
            continue
        g = res.get((fqn, "grain", None))
        if isinstance(g, SqlError):
            failed[fqn] = str(g)[:300]
            continue
        apply_grain(obj, g[0] if g else None)
        cadence: dict[str, Any] = {}
        for name, meta in obj["columns"].items():
            if not meta["is_date"] or not meta.get("distinct"):
                continue
            c = res.get((fqn, "cadence", name))
            if isinstance(c, SqlError):
                failed[fqn] = str(c)[:300]
                break
            cadence[name] = cadence_entry(c[0])
        else:
            obj["cadence"] = cadence
    return failed


def normalize_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def join_pairs(objects: dict[str, dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    id_cols = [
        (fqn, name)
        for fqn, obj in objects.items() if obj["official"]
        for name, meta in obj["columns"].items() if meta["is_id"]
    ]
    return [(lf, lc, rf, rc) for (lf, lc), (rf, rc) in itertools.combinations(id_cols, 2)
            if lf != rf and normalize_key(lc) == normalize_key(rc)]


def join_sql(lf: str, lc: str, rf: str, rc: str) -> str:
    lq, rq = quote_ident(lc), quote_ident(rc)
    return (f"with l as (select {lq} k, count(*) c from {quote_fqn(lf)} where {lq} is not null group by 1), "
            f"r as (select {rq} k, count(*) c from {quote_fqn(rf)} where {rq} is not null group by 1) "
            f"select (select count(*) from l) as nl, (select count(*) from r) as nr, "
            f"(select count(*) from l join r using (k)) as nboth, "
            f"(select max(c) from l) as maxl, (select max(c) from r) as maxr")


def join_entry(lf: str, lc: str, rf: str, rc: str, row: dict[str, Any]) -> dict[str, Any] | None:
    nl, nr, nboth = int(row["NL"] or 0), int(row["NR"] or 0), int(row["NBOTH"] or 0)
    if not nboth:
        return None
    maxl, maxr = int(row["MAXL"] or 0), int(row["MAXR"] or 0)
    return {"left": f"{lf}.{lc}", "right": f"{rf}.{rc}",
            "overlap_left": round(nboth / nl, 3) if nl else 0,
            "overlap_right": round(nboth / nr, 3) if nr else 0,
            "cardinality": f"{'N' if maxl > 1 else '1'}:{'N' if maxr > 1 else '1'}"}


def infer_joins(run: Runner, objects: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Overlap + multiplicity for id-like columns with matching names across tables."""
    pairs = join_pairs(objects)
    if getattr(run, "many", None):
        rows = run_many(run, [join_sql(*p) for p in pairs])
        for r in rows:  # same behaviour as one-by-one: a failing join query is an error
            if isinstance(r, SqlError):
                raise r
    else:
        rows = [run(join_sql(*p)) for p in pairs]
    return [j for p, r in zip(pairs, rows) if (j := join_entry(*p, r[0])) is not None]


def list_semantic_views(run: Runner, database: str) -> list[dict[str, Any]]:
    try:
        views = run(f"show semantic views in database {quote_ident(database)}")
    except SqlError:
        return []
    out = []
    for v in views:
        fqn = f"{v.get('DATABASE_NAME')}.{v.get('SCHEMA_NAME')}.{v.get('NAME')}"
        if not is_official(fqn):
            continue
        entry: dict[str, Any] = {"fqn": fqn, "comment": v.get("COMMENT"), "dimensions": [],
                                 "metrics": [], "base_tables": []}
        try:
            for r in run(f"describe semantic view {quote_fqn(fqn)}"):
                kind, name = str(r.get("OBJECT_KIND", "")).upper(), r.get("OBJECT_NAME")
                if kind == "DIMENSION":
                    entry["dimensions"].append(name)
                elif kind == "METRIC":
                    entry["metrics"].append(name)
                elif kind == "TABLE" and r.get("PROPERTY") == "BASE_TABLE_NAME":
                    entry["base_tables"].append(r.get("PROPERTY_VALUE"))
        except SqlError:
            pass
        out.append(entry)
    return out


def build_index(run: Runner, connection: str, databases: list[str],
                schemas: list[str], tables: list[str] | None = None,
                prior: dict[str, Any] | None = None) -> dict[str, Any]:
    """Profile a scope. With ``tables`` only those objects are profiled; with ``prior``
    objects already profiled there are kept as-is (adding skills/tables later is incremental)."""
    wanted = {t.upper() for t in tables} if tables else None
    if wanted:
        databases = sorted({t.split(".")[0] for t in tables})
        schemas = sorted({t.split(".")[1] for t in tables})
    index: dict[str, Any] = {
        "version": 2,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "scope": {"connection": connection, "databases": databases, "schemas": schemas,
                  "tables": sorted(set(tables or []))},
        "objects": {}, "joins": [], "semantic_views": [], "gaps": [],
    }
    profiled = {f.upper(): o for f, o in (prior or {}).get("objects", {}).items() if "grain_candidates" in o}
    profiled.update({f.upper(): o for f, o in (prior or {}).get("profile_cache", {}).items()})
    for db in databases:
        progress("list_objects", database=db)
        found = list_objects(run, db, schemas)
        if wanted:
            found = {f: o for f, o in found.items() if f.upper() in wanted}
        index["objects"].update(found)
        index["semantic_views"].extend(list_semantic_views(run, db))
    if not wanted:
        for fqn, obj in (prior or {}).get("objects", {}).items():
            index["objects"].setdefault(fqn, obj)
    # Profiles of tables no longer chosen stay cached (re-choosing them is free) but are
    # invisible to matching, so a dropped table can never be bound.
    index["profile_cache"] = {f: o for f, o in {**(prior or {}).get("profile_cache", {}),
                                                **(prior or {}).get("objects", {})}.items()
                              if "grain_candidates" in o and f not in index["objects"]}
    todo = []
    for fqn, obj in index["objects"].items():
        if not obj["official"]:
            continue
        if fqn.upper() in profiled:
            index["objects"][fqn] = profiled[fqn.upper()]
            progress("profile_object", fqn=fqn, cached=True)
            continue
        progress("profile_object", fqn=fqn)
        if getattr(run, "many", None):  # local CLI: batch all tables (one login per round)
            todo.append((fqn, obj))
            continue
        try:
            profile_columns(run, fqn, obj)
            profile_code_values(run, fqn, obj)
            detect_grain(run, fqn, obj)
            detect_cadence(run, fqn, obj)
        except SqlError as exc:
            index["gaps"].append({"fqn": fqn, "reason": str(exc)[:300]})
    if todo:
        for fqn, reason in profile_many(run, todo).items():
            index["gaps"].append({"fqn": fqn, "reason": reason})
    progress("infer_joins")
    index["joins"] = infer_joins(run, index["objects"])
    return index


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", required=True)
    parser.add_argument("--database", action="append", default=[])
    parser.add_argument("--schema", action="append", default=[])
    parser.add_argument("--table", action="append", default=[], help="DB.SCHEMA.TABLE; profile only these")
    parser.add_argument("--merge", action="store_true", help="keep objects already profiled in --out")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if not (args.database or args.table):
        raise SystemExit("--database or --table is required")
    run = make_runner(args.connection, args.timeout)
    prior = None
    if args.merge and args.out.exists():
        prior = json.loads(args.out.read_text(encoding="utf-8"))
    index = build_index(run, args.connection, args.database, args.schema, args.table or None, prior)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(index, indent=2, default=str) + "\n", encoding="utf-8")
    progress("done", objects=len(index["objects"]), joins=len(index["joins"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
