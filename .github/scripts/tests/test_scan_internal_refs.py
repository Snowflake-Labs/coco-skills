#!/usr/bin/env python3
"""Assert scan_internal_refs.py catches each pattern class, does not fire on
the clean fixture, and refuses to read through symlinks.

Run: python3 .github/scripts/tests/test_scan_internal_refs.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scan_internal_refs import finding_key, is_safe_to_read, scan_dir  # noqa: E402

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

    failures.extend(check_symlink_refused())
    failures.extend(check_baseline_key_is_hashed())

    print()
    if failures:
        print(f"{len(failures)} test(s) failed:")
        for f in failures:
            print(f"  FAIL {f}")
        sys.exit(1)

    print(f"All internal-reference scanner tests passed ({len(found)} patterns fired).")


def check_symlink_refused():
    """A PR can add a symlink, and is_file() follows it.

    Without this guard, `skills/x/notes.md -> ~/.ssh/id_rsa` is read by CI and
    any matched text echoed into a public log.
    """
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        outside = Path(tmp) / "outside.txt"
        outside.write_text("SNOW-12345678 and go/secret\n", encoding="utf-8")

        link = Path(tmp) / "link.md"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            print("  skip  symlink test (not supported on this platform)")
            return failures

        if is_safe_to_read(link):
            failures.append("symlink guard: a symlink was accepted as safe to read")
        else:
            print("  ok  symlink is refused")

        # A regular file outside the repo must also be refused.
        if is_safe_to_read(outside):
            failures.append("symlink guard: a file outside the repo was accepted")
        else:
            print("  ok  path outside the repo is refused")

    return failures


def check_baseline_key_is_hashed():
    """The baseline must not contain the matched text verbatim.

    Storing it would republish, in this repo, exactly what the scan exists to
    keep out of it.
    """
    failures = []
    secret = "SNOW-7654321"
    key = finding_key({"file": "skills/x/SKILL.md", "label": "internal ticket reference", "match": secret})

    if secret in key:
        failures.append("baseline key: matched text appears verbatim in the key")
    else:
        print("  ok  baseline key hashes the matched text")

    # Stability: the same finding must produce the same key, or the ratchet
    # would treat every run as new leakage.
    again = finding_key({"file": "skills/x/SKILL.md", "label": "internal ticket reference", "match": secret})
    if key != again:
        failures.append("baseline key: not stable across calls")
    else:
        print("  ok  baseline key is stable")

    return failures


if __name__ == "__main__":
    main()
