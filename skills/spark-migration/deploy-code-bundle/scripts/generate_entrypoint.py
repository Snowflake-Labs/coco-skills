"""
generate_entrypoint.py — Step 0.6 drafting helper of deploy-code-bundle (deterministic, offline).

Writes a thin Code Bundle entrypoint (a launcher) that reproduces the environment
setup a migrated CLI expects and then calls the project's EXISTING entry with
argparse-equivalent args. It never changes project logic — it is additive.

Template blocks (emitted conditionally):
- B1 import snowflake.snowpark_connect FIRST (always; it vendors pyspark).
- B2 os.environ namespace (only when --namespace-db/--namespace-schema given;
  the bundle spec cannot carry SNOWFLAKE_* env vars).
- B3 sys.path insert of the source root (only with --source-root).
- B4 get_active_session().file.get(...) per --stage-file, binding the local path
  to the named arg dest.
- B5 import the entry callable and call it with SimpleNamespace(**dests), where
  dests come from --arg (project-relative paths) and the --stage-file bindings.

CLI:
  generate_entrypoint.py --project <dir> --entry-import "module:Callable.method"
     [--out run_bundle.py] [--source-root src]
     [--namespace-db DB] [--namespace-schema SCH]
     [--arg dest=relpath ...]
     [--stage-file "STAGE_PATH::LOCAL_DIR::dest" ...]
     [--force] [--json]
"""

import argparse
import json
import sys
from pathlib import Path


def _check_py_name(name: str, what: str) -> str:
    """Validate a value used in a CODE position (variable name / kwarg) — it must be
    a plain Python identifier, else it could inject arbitrary code into the generated
    launcher (which is executed inside Snowflake)."""
    if not isinstance(name, str) or not name.isidentifier():
        raise ValueError(f"{what} must be a valid Python identifier, got: {name!r}")
    return name


def _check_dotted_py(path: str, what: str) -> str:
    """Validate a dotted code path (module path / attribute path): every part must be
    a Python identifier. Used for `from <module> import ...` and `<attrpath>(...)`."""
    if not isinstance(path, str) or not path:
        raise ValueError(f"{what} must be a non-empty dotted identifier, got: {path!r}")
    parts = path.split(".")
    if not all(p.isidentifier() for p in parts):
        raise ValueError(f"{what} must be a dotted Python identifier, got: {path!r}")
    return path



def _parse_stage_file(spec: str) -> dict:
    """'@db.sch.stage/geocode/f.csv::/tmp::geoCodeFilePath' -> dict."""
    parts = spec.split("::")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        raise ValueError(f"--stage-file must be 'STAGE_PATH::LOCAL_DIR::dest', got: {spec}")
    stage_path, local_dir, dest = (p.strip() for p in parts)
    basename = stage_path.rstrip("/").split("/")[-1]
    return {"stage_path": stage_path, "local_dir": local_dir, "dest": dest, "basename": basename}


def _parse_arg(spec: str) -> tuple[str, str]:
    """'configFilePath=config/x.json' -> ('configFilePath', 'config/x.json')."""
    if "=" not in spec:
        raise ValueError(f"--arg must be 'dest=relpath', got: {spec}")
    dest, _, val = spec.partition("=")
    return dest.strip(), val.strip()


