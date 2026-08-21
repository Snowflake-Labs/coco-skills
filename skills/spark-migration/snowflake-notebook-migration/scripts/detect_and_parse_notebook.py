#!/usr/bin/env python3
"""
detect_and_parse_notebook.py — Detect Databricks notebook format and parse cells.

Handles all Databricks notebook formats:
  - .ipynb (standard Jupyter JSON)
  - .python (always Databricks native JSON)
  - .scala/.sql (native JSON if first byte is '{')
  - .scala (exported text if first line is '// Databricks notebook source')
  - .py (exported text if first line is '# Databricks notebook source')

Usage:
    python detect_and_parse_notebook.py <file_path>
    python detect_and_parse_notebook.py --scan <directory>

Single file mode outputs JSON:
    {
      "format": "ipynb|native_json|exported_text|not_notebook",
      "language": "python|scala|sql|unknown",
      "cell_count": 12,
      "cells": [{"cell_type": "python|scala|sql|markdown|r|shell|fs", "source": "..."}]
    }

Scan mode outputs JSON array of detected notebooks in a directory.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TypedDict

__all__ = ["detect_format", "parse_notebook", "scan_directory", "convert_filename"]


class FormatInfo(TypedDict, total=False):
    format: str
    language: str
    reason: str


class CellInfo(TypedDict):
    cell_type: str
    source: str
    metadata: dict


class ParsedNotebook(TypedDict, total=False):
    file: str
    format: str
    language: str
    reason: str
    error: str
    cell_count: int
    cells: list[CellInfo]


def convert_filename(original: str) -> str:
    """Return the post-conversion .ipynb filename for a notebook source file.

    Appends .ipynb to the full original filename for non-.ipynb files.
    Files already ending in .ipynb are returned unchanged.
    E.g. config.py -> config.py.ipynb, dashboard.ipynb -> dashboard.ipynb
    """
    if original.endswith(".ipynb"):
        return original
    return original + ".ipynb"


def detect_format(file_path: str) -> FormatInfo:
    """Detect the notebook format of a file.

    Returns dict with 'format' and 'language' keys.
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    if not path.is_file():
        return {"format": "not_notebook", "language": "unknown", "reason": "file not found"}

    if ext == ".ipynb":
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "worksheets" in data and "cells" not in data:
                return {"format": "not_notebook", "language": "unknown", "reason": "unsupported nbformat v3"}
            if "cells" in data:
                lang = (
                    data.get("metadata", {})
                    .get("kernelspec", {})
                    .get("language", "python")
                )
                return {"format": "ipynb", "language": lang}
        except (json.JSONDecodeError, OSError):
            return {"format": "not_notebook", "language": "unknown", "reason": "invalid JSON"}
        return {"format": "not_notebook", "language": "unknown", "reason": "no cells key"}

    if ext == ".python":
        return {"format": "native_json", "language": "python"}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            first_char = f.read(1)
            f.seek(0)
            first_line = f.readline().rstrip("\n\r")
    except OSError:
        return {"format": "not_notebook", "language": "unknown", "reason": "cannot read file"}

    if ext == ".scala":
        if first_char == "{":
            return {"format": "native_json", "language": "scala"}
        if first_line == "// Databricks notebook source":
            return {"format": "exported_text", "language": "scala"}
        return {"format": "not_notebook", "language": "scala", "reason": "plain scala file"}

    if ext == ".sql":
        if first_char == "{":
            return {"format": "native_json", "language": "sql"}
        return {"format": "not_notebook", "language": "sql", "reason": "plain sql file"}

    if ext == ".py":
        if first_line == "# Databricks notebook source":
            return {"format": "exported_text", "language": "python"}
        return {"format": "not_notebook", "language": "python", "reason": "plain python file"}

    return {"format": "not_notebook", "language": "unknown", "reason": f"unsupported extension: {ext}"}


def parse_ipynb(file_path: str) -> list[CellInfo]:
    """Parse a standard Jupyter .ipynb file into cells."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    cells = []
    for cell in data.get("cells", []):
        source = cell.get("source", [])
        if isinstance(source, list):
            source = "".join(source)
        cells.append({
            "cell_type": cell.get("cell_type", "code"),
            "source": source,
            "metadata": cell.get("metadata", {}),
        })
    return cells


def parse_native_json(file_path: str, language: str) -> list[CellInfo]:
    """Parse a Databricks native JSON notebook (.python, .scala JSON, .sql JSON).

    Format: {"version":"NotebookV1","language":"...","commands":[...]}
    Cells are in commands[], sorted by position. Only subtype=="command" entries
    are code cells. Magic commands appear as bare %sql, %md, etc.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    commands = sorted(data.get("commands", []), key=lambda c: c.get("position", 0))

    cells = []
    for cmd in commands:
        if cmd.get("subtype") != "command":
            continue
        source = cmd.get("command", "")
        cell_type = _detect_cell_type_from_magic(source, language)
        metadata: dict = {}
        if cmd.get("commandTitle"):
            metadata["title"] = cmd["commandTitle"]
        cells.append({
            "cell_type": cell_type,
            "source": source,
            "metadata": metadata,
        })
    return cells


