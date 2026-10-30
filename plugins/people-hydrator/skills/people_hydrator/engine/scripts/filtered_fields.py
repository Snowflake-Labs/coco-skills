#!/usr/bin/env python3
"""Check bound fields on the rows a need actually reads. Stdlib only.

When Bind keeps only some rows of a table (a row filter, e.g. month-end snapshot rows of a
table that also holds hire/exit event rows), a flag or category column can be constant on
those rows: on such a table the exit flag is only ever true on the event rows. A field like
that carries no information for the need, so it is unbound (Tune asks where it lives, and the
skill takes it from another bound source instead) and the table's layout is
recorded as a note the hydrated skill reads. Nothing here is specific to one customer: the
decision comes from counting values on the customer's own rows.

  filtered_fields.py --workspace W --connection C
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from match_fields import merged_needs  # noqa: E402

CATALOG = Path(__file__).resolve().parents[2] / "catalog" / "people.json"
CHECKED_TYPES = {"boolean", "category"}
Runner = Callable[[str], list[dict[str, Any]]]


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def literal(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def check(bindings: dict[str, Any], catalog: dict[str, Any], run: Runner) -> list[dict[str, Any]]:
    """Unbind boolean/category fields that have one value on the filtered rows. Returns notes."""
    specs = merged_needs(catalog)
    notes: list[dict[str, Any]] = []
    for key, b in bindings.get("needs", {}).items():
        rf = b.get("row_filter")
        if not rf or not b.get("fqn") or key not in specs:
            continue
        kept = ", ".join(literal(v) for v in rf["values"])
        where = f"{quote(rf['column'])} in ({kept})"
        fields = [(f, x["column"]) for f, x in (b.get("fields") or {}).items()
                  if x.get("column") and not x.get("expression") and x.get("status") != "confirmed"
                  and specs[key]["fields"].get(f, {}).get("semantic_type") in CHECKED_TYPES]
        if not fields:
            continue
        counts = ", ".join(f"count(distinct {quote(c)}) as d{i}, any_value({quote(c)}) as v{i}"
                           for i, (_f, c) in enumerate(fields))
        try:
            row = run(f"select {counts} from {b['fqn']} where {where}")[0]
        except Exception as exc:  # noqa: BLE001 - advisory; never fail Bind on it
            notes.append({"need": key, "fqn": b["fqn"], "error": str(exc)[:200]})
            continue
        dropped, constant = [], []
        for i, (fname, col) in enumerate(fields):
            if int(row.get(f"D{i}", row.get(f"d{i}", 0)) or 0) <= 1:
                value = row.get(f"V{i}", row.get(f"v{i}"))
                reason = (f"{col} is always {value} on the {', '.join(map(str, rf['values']))} rows "
                          f"this need reads, so it can't tell rows apart")
                if specs[key]["fields"][fname].get("required"):
                    # keep a required binding (the skill stays runnable) but warn: the skill's
                    # skill takes this from another bound source, e.g. the exit records
                    b["fields"][fname].setdefault("evidence", []).append(reason)
                    b["fields"][fname].update(status="needs_review", confidence="LOW", constant=True)
                    constant.append({"field": fname, "column": col, "reason": reason})
                    continue
                b["fields"][fname] = {"column": None, "status": "unresolved", "confidence": "LOW",
                                      "score": 0, "evidence": [reason], "alternatives": [col]}
                dropped.append({"field": fname, "column": col, "reason": reason})
        if dropped or constant:
            b["status"] = "needs_review"
        notes.append({"need": key, "fqn": b["fqn"], "row_filter": rf, "dropped": dropped, "constant": constant,
                      "layout": (f"{b['fqn']} holds more than one kind of row ({rf['column']}); "
                                 f"this need reads only {', '.join(map(str, rf['values']))} rows")})
    return notes


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--connection", required=True)
    a = p.parse_args()
    from _sql import make_runner
    path = a.workspace / "bindings.json"
    bindings = json.loads(path.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    notes = check(bindings, catalog, make_runner(a.connection, 120))
    bindings["layout_notes"] = notes
    path.write_text(json.dumps(bindings, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"layout_notes": notes}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
