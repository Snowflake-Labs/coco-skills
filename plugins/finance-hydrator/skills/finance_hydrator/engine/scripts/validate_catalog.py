#!/usr/bin/env python3
"""Validate domain skill catalogs (JSON) against vendored templates. Stdlib only."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

# scripts -> engine -> <skill> ; templates live at <skill>/templates
DEFAULT_TEMPLATES_ROOT = Path(__file__).resolve().parents[2] / "templates"
KNOWN_EMITTERS = {"skill_plugin"}
# Skill names become directory names in the generated plugin.
SKILL_NAME_RE = re.compile(r"^[a-z0-9_]+$")
KNOWN_FINANCE_SUBDEPARTMENTS = {
    "fpa",
    "ap_ar",
    "controller",
    "finops",
    "treasury",
    "revenue_finance",
    "cfo",
    "procurement",
    "cogs",
    "other",
}


def resolve_subdepartments(data: dict[str, Any]) -> set[str]:
    """Allowed subdepartment values: catalog-borne enum, else the finance default."""
    declared = data.get("subdepartments")
    if isinstance(declared, list) and declared and all(isinstance(s, str) and s for s in declared):
        return set(declared)
    return set(KNOWN_FINANCE_SUBDEPARTMENTS)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def validate_string_list(value: Any, prefix: str, field: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        errors.append(f"{prefix}: {field} must be a non-empty list of strings")
        return []
    return value


def validate_catalog(path: Path, templates_root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    data = load_json(path)
    allowed_subdepartments = resolve_subdepartments(data)

    domain = data.get("domain")
    source_plugin = data.get("source_plugin")
    skills = data.get("skills")

    if not domain:
        errors.append(f"{path}: missing domain")
    if not source_plugin:
        errors.append(f"{path}: missing source_plugin")
    if not isinstance(skills, list) or not skills:
        errors.append(f"{path}: skills must be a non-empty list")
        return errors, warnings

    plugin_root = (templates_root / source_plugin).resolve()
    if source_plugin and not plugin_root.exists():
        errors.append(
            f"{path}: templates for source_plugin not vendored: "
            f"{source_plugin} (expected {plugin_root})."
        )

    names: set[str] = set()
    for idx, skill in enumerate(skills):
        prefix = f"{path}: skills[{idx}]"
        if not isinstance(skill, dict):
            errors.append(f"{prefix}: must be a mapping")
            continue
        name = skill.get("name")
        if not name:
            errors.append(f"{prefix}: missing name")
        elif not isinstance(name, str) or not SKILL_NAME_RE.match(name):
            errors.append(f"{prefix}: invalid skill name {name!r} (must match {SKILL_NAME_RE.pattern})")
        elif name in names:
            errors.append(f"{prefix}: duplicate skill name {name}")
        else:
            names.add(name)

        validate_string_list(skill.get("persona"), prefix, "persona", errors)
        validate_string_list(skill.get("jobs_to_be_done"), prefix, "jobs_to_be_done", errors)
        validate_string_list(skill.get("triggers"), prefix, "triggers", errors)
        validate_string_list(skill.get("workflow_cadence"), prefix, "workflow_cadence", errors)
        source_system_hints = skill.get("source_system_hints")
        if source_system_hints is not None:
            validate_string_list(source_system_hints, prefix, "source_system_hints", errors)

        emitters = validate_string_list(skill.get("supported_emitters"), prefix, "supported_emitters", errors)
        for emitter in emitters:
            if emitter not in KNOWN_EMITTERS:
                errors.append(f"{prefix}: unknown supported_emitter {emitter}")

        subdepartments = validate_string_list(
            skill.get("subdepartments") or skill.get("finance_subdepartments"),
            prefix,
            "subdepartments",
            errors,
        )
        for subdepartment in subdepartments:
            if subdepartment not in allowed_subdepartments:
                errors.append(f"{prefix}: unknown subdepartment {subdepartment}")

        source_dir = skill.get("source_dir")
        if not source_dir:
            errors.append(f"{prefix}: missing source_dir")
        elif plugin_root.exists() and not (plugin_root / source_dir).exists():
            errors.append(f"{prefix}: source_dir not vendored: {source_dir}")

        eval_dir = skill.get("eval_dir")
        if eval_dir and plugin_root.exists():
            eval_path = plugin_root / eval_dir
            if not eval_path.exists():
                errors.append(f"{prefix}: eval_dir not vendored: {eval_dir}")
            elif not (eval_path / "golden_cases.md").exists():
                # Non-fatal: the skill can still hydrate, but Phase 6 smoke-testing
                # will report it INCONCLUSIVE with no golden cases to run.
                warnings.append(
                    f"{prefix}: eval_dir {eval_dir} has no golden_cases.md "
                    "(smoke-test will be INCONCLUSIVE)"
                )

        data_needs = skill.get("data_needs")
        if not isinstance(data_needs, list) or not data_needs:
            errors.append(f"{prefix}: data_needs must be a non-empty list")
            continue

        context_queries = skill.get("context_queries")
        if not isinstance(context_queries, dict) or not context_queries:
            errors.append(f"{prefix}: context_queries must be a non-empty mapping")
            context_queries = {}

        need_keys: set[str] = set()
        for need_idx, need in enumerate(data_needs):
            need_prefix = f"{prefix}.data_needs[{need_idx}]"
            if not isinstance(need, dict):
                errors.append(f"{need_prefix}: must be a mapping")
                continue
            key = need.get("key")
            if not key:
                errors.append(f"{need_prefix}: missing key")
            elif key in need_keys:
                errors.append(f"{need_prefix}: duplicate key {key}")
            else:
                need_keys.add(key)
                if key not in context_queries:
                    errors.append(f"{need_prefix}: context_queries missing entry for {key}")
                elif not isinstance(context_queries.get(key), str) or not context_queries.get(key):
                    errors.append(f"{need_prefix}: context_queries.{key} must be a non-empty string")
            if "required" not in need:
                errors.append(f"{need_prefix}: missing required flag")
            if not need.get("concepts"):
                errors.append(f"{need_prefix}: missing concepts")
            if not need.get("grain"):
                errors.append(f"{need_prefix}: missing grain")
            columns = need.get("columns")
            fields = need.get("fields")
            if fields is not None:
                if not isinstance(fields, dict) or not fields:
                    errors.append(f"{need_prefix}: fields must be a non-empty mapping")
                else:
                    for field_name, spec in fields.items():
                        if not isinstance(spec, dict):
                            errors.append(f"{need_prefix}.fields.{field_name}: must be a mapping")
                            continue
                        if "required" not in spec or not isinstance(spec["required"], bool):
                            errors.append(
                                f"{need_prefix}.fields.{field_name}: required must be a boolean"
                            )
                        aliases = spec.get("aliases")
                        if aliases is not None and (
                            not isinstance(aliases, list)
                            or not all(isinstance(a, str) and a for a in aliases)
                        ):
                            errors.append(
                                f"{need_prefix}.fields.{field_name}: aliases must be a list of strings"
                            )
            elif not isinstance(columns, dict) or not columns:
                errors.append(f"{need_prefix}: columns must be a non-empty mapping (or use fields)")
            else:
                for column_name, requirement in columns.items():
                    if requirement not in {"required", "optional"}:
                        errors.append(
                            f"{need_prefix}.columns.{column_name}: must be required or optional"
                        )

        if isinstance(data_needs, list) and source_dir and plugin_root.exists():
            check_placeholders(skill, plugin_root / source_dir, prefix, errors, warnings)
    return errors, warnings


# {{token}} references in template markdown. `{{placeholders}}` / `{{cols.*}}`
# are prose, not tokens, and are not matched (`*` is outside the class).
TOKEN_RE = re.compile(r"\{\{([a-z0-9_.]+)\}\}")
PROSE_TOKENS = {"placeholders", "token"}


def defined_tokens(skill: dict[str, Any], skill_dir: Path) -> tuple[set[str], list[str], list[str]]:
    """Tokens the builder can fill, plus (flag errors, token collisions)."""
    tokens: set[str] = set(skill.get("runtime_placeholders") or [])
    problems: list[str] = []
    collisions: list[str] = []
    owners: dict[str, str] = {}
    for need in skill.get("data_needs") or []:
        if not isinstance(need, dict):
            continue
        key = need.get("key", "?")
        if need.get("affects_readiness") is False and need.get("required"):
            problems.append(f"data_need {key}: affects_readiness false on a required need")
        if need.get("placeholder"):
            tokens.add(f"{need['placeholder']}_fqn")
        for name, spec in (need.get("fields") or {}).items():
            if not isinstance(spec, dict):
                continue
            if spec.get("affects_readiness") is False and spec.get("required") \
                    and need.get("affects_readiness") is not False:
                problems.append(f"data_need {key}.fields.{name}: affects_readiness false on a required field")
            token = f"cols.{spec.get('placeholder') or name}"
            if token in owners and owners[token] != key:
                collisions.append(
                    f"{token} is produced by both {owners[token]} and {key}; "
                    "set a distinct `placeholder` on one field"
                )
            owners[token] = key
            tokens.add(token)
    tokens |= {f"settings.{name}" for name in (skill.get("settings") or {})}
    sidecar = skill_dir / "hydration_hints.json"
    if sidecar.exists():
        hints = load_json(sidecar)
        settings = dict(hints.get("extra_settings") or {})
        for need in (hints.get("data_needs") or {}).values():
            if isinstance(need, dict):
                settings.update(need.get("extra_settings") or {})
        tokens |= {f"settings.{name}" for name in settings}
    return tokens, problems, collisions


def check_placeholders(skill: dict[str, Any], skill_dir: Path, prefix: str,
                       errors: list[str], warnings: list[str]) -> None:
    """Every {{token}} a template uses must be fillable from the catalog.

    Enforced (error) once a skill opts in by declaring any need `placeholder`;
    skills not yet migrated only get a warning so other verticals keep building.
    """
    tokens, problems, collisions = defined_tokens(skill, skill_dir)
    errors.extend(f"{prefix}: {p}" for p in problems)
    opted_in = any(isinstance(n, dict) and n.get("placeholder") for n in skill.get("data_needs") or [])
    report = errors if opted_in else warnings
    report.extend(f"{prefix}: {c}" for c in collisions)
    used: dict[str, str] = {}
    for md in sorted(skill_dir.rglob("*.md")):
        for token in TOKEN_RE.findall(md.read_text(encoding="utf-8")):
            if token not in PROSE_TOKENS:
                used.setdefault(token, md.relative_to(skill_dir).as_posix())
    unknown = sorted(t for t in used if t not in tokens)
    if not unknown:
        return
    detail = ", ".join(f"{{{{{t}}}}} ({used[t]})" for t in unknown)
    if opted_in:
        errors.append(f"{prefix}: template tokens not defined in catalog: {detail}")
    else:
        warnings.append(f"{prefix}: template tokens not mapped (no need `placeholder` declared): {detail}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", action="append", type=Path, required=True)
    parser.add_argument("--templates-root", type=Path, default=DEFAULT_TEMPLATES_ROOT)
    parser.add_argument("--validate-catalog", action="store_true")
    args = parser.parse_args()

    catalogs = [path.resolve() for path in args.catalog]
    if args.validate_catalog:
        errors: list[str] = []
        warnings: list[str] = []
        for catalog in catalogs:
            cat_errors, cat_warnings = validate_catalog(catalog, args.templates_root.resolve())
            errors.extend(cat_errors)
            warnings.extend(cat_warnings)
        if errors:
            print(json.dumps({"status": "FAILED", "errors": errors, "warnings": warnings}, indent=2))
            return 1
        print(json.dumps(
            {"status": "OK", "catalogs": [str(path) for path in catalogs], "warnings": warnings},
            indent=2,
        ))
        return 0

    raise SystemExit("Pass --validate-catalog. Live readiness is produced by score_readiness.py.")


if __name__ == "__main__":
    raise SystemExit(main())
