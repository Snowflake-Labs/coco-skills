#!/usr/bin/env python3
"""Deterministic readiness scorer.

Maps an account_inventory.json + finance catalogs (JSON) to per-skill readiness
in the output_contract.md assessment shape. Pure function: no live DB, no LLM,
stdlib only.

Scoring (see references/scoring_model.md):
  readiness = 0.50*req_source + 0.30*req_column + 0.10*confidence + 0.10*optional
Hard caps:
  missing required source        -> <= 40 (BLOCKED)
  domain inaccessible            -> <= 30 (BLOCKED)
  missing unsafe scoping column  -> <= 40 (BLOCKED)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

CONFIDENCE_SCORE = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.3}

# Whole-token markers of non-production data. Matched against FQN parts split
# on "." and "_", so DEVICE / CONTEST / TEMPLATE never trip "dev"/"test"/"temp".
NON_OFFICIAL_TOKENS = {
    "dev", "demo", "test", "sandbox", "scratch", "tmp", "temp", "bkp",
    "backup", "clone", "snapshot", "archive",
}
DATED_SUFFIX = re.compile(r"_(\d{8}|\d{6})$")
# Staging/raw-layer markers. Blocked unless the user confirmed this staging
# layer is the official source (inventory object ``staging_confirmed: true``).
STAGING_TOKENS = {"prep", "raw", "stg", "staging", "landing", "bronze", "snap"}


class InputError(Exception):
    """Malformed input file; reported as a one-line error, not a traceback."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise InputError(f"{path}: file not found") from None
    except json.JSONDecodeError as exc:
        raise InputError(f"{path}: invalid JSON ({exc})") from None
    if not isinstance(data, dict):
        raise InputError(f"{path}: must contain a JSON object")
    return data


def non_official_reason(fqn: str, staging_confirmed: bool = False) -> str | None:
    """Return why an FQN looks non-production, or None if it looks official.

    Deterministic and token-based so it applies even when the inventory marks
    the object ``official: true``.
    """
    for part in fqn.replace('"', "").split("."):
        if DATED_SUFFIX.search(part):
            return f"dated snapshot suffix in {part!r}"
        for token in re.split(r"[._]", part.lower()):
            if token in NON_OFFICIAL_TOKENS:
                return f"non-official token {token!r} in {part!r}"
            if token in STAGING_TOKENS and not staging_confirmed:
                return (
                    f"staging/raw token {token!r} in {part!r} "
                    "(set staging_confirmed: true if the user confirmed it is official)"
                )
    return None