def build_entrypoint(entry_import: str, source_root: str | None = None,
                     namespace_db: str | None = None, namespace_schema: str | None = None,
                     literal_args: dict[str, str] | None = None,
                     stage_files: list[dict] | None = None) -> str:
    """Pure: build the launcher source text."""
    if ":" not in entry_import:
        raise ValueError("--entry-import must be 'module:Callable.method' (or 'module:func')")
    module, _, attrpath = entry_import.partition(":")
    module, attrpath = module.strip(), attrpath.strip()
    # module / attrpath are emitted as code (`from <module> import <top>`, `<attrpath>(...)`),
    # so they must be dotted Python identifiers — never interpolate them unchecked.
    _check_dotted_py(module, "--entry-import module")
    _check_dotted_py(attrpath, "--entry-import callable path")
    top = attrpath.split(".")[0]
    literal_args = literal_args or {}
    stage_files = stage_files or []

    L = [
        "#!/usr/bin/env python3",
        '"""Auto-generated Code Bundle entrypoint (deploy-code-bundle Step 0.6).',
        "",
        "Thin launcher: reproduces the environment setup the migrated entry expects,",
        "then calls the EXISTING entry with argparse-equivalent args. It does not",
        "change any project logic.",
        '"""',
        "import os",
        "import sys",
        "",
        "# B1: snowpark-connect vendors PySpark; import it before anything imports pyspark.",
        "import snowflake.snowpark_connect  # noqa: F401",
    ]

    if namespace_db or namespace_schema:
        L += ["", "# B2: SCOS session namespace (bundle env_vars cannot carry SNOWFLAKE_* names)."]
        if namespace_db:
            L.append(f'os.environ["SNOWFLAKE_DATABASE"] = {namespace_db!r}')
        if namespace_schema:
            L.append(f'os.environ["SNOWFLAKE_SCHEMA"] = {namespace_schema!r}')

    L += ["", "_ROOT = os.path.dirname(os.path.abspath(__file__))"]
    if source_root:
        L += [
            "# B3: make the project's first-party packages importable.",
            f"_SRC = os.path.join(_ROOT, {source_root!r})",
            "if _SRC not in sys.path:",
            "    sys.path.insert(0, _SRC)",
        ]

    if stage_files:
        L += [
            "",
            "# B4: localize stage files the entry reads via a local path.",
            "from snowflake.snowpark.context import get_active_session  # noqa: E402",
            "_session = get_active_session()",
        ]
        for sf in stage_files:
            dest = _check_py_name(sf["dest"], "--stage-file dest")
            L.append(f'_session.file.get({sf["stage_path"]!r}, {sf["local_dir"]!r})')
            L.append(f'{dest} = os.path.join({sf["local_dir"]!r}, {sf["basename"]!r})')

    if literal_args:
        L += ["", "# project-relative path args"]
        for dest, rel in literal_args.items():
            _check_py_name(dest, "--arg dest")
            L.append(f"{dest} = os.path.join(_ROOT, {rel!r})")

    L += [
        "",
        "from types import SimpleNamespace  # noqa: E402",
        f"from {module} import {top}  # noqa: E402",
        "",
        'if __name__ == "__main__":',
    ]
    dests = list(literal_args.keys()) + [sf["dest"] for sf in stage_files]
    kwargs = ", ".join(f"{d}={d}" for d in dests)
    L.append(f"    _args = SimpleNamespace({kwargs})")
    L.append(f"    {attrpath}(_args)")
    return "\n".join(L) + "\n"


def generate(project_dir: str, entry_import: str, out: str = "run_bundle.py",
             source_root: str | None = None, namespace_db: str | None = None,
             namespace_schema: str | None = None, literal_args: dict | None = None,
             stage_files: list[dict] | None = None, force: bool = False) -> dict:
    root = Path(project_dir)
    if not root.exists():
        return {"verdict": "FAIL", "error": f"project dir not found: {project_dir}"}
    dest = root / out
    if dest.exists() and not force:
        return {"verdict": "FAIL", "error": f"{out} already exists (pass --force to overwrite)"}
    try:
        content = build_entrypoint(entry_import, source_root, namespace_db,
                                   namespace_schema, literal_args, stage_files)
    except ValueError as e:
        return {"verdict": "FAIL", "error": str(e)}
    dest.write_text(content)
    return {
        "verdict": "PASS",
        "entrypoint": out,
        "path": str(dest),
        "entry_import": entry_import,
        "source_root": source_root,
        "namespace": {"database": namespace_db, "schema": namespace_schema},
        "args": list((literal_args or {}).keys()) + [sf["dest"] for sf in (stage_files or [])],
    }


def main():
    ap = argparse.ArgumentParser(description="Generate a Code Bundle entrypoint launcher")
    ap.add_argument("--project", required=True)
    ap.add_argument("--entry-import", required=True, help="'module:Callable.method' or 'module:func'")
    ap.add_argument("--out", default="run_bundle.py")
    ap.add_argument("--source-root", default="")
    ap.add_argument("--namespace-db", default="")
    ap.add_argument("--namespace-schema", default="")
    ap.add_argument("--arg", action="append", default=[], help="repeatable dest=relpath (project-relative)")
    ap.add_argument("--stage-file", action="append", default=[], help="repeatable STAGE_PATH::LOCAL_DIR::dest")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        literal_args = dict(_parse_arg(a) for a in args.arg)
        stage_files = [_parse_stage_file(s) for s in args.stage_file]
    except ValueError as e:
        result = {"verdict": "FAIL", "error": str(e)}
    else:
        result = generate(args.project, args.entry_import, args.out,
                          args.source_root or None, args.namespace_db or None,
                          args.namespace_schema or None, literal_args, stage_files, args.force)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Generate entrypoint: {result['verdict']}")
        if result.get("error"):
            print(f"  error: {result['error']}")
        else:
            print(f"  wrote: {result['path']}  (entry {result['entry_import']}, args {result['args']})")

    sys.exit(2 if result["verdict"] == "FAIL" else 0)


if __name__ == "__main__":
    main()