def parse_exported_text(file_path: str, language: str) -> list[CellInfo]:
    """Parse a Databricks exported text notebook (.py or .scala exported).

    Python: header '# Databricks notebook source', cells split on '# COMMAND ----------',
            magic lines prefixed with '# MAGIC '.
    Scala:  header '// Databricks notebook source', cells split on '// COMMAND ----------',
            magic lines prefixed with '// MAGIC '.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    if language == "scala":
        separator = "// COMMAND ----------"
        magic_prefix = "// MAGIC "
        header = "// Databricks notebook source"
    else:
        separator = "# COMMAND ----------"
        magic_prefix = "# MAGIC "
        header = "# Databricks notebook source"

    if content.startswith(header):
        content = content[len(header):]

    raw_cells = content.split(separator)

    cells = []
    for raw in raw_cells:
        raw = raw.strip()
        if not raw:
            continue

        lines = raw.split("\n")
        is_magic = all(
            line.startswith(magic_prefix)
            for line in lines
            if line.strip()
        ) and any(line.startswith(magic_prefix) for line in lines)

        if is_magic:
            stripped_lines = [
                line.removeprefix(magic_prefix) if line.startswith(magic_prefix) else line
                for line in lines
            ]
            source = "\n".join(stripped_lines)
        else:
            source = "\n".join(lines)

        cell_type = _detect_cell_type_from_magic(source, language)
        cells.append({
            "cell_type": cell_type,
            "source": source,
            "metadata": {},
        })
    return cells


def _detect_cell_type_from_magic(source: str, default_language: str) -> str:
    """Detect cell type from magic command at the start of the source."""
    first_line = source.lstrip().split("\n")[0].strip() if source.strip() else ""
    if first_line.startswith("%md"):
        return "markdown"
    if first_line.startswith("%sql"):
        return "sql"
    if first_line.startswith("%scala"):
        return "scala"
    if first_line.startswith("%python"):
        return "python"
    if first_line == "%r" or first_line.startswith("%r "):
        return "r"
    if first_line.startswith("%sh"):
        return "shell"
    if first_line.startswith("%fs"):
        return "fs"
    if first_line.startswith("%run"):
        return default_language
    return default_language


def parse_notebook(file_path: str) -> ParsedNotebook:
    """Detect format and parse a notebook file into structured output."""
    info = detect_format(file_path)
    fmt = info["format"]
    lang = info["language"]

    if fmt == "not_notebook":
        return {
            "file": file_path,
            "format": fmt,
            "language": lang,
            "reason": info.get("reason", ""),
            "cell_count": 0,
            "cells": [],
        }

    try:
        match fmt:
            case "ipynb":
                cells = parse_ipynb(file_path)
            case "native_json":
                cells = parse_native_json(file_path, lang)
            case "exported_text":
                cells = parse_exported_text(file_path, lang)
            case _:
                cells = []
    except (json.JSONDecodeError, KeyError, OSError, UnicodeDecodeError) as e:
        return {
            "file": file_path,
            "format": fmt,
            "language": lang,
            "error": str(e),
            "cell_count": 0,
            "cells": [],
        }

    return {
        "file": file_path,
        "format": fmt,
        "language": lang,
        "cell_count": len(cells),
        "cells": cells,
    }


def scan_directory(directory: str) -> list[dict]:
    """Recursively scan a directory for Databricks notebooks.

    Each result is a dict with:
      - ``file``: path relative to ``directory`` (stable key across machines)
      - ``abs_path``: absolute filesystem path (robust against cwd changes)
      - ``format``: notebook format reported by :func:`detect_format`
      - ``language``: notebook language reported by :func:`detect_format`
    """
    notebook_exts = {".ipynb", ".python", ".py", ".scala", ".sql"}
    results = []

    for root, _dirs, files in os.walk(directory):
        for fname in sorted(files):
            ext = Path(fname).suffix.lower()
            if ext not in notebook_exts:
                continue
            fpath = os.path.join(root, fname)
            info = detect_format(fpath)
            if info["format"] != "not_notebook":
                results.append({
                    "file": os.path.relpath(fpath, directory),
                    "abs_path": os.path.abspath(fpath),
                    "format": info["format"],
                    "language": info["language"],
                })

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Detect Databricks notebook format and parse cells"
    )
    parser.add_argument("path", help="File path or directory (with --scan)")
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan directory recursively for notebooks instead of parsing a single file",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Omit cell source content (show only metadata)",
    )
    args = parser.parse_args()

    if args.scan:
        results = scan_directory(args.path)
        print(json.dumps(results, indent=2))
        print(f"\nFound {len(results)} notebook(s)", file=sys.stderr)
    else:
        result = parse_notebook(args.path)
        if args.compact:
            for cell in result.get("cells", []):
                source = cell.get("source", "")
                cell["source"] = source[:80] + "..." if len(source) > 80 else source
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
