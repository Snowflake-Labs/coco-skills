#!/usr/bin/env python3
"""Static validation for generated customer finance plugins. Stdlib only.

Per skill, every bound source in ``data_needs`` (required and optional) and all
``extra_settings`` must be free of placeholders and empty strings, and every
FQN must have three dot-separated parts. Entries under the top-level
``unresolved:`` block are expected open items and are reported as warnings.

Statuses: READY (clean), PARTIAL (only warnings / unresolved items), BLOCKED
(any error). With ``--inventory`` (normally alongside ``--metadata-only``) the
bound FQNs and columns are cross-checked against ``account_inventory.json``;
no SQL is ever run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import _minyaml
from build_customer_plugin import has_placeholder

STATUS_RANK = {"READY": 0, "PARTIAL": 1, "BLOCKED": 2}


def fqn_parts(fqn: str) -> list[str]:
    """Split an FQN on dots that are outside double-quoted identifiers."""
    parts, buf, quoted = [], "", False
    for ch in fqn:
        if ch == '"':
            quoted = not quoted
        if ch == "." and not quoted:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return parts


def norm_ident(name: str) -> str:
    return str(name).replace('"', "").strip().upper()


def norm_fqn(fqn: str) -> str:
    return ".".join(norm_ident(p) for p in fqn_parts(fqn))


def walk_strings(value: Any, path: str):
    """Yield (path, string) for every string leaf in a nested value."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk_strings(child, f"{path}.{key}")
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            yield from walk_strings(child, f"{path}[{idx}]")
    elif isinstance(value, str):
        yield path, value


