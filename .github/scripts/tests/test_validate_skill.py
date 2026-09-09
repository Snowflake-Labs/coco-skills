#!/usr/bin/env python3
"""Assert validate_skill.py catches what it is supposed to catch.

Each fixture encodes one expectation. Several of these cases pass or crash
under the previous validator, which is the point of having them:

  partner-type              failed before (partner missing from VALID_TYPES)
  unquoted-colon            passed before (colon truncated the value silently)
  block-scalar-description  passed before ('description: |' read as "|")
  name-mismatch             passed before (check was guarded on absent 'id')
  missing-skill-md          crashed before (NameError on unbound frontmatter)

Run: python3 .github/scripts/tests/test_validate_skill.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validate_skill import load_bundled_skill_names, validate_skill  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# fixture -> (expect_pass, substring that must appear in the findings)
CASES = {
    "ok-minimal": (True, None),
    "partner-type": (True, None),
    "colon-in-description": (True, None),
    "missing-skill-md": (False, "Missing SKILL.md"),
    "name-mismatch": (False, "does not match folder name"),
    "apache-on-snowflake-author": (False, "Snowflake Skills License"),
    "bundled-collision": (False, "collides with a bundled"),
    "unquoted-colon": (False, "must be quoted"),
    "block-scalar-description": (False, "Missing required frontmatter field"),
}


def main():
    bundled = load_bundled_skill_names()
    failures = []

    for fixture, (expect_pass, needle) in CASES.items():
        skill_dir = FIXTURES / fixture
        if not skill_dir.is_dir():
            failures.append(f"{fixture}: fixture directory is missing")
            continue

        errors, warnings = validate_skill(skill_dir, bundled_names=bundled)
        passed = not errors
        blob = " | ".join(errors + warnings)

        if passed != expect_pass:
            failures.append(
                f"{fixture}: expected {'pass' if expect_pass else 'fail'}, "
                f"got {'pass' if passed else 'fail'} -- {blob or 'no findings'}"
            )
            continue

        if needle and needle not in blob:
            failures.append(
                f"{fixture}: expected a finding containing {needle!r}, got: {blob}"
            )
            continue

        print(f"  ok  {fixture}")

    print()
    if failures:
        print(f"{len(failures)} test(s) failed:")
        for f in failures:
            print(f"  FAIL {f}")
        sys.exit(1)

    print(f"All {len(CASES)} validator tests passed.")


if __name__ == "__main__":
    main()
