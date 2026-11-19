"""Trusted byte-collection child; no candidate parsing or execution."""
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))
from ingest import PLUGIN_LICENSE, SymlinkInput, collect, encode, plugin_license

# Exit statuses the host launcher maps to fixed diagnostics.
FAILED, SYMLINK = 2, 3

REPOSITORY_INPUTS = (
    'skills/review-skill-sflabs',
    # Public policy evidence required by offline fixture/provenance tests.
    'skills/dbt-model-generator/LICENSE',
    'skills/check-agent-access/LICENSE',
    'skills/persist-memory/LICENSE',
    'skills/snowpro-study/LICENSE',
    'skills/swap-databases/LICENSE',
    'CONTRIBUTING.md',
    '.github/scripts/validate_skill.py',
    '.github/scripts/action_review.py',
    '.github/scripts/package_reviewer.py',
    '.github/actions/contained-review/action.yml',
    '.github/scripts/test_reviewer_isolated.py',
    '.github/scripts/verify_reviewer_runtime.py',
    '.github/scripts/reviewer_test_support.py',
    '.github/workflows/validate-skill.yml',
)


def main():
    try:
        path = sys.argv[1]
        if len(sys.argv) == 3 and sys.argv[2] == '--repository-tests':
            wire = encode(collect(path, selected=REPOSITORY_INPUTS), 'proposed-reviewer')
        elif len(sys.argv) == 2:
            entries = collect(path)
            # The reserved name is trusted-collector output; a candidate file
            # with that name could otherwise impersonate the plugin LICENSE.
            if any(entry.path == PLUGIN_LICENSE for entry in entries):
                return FAILED
            inherited = plugin_license(path)
            if inherited is not None:
                entries = tuple(sorted(entries + (inherited,), key=lambda entry: entry.path))
            wire = encode(entries, os.path.basename(path.rstrip('/')))
        else:
            return FAILED
        sys.stdout.buffer.write(wire)
        return 0
    except SymlinkInput:
        return SYMLINK
    except Exception:
        # No paths, parser messages, or partial snapshots on failure.
        return FAILED


if __name__ == '__main__':
    sys.exit(main())