def load_inventory(path: Path) -> dict[str, dict[str, Any]]:
    """Index inventory objects by normalized FQN -> {columns, object_type}."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("objects", []), list):
        raise ValueError("inventory must be an object with an objects list")
    index: dict[str, dict[str, Any]] = {}
    for obj in data.get("objects", []):
        if not isinstance(obj, dict) or not obj.get("fqn"):
            continue
        entry = index.setdefault(
            norm_fqn(obj["fqn"]),
            {"columns": set(), "mapping": {}, "object_type": obj.get("object_type")},
        )
        present = obj.get("columns_present") or {}
        names = present.values() if isinstance(present, dict) else present
        entry["columns"].update(norm_ident(c) for c in names)
        if isinstance(present, dict):
            for canonical, physical in present.items():
                if isinstance(physical, str):
                    entry["mapping"][norm_ident(canonical)] = norm_ident(physical)
    return index


def validate_hints(
    path: Path, inventory: dict[str, dict[str, Any]] | None
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        data = _minyaml.load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # malformed file: report it, never a traceback
        return [f"{path}: cannot parse hydration hints ({exc})"], []
    if not isinstance(data, dict):
        return [f"{path}: hydration hints must be a mapping"], []

    needs = data.get("data_needs") or {}
    if not isinstance(needs, dict):
        return [f"{path}: data_needs must be a mapping"], []
    for key, need in needs.items():
        if not isinstance(need, dict):
            errors.append(f"{path}: data_need {key} must be a mapping")
            continue
        label = "required" if need.get("required") in {"yes", True} else "optional"
        sources = need.get("sources") or []
        if not sources:
            errors.append(f"{path}: {label} data_need {key} has no bound source")
        for where, text in walk_strings(need, f"data_needs.{key}"):
            if not text.strip():
                errors.append(f"{path}: {where} is an empty string")
            elif has_placeholder(text):
                errors.append(f"{path}: {where} is an unresolved placeholder ({text})")
        for idx, source in enumerate(sources):
            if not isinstance(source, dict):
                errors.append(f"{path}: data_need {key} source {idx} must be a mapping")
                continue
            fqn = source.get("fqn")
            if not isinstance(fqn, str) or not fqn.strip():
                errors.append(f"{path}: {label} data_need {key} source {idx} has no fqn")
                continue
            parts = fqn_parts(fqn)
            if len(parts) != 3 or not all(p.strip() for p in parts):
                errors.append(
                    f"{path}: data_need {key} source {idx} fqn {fqn!r} is not DATABASE.SCHEMA.OBJECT"
                )
                continue
            if inventory is None or has_placeholder(fqn):
                continue
            found = inventory.get(norm_fqn(fqn))
            if found is None:
                errors.append(f"{path}: data_need {key} fqn {fqn} not found in inventory")
                continue
            if found.get("object_type") == "procedure" or source.get("object_type") == "procedure":
                continue
            for canonical, physical in (source.get("columns") or {}).items():
                if isinstance(physical, str) and physical and not has_placeholder(physical):
                    if norm_ident(physical) not in found["columns"]:
                        errors.append(
                            f"{path}: data_need {key} column {canonical}={physical} "
                            f"not in inventory columns for {fqn}"
                        )
                        continue
                    mapped = found["mapping"].get(norm_ident(canonical))
                    if mapped is not None and mapped != norm_ident(physical):
                        errors.append(
                            f"{path}: data_need {key} column {canonical}={physical} "
                            f"disagrees with inventory mapping {canonical}->{mapped} for {fqn}"
                        )

    for where, text in walk_strings(data.get("extra_settings") or {}, "extra_settings"):
        if not text.strip():
            errors.append(f"{path}: {where} is an empty string")
        elif has_placeholder(text):
            errors.append(f"{path}: {where} is an unresolved placeholder ({text})")

    unresolved = data.get("unresolved") or {}
    if isinstance(unresolved, dict):
        for key in (unresolved.get("data_needs") or {}):
            warnings.append(f"{path}: optional data_need {key} is unresolved")
        for key, cols in (unresolved.get("columns") or {}).items():
            warnings.append(f"{path}: data_need {key} has unresolved optional columns {cols}")
        for where, text in walk_strings(unresolved.get("extra_settings") or {}, "extra_settings"):
            warnings.append(f"{path}: unresolved setting {where} ({text})")
    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-dir", type=Path, required=True)
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument(
        "--inventory",
        type=Path,
        help="account_inventory.json to cross-check bound FQNs/columns against (no SQL).",
    )
    parser.add_argument(
        "--doc-only",
        action="append",
        default=[],
        help="Skill name intentionally shipped without bindings (documentation-only). "
        "Repeat per skill. Exempts it from the missing-hints check.",
    )
    args = parser.parse_args()

    plugin_dir = args.plugin_dir.resolve()
    doc_only = set(args.doc_only)
    errors: list[str] = []
    warnings: list[str] = []
    skills: list[dict[str, Any]] = []

    inventory = None
    if args.inventory:
        try:
            inventory = load_inventory(args.inventory.resolve())
        except (OSError, ValueError) as exc:
            errors.append(f"cannot read inventory {args.inventory}: {exc}")

    if not (plugin_dir / ".cortex-plugin" / "plugin.json").exists():
        errors.append("missing .cortex-plugin/plugin.json")
    skills_dir = plugin_dir / "skills"
    if not skills_dir.exists():
        errors.append("missing skills directory")
    else:
        # Fail-closed: every generated skill must have resolved bindings unless it
        # is explicitly declared documentation-only. A silently-missing hints file
        # means the skill shipped with nothing to bind to.
        for skill_path in sorted(p for p in skills_dir.glob("*") if p.is_dir()):
            hints = skill_path / "hydration_hints.yaml"
            if not hints.exists():
                if skill_path.name in doc_only:
                    skills.append({"skill": skill_path.name, "status": "READY",
                                   "errors": [], "warnings": ["documentation-only"]})
                    continue
                skill_errors = [
                    f"generated skill {skill_path.name} has no hydration_hints.yaml "
                    "(no resolved bindings). Pass --doc-only to exempt intentionally."
                ]
                skill_warnings: list[str] = []
            else:
                skill_errors, skill_warnings = validate_hints(hints, inventory)
            status = "BLOCKED" if skill_errors else ("PARTIAL" if skill_warnings else "READY")
            skills.append({"skill": skill_path.name, "status": status,
                           "errors": skill_errors, "warnings": skill_warnings})
            errors.extend(skill_errors)
            warnings.extend(skill_warnings)

    overall = max((s["status"] for s in skills), key=STATUS_RANK.get, default="READY")
    if errors and overall != "BLOCKED":
        overall = "BLOCKED"
    print(json.dumps(
        {"status": overall, "skills": skills, "errors": errors, "warnings": warnings},
        indent=2,
    ))
    return 1 if overall == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
