"""
inspect_entrypoint.py — Step 0.6 detection helper of deploy-code-bundle (deterministic, offline).

Statically inspects a migrated project to inform entrypoint composition. It does
NOT run the project or touch Snowflake — pure `ast` + file walk.

Emits (JSON):
- entrypoints: files with an `if __name__ == "__main__"` block, each with the
  argparse arg contract [{flag, dest, required, default}] and the top-level call
  made in the __main__ body (e.g. "MainApplication.main").
- source_roots: top-level / one-deep directories that are Python packages.
- config_candidates: config/*.json + top-level *.json (minus bundle/deploy yml).
- reads_namespace: whether the code references SNOWFLAKE_DATABASE / SNOWFLAKE_SCHEMA.
- local_file_arg_hints: argparse dests whose name suggests a local file/path
  (candidates for stage->local localization — an ASK item).
- needs_wrapper: heuristic — true when a bare ENTRYPOINT=<entry> would not work
  (required args, or reads a session namespace, or has local-file args).

CLI:
  inspect_entrypoint.py --project <dir> [--json]
"""

import argparse
import ast
import json
import sys
from pathlib import Path

SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules", ".pytest_cache", ".ipynb_checkpoints", "tests"}
NAMESPACE_ENV = ("SNOWFLAKE_DATABASE", "SNOWFLAKE_SCHEMA")
_FILE_HINT_TOKENS = ("file", "path")


def _iter_py_files(root: Path):
    for p in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def _dest_from_flags(flags: list[str], explicit_dest: str | None) -> str:
    """Mirror argparse's dest derivation: explicit dest wins, else the first long
    option (--config-file -> config_file), else the first positional."""
    if explicit_dest:
        return explicit_dest
    longs = [f for f in flags if f.startswith("--")]
    if longs:
        return longs[0].lstrip("-").replace("-", "_")
    if flags:
        return flags[0].lstrip("-").replace("-", "_")
    return ""


def _literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def parse_argparse(tree: ast.AST) -> list[dict]:
    """Pure: extract add_argument(...) calls anywhere in the module."""
    args = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        flags = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        dest = required = default = None
        for kw in node.keywords:
            if kw.arg == "dest" and isinstance(kw.value, ast.Constant):
                dest = kw.value.value
            elif kw.arg == "required":
                required = _literal(kw.value)
            elif kw.arg == "default":
                default = _literal(kw.value)
        d = _dest_from_flags(flags, dest)
        if not d:
            continue
        args.append({"flag": flags[0] if flags else "", "dest": d,
                     "required": bool(required), "default": default})
    return args


_PLUMBING = ("parse_args", "add_argument")
_NOISE = {"sys.exit", "exit", "print", "quit"}


def _is_plumbing_or_noise(name: str) -> bool:
    if any(name.endswith(p) for p in _PLUMBING) or "ArgumentParser" in name:
        return True
    if name in _NOISE or name.endswith(".print_exc") or name.startswith("logging."):
        return True
    return False


def _main_call(tree: ast.AST) -> str | None:
    """Best-effort: the dotted name of the entry invocation inside `if __name__ ==
    '__main__'`. Skips parser plumbing (ArgumentParser/add_argument/parse_args)
    and noise (sys.exit/print/traceback/logging); prefers a call ending in
    `.main`/`main`, else the last remaining top-level call statement."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.If) and _is_main_guard(node.test)):
            continue
        kept = []
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                name = _dotted(stmt.value.func)
                if name and not _is_plumbing_or_noise(name):
                    kept.append(name)
        mains = [n for n in kept if n == "main" or n.endswith(".main")]
        if mains:
            return mains[-1]
        return kept[-1] if kept else None
    return None


def _dotted(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _is_main_guard(test) -> bool:
    return (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
            and test.left.id == "__name__")


def _has_main_guard(tree: ast.AST) -> bool:
    return any(isinstance(n, ast.If) and _is_main_guard(n.test) for n in ast.walk(tree))


def find_entrypoints(root: Path) -> list[dict]:
    eps = []
    for p in _iter_py_files(root):
        try:
            tree = ast.parse(p.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        if not _has_main_guard(tree):
            continue
        args = parse_argparse(tree)
        eps.append({
            "file": str(p.relative_to(root)).replace("\\", "/"),
            "args": args,
            "main_call": _main_call(tree),
        })
    return eps


def _source_roots(root: Path) -> list[str]:
    roots = []
    if (root / "__init__.py").exists():
        roots.append(".")
    for p in sorted(root.iterdir()):
        if p.is_dir() and p.name not in SKIP_DIRS:
            if (p / "__init__.py").exists():
                roots.append(p.name)
    return roots


def _config_candidates(root: Path) -> list[str]:
    skip = {"code_bundle.yml", "snowflake.yml", "environment.yml"}
    out = []
    for pat in ("config/*.json", "config/*.yaml", "config/*.yml", "*.json"):
        for p in sorted(root.glob(pat)):
            if p.name in skip:
                continue
            rel = str(p.relative_to(root)).replace("\\", "/")
            if rel not in out:
                out.append(rel)
    return out


def _reads_namespace(root: Path) -> dict:
    files = []
    for p in _iter_py_files(root):
        try:
            txt = p.read_text()
        except UnicodeDecodeError:
            continue
        if any(env in txt for env in NAMESPACE_ENV):
            files.append(str(p.relative_to(root)).replace("\\", "/"))
    return {"reads": bool(files), "files": files}


def _local_file_hints(entrypoints: list[dict]) -> list[str]:
    hints = []
    for ep in entrypoints:
        for a in ep["args"]:
            d = a["dest"].lower()
            if any(tok in d for tok in _FILE_HINT_TOKENS) and a["dest"] not in hints:
                hints.append(a["dest"])
    return hints


def inspect(project_dir: str) -> dict:
    root = Path(project_dir)
    if not root.exists():
        return {"verdict": "FAIL", "error": f"project dir not found: {project_dir}"}

    entrypoints = find_entrypoints(root)
    ns = _reads_namespace(root)
    hints = _local_file_hints(entrypoints)
    has_required = any(a["required"] for ep in entrypoints for a in ep["args"])
    needs_wrapper = bool(has_required or ns["reads"] or hints)

    return {
        "verdict": "PASS",
        "entrypoints": entrypoints,
        "source_roots": _source_roots(root),
        "config_candidates": _config_candidates(root),
        "reads_namespace": ns["reads"],
        "namespace_files": ns["files"],
        "local_file_arg_hints": hints,
        "needs_wrapper": needs_wrapper,
    }


def main():
    ap = argparse.ArgumentParser(description="Inspect a migrated project to inform entrypoint composition")
    ap.add_argument("--project", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = inspect(args.project)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Inspect entrypoint: {result['verdict']}")
        if result.get("error"):
            print(f"  error: {result['error']}")
        else:
            print(f"  needs_wrapper: {result['needs_wrapper']}")
            for ep in result["entrypoints"]:
                reqs = [a["dest"] for a in ep["args"] if a["required"]]
                print(f"  entry: {ep['file']}  call={ep.get('main_call')}  required_args={reqs}")
            print(f"  source_roots: {result['source_roots']}")
            print(f"  config_candidates: {result['config_candidates']}")
            print(f"  reads_namespace: {result['reads_namespace']}  local_file_arg_hints: {result['local_file_arg_hints']}")

    sys.exit(2 if result["verdict"] == "FAIL" else 0)


if __name__ == "__main__":
    main()
