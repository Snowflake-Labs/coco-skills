#!/usr/bin/env python3
"""Business-fit recommendation: from what the analyst hopes to achieve to skills. Stdlib only.

Inputs are the analyst's own words (free text) plus optional quick picks (roles,
goals from catalog/goals.json). Nothing is read from the account and no model is
called, so every recommendation has a plain reason.

  recommend_skills.py --workspace W [--text "..."] [--role chro ...] [--goal reduce_attrition ...]

Writes W/recommendation.json:
  {text, roles, goals, skills: [{skill, label, fit, reasons, persona, business_value}]}
labels: recommended (fit >= 60) | consider (30..59) | not_relevant (< 30).
Readiness (can it run on the data?) stays separate and is shown by the checks in Tune.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

CATALOG_DIR = Path(__file__).resolve().parents[2] / "catalog"
RECOMMENDED, CONSIDER = 60, 30
STOP = {"the", "and", "our", "for", "with", "how", "what", "want", "need", "help", "better", "more", "into",
        "about", "from", "that", "this", "are", "can", "get", "see", "know", "have", "make", "team", "teams",
        "people", "employee", "employees", "data", "report", "reports", "skill", "skills", "like", "would"}


def load(name: str) -> dict[str, Any]:
    return json.loads((CATALOG_DIR / name).read_text(encoding="utf-8"))


def words(text: str) -> set[str]:
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w
            for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) >= 2 and w not in STOP}


def phrase_in(phrase: str, text: str) -> bool:
    """Whole-word phrase match; plurals count ("job postings" matches "job posting")."""
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase.lower()) + r"(s|es)?(?![a-z0-9])", text.lower()) is not None


def goals_from_text(text: str, goals: list[dict[str, Any]]) -> dict[str, list[str]]:
    """goal id -> the analyst's words that point to it."""
    hits: dict[str, list[str]] = {}
    for g in goals:
        found = [w for w in g["words"] if phrase_in(w, text)]
        if found:
            hits[g["id"]] = found
    return hits


def recommend(text: str = "", roles: list[str] | None = None, goal_ids: list[str] | None = None) -> dict[str, Any]:
    catalog, gdef = load("people.json"), load("goals.json")
    goals = {g["id"]: g for g in gdef["goals"]}
    role_defs = {r["id"]: r for r in gdef["roles"]}
    picked = [g for g in (goal_ids or []) if g in goals]
    inferred = goals_from_text(text, gdef["goals"]) if text.strip() else {}
    text_words = words(text)
    out = []
    for s in catalog["skills"]:
        score, reasons = 0.0, []
        for gid in picked:
            if s["name"] in goals[gid]["skills"]:
                rank = goals[gid]["skills"].index(s["name"])
                score += 60 if rank == 0 else 35
                reasons.append(f"fits your goal \"{goals[gid]['label']}\"")
        for gid, found in inferred.items():
            if s["name"] in goals[gid]["skills"] and gid not in picked:
                rank = goals[gid]["skills"].index(s["name"])
                score += 60 if rank == 0 else 25
                reasons.append(f"you mentioned {', '.join(repr(w) for w in found[:2])} → {goals[gid]['label'].lower()}")
        trig = [t for t in s.get("triggers", []) + s.get("jobs_to_be_done", []) if text and phrase_in(t, text)]
        if trig:
            score += 30
            reasons.append(f"matches \"{trig[0]}\"")
        else:
            blob = words(" ".join(s.get("triggers", []) + s.get("jobs_to_be_done", []) + [s.get("business_value", "")]))
            overlap = sorted(text_words & blob)
            if len(overlap) >= 2:
                score += 10 * min(3, len(overlap))
                reasons.append("related words: " + ", ".join(overlap[:3]))
        for rid in roles or []:
            r = role_defs.get(rid)
            if r and set(r["matches"]) & set(s.get("persona", [])):
                score += 30
                reasons.append(f"built for {r['label']}")
                break
        fit = min(100, round(score))
        label = "recommended" if fit >= RECOMMENDED else "consider" if fit >= CONSIDER else "not_relevant"
        out.append({"skill": s["name"], "label": label, "fit": fit, "reasons": reasons[:3],
                    "persona": s.get("persona", []), "business_value": s.get("business_value", "")})
    out.sort(key=lambda r: (-r["fit"], r["skill"]))
    return {"text": text, "roles": roles or [], "goals": picked, "inferred_goals": sorted(inferred), "skills": out}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--text", default="")
    p.add_argument("--role", action="append", default=[])
    p.add_argument("--goal", action="append", default=[])
    a = p.parse_args()
    res = recommend(a.text, a.role, a.goal)
    a.workspace.mkdir(parents=True, exist_ok=True)
    (a.workspace / "recommendation.json").write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{k: r[k] for k in ("skill", "label", "fit", "reasons")} for r in res["skills"]], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
