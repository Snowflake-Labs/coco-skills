#!/usr/bin/env python3
"""data.py: numbers on screen that come from Snowflake, never from the model.

    python3 data.py resolve --project P [--connection NAME] [--only q1,q2]   run the queries, write the lock
    python3 data.py status  --project P                                       what is bound, from what, how old
    python3 data.py format  VALUE --style usd_compact --decimals 1            try a format without a query

video.json declares the queries; scenes bind to their results:

    "data": {
      "connection": "my_connection", "maxAgeDays": 7,
      "queries": {
        "orders": {"label": "TPC-H sample data",
                   "sql": "SELECT SUM(O_TOTALPRICE) AS TOTAL, COUNT(*) AS N FROM SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.ORDERS",
                   "values": {"total": {"column": "TOTAL", "style": "usd_compact", "decimals": 1},
                              "count": {"column": "N", "style": "int"}}},
        "regions": {"label": "TPC-H sample data", "sql": "SELECT ... AS REGION, ... AS REVENUE ... LIMIT 5",
                    "rows": {"max": 5, "columns": {"region": {"column": "REGION", "style": "text"},
                                                   "revenue": {"column": "REVENUE", "style": "usd_compact"}}}}
      }
    }
    scene: {"id": "stat", "src": "scenes/stat.html", "bind": {"value": "orders.total", "bars": "regions.rows"}, ...}

The lock (data/data.lock.json) records, per query: the SQL and its hash, the query id, when it ran, where (account,
region, role, warehouse; never the user), the rows, and every value both raw and as the exact string the scene will
show. A count-up animates to `to` with `decimals`, `prefix` and `suffix`, and lands on `display` because `to` is
already rounded here, in one formatter, so Python and the browser can never disagree about the last digit.
"""
import argparse
import datetime
import decimal
import hashlib
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = pathlib.Path("data") / "data.lock.json"
MAX_ROWS = 200
STYLES = ("text", "int", "number", "usd", "compact", "usd_compact", "percent")
UNITS = [(decimal.Decimal(10) ** 12, "T"), (decimal.Decimal(10) ** 9, "B"), (decimal.Decimal(10) ** 6, "M"), (decimal.Decimal(10) ** 3, "K")]


def sql_sha(sql):
    return hashlib.sha256(re.sub(r"\s+", " ", sql.strip()).encode()).hexdigest()[:16]


def fmt(raw, spec):
    """One value as {raw, to, decimals, prefix, suffix, display, style}. Rounding is half up unless
    "round": "down", which never shows more than was measured (use it for anything near a ceiling)."""
    style = spec.get("style", "number")
    if style not in STYLES:
        raise ValueError("style %r is not one of %s" % (style, ", ".join(STYLES)))
    if raw is None:
        return {"raw": None, "display": spec.get("null", "n/a"), "style": style}
    if style == "text":
        return {"raw": str(raw), "display": str(raw), "style": style}
    d = decimal.Decimal(str(raw))
    prefix, suffix = "", ""
    decimals = spec.get("decimals")
    mode = decimal.ROUND_DOWN if spec.get("round") == "down" else decimal.ROUND_HALF_UP
    note = None
    if style in ("number", "usd") and decimals is None and d != d.to_integral_value():
        raise ValueError("%s is not a whole number: set \"decimals\" (or use style int to round it on purpose)" % raw)
    if style in ("usd", "usd_compact"):
        prefix = "$"
    if style in ("compact", "usd_compact"):
        unit = spec.get("unit")
        ladder = [(decimal.Decimal(1), "")] + UNITS[::-1]          # 1, K, M, B, T
        if unit is not None and unit not in [s for _, s in ladder]:
            raise ValueError("unit %r is not one of '', K, M, B, T" % unit)
        i = [s for _, s in ladder].index(unit) if unit is not None else \
            max(j for j, (v, _) in enumerate(ladder) if abs(d) >= v or j == 0)
        dec = lambda j: (1 if ladder[j][1] else 0) if decimals is None else int(decimals)  # noqa: E731
        q = (d / ladder[i][0]).quantize(decimal.Decimal(1).scaleb(-dec(i)), rounding=mode)
        # 999,950 is "1.0M", not "1,000.0K": rounding can carry into the next unit
        while unit is None and i + 1 < len(ladder) and abs(q) >= 1000:
            i += 1
            q = (d / ladder[i][0]).quantize(decimal.Decimal(1).scaleb(-dec(i)), rounding=mode)
        d, suffix, decimals = d / ladder[i][0], ladder[i][1], dec(i)
    if style == "percent":
        if spec.get("fraction", True):
            d *= 100
        suffix = "%"
        decimals = 1 if decimals is None else decimals
    if style == "int":
        decimals = 0
    decimals = 0 if decimals is None else int(decimals)
    step = decimal.Decimal(1).scaleb(-decimals)
    q = d.quantize(step, rounding=mode)
    if style == "percent" and d < 100 <= q:
        # 99.96% on screen as "100.0%" claims perfection the data does not have (anti-pattern 3)
        q, note = d.quantize(step, rounding=decimal.ROUND_DOWN), "rounded down: half-up would have shown 100%"
    if q == 0:
        q = abs(q)  # never print "-0"
    prefix, suffix = spec.get("prefix", prefix), spec.get("suffix", suffix)
    shown = "{:,.{d}f}".format(q, d=decimals)
    out = {"raw": float(raw) if not isinstance(raw, int) else raw, "to": float(q) if decimals else int(q),
           "decimals": decimals, "prefix": prefix, "suffix": suffix, "display": prefix + shown + suffix, "style": style}
    if note:
        out["note"] = note
    return out


