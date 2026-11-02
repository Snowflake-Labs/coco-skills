#!/usr/bin/env python3
"""Report whether selected skills have golden cases available."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-dir", type=Path, required=True)
    args = parser.parse_args()

    plugin_dir = args.plugin_dir.resolve()
    results = []
    for skill_dir in sorted((plugin_dir / "skills").glob("*")):
        if not skill_dir.is_dir():
            continue
        golden = plugin_dir / "evals" / skill_dir.name / "golden_cases.md"
        results.append(
            {
                "skill": skill_dir.name,
                "case_source": str(golden) if golden.exists() else None,
                "status": "READY_TO_TEST" if golden.exists() else "INCONCLUSIVE",
                "reason": None if golden.exists() else "No golden_cases.md found",
            }
        )
    print(json.dumps({"skills": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())