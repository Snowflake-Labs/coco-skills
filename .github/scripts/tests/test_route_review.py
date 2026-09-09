#!/usr/bin/env python3
"""Assert route_review.py maps diff content to the right escalation labels.

Run: python3 .github/scripts/tests/test_route_review.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from route_review import decide_labels  # noqa: E402

CASES = [
    (
        "ci change",
        [".github/workflows/validate-skill.yml"],
        "some: yaml",
        {"needs: security-review"},
    ),
    (
        "new script",
        ["skills/foo/scripts/run.py"],
        "print('hello')",
        {"needs: code-review"},
    ),
    (
        "dependency file",
        ["skills/foo/pyproject.toml"],
        "[project]",
        {"needs: code-review"},
    ),
    (
        "sql write",
        ["skills/foo/SKILL.md"],
        "Run `CREATE OR REPLACE TABLE analytics.public.results AS SELECT 1`",
        {"needs: sql-write-review"},
    ),
    (
        "grant",
        ["skills/foo/SKILL.md"],
        "Then GRANT USAGE ON WAREHOUSE wh TO ROLE analyst;",
        {"needs: sql-write-review"},
    ),
    (
        "security domain",
        ["skills/foo/SKILL.md"],
        "Attach a masking policy to the column.",
        {"needs: security-review"},
    ),
    (
        "egress",
        ["skills/foo/scripts/fetch.py"],
        "r = requests.get('https://example.com/data')",
        {"needs: code-review", "needs: egress-review"},
    ),
    (
        "preview feature",
        ["skills/foo/SKILL.md"],
        "This is in private preview, so ask your account team.",
        {"needs: product-confirmation"},
    ),
    (
        "partner type",
        ["skills/foo/SKILL.md"],
        "type: partner",
        {"needs: positioning-review"},
    ),
    (
        "plain prose triggers nothing",
        ["skills/foo/SKILL.md"],
        "This skill explains how to read a query profile and interpret it.",
        set(),
    ),
    (
        "select-only SQL is not a write",
        ["skills/foo/SKILL.md"],
        "Run `SELECT warehouse_name FROM snowflake.account_usage.warehouse_metering_history`",
        set(),
    ),
]


def main():
    failures = []

    for name, files, added, expected in CASES:
        got = set(decide_labels(files, added))
        if got != expected:
            missing = expected - got
            extra = got - expected
            detail = []
            if missing:
                detail.append(f"missing {sorted(missing)}")
            if extra:
                detail.append(f"unexpected {sorted(extra)}")
            failures.append(f"{name}: {'; '.join(detail)}")
        else:
            print(f"  ok  {name}")

    failures.extend(check_path_scoping())

    print()
    if failures:
        print(f"{len(failures)} test(s) failed:")
        for f in failures:
            print(f"  FAIL {f}")
        sys.exit(1)

    print(f"All {len(CASES) + 3} routing tests passed.")


def check_path_scoping():
    """Content matching must apply to submissions only, not repo tooling.

    Without this, the labeler fires on its own source: route_review.py contains
    'GRANT' and 'requests.get' as regex literals, and the fixtures contain them
    on purpose.
    """
    import route_review

    def fake_diff(diff_text):
        return lambda args: diff_text if args[0] == "diff" else ""

    original = route_review.run_git
    failures = []

    try:
        # A skill genuinely adding a GRANT: must be picked up.
        route_review.run_git = fake_diff(
            "+++ b/skills/foo/SKILL.md\n+GRANT USAGE ON WAREHOUSE wh TO ROLE r;\n"
        )
        if "GRANT" not in route_review.added_lines("base", "head"):
            failures.append("path scoping: skills/ content should be scanned")
        else:
            print("  ok  path scoping: skills/ content is scanned")

        # The labeler's own source: must not be.
        route_review.run_git = fake_diff(
            "+++ b/.github/scripts/route_review.py\n+    r\"\\bGRANT\\s+\\w+\"\n"
        )
        if route_review.added_lines("base", "head").strip():
            failures.append("path scoping: .github/scripts should be exempt")
        else:
            print("  ok  path scoping: .github/scripts is exempt")

        # Fixtures inside a skill: must not be.
        route_review.run_git = fake_diff(
            "+++ b/skills/foo/tests/fixtures/bad.md\n+GRANT ALL TO ROLE r;\n"
        )
        if route_review.added_lines("base", "head").strip():
            failures.append("path scoping: tests/fixtures should be exempt")
        else:
            print("  ok  path scoping: tests/fixtures is exempt")
    finally:
        route_review.run_git = original

    return failures


if __name__ == "__main__":
    main()
