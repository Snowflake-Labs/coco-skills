#!/usr/bin/env python3
"""Binding gate: grade each skill's data connection against the rules in its eval plan. Stdlib only.

  eval_gate.py --workspace W [--skill S ...]     grade, write W/binding_gate.json, print a summary

Rules live in a fenced block in evals/<skill>/eval_plan.md:

    ```binding-gate
    {"rules": [
      {"id": "G1", "title": "...", "check": "min_needs", "needs": ["people.x", "people.y"], "min": 2},
      {"id": "G2", "title": "...", "check": "need", "need": "people.x",
       "fields": ["snapshot_date", "employment_status|active_flag"], "min_confidence": "MEDIUM"},
      {"id": "P2", "title": "...", "check": "any_need", "needs": ["people.a", "people.b"]}
    ]}
    ```

A need is bound when bindings.json has a table for it (not unresolved). A field is bound when it
has a column or expression; "a|b" means either field. G = must pass, P = correctness, S = nice to
have (reported, never graded).

Grade: any G fails -> INCOMPATIBLE; all G and all P pass -> EQUIVALENT; otherwise DEGRADED.
INCOMPATIBLE and DEGRADED skills can still be tuned and hydrated (as partial); each failed rule
becomes a question in Tune (questions.py).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[2]
CATALOG = SKILL_ROOT / "catalog" / "people.json"
TEMPLATES = SKILL_ROOT / "templates"
BLOCK_RE = re.compile(r"```binding-gate\s*\n(.*?)\n```", re.S)
CONFIDENCE = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
GRADES = ("EQUIVALENT", "DEGRADED", "INCOMPATIBLE")


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def eval_plan_path(skill: str) -> Path | None:
    cat = load_json(CATALOG, {}) or {}
    for s in cat.get("skills", []):
        if s["name"] == skill and s.get("eval_dir"):
            return TEMPLATES / cat["source_plugin"] / s["eval_dir"] / "eval_plan.md"
    return None


def load_rules(skill: str) -> list[dict[str, Any]]:
    path = eval_plan_path(skill)
    if path is None or not path.is_file():
        return []
    m = BLOCK_RE.search(path.read_text(encoding="utf-8"))
    if not m:
        return []
    return json.loads(m.group(1)).get("rules", [])


def need_bound(needs: dict[str, Any], key: str) -> bool:
    n = needs.get(key) or {}
    return bool(n.get("fqn")) and n.get("status") != "unresolved"


def field_state(needs: dict[str, Any], key: str, field: str, min_conf: str) -> str:
    """'ok', 'weak' (bound below min confidence) or 'missing'."""
    best = "missing"
    for name in field.split("|"):
        f = ((needs.get(key) or {}).get("fields") or {}).get(name) or {}
        if not (f.get("column") or f.get("expression")) or f.get("status") == "unresolved":
            continue
        if CONFIDENCE.get(str(f.get("confidence", "LOW")).upper(), 1) >= CONFIDENCE[min_conf]:
            return "ok"
        best = "weak"
    return best


def check_rule(rule: dict[str, Any], needs: dict[str, Any]) -> dict[str, Any]:
    kind = rule.get("check", "need")
    out = {"id": rule["id"], "level": rule["id"][0].upper(), "title": rule.get("title", rule["id"]),
           "passed": False, "reason": "", "need": rule.get("need"), "missing_fields": []}
    if kind == "min_needs":
        bound = [k for k in rule["needs"] if need_bound(needs, k)]
        out["passed"] = len(bound) >= rule.get("min", len(rule["needs"]))
        out["reason"] = f"{len(bound)} of {len(rule['needs'])} data needs connected (need {rule.get('min')})"
        out["unbound_needs"] = [k for k in rule["needs"] if k not in bound]
    elif kind == "any_need":
        bound = [k for k in rule["needs"] if need_bound(needs, k)]
        out["passed"] = bool(bound)
        out["reason"] = f"connected: {bound[0]}" if bound else "none of these data needs is connected"
        out["unbound_needs"] = [] if bound else list(rule["needs"])
    else:
        key = rule["need"]
        if not need_bound(needs, key):
            out["reason"] = "no table connected"
            return out
        min_conf = str(rule.get("min_confidence", "MEDIUM")).upper()
        states = {f: field_state(needs, key, f, min_conf) for f in rule.get("fields", [])}
        missing = [f for f, s in states.items() if s == "missing"]
        weak = [f for f, s in states.items() if s == "weak"]
        out["missing_fields"], out["weak_fields"] = missing, weak
        out["passed"] = not missing and not weak
        parts = ([f"missing: {', '.join(missing)}"] if missing else []) + \
                ([f"low confidence: {', '.join(weak)}"] if weak else [])
        out["reason"] = "; ".join(parts) or f"connected to {needs[key]['fqn']}"
    return out


def grade(results: list[dict[str, Any]]) -> str:
    g = [r for r in results if r["level"] == "G"]
    p = [r for r in results if r["level"] == "P"]
    if any(not r["passed"] for r in g):
        return "INCOMPATIBLE"
    if all(r["passed"] for r in g + p):
        return "EQUIVALENT"
    return "DEGRADED"


def grade_skill(ws: Path, skill: str) -> dict[str, Any]:
    rules = load_rules(skill)
    if not rules:
        return {"grade": None, "rules": [], "note": "no binding rules in this skill's eval plan"}
    needs = (load_json(ws / "bindings.json", {}) or {}).get("needs", {})
    results = [check_rule(r, needs) for r in rules]
    return {"grade": grade(results), "rules": results,
            "failed": [r["id"] for r in results if not r["passed"] and r["level"] in {"G", "P"}]}


def run(ws: Path, skills: list[str]) -> dict[str, Any]:
    data = load_json(ws / "binding_gate.json", {}) or {}
    data.setdefault("skills", {})
    for s in skills:
        data["skills"][s] = grade_skill(ws, s)
    data["at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    (ws / "binding_gate.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def summary_line(skill: str, res: dict[str, Any]) -> str:
    if not res.get("grade"):
        return f"{skill}: not graded ({res.get('note')})"
    failed = [r for r in res["rules"] if not r["passed"] and r["level"] in {"G", "P"}]
    tail = "; ".join(f"{r['id']} {r['title']}: {r['reason']}" for r in failed)
    return f"{skill}: {res['grade']}" + (f" ({tail})" if tail else "")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--skill", action="append", default=[])
    a = p.parse_args()
    ws = a.workspace.resolve()
    skills = a.skill or (load_json(ws / "topics.json", {}) or {}).get("skills", [])
    data = run(ws, skills)
    for s in skills:
        print(summary_line(s, data["skills"][s]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
