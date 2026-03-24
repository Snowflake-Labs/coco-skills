#!/usr/bin/env python3
"""Validate skill submissions against the cortex-code-skills repo requirements.

Checks each modified or added skill directory under skills/ for:
  - Required SKILL.md with valid frontmatter
  - Required frontmatter fields
  - Folder name matching the id field
  - LICENSE file using an approved license
"""

import os
import re
import subprocess
import sys
from pathlib import Path

REQUIRED_FIELDS = ["name", "description"]
RECOMMENDED_FIELDS = ["id", "authors", "type", "status", "categories"]
VALID_TYPES = ["community", "snowflake"]
VALID_STATUSES = ["stable", "beta", "draft"]

APACHE_MARKER = "apache license"
SNOWFLAKE_MARKER = "snowflake skills license"


def get_changed_skill_dirs():
    """Return skill directories touched in this PR relative to origin/main."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        capture_output=True,
        text=True,
    )
    changed_files = result.stdout.strip().splitlines()
    skill_dirs = set()
    for f in changed_files:
        parts = Path(f).parts
        if len(parts) >= 2 and parts[0] == "skills" and parts[1] != "placeholder":
            skill_dirs.add(Path("skills") / parts[1])
    return sorted(skill_dirs)


def parse_frontmatter(content):
    """Extract and parse YAML frontmatter from a markdown file."""
    if not content.startswith("---"):
        return None, "SKILL.md does not start with a frontmatter block (---)"
    closing = content.find("\n---", 3)
    if closing == -1:
        return None, "Frontmatter block is not closed with ---"
    raw_yaml = content[3:closing].strip()
    try:
        import json
        # Simple key: value YAML parser (avoids requiring PyYAML)
        frontmatter = {}
        for line in raw_yaml.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, val = line.partition(":")
                frontmatter[key.strip()] = val.strip().strip('"').strip("'")
        return frontmatter, None
    except Exception as e:
        return None, f"Could not parse frontmatter: {e}"


def validate_skill(skill_dir):
    errors = []
    warnings = []

    skill_md = skill_dir / "SKILL.md"
    license_file = skill_dir / "LICENSE"

    # SKILL.md
    if not skill_md.exists():
        errors.append("Missing SKILL.md")
    else:
        content = skill_md.read_text(encoding="utf-8")
        frontmatter, parse_error = parse_frontmatter(content)
        if parse_error:
            errors.append(parse_error)
        else:
            # Required fields
            for field in REQUIRED_FIELDS:
                if not frontmatter.get(field):
                    errors.append(f"Missing required frontmatter field: '{field}'")

            # Recommended fields
            for field in RECOMMENDED_FIELDS:
                if not frontmatter.get(field):
                    warnings.append(f"Missing recommended frontmatter field: '{field}'")

            # id must match folder name
            fm_id = frontmatter.get("id")
            if fm_id and fm_id != skill_dir.name:
                errors.append(
                    f"Frontmatter 'id' ({fm_id!r}) does not match folder name ({skill_dir.name!r})"
                )

            # id must be lowercase with hyphens only
            if fm_id and not re.match(r"^[a-z0-9-]+$", fm_id):
                errors.append(
                    f"Frontmatter 'id' must be lowercase letters, numbers, and hyphens only (got {fm_id!r})"
                )

            # type must be a valid value
            fm_type = frontmatter.get("type")
            if fm_type and fm_type not in VALID_TYPES:
                errors.append(
                    f"Frontmatter 'type' must be one of {VALID_TYPES} (got {fm_type!r})"
                )

            # status must be a valid value
            fm_status = frontmatter.get("status")
            if fm_status and fm_status not in VALID_STATUSES:
                warnings.append(
                    f"Frontmatter 'status' should be one of {VALID_STATUSES} (got {fm_status!r})"
                )

    # LICENSE
    if not license_file.exists():
        errors.append("Missing LICENSE file")
    else:
        license_content = license_file.read_text(encoding="utf-8").lower()
        if APACHE_MARKER not in license_content and SNOWFLAKE_MARKER not in license_content:
            errors.append(
                "LICENSE must be either Apache 2.0 (community skills) or the Snowflake Skills License (Snowflake employee skills)"
            )

        # Snowflake email authors must use the Snowflake Skills License
        fm_authors = frontmatter.get("authors", "") if frontmatter else ""
        has_snowflake_email = bool(re.search(r"@snowflake\.com", fm_authors, re.IGNORECASE))
        if has_snowflake_email and SNOWFLAKE_MARKER not in license_content:
            errors.append(
                "Authors with a @snowflake.com email must use the Snowflake Skills License, not Apache 2.0"
            )

    return errors, warnings


def main():
    skill_dirs = get_changed_skill_dirs()

    if not skill_dirs:
        print("No skill directories changed. Skipping validation.")
        sys.exit(0)

    all_passed = True

    for skill_dir in skill_dirs:
        print(f"\n--- Validating: {skill_dir} ---")
        errors, warnings = validate_skill(skill_dir)

        for w in warnings:
            print(f"  WARNING: {w}")
        for e in errors:
            print(f"  ERROR:   {e}")

        if not errors and not warnings:
            print("  All checks passed.")
        elif not errors:
            print("  Passed with warnings.")
        else:
            print("  FAILED.")
            all_passed = False

    print()
    if all_passed:
        print("Skill validation passed.")
        sys.exit(0)
    else:
        print("Skill validation failed. Please fix the errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
