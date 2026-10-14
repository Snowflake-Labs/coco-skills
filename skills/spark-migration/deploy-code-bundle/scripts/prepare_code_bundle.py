"""
prepare_code_bundle.py — Step 0.7 of deploy-code-bundle (deterministic).

Writes the `code_bundle.yml` spec a Snowflake **Code Bundle** needs to run a
migrated Snowpark/SCOS project, on a **warehouse** (default) or a **compute
pool** (SPCS). The spec is written into the project root so it is uploaded to
the stage alongside the code (Step 1) and picked up by CREATE CODE BUNDLE.

Spec shape (PrPr/PuPr-supported subset):

    bundle:
      type: custom
      compute_type: warehouse | compute_pool
      language: python | java
      compute_options:                 # omitted when empty
        runtime_version: <ver>         # optional
        compute_pool: <cp>             # compute_pool only
        query_warehouse: <wh>          # compute_pool only
      properties:                      # omitted when empty
        requirements_file: requirements.txt
      env_vars:                        # omitted when empty
        - KEY: VALUE

Notes:
- `type` is `custom` (the only supported value at PrPr).
- On a **warehouse**, `query_warehouse` is intentionally NOT written to the file
  (it is taken from session context); `compute_pool`/`query_warehouse` apply to
  the **compute_pool** target only.
- `requirements_file` is auto-detected (`requirements.txt`, else `pyproject.toml`)
  when not passed explicitly.
- The ENTRYPOINT is NOT part of this spec — it is supplied at EXECUTE time
  (see execute_code_bundle.py / SKILL.md Step 3).

CLI:
  prepare_code_bundle.py --project <dir>
      [--compute-type warehouse|compute_pool] [--language python|java]
      [--runtime-version <ver>] [--compute-pool <cp>] [--query-warehouse <wh>]
      [--requirements-file <name>] [--env KEY=VALUE ...] [--json]
"""

import argparse
import json
import sys
from pathlib import Path

VALID_COMPUTE_TYPES = ("warehouse", "compute_pool")
VALID_LANGUAGES = ("python", "java")

# runtime_version is REQUIRED inside compute_options for warehouse code bundles
# (EXECUTE fails with "Missing runtime_version configuration" otherwise); it is
# also accepted for compute_pool. Default to a Snowpark Connect-compatible 3.10.
DEFAULT_RUNTIME_VERSION = "3.10"

# Curated requirements for a Snowpark Connect (SCOS) bundle: drop packages that
# fight the vendored runtime, and guarantee snowpark-connect is present (it is
# NOT auto-provided in the warehouse code-bundle runtime).
CURATE_DROP_PREFIXES = ("pyspark",)          # snowpark-connect vendors PySpark
CURATE_REQUIRED = ("snowpark-connect",)
CURATED_REQUIREMENTS_FILE = "requirements-bundle.txt"


def _detect_requirements(project_dir: Path, explicit: str | None) -> str | None:
    """Resolve the requirements file: explicit wins, else auto-detect."""
    if explicit:
        return explicit
    for candidate in ("requirements.txt", "pyproject.toml"):
        if (project_dir / candidate).exists():
            return candidate
    return None


def curate_requirements(lines: list[str]) -> list[str]:
    """Pure: filter a requirements list for a SCOS bundle.

    Drops runtime-conflicting packages (pyspark*) and appends any required
    packages (snowpark-connect) not already present. Comments/blank lines kept.
    """
    kept = []
    seen_pkgs = set()
    for raw in lines:
        line = raw.rstrip("\n")
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            name = stripped.lower().replace("_", "-")
            if any(name.startswith(p) for p in CURATE_DROP_PREFIXES):
                continue
            # record the bare package name (before any version specifier)
            bare = name.split("[")[0]
            for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "="):
                bare = bare.split(sep)[0]
            seen_pkgs.add(bare.strip())
        kept.append(line)
    for req in CURATE_REQUIRED:
        if req.lower().replace("_", "-") not in seen_pkgs:
            kept.append(req)
    return kept


def write_curated_requirements(project_dir: Path) -> str:
    """Write CURATED_REQUIREMENTS_FILE from the project's requirements.txt (if any)
    and return its filename. Safe to call when no requirements.txt exists."""
    src = project_dir / "requirements.txt"
    src_lines = src.read_text().splitlines() if src.exists() else []
    curated = curate_requirements(src_lines)
    (project_dir / CURATED_REQUIREMENTS_FILE).write_text("\n".join(curated) + "\n")
    return CURATED_REQUIREMENTS_FILE


