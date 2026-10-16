#!/usr/bin/env python3
"""Validate skill submissions against the coco-skills repo requirements.

Checks each changed skill directory under skills/ for SKILL.md with parseable
frontmatter, required fields, folder name matching the skill's name, a licence
matching the declared type, no name collision with a repo or bundled skill, and
body length.

Canonical frontmatter schema is CONTRIBUTING.md's (`name`, `author`,
`status: Published`). The older `id` / `authors` keys are accepted as deprecated
aliases so existing skills keep passing; they warn rather than error.

Pre-existing errors sit in a baseline, so this check can be required without a
cleanup first. New errors fail; baselined ones report as debt.

Usage:
  validate_skill.py                        # skills changed vs origin/main
  validate_skill.py --all                  # every skill
  validate_skill.py --json PATH            # write findings as JSON
  validate_skill.py --all --write-baseline # grandfather current errors
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install pyyaml")
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"
BUNDLED_LIST = Path(__file__).resolve().parent / "bundled_skills.txt"
BASELINE_PATH = REPO_ROOT / ".github" / "validator-baseline.json"

# Fields whose absence breaks catalog rendering, licensing, or invocation.
REQUIRED_FIELDS = ["name", "description", "type", "title", "summary", "author"]

# Fields that should be present but do not block a merge.
RECOMMENDED_FIELDS = ["prompt", "language", "status", "tools"]

# Deprecated key -> canonical key.
DEPRECATED_ALIASES = {"id": "name", "authors": "author"}

VALID_TYPES = ["community", "snowflake", "partner"]

# Canonical statuses per CONTRIBUTING.md, plus legacy values still in the repo.
VALID_STATUSES = ["Published", "Archived", "Hidden"]
LEGACY_STATUSES = ["stable", "beta", "draft"]

APACHE_MARKER = "apache license"
SNOWFLAKE_MARKER = "snowflake skills license"

# Types that must ship the Snowflake Skills License rather than Apache 2.0.
SNOWFLAKE_LICENSED_TYPES = ["snowflake"]

MAX_TITLE_CHARS = 30
MAX_SUMMARY_CHARS = 140
MAX_BODY_WORDS = 500

SKIP_DIRS = {"placeholder"}


def load_bundled_skill_names():
    """Names shipped with Cortex Code. A repo skill reusing one silently shadows it."""
    if not BUNDLED_LIST.exists():
        return set()
    names = set()
    for line in BUNDLED_LIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.add(line)
    return names


def get_changed_skill_dirs():
    """Skill directories touched in this PR, relative to origin/main."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        print("ERROR: could not diff against origin/main. Is the ref fetched?")
        print(result.stderr.strip())
        sys.exit(1)

    skill_dirs = set()
    for f in result.stdout.strip().splitlines():
        parts = Path(f).parts
        if len(parts) >= 2 and parts[0] == "skills" and parts[1] not in SKIP_DIRS:
            candidate = REPO_ROOT / "skills" / parts[1]
            # A deleted skill has no directory left to validate.
            if candidate.is_dir():
                skill_dirs.add(candidate)
    return sorted(skill_dirs)


def get_all_skill_dirs():
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(
        d for d in SKILLS_DIR.iterdir() if d.is_dir() and d.name not in SKIP_DIRS
    )