def index_inventory(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    objects = inventory.get("objects", [])
    if not isinstance(objects, list):
        raise InputError("inventory.objects must be a list")
    index: dict[str, dict[str, Any]] = {}
    for idx, obj in enumerate(objects):
        if not isinstance(obj, dict):
            raise InputError(f"inventory.objects[{idx}] must be an object")
        key = obj.get("data_need_key")
        if not key or not obj.get("fqn"):
            raise InputError(f"inventory.objects[{idx}] is missing data_need_key or fqn")
        if key in index:
            raise InputError(
                f"duplicate data_need_key {key!r} in inventory.objects "
                "(emit one object per data-need key)"
            )
        index[key] = obj
    return index


def confidence_of(obj: dict[str, Any], default: str) -> str:
    return str(obj.get("confidence") or default).upper()


def column_key(name: str) -> str:
    return str(name).replace('"', "").lower()


def normalize_fields(need: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a uniform field spec list from either the legacy `columns` map or
    the richer `fields` map.

    Legacy: ``"columns": {"period": "required", "amount": "optional"}`` — the
    canonical name is its own only alias (exact-match behavior, unchanged).
    Rich:   ``"fields": {"period": {"required": true,
                                    "aliases": ["period", "fiscal_quarter_start"],
                                    "semantic_type": "date_or_period"}}``.
    Aliases always include the canonical name so a table whose physical column
    already equals the canonical name still resolves.
    """
    fields: list[dict[str, Any]] = []
    rich = need.get("fields")
    if isinstance(rich, dict) and rich:
        for canonical, spec in rich.items():
            spec = spec or {}
            aliases = list(spec.get("aliases") or [])
            if canonical not in aliases:
                aliases = [canonical, *aliases]
            fields.append({
                "canonical": canonical,
                "required": bool(spec.get("required")),
                "aliases": aliases,
                "semantic_type": spec.get("semantic_type"),
            })
        return fields
    for canonical, requirement in (need.get("columns") or {}).items():
        fields.append({
            "canonical": canonical,
            "required": requirement == "required",
            "aliases": [canonical],
            "semantic_type": None,
        })
    return fields


def resolve_field(field: dict[str, Any], present_map: dict[str, str]) -> str | None:
    """Return the actual physical column for a field, matched by alias.

    ``present_map`` maps lowercased physical column name -> original physical
    name. The first alias that exists (case-insensitive) wins, so alias order in
    the catalog expresses preference. Alias matching is explicit and
    deterministic; it never fuzzy-guesses a column.
    """
    for alias in field["aliases"]:
        physical = present_map.get(column_key(alias))
        if physical is not None:
            return physical
    return None


def score_need(need: dict[str, Any], obj: dict[str, Any] | None) -> dict[str, Any]:
    """Return per-need coverage signals and any blocking reasons."""
    result: dict[str, Any] = {
        "key": need["key"],
        "required": bool(need.get("required")),
        "source_present": obj is not None and obj.get("official", False),
        "req_columns_ok": False,
        "optional_ratio": 0.0,
        "confidence": 0.0,
        "unsafe_missing": False,
        "binding": None,
        "block_reason": None,
        "block_kind": None,
    }
    fields = normalize_fields(need)
    required_fields = [f for f in fields if f["required"]]
    optional_fields = [f for f in fields if not f["required"]]

    reason = (
        non_official_reason(obj["fqn"], bool(obj.get("staging_confirmed")))
        if obj is not None
        else None
    )
    if obj is not None and (reason or not obj.get("official", False)):
        result["source_present"] = False
        if result["required"]:
            why = reason or "inventory marks it official: false"
            result["block_reason"] = (
                f"non-official source {obj['fqn']} for {need['key']} ({why})"
            )
            result["block_kind"] = "NON_OFFICIAL"
        return result

    if obj is None:
        if result["required"]:
            result["block_reason"] = f"missing required source for {need['key']}"
            result["block_kind"] = "MISSING_SOURCE"
        return result

    # Stored-procedure-backed sources: return schema is not describable at inventory
    # time — the skill discovers output columns at runtime via CALL. Accept the
    # procedure as fully column-resolved (MEDIUM confidence already set by discovery)
    # so the skill is not incorrectly blocked by a missing-column check.
    if obj.get("object_type") == "procedure":
        result["req_columns_ok"] = True
        result["optional_ratio"] = 1.0
        result["confidence"] = CONFIDENCE_SCORE.get(confidence_of(obj, "MEDIUM"), 0.6)
        result["binding"] = {
            "fqn": obj["fqn"],
            "confidence": confidence_of(obj, "MEDIUM"),
            "columns": {},
            "object_type": "procedure",
        }
        return result

    columns_present_raw = obj.get("columns_present") or {}
    if isinstance(columns_present_raw, dict):
        # Support canonical→physical dict (e.g. {"period": "ACCOUNTING_DATE"}).
        # Index by both the physical name and the canonical name so alias
        # matching works regardless of which inventory format was written.
        present_map: dict[str, str] = {}
        for _canonical, _physical in columns_present_raw.items():
            present_map[column_key(_physical)] = _physical
            present_map[column_key(_canonical)] = _physical
    elif isinstance(columns_present_raw, list):
        present_map = {column_key(c): c for c in columns_present_raw}
    else:
        raise InputError(f"columns_present for {obj['fqn']} must be a mapping or list")
    present = set(present_map)
    # Resolve each canonical field to a real physical column via alias matching.
    resolved: dict[str, str] = {}
    for field in fields:
        physical = resolve_field(field, present_map)
        if physical is not None:
            resolved[field["canonical"]] = physical
    have_required = [f for f in required_fields if f["canonical"] in resolved]
    result["req_columns_ok"] = len(have_required) == len(required_fields)
    if optional_fields:
        result["optional_ratio"] = len(
            [f for f in optional_fields if f["canonical"] in resolved]
        ) / len(optional_fields)
    else:
        result["optional_ratio"] = 1.0
    result["confidence"] = CONFIDENCE_SCORE.get(confidence_of(obj, "LOW"), 0.3)

    unsafe = need.get("unsafe_scoping_columns") or []
    missing_unsafe = [
        c for c in unsafe if c not in resolved and column_key(c) not in present
    ]
    if missing_unsafe:
        result["unsafe_missing"] = True
        result["block_reason"] = (
            f"missing unsafe scoping column(s) {missing_unsafe} for {need['key']}"
        )
        result["block_kind"] = "MISSING_REQUIRED_FIELD"

    # Only set required-field block_reason when unsafe is not already the cause,
    # to preserve the specific unsafe column name in the gap message.
    if result["required"] and not result["req_columns_ok"] and not result["unsafe_missing"]:
        unmapped = [f["canonical"] for f in required_fields if f["canonical"] not in resolved]
        result["block_reason"] = f"unmapped required field(s) {unmapped} for {need['key']}"
        result["block_kind"] = "MISSING_REQUIRED_FIELD"

    # Bindings carry the REAL physical column names (canonical -> physical), so
    # generated hydration_hints reference columns that actually exist.
    result["binding"] = {
        "fqn": obj["fqn"],
        "confidence": confidence_of(obj, "LOW"),
        "columns": dict(resolved),
    }
    if obj.get("object_type"):
        result["binding"]["object_type"] = obj["object_type"]
    return result


def score_skill(skill: dict[str, Any], domain: str, domain_ok: bool,
                inv_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    needs = skill.get("data_needs", [])
    per_need = [score_need(n, inv_index.get(n["key"])) for n in needs]
    required = [r for r in per_need if r["required"]]

    gaps = [r["block_reason"] for r in per_need if r["block_reason"]]
    bindings = {r["key"]: r["binding"] for r in per_need if r["binding"]}
    block_kinds = sorted({r["block_kind"] for r in per_need if r.get("block_kind")})

    # Coverage components over REQUIRED needs.
    n_req = len(required) or 1
    req_source = sum(1 for r in required if r["source_present"]) / n_req
    req_column = sum(1 for r in required if r["req_columns_ok"]) / n_req
    confidence = (sum(r["confidence"] for r in required) / n_req)
    optional = (sum(r["optional_ratio"] for r in per_need) / (len(per_need) or 1))

    readiness = round(100 * (0.50 * req_source + 0.30 * req_column
                             + 0.10 * confidence + 0.10 * optional))

    blocked = any(r["block_reason"] for r in required) or any(
        r["unsafe_missing"] for r in per_need
    )
    if not domain_ok:
        readiness = min(readiness, 30)
        blocked = True
        gaps.append(f"domain {domain} not accessible")
        block_kinds = sorted(set(block_kinds) | {"DOMAIN_INACCESSIBLE"})
    if any((not r["source_present"]) for r in required) or any(
        r["unsafe_missing"] for r in per_need
    ):
        readiness = min(readiness, 40)
    if any(r["required"] and not r["req_columns_ok"] for r in per_need):
        readiness = min(readiness, 40)

    if blocked:
        status = "BLOCKED"
    elif req_source == 1.0 and req_column == 1.0 and optional >= 0.999 and confidence >= 0.6:
        status = "READY"
    elif req_source == 1.0 and req_column == 1.0:
        status = "PARTIAL"
    else:
        status = "BLOCKED"

    # Keep the READY/PARTIAL bands visually distinct: a PARTIAL skill must never
    # display a near-READY score (e.g. 91%). Status is primary; percent secondary.
    if status == "PARTIAL":
        readiness = min(readiness, 74)

    recommendation = {"READY": "include", "PARTIAL": "ask", "BLOCKED": "exclude"}[status]
    if status == "READY":
        reason = "All required sources and columns resolved from account inventory."
    elif status == "PARTIAL":
        reason = "Required data resolved; optional enrichment or confidence is degraded."
    else:
        reason = "; ".join(dict.fromkeys(gaps)) or "Required data not available."

    return {
        "domain": domain,
        "skill": skill["name"],
        "readiness": readiness,
        "status": status,
        "recommendation": recommendation,
        "reason": reason,
        "bindings": bindings,
        "gaps": sorted(set(g for g in gaps if g)),
        "block_kinds": block_kinds if status == "BLOCKED" else [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--catalog", action="append", type=Path, required=True)
    args = parser.parse_args()

    try:
        inventory = load_json(args.inventory.resolve())
        inv_index = index_inventory(inventory)
        domain_access = inventory.get("domain_access") or {}
        if not isinstance(domain_access, dict):
            raise InputError("inventory.domain_access must be an object")

        skills_out: list[dict[str, Any]] = []
        for cat_path in args.catalog:
            catalog = load_json(cat_path.resolve())
            domain = catalog.get("domain")
            if not domain:
                raise InputError(f"{cat_path}: missing domain")
            domain_ok = domain_access.get(domain, True)
            for skill in catalog.get("skills", []):
                skills_out.append(score_skill(skill, domain, domain_ok, inv_index))
    except InputError as exc:
        print(f"score_readiness.py: error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({"skills": skills_out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
