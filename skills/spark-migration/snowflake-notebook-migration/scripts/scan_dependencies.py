#!/usr/bin/env python3
"""
scan_dependencies.py — Scan a directory for notebook dependencies.

Traces %run references and Python imports across notebooks to produce
a dependency graph with recommended conversion order (leaf-first).

Usage:
    python scan_dependencies.py <directory>

Output JSON:
    {
      "notebooks": [{"file": "a.py", "format": "...", "language": "...", "converted_name": "a.py.ipynb"}, ...],
      "py_modules": [...],
      "dependencies": [{"source": "a.py", "target": "b.py", "target_converted": "b.py.ipynb", "type": "run", "resolved": true}, ...],
      "conversion_order": ["leaf.py", "mid.py", "root.py"],
      "conversion_order_converted": ["leaf.py.ipynb", "mid.py.ipynb", "root.py.ipynb"]
    }
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detect_and_parse_notebook import detect_format, parse_notebook, convert_filename, scan_directory


def find_py_modules(directory: str) -> list[str]:
    """Find plain .py files (not Databricks notebooks) that could be imported."""
    modules = []
    for root, _dirs, files in os.walk(directory):
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            info = detect_format(fpath)
            if info["format"] == "not_notebook":
                rel = os.path.relpath(fpath, directory)
                modules.append(rel)
    return modules


def extract_run_refs(source: str) -> list[str]:
    """Extract %run paths from notebook source."""
    refs = []
    for line in source.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# MAGIC %run"):
            path = stripped[len("# MAGIC %run"):].strip()
            refs.append(path)
        elif stripped.startswith("// MAGIC %run"):
            path = stripped[len("// MAGIC %run"):].strip()
            refs.append(path)
        elif stripped.startswith("%run "):
            path = stripped[5:].strip()
            refs.append(path)
    return refs


def extract_local_imports(source: str, py_modules: list[str]) -> list[str]:
    """Extract Python import statements that reference local .py modules."""
    module_stems = set()
    for mod in py_modules:
        stem = Path(mod).stem
        module_stems.add(stem)

    imports = []
    for line in source.split("\n"):
        stripped = line.strip()
        match = re.match(r'^(?:from|import)\s+([\w.]+)', stripped)
        if match:
            module_name = match.group(1).split(".")[0]
            if module_name in module_stems:
                found = next((mod for mod in py_modules if Path(mod).stem == module_name), None)
                if found:
                    imports.append(found)
    return imports


def resolve_run_path(
    run_ref: str,
    source_notebook: str,
    directory: str,
    nb_files: set[str],
    nb_by_stem: dict[str, list[str]],
) -> str | None:
    """Resolve a %run reference to a notebook file path.

    nb_files and nb_by_stem are precomputed indexes passed in by the caller
    to avoid rebuilding them on every call.
    """
    source_dir = os.path.dirname(os.path.join(directory, source_notebook))

    candidates = [run_ref]
    if not run_ref.endswith(".ipynb"):
        candidates.append(run_ref + ".ipynb")

    base_ref = run_ref.removeprefix("./")
    if not base_ref.endswith(".ipynb"):
        candidates.append(base_ref + ".ipynb")
    candidates.append(base_ref)

    ref_stem = Path(base_ref).stem
    if ref_stem in nb_by_stem:
        for nb_file in nb_by_stem[ref_stem]:
            conv_name = convert_filename(nb_file)
            if conv_name not in candidates:
                candidates.append(conv_name)

    for candidate in candidates:
        resolved = os.path.normpath(os.path.join(source_dir, candidate))
        rel = os.path.relpath(resolved, directory)
        if rel in nb_files:
            return rel

    # Fallback: match by stem — warn if ambiguous
    if ref_stem in nb_by_stem:
        matches = nb_by_stem[ref_stem]
        if len(matches) > 1:
            print(
                f"Warning: %run '{run_ref}' in '{source_notebook}' is ambiguous — "
                f"stem '{ref_stem}' matches multiple files: {matches}. "
                f"Using '{matches[0]}'.",
                file=sys.stderr,
            )
        return matches[0]

    return None


def build_dependency_graph(directory: str) -> dict:
    """Build a full dependency graph for all notebooks in a directory."""
    notebooks = scan_directory(directory)
    py_modules = find_py_modules(directory)

    # Precompute indexes once — used by every resolve_run_path call
    nb_files = {nb["file"] for nb in notebooks}
    nb_by_stem: dict[str, list[str]] = defaultdict(list)
    for nb in notebooks:
        nb_by_stem[Path(nb["file"]).stem].append(nb["file"])

    dependencies = []
    notebook_deps = defaultdict(set)

    for nb in notebooks:
        parsed = parse_notebook(nb["abs_path"])
        all_source = "\n".join(cell.get("source", "") for cell in parsed.get("cells", []))

        run_refs = extract_run_refs(all_source)
        for ref in run_refs:
            target = resolve_run_path(ref, nb["file"], directory, nb_files, nb_by_stem)
            dep = {
                "source": nb["file"],
                "target": target or ref,
                "target_converted": convert_filename(target) if target else None,
                "type": "run",
                "resolved": target is not None,
            }
            dependencies.append(dep)
            if target:
                notebook_deps[nb["file"]].add(target)

        if nb["language"] == "python":
            local_imports = extract_local_imports(all_source, py_modules)
            for imp in local_imports:
                dependencies.append({
                    "source": nb["file"],
                    "target": imp,
                    "type": "import",
                    "resolved": True,
                })

    conversion_order = _topological_sort(
        [nb["file"] for nb in notebooks], notebook_deps
    )
    conversion_order_converted = [convert_filename(f) for f in conversion_order]

    return {
        "directory": directory,
        "notebooks": [
            {
                "file": nb["file"],
                "format": nb["format"],
                "language": nb["language"],
                "converted_name": convert_filename(nb["file"]),
            }
            for nb in notebooks
        ],
        "py_modules": py_modules,
        "dependencies": dependencies,
        "conversion_order": conversion_order,
        "conversion_order_converted": conversion_order_converted,
    }


def _topological_sort(nodes: list[str], deps: dict[str, set]) -> list[str]:
    """Topological sort: leaves first, then nodes that depend on them.

    deps maps node -> set of nodes it depends on (e.g. A depends on B).
    Nodes with no dependencies (leaves) are emitted first.
    Falls back to original order for cycles.
    """
    reverse_deps = defaultdict(set)
    in_degree = defaultdict(int)
    for node in nodes:
        in_degree[node] = 0
    for node, targets in deps.items():
        for t in targets:
            reverse_deps[t].add(node)
            in_degree[node] += 1

    queue = deque(sorted(n for n in nodes if in_degree[n] == 0))
    ordered = []
    visited = set()

    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        ordered.append(node)
        for dependent in sorted(reverse_deps.get(node, [])):
            in_degree[dependent] -= 1
            if in_degree[dependent] <= 0 and dependent not in visited:
                queue.append(dependent)

    for n in nodes:
        if n not in visited:
            ordered.append(n)

    return ordered


def main():
    parser = argparse.ArgumentParser(
        description="Scan directory for notebook dependencies and build conversion order"
    )
    parser.add_argument("directory", help="Directory to scan")
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print(f"Error: {args.directory} is not a directory", file=sys.stderr)
        sys.exit(1)

    result = build_dependency_graph(args.directory)
    print(json.dumps(result, indent=2))

    nb_count = len(result["notebooks"])
    dep_count = len(result["dependencies"])
    py_count = len(result["py_modules"])
    print(
        f"\nFound {nb_count} notebook(s), {dep_count} dependency(ies), "
        f"{py_count} Python module(s)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
