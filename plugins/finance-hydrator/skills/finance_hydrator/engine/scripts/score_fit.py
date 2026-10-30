#!/usr/bin/env python3
"""Deterministic business-fit scorer for hydrator catalogs. Stdlib only.

This complements score_readiness.py. Readiness answers "can this skill bind to
the customer's data?" Business fit answers "should this skill be recommended for
this user's finance role and workflow?" Keep the two scores separate so data
safety can remain fail-closed.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

WEIGHTS = {
    "persona": 0.30,
    "jobs_to_be_done": 0.30,
    "subdepartment": 0.20,
    "source_systems": 0.10,
    "triggers": 0.10,
}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def tokens(values: list[str]) -> set[str]:
    result: set[str] = set()
    for value in values:
        result.update(t for t in re.split(r"[^a-z0-9]+", value.lower()) if len(t) >= 2)
    return result


def phrase_match(profile_values: list[str], catalog_values: list[str]) -> float:
    """Return 0..1 based on phrase containment and token overlap."""
    if not profile_values or not catalog_values:
        return 0.0
    profile_lower = [v.lower() for v in profile_values]
    catalog_lower = [v.lower() for v in catalog_values]
    for profile in profile_lower:
        for catalog in catalog_lower:
            if profile and catalog and (profile in catalog or catalog in profile):
                return 1.0
    p_tokens = tokens(profile_values)
    c_tokens = tokens(catalog_values)
    if not p_tokens or not c_tokens:
        return 0.0
    overlap = len(p_tokens & c_tokens)
    return min(1.0, overlap / max(1, min(len(p_tokens), len(c_tokens))))


def score_skill(skill: dict[str, Any], domain: str, profile: dict[str, Any]) -> dict[str, Any]:
    components = {
        "persona": phrase_match(as_list(profile.get("persona")), as_list(skill.get("persona"))),
        "jobs_to_be_done": phrase_match(
            as_list(profile.get("jobs_to_be_done")), as_list(skill.get("jobs_to_be_done"))
        ),
        "subdepartment": phrase_match(
            as_list(profile.get("subdepartment") or profile.get("finance_subdepartment")),
            as_list(skill.get("subdepartments") or skill.get("finance_subdepartments")),
        ),
        "source_systems": phrase_match(
            as_list(profile.get("source_systems")), as_list(skill.get("source_system_hints"))
        ),
        "triggers": phrase_match(as_list(profile.get("jobs_to_be_done")), as_list(skill.get("triggers"))),
    }
    business_fit = round(100 * sum(WEIGHTS[key] * components[key] for key in WEIGHTS))
    if business_fit >= 70:
        fit_status = "HIGH"
    elif business_fit >= 40:
        fit_status = "MEDIUM"
    else:
        fit_status = "LOW"

    reasons = []
    if components["subdepartment"] > 0:
        reasons.append("matches subdepartment")
    if components["persona"] > 0:
        reasons.append("matches persona")
    if components["jobs_to_be_done"] > 0:
        reasons.append("matches job-to-be-done")
    if components["source_systems"] > 0:
        reasons.append("matches source-system hints")
    if components["triggers"] > 0:
        reasons.append("matches trigger language")
    if not reasons:
        reasons.append("no strong profile match")

    return {
        "domain": domain,
        "skill": skill["name"],
        "business_fit": business_fit,
        "fit_status": fit_status,
        "fit_reasons": reasons,
        "fit_components": {key: round(value, 3) for key, value in components.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--catalog", action="append", type=Path, required=True)
    args = parser.parse_args()

    profile_data = load_json(args.profile.resolve())
    profile = profile_data.get("profile", profile_data)
    skills_out = []
    for cat_path in args.catalog:
        catalog = load_json(cat_path.resolve())
        domain = catalog["domain"]
        for skill in catalog.get("skills", []):
            skills_out.append(score_skill(skill, domain, profile))

    print(json.dumps({"skills": skills_out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())