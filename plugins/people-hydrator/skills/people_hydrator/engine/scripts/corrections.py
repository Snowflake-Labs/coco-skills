#!/usr/bin/env python3
"""Saved corrections: every accepted fix, kept as a check that is re-run by the checks in Tune.

  corrections.py list   --workspace W
  corrections.py check  --workspace W          (the Tune checks run this as gate A6_corrections)

W/corrections.json: {"corrections": [{id, text, skills, metric?, value_after?, ops, at}]}
A correction "no longer holds" when its change was undone (a setting or table changed back) or,
for a number, when today's value moved away from the one the analyst accepted.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


def load(ws: Path) -> dict[str, Any]:
    try:
        return json.loads((ws / "corrections.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"corrections": []}


def save(ws: Path, data: dict[str, Any]) -> None:
    (ws / "corrections.json").write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def same(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.05, abs(b) * 0.005)


def record(ws: Path, item: dict[str, Any]) -> dict[str, Any]:
    """Save an applied or explained fix ledger item as a correction (idempotent by item id)."""
    data = load(ws)
    c = {"id": item["id"], "text": item.get("text", ""), "skills": item.get("skills", []),
         "metric": item.get("metric"), "value_after": item.get("target") if item.get("metric") else None,
         "ops": item.get("applied_ops") or [], "explained": item.get("status") == "resolved",
         "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    data["corrections"] = [x for x in data["corrections"] if x["id"] != c["id"]] + [c]
    save(ws, data)
    return c


def op_holds(ws: Path, op: dict[str, Any]) -> bool:
    """Is the change still in place?"""
    if op["op"] == "bind_table":
        b = (_json(ws / "bindings.json") or {}).get("needs", {}).get(op["need"], {})
        return (b.get("fqn") or "").upper() == op["fqn"].upper()
    if op["op"] == "bind_column":
        b = (_json(ws / "bindings.json") or {}).get("needs", {}).get(op["need"], {})
        return ((b.get("fields") or {}).get(op["field"]) or {}).get("column") == op["column"]
    tid = "attrition_rate_method" if op["op"] == "set_method" else op["tunable"]
    seen = False
    for path in sorted((ws / "tuning").glob("*.json")):  # every skill that uses the setting must still have it
        t = (_json(path) or {}).get("tunables", {}).get(tid)
        if t is None:
            continue
        seen, cur = True, t.get("value")
        if isinstance(cur, list) and isinstance(op["value"], list):
            if sorted(map(str, cur)) != sorted(map(str, op["value"])):
                return False
        elif not (cur == op["value"] or str(cur) == str(op["value"])):
            return False
    return seen


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def check(ws: Path, values: dict[str, dict[str, Any]] | None = None,
          skills: list[str] | None = None) -> dict[str, Any]:
    """Gate A6: every saved correction still holds. `values` = {skill: {metric: {"VALUE": v}}} (fresh)."""
    mine = [c for c in load(ws)["corrections"] if not skills or set(c["skills"]) & set(skills)]
    if not mine:
        return {"gate": "A6_corrections", "status": "SKIP", "detail": "no saved corrections yet"}
    broken = []
    for c in mine:
        undone = [o for o in c["ops"] if not op_holds(ws, o)]
        if undone:
            broken.append(f"your earlier correction '{c['text']}' was undone")
            continue
        if c.get("metric") and c.get("value_after") is not None and not c.get("explained") and values:
            now = next((((values.get(s) or {}).get(c["metric"]) or {}).get("VALUE") for s in c["skills"]
                        if ((values.get(s) or {}).get(c["metric"]) or {}).get("VALUE") is not None), None)
            try:
                if now is not None and not same(float(now), float(c["value_after"])):
                    broken.append(f"your earlier correction '{c['text']}' no longer holds: now {float(now):g}")
            except (TypeError, ValueError):
                pass
    return {"gate": "A6_corrections", "status": "FAIL" if broken else "PASS",
            "detail": "; ".join(broken) or f"{len(mine)} saved corrections still hold"}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["list", "check"])
    p.add_argument("--workspace", type=Path, required=True)
    a = p.parse_args()
    out = load(a.workspace) if a.action == "list" else check(a.workspace)
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
