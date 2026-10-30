#!/usr/bin/env python3
"""Static validation for generated customer finance plugins. Stdlib only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _minyaml


def has_placeholder(value: object) -> bool:
    return isinstance(value, str) and ("<" in value or "YOUR" in value)


def validate_hints(path: Path) -> list[str]:
    errors: list[str] = []
    data = _minyaml.load(path.read_text(encoding="utf-8")) or {}
    for key, need in (data.get("data_needs") or {}).items():
        if need.get("required") not in {"yes", True}:
            continue
        for idx, source in enumerate(need.get("sources") or []):
            fqn = source.get("fqn")
            if not fqn or has_placeholder(fqn):
                errors.append(f"{path}: required data_need {key} source {idx} has unresolved fqn")
            for column_key, column_name in (source.get("columns") or {}).items():
                if has_placeholder(column_name):
                    errors.append(
                        f"{path}: required data_need {key} column {column_key} is unresolved"
                    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-dir", type=Path, required=True)
    parser.add_argument("--metadata-only", action="store_true")
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
                    continue
                errors.append(
                    f"generated skill {skill_path.name} has no hydration_hints.yaml "
                    "(no resolved bindings). Pass --doc-only to exempt intentionally."
                )
                continue
            errors.extend(validate_hints(hints))

    status = "FAILED" if errors else "OK"
    print(json.dumps({"status": status, "errors": errors}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
