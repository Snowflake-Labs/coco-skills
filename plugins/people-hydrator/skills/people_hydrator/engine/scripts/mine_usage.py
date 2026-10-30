#!/usr/bin/env python3
"""Mine structural usage patterns from query history. Stdlib only.

Reads recent query text for the scoped databases, reduces each statement to
STRUCTURE (tables touched, join keys, filter columns, group-by columns,
aggregate functions), and discards the text. String and numeric literals are
stripped before parsing; raw query text is never written to disk.

Merges the patterns into data_index.json under ``usage``:
  usage.object_queries      fqn -> query count
  usage.join_pairs          "A.col=B.col" -> count
  usage.filter_columns      fqn.col -> count
  usage.group_by_columns    fqn.col -> count
  usage.aggregates          "FUNC(fqn.col)" -> count
  usage.source              account_usage | information_schema | unavailable
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sql import ENGINE_TAG, Runner, SqlError, make_runner, quote_literal  # noqa: E402

STRING_LIT = re.compile(r"'(?:[^']|'')*'")
NUMBER_LIT = re.compile(r"\b\d+(?:\.\d+)?\b")
COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
IDENT = r'(?:"[^"]+"|[A-Za-z_][\w$]*)'
TABLE_REF = re.compile(
    rf"\b(?:from|join)\s+({IDENT}(?:\.{IDENT}){{0,2}})(?:\s+(?:as\s+)?({IDENT}))?", re.I
)
JOIN_ON = re.compile(rf"({IDENT})\.({IDENT})\s*=\s*({IDENT})\.({IDENT})")
WHERE_COL = re.compile(rf"({IDENT})\.({IDENT})\s*(?:=|<>|!=|<|>|<=|>=|\bin\b|\blike\b|\bilike\b|\bis\b|\bbetween\b)", re.I)
GROUP_BY = re.compile(r"\bgroup\s+by\s+(.+?)(?:\border\b|\bhaving\b|\blimit\b|\bqualify\b|\)|$)", re.I | re.S)
AGG = re.compile(rf"\b(sum|count|avg|min|max|median|count_if)\s*\(\s*(?:distinct\s+)?(?:({IDENT})\.)?({IDENT})", re.I)
RESERVED = {"select", "where", "on", "group", "order", "left", "right", "inner", "outer",
            "full", "cross", "join", "limit", "using", "as", "lateral", "table", "union",
            "all", "case", "when", "then", "else", "end", "distinct", "by", "asc", "desc",
            "nulls", "and", "or", "not", "null", "true", "false", "rollup", "cube", "with"}


def unquote(ident: str) -> str:
    return ident[1:-1] if ident.startswith('"') else ident.upper()


def sanitize(text: str) -> str:
    """Strip comments and literals so no data values survive parsing."""
    text = COMMENT.sub(" ", text)
    text = STRING_LIT.sub("?", text)
    return NUMBER_LIT.sub("?", text)


def resolve_fqn(name: str, known: dict[str, str]) -> str | None:
    """Map a (possibly partial) table reference onto a known FQN."""
    parts = [unquote(p) for p in re.findall(IDENT, name)]
    if not parts:
        return None
    key = ".".join(parts).upper()
    for fqn_upper, fqn in known.items():
        if fqn_upper == key or fqn_upper.endswith("." + key):
            return fqn
    return None


def parse_statement(text: str, known: dict[str, str]) -> dict[str, Any]:
    clean = sanitize(text)
    aliases: dict[str, str] = {}
    tables: set[str] = set()
    for ref, alias in TABLE_REF.findall(clean):
        fqn = resolve_fqn(ref, known)
        if not fqn:
            continue
        tables.add(fqn)
        short = unquote(re.findall(IDENT, ref)[-1])
        aliases[short] = fqn
        if alias and alias.lower() not in RESERVED:
            aliases[unquote(alias)] = fqn

    def col(alias: str, column: str) -> str | None:
        fqn = aliases.get(unquote(alias))
        return f"{fqn}.{unquote(column)}" if fqn else None

    joins: list[str] = []
    for la, lc, ra, rc in JOIN_ON.findall(clean):
        left, right = col(la, lc), col(ra, rc)
        if left and right and left.rsplit(".", 1)[0] != right.rsplit(".", 1)[0]:
            joins.append("=".join(sorted([left, right])))
    filters = [c for a, column in WHERE_COL.findall(clean) if (c := col(a, column))]
    group_cols: list[str] = []
    for clause in GROUP_BY.findall(clean):
        for a, column in re.findall(rf"({IDENT})\.({IDENT})", clause):
            if c := col(a, column):
                group_cols.append(c)
        if len(tables) == 1:
            only = next(iter(tables))
            for bare in re.findall(rf"(?<![.\w]){IDENT}(?![.\w(])", clause):
                if bare.lower() not in RESERVED and bare != "?":
                    group_cols.append(f"{only}.{unquote(bare)}")
    aggs: list[str] = []
    for func, a, column in AGG.findall(clean):
        if column.lower() in RESERVED:
            continue
        if a:
            target = col(a, column)
        elif len(tables) == 1:
            target = f"{next(iter(tables))}.{unquote(column)}"
        else:
            target = None
        if target:
            aggs.append(f"{func.upper()}({target})")
    return {"tables": sorted(tables), "joins": joins, "filters": filters,
            "group_by": group_cols, "aggregates": aggs}


def fetch_history(run: Runner, databases: list[str], days: int, limit: int) -> tuple[list[str], str]:
    db_filter = " or ".join(f"query_text ilike {quote_literal('%' + db + '%')}" for db in databases)
    tag = quote_literal("%" + ENGINE_TAG.strip("/* ").strip() + "%")
    base = (
        f"query_type = 'SELECT' and execution_status = 'SUCCESS' and ({db_filter}) "
        f"and query_text not ilike {tag}"
    )
    try:
        rows = run(
            f"select query_text from snowflake.account_usage.query_history "
            f"where start_time >= dateadd('day', -{int(days)}, current_timestamp()) and {base} "
            f"order by start_time desc limit {int(limit)}"
        )
        return [r["QUERY_TEXT"] for r in rows], "account_usage"
    except SqlError:
        pass
    try:
        rows = run(
            f"select query_text from table(information_schema.query_history(result_limit => 10000)) "
            f"where {base} limit {int(limit)}"
        )
        return [r["QUERY_TEXT"] for r in rows], "information_schema"
    except SqlError:
        return [], "unavailable"


def mine(texts: list[str], known_fqns: list[str]) -> dict[str, Any]:
    known = {f.upper(): f for f in known_fqns}
    obj_q, join_c, filt_c, grp_c, agg_c = Counter(), Counter(), Counter(), Counter(), Counter()
    for text in texts:
        parsed = parse_statement(text, known)
        obj_q.update(parsed["tables"])
        join_c.update(set(parsed["joins"]))
        filt_c.update(set(parsed["filters"]))
        grp_c.update(set(parsed["group_by"]))
        agg_c.update(set(parsed["aggregates"]))
    return {
        "queries_parsed": len(texts),
        "object_queries": dict(obj_q.most_common()),
        "join_pairs": dict(join_c.most_common(200)),
        "filter_columns": dict(filt_c.most_common(300)),
        "group_by_columns": dict(grp_c.most_common(300)),
        "aggregates": dict(agg_c.most_common(300)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", required=True)
    parser.add_argument("--index", type=Path, required=True, help="data_index.json to update")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=2000)
    args = parser.parse_args()
    index = json.loads(args.index.read_text(encoding="utf-8"))
    run = make_runner(args.connection, 300)
    texts, source = fetch_history(run, index["scope"]["databases"], args.days, args.limit)
    usage = mine(texts, list(index["objects"]))
    del texts  # text is never persisted
    usage["source"] = source
    usage["window_days"] = args.days
    index["usage"] = usage
    args.index.write_text(json.dumps(index, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"event": "usage_mined", "source": source,
                      "queries": usage["queries_parsed"]}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