def parse_frontmatter(content):
    """Return (mapping, error). Uses a real YAML parser.

    The previous implementation split each line on the first ':', which
    truncated any description containing a colon and read block scalars
    (`description: |`) as the literal string '|'.
    """
    if not content.startswith("---"):
        return None, "SKILL.md does not start with a frontmatter block (---)"

    # Closing delimiter must be a line of its own, so '---' inside a value
    # or a horizontal rule in the body does not terminate the block early.
    match = re.search(r"^---\s*$", content[3:], re.MULTILINE)
    if not match:
        return None, "Frontmatter block is not closed with ---"

    raw_yaml = content[3 : 3 + match.start()]
    try:
        frontmatter = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as e:
        detail = str(e).replace("\n", " ")[:200]
        hint = ""
        # By far the most common cause: an unquoted value containing ': ',
        # e.g. `description: Use when: the user asks...`. The previous
        # line-splitting parser accepted this and silently truncated the value.
        if "mapping values are not allowed" in str(e):
            hint = (
                " Hint: a value containing a colon must be quoted, "
                'e.g. description: "Use when: ..." -- or written as a '
                "block scalar with `description: |`."
            )
        return None, f"Frontmatter is not valid YAML: {detail}{hint}"

    if frontmatter is None:
        return None, "Frontmatter block is empty"
    if not isinstance(frontmatter, dict):
        return None, "Frontmatter must be a mapping of key: value pairs"

    return frontmatter, None


def body_word_count(content):
    match = re.search(r"^---\s*$", content[3:], re.MULTILINE)
    if not match:
        return 0
    return len(content[3 + match.end() :].split())


def is_safe_to_read(path):
    """Reject anything that could read outside the repo.

    A pull request can add a symlink, and Path.exists()/is_file() follow them,
    so `skills/x/SKILL.md -> /etc/passwd` would otherwise be read and echoed
    into a public CI log.
    """
    try:
        if path.is_symlink():
            return False
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return resolved.is_relative_to(REPO_ROOT.resolve())


def read_text_safely(path, max_bytes=1_000_000):
    """Read a contributor-supplied file, or return None if it is not safe to."""
    if not is_safe_to_read(path):
        return None
    try:
        if path.stat().st_size > max_bytes:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def find_license_file(skill_dir):
    """Locate a license file.

    Accepts descriptive filenames as well as plain LICENSE, because at least
    one skill ships 'Snowflake Skills License [FINAL].txt'. A non-standard
    name is a warning, not an error.
    """
    try:
        entries = [p for p in skill_dir.iterdir() if is_safe_to_read(p)]
    except OSError:
        return None, False

    exact = [p for p in entries if p.name in ("LICENSE", "LICENSE.txt", "LICENSE.md")]
    if exact:
        return exact[0], True

    fuzzy = [p for p in entries if "licen" in p.name.lower()]
    if fuzzy:
        return fuzzy[0], False

    return None, False


def canonical_field(frontmatter, key, warnings):
    """Read a field, falling back to its deprecated alias."""
    if frontmatter.get(key):
        return frontmatter[key]
    for old, new in DEPRECATED_ALIASES.items():
        if new == key and frontmatter.get(old):
            warnings.append(
                f"Frontmatter key '{old}' is deprecated; rename it to '{key}'"
            )
            return frontmatter[old]
    return None


def as_text(value):
    """Frontmatter values may be scalars or lists; normalize for text checks."""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value) if value is not None else ""


