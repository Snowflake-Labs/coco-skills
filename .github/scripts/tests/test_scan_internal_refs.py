#!/usr/bin/env python3
"""Assert scan_internal_refs.py catches each pattern class, and does not
fire on the clean fixture.

Run: python3 .github/scripts/tests/test_scan_internal_refs.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scan_internal_refs import scan_dir  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Every label the leaky fixture is expected to trigger.
EXPECTED_LABELS = {
    "internal short link",
    "Google Docs or Drive link",
    "Slack archive link",
    "internal ticket reference",
    "confidentiality marker",
    "internal reviewer handle in prose",
    "non-public Snowflake hostname",
}


def main():
    failures = []

    leaky = scan_dir(FIXTURES / "leaky-fixture")
    found = {f["label"] for f in leaky}

    for label in sorted(EXPECTED_LABELS - found):
        failures.append(f"leaky-fixture: pattern never fired: {label}")

    for label in sorted(found - EXPECTED_LABELS):
        failures.append(f"leaky-fixture: unexpected pattern fired: {label}")

    # The author line carries an sfc-gh handle legitimately and must be exempt.
    author_hits = [
        f for f in leaky if f["label"] == "internal reviewer handle in prose" and f["line"] == 6
    ]
    if author_hits:
        failures.append(
            "leaky-fixture: author field should be exempt from the sfc-gh handle check"
        )

    clean = scan_dir(FIXTURES / "ok-minimal")
    if clean:
        failures.append(f"ok-minimal: expected no findings, got {clean}")

    print()
    if failures:
        print(f"{len(failures)} test(s) failed:")
        for f in failures:
            print(f"  FAIL {f}")
        sys.exit(1)

    print(f"All internal-reference scanner tests passed ({len(found)} patterns fired).")


if __name__ == "__main__":
    main()
