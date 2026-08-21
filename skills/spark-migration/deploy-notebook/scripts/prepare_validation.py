"""
prepare_validation.py — Step 3 helper of deploy-notebook (deterministic, OPT-IN).

Writes the two artifacts a Snowflake WAREHOUSE notebook needs to actually RUN a
Snowpark Connect (SCOS) workload:

- `environment.yml` — declares the kernel packages. SCOS needs Python 3.10 and
  `snowpark-connect`; migrated workloads also commonly need `scikit-learn` /
  `numpy` (e.g. a reverse-geocoder BallTree) on the driver. Extra packages can
  be added with `--extra-packages`.
- `snowflake.yml` — the `snow notebook deploy` project definition. Only the CLI
  deploy applies `environment.yml`; SQL `CREATE NOTEBOOK FROM stage` ignores it.

This is used ONLY by the opt-in validation step (when the user asks to validate
/ run the notebook). The normal deploy path (upload_project -> create_notebook)
never calls it, so a plain deploy does not gain these files.

CLI:
  prepare_validation.py --project <dir> --notebook-file <main.ipynb>
                        --notebook-name <NB> --query-warehouse <wh>
                        [--extra-packages a,b] [--json]
"""

import argparse
import json
import sys
from pathlib import Path

# Base Anaconda packages required for a Snowpark Connect warehouse notebook.
# pygeohash and other PyPI-only packages are intentionally omitted (not in the
# Snowflake Anaconda channel); server-side UDF packages are declared in code via
# snowpark.connect.udf.packages, not here.
BASE_PACKAGES = ["snowpark-connect", "scikit-learn", "numpy"]


def build_environment_yml(name: str, extra_packages: list[str] | None = None) -> str:
    """Pure: build environment.yml content (python 3.10 + base + extras, deduped, ordered)."""
    pkgs = list(BASE_PACKAGES)
    for p in (extra_packages or []):
        p = p.strip()
        if p and p not in pkgs:
            pkgs.append(p)
    lines = [f"name: {name}", "channels:", "  - snowflake", "dependencies:", "  - python=3.10"]
    lines += [f"  - {p}" for p in pkgs]
    return "\n".join(lines) + "\n"


def build_snowflake_yml(entity: str, notebook_name: str, notebook_file: str,
                        query_warehouse: str, artifacts: list[str]) -> str:
    """Pure: build a `snow notebook deploy` project definition (definition_version 2)."""
    lines = [
        "definition_version: 2",
        "entities:",
        f"  {entity}:",
        "    type: notebook",
        "    identifier:",
        f"      name: {notebook_name}",
        f"    query_warehouse: {query_warehouse}",
        f"    notebook_file: {notebook_file}",
        "    artifacts:",
    ]
    lines += [f"      - {a}" for a in artifacts]
    return "\n".join(lines) + "\n"


def _discover_artifacts(project_dir: Path, notebook_file: str) -> list[str]:
    """Notebook + environment.yml + every top-level entry (dirs/files) needed at runtime."""
    arts = [notebook_file, "environment.yml"]
    for p in sorted(project_dir.iterdir()):
        if p.name in {"snowflake.yml", "environment.yml", notebook_file}:
            continue
        if p.name.startswith(".") or p.name == "__pycache__":
            continue
        arts.append(f"{p.name}/" if p.is_dir() else p.name)
    return arts


def prepare(project_dir: str, notebook_file: str, notebook_name: str,
            query_warehouse: str, extra_packages: list[str] | None = None) -> dict:
    root = Path(project_dir)
    if not root.exists():
        return {"verdict": "FAIL", "error": f"project dir not found: {project_dir}"}
    if not (root / notebook_file).exists():
        return {"verdict": "FAIL", "error": f"notebook file not found: {notebook_file}"}

    entity = notebook_name.lower()
    env_yml = build_environment_yml(entity, extra_packages)
    artifacts = _discover_artifacts(root, notebook_file)
    snow_yml = build_snowflake_yml(entity, notebook_name, notebook_file, query_warehouse, artifacts)

    (root / "environment.yml").write_text(env_yml)
    (root / "snowflake.yml").write_text(snow_yml)

    return {
        "verdict": "PASS",
        "environment_yml": str(root / "environment.yml"),
        "snowflake_yml": str(root / "snowflake.yml"),
        "packages": BASE_PACKAGES + [p for p in (extra_packages or []) if p not in BASE_PACKAGES],
        "artifacts": artifacts,
    }


def main():
    ap = argparse.ArgumentParser(description="Write environment.yml + snowflake.yml for notebook validation")
    ap.add_argument("--project", required=True)
    ap.add_argument("--notebook-file", required=True, help="main .ipynb relative to the project root")
    ap.add_argument("--notebook-name", required=True)
    ap.add_argument("--query-warehouse", required=True)
    ap.add_argument("--extra-packages", default="", help="comma-separated extra Anaconda packages")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    extras = [p for p in args.extra_packages.split(",") if p.strip()]
    result = prepare(args.project, args.notebook_file, args.notebook_name,
                     args.query_warehouse, extras)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Prepare validation: {result['verdict']}")
        if result.get("error"):
            print(f"  error: {result['error']}")
        else:
            print(f"  environment.yml: {result['environment_yml']}")
            print(f"  snowflake.yml:   {result['snowflake_yml']}")
            print(f"  packages: {', '.join(result['packages'])}")

    sys.exit(2 if result["verdict"] == "FAIL" else 0)


if __name__ == "__main__":
    main()