def validate_skill(skill_dir, sibling_names=None, bundled_names=None):
    errors = []
    warnings = []
    frontmatter = None

    skill_md = skill_dir / "SKILL.md"

    if not skill_md.exists():
        errors.append("Missing SKILL.md")
    else:
        content = read_text_safely(skill_md)
        if content is None:
            errors.append(
                "SKILL.md could not be read safely. It must be a regular file "
                "inside the repo -- symlinks are rejected."
            )
            frontmatter, parse_error = None, None
        else:
            frontmatter, parse_error = parse_frontmatter(content)

        if parse_error:
            errors.append(parse_error)
        elif frontmatter is not None:
            for field in REQUIRED_FIELDS:
                if not canonical_field(frontmatter, field, warnings):
                    errors.append(f"Missing required frontmatter field: '{field}'")

            for field in RECOMMENDED_FIELDS:
                if not frontmatter.get(field):
                    warnings.append(
                        f"Missing recommended frontmatter field: '{field}'"
                    )

            fm_name = canonical_field(frontmatter, "name", warnings)
            if fm_name:
                if fm_name != skill_dir.name:
                    errors.append(
                        f"Frontmatter 'name' ({fm_name!r}) does not match folder name "
                        f"({skill_dir.name!r})"
                    )
                if not re.match(r"^[a-z0-9-]+$", str(fm_name)):
                    errors.append(
                        f"Frontmatter 'name' must be lowercase letters, numbers, and "
                        f"hyphens only (got {fm_name!r})"
                    )
                if bundled_names and fm_name in bundled_names:
                    errors.append(
                        f"Name {fm_name!r} collides with a bundled Cortex Code skill. "
                        f"A repo skill silently shadows the bundled one -- rename it."
                    )
                if sibling_names and fm_name in sibling_names:
                    errors.append(
                        f"Name {fm_name!r} is already used by another skill in this repo"
                    )

            fm_type = frontmatter.get("type")
            if fm_type and fm_type not in VALID_TYPES:
                errors.append(
                    f"Frontmatter 'type' must be one of {VALID_TYPES} (got {fm_type!r})"
                )

            fm_status = frontmatter.get("status")
            if fm_status and fm_status not in VALID_STATUSES:
                if fm_status in LEGACY_STATUSES:
                    warnings.append(
                        f"Frontmatter 'status' value {fm_status!r} is deprecated; "
                        f"use one of {VALID_STATUSES}"
                    )
                else:
                    warnings.append(
                        f"Frontmatter 'status' should be one of {VALID_STATUSES} "
                        f"(got {fm_status!r})"
                    )

            fm_title = frontmatter.get("title")
            if fm_title and len(str(fm_title)) > MAX_TITLE_CHARS:
                warnings.append(
                    f"'title' is {len(str(fm_title))} characters; "
                    f"keep it under {MAX_TITLE_CHARS}"
                )

            fm_summary = frontmatter.get("summary")
            if fm_summary and len(str(fm_summary)) > MAX_SUMMARY_CHARS:
                warnings.append(
                    f"'summary' is {len(str(fm_summary))} characters; "
                    f"keep it under {MAX_SUMMARY_CHARS}"
                )

            words = body_word_count(content)
            if words > MAX_BODY_WORDS:
                warnings.append(
                    f"SKILL.md body is {words} words; aim for under {MAX_BODY_WORDS} "
                    f"and move reference material into references/"
                )

    license_file, standard_name = find_license_file(skill_dir)

    if license_file is None:
        errors.append("Missing LICENSE file")
    else:
        if not standard_name:
            warnings.append(
                f"License file is named {license_file.name!r}; rename it to 'LICENSE'"
            )

        license_content = (read_text_safely(license_file) or "").lower()
        is_apache = APACHE_MARKER in license_content
        is_snowflake = SNOWFLAKE_MARKER in license_content

        if not is_apache and not is_snowflake:
            errors.append(
                "LICENSE must be either Apache 2.0 (community and partner skills) "
                "or the Snowflake Skills License (Snowflake employee skills)"
            )
        elif frontmatter:
            fm_type = frontmatter.get("type")
            if fm_type in SNOWFLAKE_LICENSED_TYPES and not is_snowflake:
                errors.append(
                    f"type: {fm_type} requires the Snowflake Skills License, not Apache 2.0"
                )
            if fm_type in ("community", "partner") and not is_apache:
                errors.append(
                    f"type: {fm_type} requires Apache 2.0, not the Snowflake Skills License"
                )

            author_text = as_text(canonical_field(frontmatter, "author", []))
            if re.search(r"@snowflake\.com", author_text, re.IGNORECASE) and not is_snowflake:
                errors.append(
                    "Authors with a @snowflake.com email must use the Snowflake "
                    "Skills License, not Apache 2.0"
                )

    return errors, warnings


