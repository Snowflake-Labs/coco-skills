"""DVP Entrypoint Identifier — ASG-only wrapper.

Generates dvp/04-results/entrypoints.json from an ASG JSON (*_asg.json)
using the embedded WARP EntrypointDetector.

This intentionally does NOT parse SMA inventories or source code.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_SKILL_LIB = Path(__file__).resolve().parent.parent / "warp"
_SHARED_LIB = Path(__file__).resolve().parent.parent.parent / "dvp-orchestrator"
sys.path.insert(0, str(_SHARED_LIB))
sys.path.insert(0, str(_SKILL_LIB))

from entrypoints import EntrypointDetector  # noqa: E402

logger = logging.getLogger("dvp-entrypoint-identifier")


def resolve_asg_path(results_dir: Path) -> Path | None:
    matches = sorted(results_dir.glob("*_asg.json"))
    return matches[0] if matches else None


def run(asg_path: Path, output_path: Path) -> int:
    if not asg_path.exists():
        logger.error("ASG file not found: %s", asg_path)
        return 1

    detector = EntrypointDetector()
    detector.detect_from_file(asg_path)

    entrypoints = detector.to_list()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(entrypoints, indent=2), encoding="utf-8")

    if detector.issues.total:
        logger.info("Entrypoint detection issues: %d", detector.issues.total)

    print(f"Saved {len(entrypoints)} entry points to {output_path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate entrypoints.json from ASG")
    parser.add_argument(
        "--asg",
        type=Path,
        default=None,
        help="Path to ASG JSON (XX_asg.json). Auto-detected from --results-dir if omitted.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="Path to dvp/04-results/ directory (for auto-detecting ASG)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to save entrypoints.json (should be dvp/04-results/entrypoints.json)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(name)s %(levelname)s: %(message)s",
    )

    asg_path = args.asg
    if not asg_path and args.results_dir:
        asg_path = resolve_asg_path(args.results_dir)
        if asg_path:
            logger.info("Auto-detected ASG: %s", asg_path)

    if not asg_path:
        logger.error("No ASG file specified or found. Use --asg or --results-dir.")
        raise SystemExit(1)

    raise SystemExit(run(asg_path=asg_path, output_path=args.output))


if __name__ == "__main__":
    main()