def as_of(ts):
    local = ts.astimezone()
    return {"asOfDate": local.date().isoformat(), "asOf": "%s %d, %d" % (local.strftime("%b"), local.day, local.year)}


def load(root):
    spec = json.loads((root / "video.json").read_text())
    block = spec.get("data")
    if not block or not block.get("queries"):
        raise SystemExit("video.json has no data.queries: nothing to resolve")
    return spec, block


def read_lock(root):
    try:
        return json.loads((root / LOCK).read_text())
    except FileNotFoundError:
        return None


def column(cols, name, qid):
    up = {c.upper(): i for i, c in enumerate(cols)}
    if name.upper() not in up:
        raise SystemExit("query %s has no column %s (it returned %s)" % (qid, name, ", ".join(cols)))
    return up[name.upper()]


def run_query(cur, qid, q, where):
    from snowflake.connector.errors import ProgrammingError
    try:
        cur.execute(q["sql"])
    except ProgrammingError as e:
        raise SystemExit("query %s failed: %s" % (qid, str(e).splitlines()[-1][:300]))
    cols = [c[0] for c in cur.description]
    rows = cur.fetchmany(MAX_ROWS + 1)
    if len(rows) > MAX_ROWS:
        raise SystemExit("query %s returned more than %d rows; a scene cannot show that many, so aggregate or LIMIT it" % (qid, MAX_ROWS))
    ran = datetime.datetime.now(datetime.timezone.utc)
    out = {"label": q.get("label", qid), "sql": q["sql"], "sqlSha": sql_sha(q["sql"]), "queryId": cur.sfqid,
           "ranAt": ran.isoformat(timespec="seconds"), **as_of(ran), "ranAs": where, "columns": cols, "rowCount": len(rows)}
    try:
        if q.get("values"):
            if not rows:
                raise SystemExit("query %s returned no rows, so it has no values to show" % qid)
            if len(rows) > 1 and q.get("row") != "first":
                raise SystemExit("query %s returned %d rows; values read one row (add \"row\": \"first\" if that is intended)" % (qid, len(rows)))
            out["values"] = {k: fmt(rows[0][column(cols, v["column"], qid)], v) for k, v in q["values"].items()}
        if q.get("rows"):
            r = q["rows"]
            cap = int(r.get("max", 12))
            if len(rows) > cap:
                raise SystemExit("query %s returned %d rows; rows.max is %d" % (qid, len(rows), cap))
            spec_cols = r["columns"]
            out["rows"] = [{k: fmt(row[column(cols, v["column"], qid)], v) for k, v in spec_cols.items()} for row in rows]
    except ValueError as e:
        raise SystemExit("query %s: %s" % (qid, e))
    return out