def build_sibling_name_map(target_dirs):
    """Map skill name -> folder, for every skill NOT under validation.

    Lets a collision be reported against skills the PR did not touch.
    """
    names = {}
    targets = {d.resolve() for d in target_dirs}
    for d in get_all_skill_dirs():
        if d.resolve() in targets:
            continue
        skill_md = d / "SKILL.md"
        if not skill_md.exists():
            continue
        content = read_text_safely(skill_md)
        if content is None:
            continue
        fm, err = parse_frontmatter(content)
        if err or not fm:
            continue
        name = fm.get("name") or fm.get("id")
        if name:
            names[name] = d.name
    return names


def load_validator_baseline():
    """Errors already present in the repo, accepted so this check can be required."""
    if not BASELINE_PATH.exists():
        return set()
    try:
        data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"WARNING: could not read baseline at {BASELINE_PATH}; treating as empty.")
        return set()
    return set(data.get("accepted", []))


def main():
    parser = argparse.ArgumentParser(description="Validate skill submissions.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="validate every skill, not just those changed in this PR",
    )
    parser.add_argument("--json", metavar="PATH", help="write findings to a JSON file")
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="record current errors as accepted debt and exit 0",
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="ignore the baseline and report every error (for auditing debt)",
    )
    args = parser.parse_args()

    skill_dirs = get_all_skill_dirs() if args.all else get_changed_skill_dirs()

    if not skill_dirs:
        print("No skill directories changed. Skipping validation.")
        sys.exit(0)

    bundled_names = load_bundled_skill_names()
    sibling_names = build_sibling_name_map(skill_dirs)

    raw = {}
    for skill_dir in skill_dirs:
        rel = str(skill_dir.relative_to(REPO_ROOT))
        raw[rel] = validate_skill(skill_dir, sibling_names, bundled_names)

    if args.write_baseline:
        if not args.all:
            print("ERROR: --write-baseline requires --all, so the baseline is complete.")
            sys.exit(2)
        accepted = sorted(
            f"{rel}|{e}" for rel, (errors, _) in raw.items() for e in errors
        )
        BASELINE_PATH.write_text(
            json.dumps(
                {
                    "_comment": (
                        "Pre-existing validation errors, grandfathered so the check "
                        "can be required without blocking on cleanup. Each entry is "
                        "'<skill path>|<error text>'. Remove entries as they are "
                        "fixed; never add by hand. Regenerate with: "
                        "python3 .github/scripts/validate_skill.py --all --write-baseline"
                    ),
                    "accepted": accepted,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {len(accepted)} grandfathered error(s) to {BASELINE_PATH}.")
        sys.exit(0)

    baseline = set() if args.no_baseline else load_validator_baseline()

    findings = {}
    all_passed = True
    grandfathered = 0

    for rel, (errors, warnings) in raw.items():
        fresh = []
        for e in errors:
            if f"{rel}|{e}" in baseline:
                grandfathered += 1
                warnings.append(f"[grandfathered] {e}")
            else:
                fresh.append(e)

        print(f"\n--- Validating: {rel} ---")
        findings[rel] = {"errors": fresh, "warnings": warnings}

        for w in warnings:
            print(f"  WARNING: {w}")
        for e in fresh:
            print(f"  ERROR:   {e}")

        if not fresh and not warnings:
            print("  All checks passed.")
        elif not fresh:
            print("  Passed with warnings.")
        else:
            print("  FAILED.")
            all_passed = False

    if args.json:
        Path(args.json).write_text(json.dumps(findings, indent=2), encoding="utf-8")

    total_errors = sum(len(f["errors"]) for f in findings.values())
    total_warnings = sum(len(f["warnings"]) for f in findings.values())
    print(
        f"\n{len(skill_dirs)} skill(s) checked: "
        f"{total_errors} error(s), {total_warnings} warning(s)."
    )
    if grandfathered:
        print(
            f"{grandfathered} pre-existing error(s) grandfathered via baseline. "
            f"Not blocking, but still debt."
        )

    if all_passed:
        print("Skill validation passed.")
        sys.exit(0)

    print("Skill validation failed. Please fix the errors above.")
    sys.exit(1)


if __name__ == "__main__":
    main()
