# flake8: noqa
"""Catalog loader for the SQL rule catalog (``data/sql_rules.json``).

Kept dependency-light (stdlib + json only) and free of imports on the engine /
matchers so it can be imported by ``sql_ast`` / ``sql_rewrite`` to re-export the
derived id sets without creating an import cycle.
"""
from __future__ import annotations

import json
from pathlib import Path

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "sql_rules.json"


def _load() -> list[dict]:
    try:
        with open(_CATALOG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    rules = data.get("rules", data) if isinstance(data, dict) else data
    _validate(rules)
    return rules


def _validate(rules) -> None:
    """Fail fast on a malformed catalog: a bad entry would otherwise degrade to
    silent partial behavior (a rule that never fires, a missing transform, a
    severity default). Raise a clear ``ValueError`` naming the offending rule."""
    if not isinstance(rules, list):
        raise ValueError(f"sql_rules.json: expected a list of rules, got {type(rules).__name__}")
    for i, r in enumerate(rules):
        where = f"rule #{i}" + (f" ({r.get('id')})" if isinstance(r, dict) else "")
        if not isinstance(r, dict):
            raise ValueError(f"sql_rules.json: {where} is not an object")
        for key in ("id", "anchor", "severity", "find"):
            if not isinstance(r.get(key), str) or not r[key]:
                raise ValueError(f"sql_rules.json: {where} missing/invalid required string field '{key}'")
        for key, typ in (("matcher", str), ("transform", str), ("fixer_action", str)):
            if key in r and not isinstance(r[key], typ):
                raise ValueError(f"sql_rules.json: {where} field '{key}' must be a {typ.__name__}")
        if "when" in r and not isinstance(r["when"], list):
            raise ValueError(f"sql_rules.json: {where} field 'when' must be a list")
        if "mechanical" in r and not isinstance(r["mechanical"], bool):
            raise ValueError(f"sql_rules.json: {where} field 'mechanical' must be a bool")
        # A detecting rule needs a way to fire: a named matcher or declarative
        # `when`. detect:false rules (lexical/transform-only, e.g. colon_cast,
        # cache) are exempt.
        if r.get("detect", True) and "matcher" not in r and "when" not in r:
            raise ValueError(
                f"sql_rules.json: {where} is a detecting rule but has neither "
                "'matcher' nor 'when'")


RULES: list[dict] = _load()

# A rule is MECHANICAL (gate-enforced: "annotation is not a fix") only when it is
# explicitly flagged so — i.e. a SAFE, semantics-preserving deterministic rewrite
# exists. NOTE: some rules carry a best-effort `transform` but are NOT mechanical
# (window ORDER-BY synthesis isn't truly deterministic; NOT IN -> NOT EXISTS isn't
# NULL-equivalent) — those stay JUDGMENT and the gate does not enforce them.
MECHANICAL_RULE_IDS = frozenset(r["id"] for r in RULES if r.get("mechanical"))
# JUDGMENT = detected but not gate-enforced-mechanical (annotate / LLM fixer).
JUDGMENT_RULE_IDS = frozenset(
    r["id"] for r in RULES if r.get("detect", True) and not r.get("mechanical")
)
# Concrete remediation guidance per rule, for the LLM fixer's suggested_fixer_action.
SQL_FIXER_ACTIONS: dict[str, str] = {
    r["id"]: r["fixer_action"] for r in RULES if r.get("fixer_action")
}