def resolve(root, cli_conn=None, only=None):
    spec, block = load(root)
    import sf
    name = sf.connection_name(block, cli_conn)
    queries = block["queries"]
    todo = [k for k in queries if not only or k in only]
    missing = [k for k in (only or []) if k not in queries]
    if missing:
        raise SystemExit("no such query: %s" % ", ".join(missing))
    con = sf.connect(name)
    try:
        cur = con.cursor()
        where = sf.context(cur)
        lock = read_lock(root) if only else None
        lock = lock or {"version": 1, "queries": {}}
        for qid in todo:
            lock["queries"][qid] = run_query(cur, qid, queries[qid], where)
            print("RESOLVED  %-12s %s  (%d rows, query %s)" % (qid, ", ".join("%s=%s" % (k, v["display"]) for k, v in
                  lock["queries"][qid].get("values", {}).items()) or "rows", lock["queries"][qid]["rowCount"], lock["queries"][qid]["queryId"]))
    finally:
        con.close()
    lock["queries"] = {k: v for k, v in lock["queries"].items() if k in queries}
    lock.update(connection=name, **where, resolvedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"))
    path = root / LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(lock, indent=1, default=str))
    tmp.replace(path)
    print("wrote     %s  (%s, role %s)" % (LOCK, where["account"], where["role"]))


def binding(ref):
    """'orders.total' -> ('orders', 'value', 'total'); 'regions.rows' -> ('regions', 'rows', None)."""
    q, _, rest = str(ref).partition(".")
    if not q or not rest:
        raise ValueError("bind %r: use query.value or query.rows" % ref)
    return (q, "rows", None) if rest == "rows" else (q, "value", rest)


def status(root):
    spec = json.loads((root / "video.json").read_text())
    block = spec.get("data") or {}
    lock = read_lock(root) or {"queries": {}}
    now = datetime.datetime.now(datetime.timezone.utc)
    out = {"queries": [], "bindings": []}
    for qid, q in (block.get("queries") or {}).items():
        lq = lock["queries"].get(qid)
        age = (now - datetime.datetime.fromisoformat(lq["ranAt"])).total_seconds() / 86400 if lq else None
        out["queries"].append({"query": qid, "resolved": bool(lq), "changed": bool(lq) and lq["sqlSha"] != sql_sha(q["sql"]),
                               "ageDays": round(age, 2) if age is not None else None, "asOf": lq and lq["asOf"]})
    for s in spec.get("scenes", []):
        for k, ref in (s.get("bind") or {}).items():
            q, kind, v = binding(ref)
            lq = lock["queries"].get(q) or {}
            shown = (lq.get("values") or {}).get(v, {}).get("display") if kind == "value" else ("%d rows" % len(lq.get("rows") or []) if lq.get("rows") is not None else None)
            out["bindings"].append({"scene": s["id"], "param": k, "bind": ref, "shows": shown})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve")
    r.add_argument("--project", required=True)
    r.add_argument("--connection")
    r.add_argument("--only", help="comma-separated query ids; the rest of the lock is kept")
    s = sub.add_parser("status")
    s.add_argument("--project", required=True)
    s.add_argument("--json", action="store_true")
    f = sub.add_parser("format")
    f.add_argument("value")
    f.add_argument("--style", default="number")
    f.add_argument("--decimals", type=int)
    f.add_argument("--round", choices=("half_up", "down"))
    a = ap.parse_args()
    if a.cmd == "format":
        spec = {"style": a.style, **({"decimals": a.decimals} if a.decimals is not None else {}), **({"round": a.round} if a.round else {})}
        print(json.dumps(fmt(decimal.Decimal(a.value) if a.style != "text" else a.value, spec)))
        return
    root = pathlib.Path(a.project).expanduser().resolve()
    if a.cmd == "status":
        st = status(root)
        if a.json:
            print(json.dumps(st, indent=1))
            return
        for q in st["queries"]:
            print("%-12s %s" % (q["query"], "not resolved" if not q["resolved"] else "CHANGED since resolved" if q["changed"]
                                 else "as of %s (%.1f days old)" % (q["asOf"], q["ageDays"])))
        for b in st["bindings"]:
            print("  %s.%s <- %s : %s" % (b["scene"], b["param"], b["bind"], b["shows"] if b["shows"] is not None else "UNRESOLVED"))
        return
    sys.path.insert(0, str(HERE))
    import sf
    try:
        resolve(root, a.connection, set(a.only.split(",")) if a.only else None)
    except sf.Refusal as e:
        sf.fail(e)


if __name__ == "__main__":
    main()
