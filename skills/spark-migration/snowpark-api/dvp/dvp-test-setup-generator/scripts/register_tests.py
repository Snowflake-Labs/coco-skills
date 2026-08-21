"""
DVP Test Registration — CLI wrapper.

Scans dvp/03-tests/ for generated test files, matches them to entrypoints,
and registers them in sma_storage.sqlite3 via sma_api.register_tests().

Also ensures workload-root files are in place:
  - .vscode/settings.json (pytest integration)
  - .gitignore (Spark/pytest/venv ignores)
  - DVP-TESTING.md (how-to-run guide)
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

_SMA_API_DIR = Path(__file__).resolve().parent.parent.parent.parent / "scripts"
sys.path.insert(0, str(_SMA_API_DIR))

import sma_api  # noqa: E402

logger = logging.getLogger("dvp-test-setup-generator")

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def _detect_migrated_suite(tests_dir: Path) -> str | None:
    if (tests_dir / "migrated").is_dir():
        return "migrated"
    if (tests_dir / "migrated_scos").is_dir():
        return "migrated_scos"
    return None


def _normalize_name(name: str) -> str:
    """Normalize an entrypoint/test name for matching.

    Handles CamelCase vs snake_case (MyFile ↔ my_file) by stripping
    hyphens and underscores after lowercasing.
    """
    return name.lower().replace("-", "").replace("_", "")


def _match_test_to_entrypoint(test_file: Path, entrypoints: list[dict]) -> dict | None:
    stem = test_file.stem
    if stem.startswith("test_"):
        stem = stem[5:]
    norm_stem = _normalize_name(stem)
    for ep in entrypoints:
        if _normalize_name(ep["name"]) == norm_stem:
            return ep
    return None


def _update_dashboard_manifest(workload_path: Path) -> None:
    manifest_path = workload_path / "sma-dashboard" / "manifest.json"
    if not manifest_path.exists():
        logger.debug("No dashboard manifest found at %s — skipping", manifest_path)
        return
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
        modules = manifest.get("modules", {})
        if "test_tracker" in modules:
            modules["test_tracker"]["has_data"] = True
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)
            logger.info("Updated dashboard manifest: test_tracker.has_data = true")
    except Exception as exc:
        logger.warning("Could not update dashboard manifest: %s", exc)


def _ensure_workload_root_files(workload_path: Path) -> list[str]:
    """Copy DVP-TESTING.md and test-specific .gitignore entries to workload root."""
    created = []

    # DVP-TESTING.md
    testing_src = TEMPLATES_DIR / "DVP-TESTING.md"
    testing_dst = workload_path / "DVP-TESTING.md"
    if testing_src.exists():
        shutil.copy2(testing_src, testing_dst)
        created.append("DVP-TESTING.md")

    # Merge test-specific .gitignore entries (Spark/pytest)
    gitignore_dst = workload_path / ".gitignore"
    test_gitignore_lines = [
        "# Spark / Hive (created by source tests)",
        "metastore_db/",
        "spark-warehouse/",
        "derby.log",
        "",
        "# pytest",
        ".pytest_cache/",
    ]
    if gitignore_dst.exists():
        existing_lines = set(gitignore_dst.read_text().splitlines())
        to_add = [line for line in test_gitignore_lines if line not in existing_lines]
        if to_add:
            with open(gitignore_dst, "a") as f:
                f.write("\n" + "\n".join(to_add) + "\n")
            created.append(".gitignore (merged test entries)")
    else:
        with open(gitignore_dst, "w") as f:
            f.write("\n".join(test_gitignore_lines) + "\n")
        created.append(".gitignore")

    return created


def run(workload_path: Path) -> int:
    entrypoints_path = workload_path / "dvp" / "04-results" / "entrypoints.json"
    if not entrypoints_path.exists():
        logger.error("entrypoints.json not found: %s", entrypoints_path)
        return 1

    with open(entrypoints_path) as f:
        raw = json.load(f)

    # Normalize format: canonical is a flat list [{name, source, status, ...}]
    # but some agents produce {"entrypoints": [{file, source_path, ...}]}
    if isinstance(raw, dict) and "entrypoints" in raw:
        all_entrypoints = raw["entrypoints"]
    elif isinstance(raw, list):
        all_entrypoints = raw
    else:
        logger.error("Unrecognized entrypoints.json format")
        return 1

    # Normalize each entry to have 'name' and 'status' fields
    for ep in all_entrypoints:
        if "name" not in ep and "file" in ep:
            ep["name"] = Path(ep["file"]).stem
        if "source" not in ep and "source_path" in ep:
            ep["source"] = ep["source_path"].split("/")[-1] + ":1"
        if "status" not in ep:
            ep["status"] = "detected"

    detected = [ep for ep in all_entrypoints if ep.get("status") == "detected"]
    if not detected:
        logger.warning("No detected entrypoints in entrypoints.json — nothing to register")
        return 0

    tests_dir = workload_path / "dvp" / "03-tests"
    if not tests_dir.is_dir():
        logger.error("Tests directory not found: %s", tests_dir)
        return 1

    migrated_suite = _detect_migrated_suite(tests_dir)

    tests_to_register: list[dict] = []

    source_dir = tests_dir / "source"
    if source_dir.is_dir():
        for test_file in sorted(source_dir.rglob("test_*.py")):
            ep = _match_test_to_entrypoint(test_file, detected)
            if ep:
                rel_path = str(test_file.relative_to(workload_path))
                tests_to_register.append({
                    "entrypoint_name": ep["name"],
                    "entrypoint_source": ep.get("source", ""),
                    "test_file": rel_path,
                    "test_type": "source",
                })

    if migrated_suite:
        migrated_dir = tests_dir / migrated_suite
        if migrated_dir.is_dir():
            test_type = "migrated" if migrated_suite == "migrated" else "migrated_scos"
            for test_file in sorted(migrated_dir.rglob("test_*.py")):
                ep = _match_test_to_entrypoint(test_file, detected)
                if ep:
                    rel_path = str(test_file.relative_to(workload_path))
                    tests_to_register.append({
                        "entrypoint_name": ep["name"],
                        "entrypoint_source": ep.get("source", ""),
                        "test_file": rel_path,
                        "test_type": test_type,
                    })

    if not tests_to_register:
        logger.warning("No test files matched any entrypoints")
        return 0

    result = sma_api.register_tests(str(workload_path), tests_to_register)

    if "error" in result:
        logger.error("Registration failed: %s", result["error"])
        return 1

    _update_dashboard_manifest(workload_path)

    # Ensure .vscode/settings.json, .gitignore, DVP-TESTING.md at workload root
    root_files = _ensure_workload_root_files(workload_path)

    print("\nTest Registration Summary")
    print("=" * 55)
    print(f"\nEntrypoints: {len(detected)}")
    print(f"Test files found: {len(tests_to_register)}")
    print(f"  Source tests: {sum(1 for t in tests_to_register if t['test_type'] == 'source')}")
    if migrated_suite:
        mcount = sum(1 for t in tests_to_register if t["test_type"] != "source")
        print(f"  {migrated_suite.replace('_', ' ').title()} tests: {mcount}")
    print(f"New tests registered: {result.get('inserted', 0)}")
    print(f"Database: {workload_path / 'sma_storage.sqlite3'}")
    if root_files:
        print(f"\nWorkload root files: {', '.join(root_files)}")
    print()
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Register generated DVP tests in sma_storage.sqlite3 for dashboard tracking"
    )
    parser.add_argument(
        "--workload-path",
        required=True,
        help="Path to the SMA conversion output (e.g., sma-output/, Conversion-*/, or v3 .../sma-code-process-*/)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(name)s %(levelname)s: %(message)s",
    )

    sys.exit(run(workload_path=Path(args.workload_path)))


if __name__ == "__main__":
    main()