def build_code_bundle_yml(compute_type: str, language: str,
                          runtime_version: str | None = None,
                          compute_pool: str | None = None,
                          query_warehouse: str | None = None,
                          requirements_file: str | None = None,
                          env_vars: dict[str, str] | None = None) -> str:
    """Pure: build code_bundle.yml content. Empty sub-blocks are omitted.

    compute_options always carries runtime_version (REQUIRED for warehouse,
    accepted for compute_pool). query_warehouse is NOT valid for warehouse
    ("query_warehouse is not supported") and is emitted only for compute_pool.
    """
    lines = ["bundle:", "  type: custom", f"  compute_type: {compute_type}", f"  language: {language}"]

    compute_options = [f"    runtime_version: \"{runtime_version or DEFAULT_RUNTIME_VERSION}\""]
    if compute_type == "compute_pool":
        if compute_pool:
            compute_options.append(f"    compute_pool: {compute_pool}")
        if query_warehouse:
            compute_options.append(f"    query_warehouse: {query_warehouse}")
    lines.append("  compute_options:")
    lines += compute_options

    if requirements_file:
        lines.append("  properties:")
        lines.append(f"    requirements_file: {requirements_file}")

    if env_vars:
        lines.append("  env_vars:")
        for k, v in env_vars.items():
            lines.append(f"    - {k}: {v}")

    return "\n".join(lines) + "\n"


def prepare(project_dir: str, compute_type: str = "warehouse", language: str = "python",
            runtime_version: str | None = None, compute_pool: str | None = None,
            query_warehouse: str | None = None, requirements_file: str | None = None,
            env_vars: dict[str, str] | None = None, curate_requirements_file: bool = False) -> dict:
    root = Path(project_dir)
    if not root.exists():
        return {"verdict": "FAIL", "error": f"project dir not found: {project_dir}"}
    if compute_type not in VALID_COMPUTE_TYPES:
        return {"verdict": "FAIL", "error": f"invalid --compute-type: {compute_type} (use warehouse|compute_pool)"}
    if language not in VALID_LANGUAGES:
        return {"verdict": "FAIL", "error": f"invalid --language: {language} (use python|java)"}
    if compute_type == "compute_pool" and not compute_pool:
        return {"verdict": "FAIL", "error": "--compute-pool is required when --compute-type=compute_pool"}

    # env_vars cannot use SNOWFLAKE_-prefixed keys — the runtime rejects them
    # ("'SNOWFLAKE_DATABASE' is not a permitted environment variable name").
    # The SCOS session namespace must be set by the entrypoint, not via env_vars.
    bad = [k for k in (env_vars or {}) if k.upper().startswith("SNOWFLAKE_")]
    if bad:
        return {"verdict": "FAIL",
                "error": f"reserved env var name(s) not permitted in a code bundle: {', '.join(bad)}. "
                         "Set the SCOS namespace in the entrypoint (e.g. session.sql('USE SCHEMA ...')), not via env_vars."}

    resolved_reqs = write_curated_requirements(root) if curate_requirements_file \
        else _detect_requirements(root, requirements_file)
    resolved_runtime = runtime_version or DEFAULT_RUNTIME_VERSION
    content = build_code_bundle_yml(compute_type, language, resolved_runtime,
                                    compute_pool, query_warehouse, resolved_reqs, env_vars)
    out = root / "code_bundle.yml"
    out.write_text(content)

    return {
        "verdict": "PASS",
        "code_bundle_yml": str(out),
        "compute_type": compute_type,
        "language": language,
        "runtime_version": resolved_runtime,
        "compute_pool": compute_pool if compute_type == "compute_pool" else None,
        "query_warehouse": query_warehouse if compute_type == "compute_pool" else None,
        "requirements_file": resolved_reqs,
        "curated": bool(curate_requirements_file),
        "env_vars": env_vars or {},
    }


def _parse_env(pairs: list[str]) -> dict[str, str]:
    env = {}
    for p in pairs or []:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        k = k.strip()
        if k:
            env[k] = v.strip()
    return env


def main():
    ap = argparse.ArgumentParser(description="Write code_bundle.yml for a Code Bundle deploy")
    ap.add_argument("--project", required=True)
    ap.add_argument("--compute-type", default="warehouse", choices=VALID_COMPUTE_TYPES)
    ap.add_argument("--language", default="python", choices=VALID_LANGUAGES)
    ap.add_argument("--runtime-version", default="")
    ap.add_argument("--compute-pool", default="")
    ap.add_argument("--query-warehouse", default="")
    ap.add_argument("--requirements-file", default="", help="auto-detected (requirements.txt/pyproject.toml) when omitted")
    ap.add_argument("--curate-requirements", action="store_true",
                    help="write a SCOS-curated requirements-bundle.txt (drop pyspark, ensure snowpark-connect) and use it")
    ap.add_argument("--env", action="append", default=[], help="repeatable KEY=VALUE env var (SNOWFLAKE_* names are rejected)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = prepare(
        args.project, args.compute_type, args.language,
        args.runtime_version or None, args.compute_pool or None,
        args.query_warehouse or None, args.requirements_file or None,
        _parse_env(args.env), args.curate_requirements,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Prepare code bundle: {result['verdict']}")
        if result.get("error"):
            print(f"  error: {result['error']}")
        else:
            print(f"  code_bundle.yml: {result['code_bundle_yml']}")
            print(f"  compute_type: {result['compute_type']}  language: {result['language']}")
            if result.get("compute_pool"):
                print(f"  compute_pool: {result['compute_pool']}  query_warehouse: {result.get('query_warehouse')}")
            print(f"  requirements_file: {result.get('requirements_file')}{'  (curated)' if result.get('curated') else ''}")

    sys.exit(2 if result["verdict"] == "FAIL" else 0)


if __name__ == "__main__":
    main()
