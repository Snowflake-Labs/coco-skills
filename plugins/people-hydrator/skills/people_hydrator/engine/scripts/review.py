#!/usr/bin/env python3
"""Preview review per skill: all metrics and definitions at once, one prompt ("anything look off?").

  review.py done    --workspace W --skill S        the analyst said it looks good
  review.py reopen  --workspace W --skill S        something changed; review again
  review.py status  --workspace W [--skill S ...]

W/review_state.json: {skill: {status: open|corrected|reviewed, items: [fix ledger ids], at}}.
Corrections are recorded by fix_ledger (apply) and saved as re-runnable checks (corrections.json).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


def load(ws: Path) -> dict[str, Any]:
    try:
        return json.loads((ws / "review_state.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(ws: Path, state: dict[str, Any]) -> None:
    (ws / "review_state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def mark(ws: Path, skill: str, status: str, item: str | None = None) -> dict[str, Any]:
    state = load(ws)
    s = state.setdefault(skill, {"status": "open", "items": []})
    if item and item not in s["items"]:
        s["items"].append(item)
    s["status"] = status  # a correction asks for another look; only "looks good" marks it reviewed
    s["at"] = now()
    save(ws, state)
    return s


def status(ws: Path, skills: list[str]) -> dict[str, str]:
    state = load(ws)
    return {s: state.get(s, {}).get("status", "open") for s in skills}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["done", "reopen", "status"])
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--skill", action="append", default=[])
    a = p.parse_args()
    if a.action == "status":
        out: Any = status(a.workspace, a.skill or sorted(load(a.workspace)))
    else:
        out = {s: mark(a.workspace, s, "reviewed" if a.action == "done" else "open") for s in a.skill}
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
