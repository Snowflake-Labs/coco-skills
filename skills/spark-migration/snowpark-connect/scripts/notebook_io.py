#!/usr/bin/env python3
"""
notebook_io.py — Shared parser/serializer for Databricks and Jupyter notebooks.

Used by every script under ``snowpark-connect/scripts/`` and by both migration
sub-skills so that analysis, fixes, reporting, and EWI injection share a single
source of truth for notebook cell structure.

Supported formats (six total):
  - ``.ipynb``                         standard Jupyter JSON
  - ``.python``                        Databricks native JSON (Python)
  - ``.scala`` with first char ``{``   Databricks native JSON (Scala)
  - ``.sql``   with first char ``{``   Databricks native JSON (SQL)
  - ``.scala`` first line ``// Databricks notebook source``  exported text
  - ``.py``    first line ``# Databricks notebook source``   exported text

Key design property: **format-preserving round-trip**. Parsing a notebook and
writing it back without modification produces a near-identical byte stream
(only inconsequential whitespace normalization is tolerated). Cell order and
underlying container key order are preserved because we mutate parsed
dicts/lists in place rather than rebuilding them.

Public API:
    detect_format(path)                 -> FormatInfo dict
    parse_notebook(path)                -> Notebook
    write_notebook(path, notebook)      -> None
    is_notebook(path)                   -> bool
    scan_notebooks(directory)           -> list[ScanEntry]
    flatten_cells_to_script(path, lang) -> str
    walk_filtered(directory)            -> os.walk-compatible generator

Migration-header constants (single source of truth for ``# SCOS Migration Output``
/ ``// SCOS Migration Output`` / ``\"\"\"SCOS Migration Output\"\"\"`` detection
across the scripts/):
    MIGRATION_HEADER_MARKER
    PYTHON_MIGRATION_HEADER_DOCSTRING
    SCALA_MIGRATION_HEADER_COMMENT

Note on DBTITLE metadata: parsed Databricks exported-text cells preserve the
leading ``DBTITLE`` comment in ``cell.metadata["dbtitle"]`` and round-trip it
on write for untouched cells. Newly inserted cells (for example via
``fallback_transform._prepend_markdown_cell``) do NOT currently wire a
``dbtitle`` through — pass a pre-formatted source if you need a title on a
synthetic cell.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, TypedDict

__all__ = [
    "Cell",
    "Notebook",
    "detect_format",
    "parse_notebook",
    "write_notebook",
    "is_notebook",
    "scan_notebooks",
    "scan_and_parse_notebooks",
    "flatten_cells_to_script",
    "convert_filename",
    "walk_filtered",
    "SKIP_DIRS",
    "MIGRATION_HEADER_MARKER",
    "PYTHON_MIGRATION_HEADER_DOCSTRING",
    "SCALA_MIGRATION_HEADER_COMMENT",
]

# Size of the prefix read for metadata-only operations (format detection,
# indent sniffing). 4 KiB is enough to comfortably include the ipynb
# metadata block and the first newline of both ipynb and native_json
# notebooks without loading the full file.
_HEAD_BYTES = 4096

# Matches ``"language": "python"`` inside nbformat's ``metadata.kernelspec``.
# Used by detect_format to sniff the kernel language from a bounded head of
# an .ipynb file without paying for a full json.load. The language string is
# always nested under kernelspec in valid nbformat files, so anchoring on
# kernelspec avoids matching unrelated ``"language"`` keys that some
# per-cell metadata blocks carry.
_IPYNB_LANGUAGE_RE = re.compile(
    r'"kernelspec"\s*:\s*\{[^{}]*?"language"\s*:\s*"([^"]+)"'
)


NOTEBOOK_EXTS = {".ipynb", ".python", ".py", ".scala", ".sql"}

# Directories that are never user source and would contain binary artifacts
# that trip UTF-8 reads or notebook detection. Shared across every os.walk
# call in the scripts/ package.
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".idea", ".vscode",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".venv", "venv", "env", ".env",
    "node_modules", "target", "build", "dist",
})

# Single source of truth for SCOS migration-header detection. Every script
# that checks "is this file already migrated?" must consume these constants
# instead of redefining the strings locally.
MIGRATION_HEADER_MARKER = "SCOS Migration Output"
PYTHON_MIGRATION_HEADER_DOCSTRING = '"""\nSCOS Migration Output\n'
SCALA_MIGRATION_HEADER_COMMENT = "// SCOS Migration Output"

# Sentinel line written by generate_scos_reports.ensure_migration_headers when
# it has to stamp a placeholder header because Phase 3 (update_imports.py) never
# ran. It carries the migration marker but NO real Changes Overview / Known
# Limitations content, so it must never be treated as a finished header:
#   * update_imports.add_migration_header REPLACES it with a rich header.
#   * scos_gates.py imports FAILS on it so the coordinator is forced to run
#     Phase 3 deterministically instead of shipping the stub.
STUB_HEADER_SENTINEL = "Deterministic header added by report generator"


def walk_filtered(directory: str) -> Iterator[tuple[str, list[str], list[str]]]:
    """Yield ``(root, dirs, files)`` from ``os.walk`` with :data:`SKIP_DIRS` pruned.

    Pruning happens in-place on the mutable ``dirs`` list so ``os.walk``
    doesn't descend into the skipped directory at all — this keeps binary
    artifacts (``.pyc``, git objects, packaged wheels, etc.) out of any
    downstream UTF-8 read or notebook-detection call.
    """
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        yield root, dirs, files


class FormatInfo(TypedDict, total=False):
    format: str          # "ipynb" | "native_json" | "exported_text" | "not_notebook"
    language: str        # "python" | "scala" | "sql" | "unknown"
    reason: str


class ScanEntry(TypedDict):
    file: str
    abs_path: str
    format: str
    language: str


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def convert_filename(original: str) -> str:
    """Return the post-conversion ``.ipynb`` filename for a notebook source file.

    Mirrors the helper used by ``snowflake-notebook-migration`` so callers
    sharing this module get consistent naming.
    """
    if original.endswith(".ipynb"):
        return original
    return original + ".ipynb"


def detect_format(file_path: str) -> FormatInfo:
    """Detect the notebook format of ``file_path``.

    Rules match ``snowflake-notebook-migration/scripts/detect_and_parse_notebook.py``
    so both skills classify the same file identically.

    Binary files and files with unsupported extensions are short-circuited to
    ``not_notebook`` without ever being opened — this protects callers (e.g.
    ``scan_scos_comments``) that walk a directory tree which may contain
    ``.pyc``, ``.so``, git objects, etc.
    """
    path = Path(file_path)
    if not path.is_file():
        return {"format": "not_notebook", "language": "unknown", "reason": "file not found"}

    ext = path.suffix.lower()

    # Fast reject: if the extension is not one we ever treat as a notebook,
    # don't even open the file. Avoids UnicodeDecodeError on binary artifacts
    # like __pycache__/*.pyc and .git/objects/*.
    if ext not in NOTEBOOK_EXTS:
        return {"format": "not_notebook", "language": "unknown", "reason": f"unsupported extension: {ext}"}

    if ext == ".ipynb":
        # Fast path: sniff the kernel language from a bounded head read
        # instead of loading the full JSON tree. ``parse_notebook`` does the
        # full ``json.load`` when it actually needs the cell list, so paying
        # for it here (plus a second time in ``parse_notebook``) is wasted
        # work on the scan + parse hot path.
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                head = f.read(_HEAD_BYTES)
        except (OSError, UnicodeDecodeError):
            return {"format": "not_notebook", "language": "unknown", "reason": "cannot read file as text"}

        # Must at least look like a JSON object. Cheap sanity check before
        # we hand the file off to callers as a parseable notebook.
        if not head.lstrip().startswith("{"):
            return {"format": "not_notebook", "language": "unknown", "reason": "not JSON"}

        # nbformat v3 files carry ``worksheets`` instead of ``cells``; we
        # don't support them. Detect via substring on the head — v3 files
        # always surface the key near the top-level metadata.
        if '"worksheets"' in head and '"cells"' not in head:
            return {"format": "not_notebook", "language": "unknown", "reason": "unsupported nbformat v3"}

        match = _IPYNB_LANGUAGE_RE.search(head)
        if match:
            return {"format": "ipynb", "language": match.group(1)}

        # Fallback: the kernelspec block can live past the 4 KiB head when a
        # notebook carries large top-level metadata (e.g. extensive widget
        # state, long comments in a worksheet). Silently defaulting to
        # ``"python"`` misclassifies Scala/SQL kernels, which the Scala
        # analyzer would then skip entirely because no cell reports
        # ``cell_language == "scala"``. Pay for the full json.load in this
        # rare case so the kernelspec.language is read authoritatively.
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"format": "ipynb", "language": "python"}
        kernelspec = (data.get("metadata") or {}).get("kernelspec") or {}
        lang = kernelspec.get("language") or "python"
        return {"format": "ipynb", "language": lang}

    if ext == ".python":
        return {"format": "native_json", "language": "python"}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            first_char = f.read(1)
            f.seek(0)
            first_line = f.readline().rstrip("\n\r")
    except (OSError, UnicodeDecodeError):
        return {"format": "not_notebook", "language": "unknown", "reason": "cannot read file as text"}

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


def is_notebook(file_path: str) -> bool:
    info = detect_format(file_path)
    return info.get("format") != "not_notebook"


# ---------------------------------------------------------------------------
# Per-cell language inference
# ---------------------------------------------------------------------------

_MAGIC_LANG_RULES = [
    ("%md", "markdown"),
    ("%sql", "sql"),
    ("%scala", "scala"),
    ("%python", "python"),
    ("%pyspark", "python"),
    ("%r", "r"),
    ("%sh", "shell"),
    ("%fs", "fs"),
    # Package-install magics are NOT Python — a bare ``%pip install ...`` /
    # ``%conda ...`` line is a syntax error if concatenated into a module, and
    # there is nothing to migrate. Map them to "shell" (already in the
    # analyzer/fixer skip-set) so they are excluded from Python processing.
    ("%pip", "shell"),
    ("%conda", "shell"),
    ("%run", "run"),
]

# Jupyter ``%%<lang>`` CELL magics apply to the WHOLE cell, so the entire cell
# body is that language (unlike Databricks single-``%`` line magics). Only the
# language-switching cell magics are listed: Python-wrapping cell magics
# (``%%time``, ``%%timeit``, ``%%capture``, ``%%prun``, ``%%debug`` …) wrap a
# Python body, so they intentionally fall through to ``default`` (python) rather
# than being reclassified — reclassifying them would drop real migration work.
_CELL_MAGIC_LANG_RULES = [
    ("%%sql", "sql"),
    ("%%bash", "shell"),
    ("%%sh", "shell"),
    ("%%script", "shell"),
    ("%%html", "markdown"),
    ("%%markdown", "markdown"),
    ("%%md", "markdown"),
    ("%%latex", "markdown"),
    ("%%javascript", "shell"),
    ("%%js", "shell"),
    ("%%r", "r"),
    ("%%scala", "scala"),
    ("%%fs", "fs"),
    ("%%pip", "shell"),
    ("%%conda", "shell"),
]


def _infer_cell_language(source: str, default: str) -> str:
    """Derive a per-cell language from leading ``%magic`` markers.

    ``default`` is the notebook's primary language, applied to cells with no
    recognisable magic marker.

    A ``%run`` cell (a Databricks notebook include) is classified as ``"run"``,
    NOT the notebook's default language. Its body (e.g. ``%run ../config``) is
    not valid Python, so labelling it Python would (a) break ``parse_module`` on
    the concatenated Python cells — silently disabling every module-scope recipe
    for that notebook — and (b) feed a non-Python line to the per-cell Python
    fixer. The analyzer and fixer already treat ``run`` (and ``sql``/``r``/
    ``shell``/``fs``) as non-Python cells to skip, so ``"run"`` is the
    contract-correct classification.

    Non-Databricks (Jupyter) notebooks use two additional markers that are also
    not Python and would break the concatenated-module parse if left as
    ``python``:

    * ``%%<lang>`` **cell** magics (``%%sql``, ``%%bash``, …) — the marker
      governs the entire cell, so the whole cell is classified as that language.
    * A cell whose every non-blank line is an IPython ``!`` shell escape
      (``!pip install …``) is a pure shell cell → ``"shell"``.

    A cell that only *interleaves* ``!`` escapes with Python is left as Python
    (its body is real Python); neutralising individual magic lines is a separate
    concern from cell-level classification.
    """
    if not source.strip():
        return default
    stripped = source.lstrip()
    first = stripped.split("\n", 1)[0].strip()

    # Jupyter ``%%<lang>`` cell magic governs the whole cell — check before the
    # single-``%`` line magics so ``%%sql`` is not mistaken for ``%sql``.
    for prefix, lang in _CELL_MAGIC_LANG_RULES:
        if first == prefix or first.startswith(prefix + " ") or first.startswith(prefix + "\t"):
            return lang

    # Databricks / IPython single-``%`` line magic on the first line.
    for prefix, lang in _MAGIC_LANG_RULES:
        if first == prefix or first.startswith(prefix + " "):
            return lang

    # A cell that is ENTIRELY ``!`` shell escapes is a shell cell. (Mixed
    # ``!``+Python cells stay Python so real code is not dropped.)
    non_blank = [ln for ln in source.splitlines() if ln.strip()]
    if non_blank and all(ln.lstrip().startswith("!") for ln in non_blank):
        return "shell"

    return default


# ---------------------------------------------------------------------------
# Notebook / Cell data model
# ---------------------------------------------------------------------------


@dataclass
class Cell:
    """Normalized view of a single notebook cell.

    Mutate ``source`` (and optionally ``cell_type`` / ``metadata``) and the
    containing :class:`Notebook` will serialize the change back into the
    original file format on :func:`write_notebook`.
    """

    index: int
    cell_type: str              # "code" | "markdown" | "raw" | "sql" | "r" | "shell" | "fs"
    cell_language: str          # "python" | "scala" | "sql" | "markdown" | "r" | "shell" | "fs"
    source: str
    metadata: dict = field(default_factory=dict)
    # Private fields used by write_notebook to produce format-preserving output.
    _raw: Any = None            # underlying dict for ipynb / native_json cells
    _original_source: str = ""  # source captured at parse time (for dirty detection)
    _exported: dict = field(default_factory=dict)  # exported_text per-cell reconstruction state


@dataclass
class Notebook:
    """Parsed notebook with enough state to serialize back without churn."""

    file: str
    format: str                 # "ipynb" | "native_json" | "exported_text"
    language: str               # notebook primary language
    cells: list[Cell] = field(default_factory=list)
    # Format-specific state for round-trip preservation.
    _raw: Any = None            # parsed JSON for ipynb / native_json
    _ipynb_indent: Any = 1      # indent value for json.dump on ipynb
    _ipynb_trailing_newline: bool = False  # whether source file ended with a newline
    _native_indent: Any = None  # indent value for json.dump on native_json (usually None = compact)
    _native_separators: Any = (",", ":")  # compact separators for native_json
    _native_trailing_newline: bool = False  # whether native_json source ended with a newline
    _exported_header: str = ""  # e.g. "# Databricks notebook source"
    _exported_separator: str = ""  # e.g. "# COMMAND ----------"
    _exported_magic_prefix: str = ""  # e.g. "# MAGIC "
    _exported_raw_segments: list[str] = field(default_factory=list)
    _exported_joiner: str = "\n"  # joiner between segments (typically "\n\n")
    _exported_trailing_newline: bool = True
    _exported_opens_with_header: bool = True


# ---------------------------------------------------------------------------
# ipynb parse / write
# ---------------------------------------------------------------------------


def _read_text_with_meta(file_path: str) -> tuple[str, bool, str]:
    """Read ``file_path`` as UTF-8 text once and return ``(text, trailing_newline, head)``.

    Used by both the ipynb and native_json parse paths so we pay for one
    file open + one decode per parse instead of three (raw bytes for
    trailing-newline detection, text for ``json.load``, and another text
    read inside the indent detectors). ``head`` is the first
    :data:`_HEAD_BYTES` characters of ``text`` so indent-detection
    heuristics don't need to rescan the whole file.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read()
    trailing_newline = text.endswith("\n")
    head = text[:_HEAD_BYTES]
    return text, trailing_newline, head


def _detect_json_indent_from_head(head: str) -> Any:
    """Return the pretty-print indent for a JSON notebook head, or ``None`` if compact.

    Same heuristic as the previous ``_detect_ipynb_indent`` / ``_detect_native_indent``
    helpers: look at the first non-empty line after the opening brace and
    count its leading spaces. When nothing decisive is found, fall through
    to the same default the originals used (indent=1, which matches
    nbformat's pretty-print default).
    """
    idx = head.find("\n")
    if idx < 0:
        return None
    rest = head[idx + 1:]
    for line in rest.splitlines():
        if not line:
            continue
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        return indent if indent > 0 else None
    return 1


def _parse_ipynb(file_path: str, language: str) -> Notebook:
    text, trailing_newline, head = _read_text_with_meta(file_path)
    data = json.loads(text)

    nb = Notebook(
        file=file_path,
        format="ipynb",
        language=language,
        _raw=data,
        _ipynb_indent=_detect_json_indent_from_head(head),
        _ipynb_trailing_newline=trailing_newline,
    )

    for idx, raw_cell in enumerate(data.get("cells", [])):
        source = raw_cell.get("source", [])
        if isinstance(source, list):
            source_str = "".join(source)
        else:
            source_str = str(source)
        cell_type = raw_cell.get("cell_type", "code")
        # cell_language is derived from the notebook's kernel language for code cells,
        # and kept as the cell_type label for non-code (markdown/raw).
        if cell_type == "code":
            cell_language = _infer_cell_language(source_str, default=language)
        else:
            cell_language = cell_type  # "markdown" | "raw"

        cell = Cell(
            index=idx,
            cell_type=cell_type,
            cell_language=cell_language,
            source=source_str,
            metadata=dict(raw_cell.get("metadata", {})),
            _raw=raw_cell,
            _original_source=source_str,
        )
        nb.cells.append(cell)
    return nb


def _atomic_write_text(path: str, content: str) -> None:
    """Write ``content`` to ``path`` atomically.

    Writes to a sibling temp file in the same directory (so ``os.replace``
    is a rename within a single filesystem) and then swaps it into place.
    A crash mid-write leaves the original file intact instead of truncating
    the user's notebook to zero or partial bytes. This preserves the
    format-preserving round-trip guarantee even under signal or OOM kill.
    """
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # fsync is not supported on every filesystem (e.g. some
                # network mounts); the atomic rename below is still safe.
                pass
        os.replace(tmp_path, path)
    except BaseException:
        # Clean up the temp file on any failure so we don't leak .tmp
        # siblings next to the original notebook.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _write_ipynb(path: str, nb: Notebook) -> None:
    data = nb._raw
    # Mutate each cell's source field in the original raw dict when changed.
    for cell in nb.cells:
        if cell.source == cell._original_source and cell._raw is not None:
            continue
        raw_cell = cell._raw
        if raw_cell is None:
            continue
        # Preserve original source shape: list of str lines (with trailing \n) vs single str.
        original = raw_cell.get("source")
        if isinstance(original, list):
            raw_cell["source"] = _split_source_to_lines(cell.source)
        else:
            raw_cell["source"] = cell.source

    indent = nb._ipynb_indent
    if indent is None:
        serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        serialized = json.dumps(data, ensure_ascii=False, indent=indent)
    if nb._ipynb_trailing_newline:
        serialized += "\n"
    _atomic_write_text(path, serialized)


def _split_source_to_lines(source: str) -> list[str]:
    """Split a source string into the list-of-lines form used by ``.ipynb``.

    Each element keeps its trailing ``\\n`` except the last line, matching
    nbformat's storage convention.
    """
    if source == "":
        return []
    lines = source.split("\n")
    result = [line + "\n" for line in lines[:-1]]
    if lines[-1] != "":
        result.append(lines[-1])
    return result


# ---------------------------------------------------------------------------
# Databricks native JSON parse / write
# ---------------------------------------------------------------------------


def _detect_native_indent_from_head(head: str) -> tuple[Any, Any]:
    """Detect ``(indent, separators)`` for a Databricks native JSON notebook.

    Databricks exports are typically compact single-line JSON, but we honor
    pretty-printed inputs if detected. Operates on a pre-read ``head`` so
    :func:`_parse_native_json` doesn't reopen the file just for indent
    detection.
    """
    indent = _detect_json_indent_from_head(head)
    if indent is None or indent == 0:
        return None, (",", ":")
    return indent, None


def _parse_native_json(file_path: str, language: str) -> Notebook:
    text, trailing_newline, head = _read_text_with_meta(file_path)
    data = json.loads(text)

    indent, separators = _detect_native_indent_from_head(head)

    nb = Notebook(
        file=file_path,
        format="native_json",
        language=language,
        _raw=data,
        _native_indent=indent,
        _native_separators=separators,
        _native_trailing_newline=trailing_newline,
    )

    # Databricks sorts cells by "position" but the wire-level commands[] order
    # is what the file actually renders; preserve that order to keep writes
    # byte-stable. Index matches commands[] position.
    commands = data.get("commands", [])

    for idx, cmd in enumerate(commands):
        if cmd.get("subtype") != "command":
            continue
        source = cmd.get("command", "") or ""
        cell_language = _infer_cell_language(source, default=language)
        # Use the cell's effective language as cell_type for native_json, so
        # downstream code can treat it uniformly with ipynb (where cell_type is
        # "code" / "markdown" / "raw").
        cell_type = "markdown" if cell_language == "markdown" else "code"

        metadata: dict = {}
        if cmd.get("commandTitle"):
            metadata["title"] = cmd["commandTitle"]
        if cmd.get("nuid"):
            metadata["nuid"] = cmd["nuid"]

        cell = Cell(
            index=idx,
            cell_type=cell_type,
            cell_language=cell_language,
            source=source,
            metadata=metadata,
            _raw=cmd,
            _original_source=source,
        )
        nb.cells.append(cell)
    return nb


def _write_native_json(path: str, nb: Notebook) -> None:
    data = nb._raw
    for cell in nb.cells:
        if cell.source == cell._original_source and cell._raw is not None:
            continue
        raw_cmd = cell._raw
        if raw_cmd is None:
            continue
        raw_cmd["command"] = cell.source

    if nb._native_indent is None:
        serialized = json.dumps(data, ensure_ascii=False, separators=nb._native_separators)
    else:
        serialized = json.dumps(data, ensure_ascii=False, indent=nb._native_indent)
    if nb._native_trailing_newline:
        serialized += "\n"
    _atomic_write_text(path, serialized)


# ---------------------------------------------------------------------------
# Databricks exported text parse / write
# ---------------------------------------------------------------------------

_EXPORTED_CONFIG = {
    "python": {
        "header": "# Databricks notebook source",
        "separator": "# COMMAND ----------",
        "magic_prefix": "# MAGIC ",
        "comment_prefix": "# ",
        "dbtitle_prefix": "# DBTITLE ",
    },
    "scala": {
        "header": "// Databricks notebook source",
        "separator": "// COMMAND ----------",
        "magic_prefix": "// MAGIC ",
        "comment_prefix": "// ",
        "dbtitle_prefix": "// DBTITLE ",
    },
}


def _parse_exported_text(file_path: str, language: str) -> Notebook:
    cfg = _EXPORTED_CONFIG[language]
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    trailing_newline = content.endswith("\n")

    # Split into segments (cell bodies) along the separator line. Keep the
    # raw segment text verbatim so unchanged cells write back byte-for-byte.
    # Segments are joined with ``\n<separator>\n`` on write — the inter-cell
    # blank lines are part of each segment's leading/trailing whitespace.
    header = cfg["header"]
    separator = cfg["separator"]
    magic_prefix = cfg["magic_prefix"]
    dbtitle_prefix = cfg["dbtitle_prefix"]

    opens_with_header = content.startswith(header)
    body = content[len(header):] if opens_with_header else content

    # Split keeping original segment text. Databricks always writes
    # "\n# COMMAND ----------\n" between cells, so a simple split on the
    # separator keyword preserves the flanking whitespace on each side.
    # One split + length check is cheaper than a membership test followed
    # by a split: the ``in`` scan and the ``split`` each walk ``body``
    # end-to-end, and on large exported-text files that doubles the cost.
    joiner = "\n" + separator + "\n"
    raw_segments = body.split(joiner)
    if len(raw_segments) == 1:
        raw_segments = body.split(separator)

    nb = Notebook(
        file=file_path,
        format="exported_text",
        language=language,
        _exported_header=header,
        _exported_separator=separator,
        _exported_magic_prefix=magic_prefix,
        _exported_raw_segments=raw_segments,
        _exported_trailing_newline=trailing_newline,
        _exported_opens_with_header=opens_with_header,
    )

    for idx, segment in enumerate(raw_segments):
        body_text = segment.strip("\n")
        if not body_text.strip():
            # Empty cell: preserve, no source.
            cell = Cell(
                index=idx,
                cell_type="code",
                cell_language=language,
                source="",
                metadata={},
                _original_source="",
                _exported={"kind": "empty", "segment": segment},
            )
            nb.cells.append(cell)
            continue

        lines = body_text.split("\n")
        # Strip an optional leading DBTITLE line into metadata.
        dbtitle: Optional[str] = None
        if lines and lines[0].startswith(dbtitle_prefix):
            dbtitle = lines[0]
            lines = lines[1:]

        # Determine whether this cell is a MAGIC cell (all remaining non-blank
        # lines start with the magic prefix) or plain code.
        non_blank = [ln for ln in lines if ln.strip()]
        is_magic = bool(non_blank) and all(ln.startswith(magic_prefix) for ln in non_blank)

        if is_magic:
            stripped = [
                ln[len(magic_prefix):] if ln.startswith(magic_prefix) else ln
                for ln in lines
            ]
            source = "\n".join(stripped)
            cell_language = _infer_cell_language(source, default=language)
            cell_type = "markdown" if cell_language == "markdown" else "code"
        else:
            source = "\n".join(lines)
            cell_language = language
            cell_type = "code"

        metadata: dict = {}
        if dbtitle is not None:
            metadata["dbtitle"] = dbtitle

        cell = Cell(
            index=idx,
            cell_type=cell_type,
            cell_language=cell_language,
            source=source,
            metadata=metadata,
            _original_source=source,
            _exported={
                "kind": "magic" if is_magic else "plain",
                "segment": segment,
                "dbtitle": dbtitle,
            },
        )
        nb.cells.append(cell)

    return nb


def _rebuild_exported_segment(
    cell: Cell,
    cfg: dict,
    *,
    is_first: bool = False,
    is_last: bool = False,
    opens_with_header: bool = True,
    trailing_newline: bool = True,
) -> str:
    """Rebuild the full segment text (between separators) for a modified cell.

    Segment shape must match what :func:`_parse_exported_text` produced via
    ``body.split("\\n<sep>\\n")``:

    - Interior segments carry **no** leading or trailing newline — the
      writer's ``"\\n<sep>\\n"`` joiner already supplies them on both sides.
    - The **first** segment carries a leading ``"\\n"`` when
      ``opens_with_header`` is ``True`` (parse prepended the stripped
      header's trailing newline to ``body``); otherwise it carries none.
    - The **last** segment carries a trailing ``"\\n"`` when
      ``trailing_newline`` is ``True`` (parse preserved the file's final
      newline inside that segment); otherwise it carries none.

    Emitting ``"\\n" + body + "\\n"`` unconditionally — as this helper used
    to do — inflated every modified cell with a blank line on each side at
    write time, breaking the format-preserving round-trip contract.
    """
    state = cell._exported
    kind = state.get("kind", "plain")
    magic_prefix = cfg["magic_prefix"]

    # A cell parsed as "empty" that is still empty round-trips verbatim.
    # Otherwise, treat a populated empty cell as plain code for rebuild.
    if kind == "empty" and not cell.source.strip():
        return state.get("segment", "")
    effective_kind = "plain" if kind == "empty" else kind

    body_lines = cell.source.split("\n")
    if effective_kind == "magic":
        prefixed = [
            magic_prefix + ln if ln else magic_prefix.rstrip()
            for ln in body_lines
        ]
        body_text = "\n".join(prefixed)
    else:
        body_text = cell.source

    dbtitle = state.get("dbtitle")
    if dbtitle:
        body_text = dbtitle + "\n" + body_text

    leading = "\n" if (is_first and opens_with_header) else ""
    trailing = "\n" if (is_last and trailing_newline) else ""
    return leading + body_text + trailing


def _write_exported_text(path: str, nb: Notebook) -> None:
    cfg = _EXPORTED_CONFIG[nb.language]
    segments: list[str] = []
    total = len(nb.cells)
    for i, cell in enumerate(nb.cells):
        raw_segment = nb._exported_raw_segments[i] if i < len(nb._exported_raw_segments) else None
        if cell.source == cell._original_source and raw_segment is not None:
            segments.append(raw_segment)
        else:
            segments.append(_rebuild_exported_segment(
                cell,
                cfg,
                is_first=(i == 0),
                is_last=(i == total - 1),
                opens_with_header=nb._exported_opens_with_header,
                trailing_newline=nb._exported_trailing_newline,
            ))

    separator_joiner = "\n" + cfg["separator"] + "\n"
    body = separator_joiner.join(segments)
    if nb._exported_opens_with_header:
        content = cfg["header"] + body
    else:
        content = body

    # Preserve the original trailing-newline state. When we need to strip it,
    # strip exactly ONE newline (not all of them) so multi-newline content
    # that legitimately ends in "\n\n" isn't flattened.
    if nb._exported_trailing_newline and not content.endswith("\n"):
        content += "\n"
    elif not nb._exported_trailing_newline and content.endswith("\n"):
        content = content[:-1]

    _atomic_write_text(path, content)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def parse_notebook(file_path: str, info: Optional[FormatInfo] = None) -> Notebook:
    """Detect format and return a :class:`Notebook` with normalized cells.

    Raises :class:`ValueError` if the file is not a recognised notebook. Use
    :func:`is_notebook` first if the caller wants to silently skip non-notebook
    files.

    When ``info`` is provided (typically from a prior :func:`scan_notebooks`
    / :func:`detect_format` call), detection is skipped — callers that walk
    a tree via :func:`scan_notebooks` can pass the already-computed
    ``FormatInfo`` back in to avoid paying the detection cost twice.
    """
    if info is None:
        info = detect_format(file_path)
    fmt = info.get("format", "not_notebook")
    if fmt == "not_notebook":
        raise ValueError(
            f"{file_path}: not a notebook ({info.get('reason', 'unknown reason')})"
        )

    language = info.get("language", "unknown")
    if fmt == "ipynb":
        return _parse_ipynb(file_path, language)
    if fmt == "native_json":
        return _parse_native_json(file_path, language)
    if fmt == "exported_text":
        return _parse_exported_text(file_path, language)
    raise ValueError(f"{file_path}: unsupported notebook format {fmt!r}")


def write_notebook(path: str, notebook: Notebook) -> None:
    """Serialize ``notebook`` back to ``path`` in its original format.

    Unchanged cells round-trip with near-zero byte difference; modified cells
    are re-serialised using the format's native encoding. Cell order and
    container key order are preserved. Writes are atomic (temp file +
    ``os.replace``) so a crash mid-write cannot leave the target truncated.
    """
    if notebook.format == "ipynb":
        _write_ipynb(path, notebook)
    elif notebook.format == "native_json":
        _write_native_json(path, notebook)
    elif notebook.format == "exported_text":
        _write_exported_text(path, notebook)
    else:
        raise ValueError(f"unknown notebook format: {notebook.format!r}")


def scan_notebooks(directory: str) -> list[ScanEntry]:
    """Recursively discover Databricks/Jupyter notebooks under ``directory``.

    Each entry carries the relative path (stable across machines), absolute
    path (robust against cwd changes), detected format, and language.

    Common build/VCS/cache directories (:data:`SKIP_DIRS`) are pruned via
    :func:`walk_filtered` so binary artifacts and virtual-environment copies
    never reach the notebook detector.
    """
    results: list[ScanEntry] = []
    for root, _dirs, files in walk_filtered(directory):
        for fname in sorted(files):
            ext = Path(fname).suffix.lower()
            if ext not in NOTEBOOK_EXTS:
                continue
            fpath = os.path.join(root, fname)
            info = detect_format(fpath)
            if info.get("format") == "not_notebook":
                continue
            results.append({
                "file": os.path.relpath(fpath, directory),
                "abs_path": os.path.abspath(fpath),
                "format": info["format"],
                "language": info["language"],
            })
    return results


def scan_and_parse_notebooks(directory: str) -> Iterator[tuple[ScanEntry, Notebook]]:
    """Yield ``(ScanEntry, Notebook)`` pairs for every notebook under ``directory``.

    Parses each notebook exactly once: :func:`scan_notebooks` already paid
    for the detection, so we hand the ``FormatInfo`` straight to
    :func:`parse_notebook` via its new ``info`` parameter. Callers that
    need both the index metadata and the parsed cell list (e.g.
    ``orchestrate_phases.build_notebook_index``) should prefer this helper
    over calling :func:`scan_notebooks` and :func:`parse_notebook`
    separately, which would reparse every file.

    Notebooks that fail to parse (malformed JSON, unexpected structure) are
    skipped silently, matching the existing ``scan_notebooks`` contract
    where detection alone is enough to enumerate files — callers that need
    per-file error reporting should call :func:`parse_notebook` directly.
    """
    for entry in scan_notebooks(directory):
        info: FormatInfo = {"format": entry["format"], "language": entry["language"]}
        try:
            nb = parse_notebook(entry["abs_path"], info=info)
        except (ValueError, OSError):
            continue
        yield entry, nb


def flatten_cells_to_script(file_path: str, target_language: Optional[str] = None) -> str:
    """Concatenate code cells of ``target_language`` into a plain script.

    Used by the validation sub-skills to hand the migrated notebook to the
    Python/Scala test entrypoint without requiring ``jupyter nbconvert``.
    Markdown/SQL/shell/fs cells are emitted as comments so line numbers stay
    meaningful for tooling that walks the output.
    """
    nb = parse_notebook(file_path)
    if target_language is None:
        target_language = nb.language

    # JVM languages use ``//`` line comments; Python/SQL-ish targets use ``#``.
    # A ``!= "scala"`` test used to send Java down the ``#`` branch, producing
    # cell headers that are syntax errors in a flattened .java unit.
    comment = "// " if target_language in ("scala", "java") else "# "
    out_lines: list[str] = []
    for cell in nb.cells:
        header = f"{comment}--- cell {cell.index} ({cell.cell_language}) ---"
        out_lines.append(header)
        if cell.cell_language == target_language:
            out_lines.append(cell.source)
        else:
            # Emit non-target cells as commented lines so line numbers survive.
            for line in cell.source.splitlines() or [""]:
                out_lines.append(f"{comment}{line}")
        out_lines.append("")
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_detect = sub.add_parser("detect", help="Print format info for a file")
    p_detect.add_argument("path")

    p_scan = sub.add_parser("scan", help="Scan a directory for notebooks")
    p_scan.add_argument("directory")

    p_parse = sub.add_parser("parse", help="Print parsed cell summary")
    p_parse.add_argument("path")

    p_flat = sub.add_parser("flatten", help="Print flattened script for validation")
    p_flat.add_argument("path")
    p_flat.add_argument("--language", default=None)

    args = parser.parse_args()

    if args.cmd == "detect":
        print(json.dumps(detect_format(args.path), indent=2))
        return 0
    if args.cmd == "scan":
        print(json.dumps(scan_notebooks(args.directory), indent=2))
        return 0
    if args.cmd == "parse":
        nb = parse_notebook(args.path)
        summary = {
            "file": nb.file,
            "format": nb.format,
            "language": nb.language,
            "cells": [
                {
                    "index": c.index,
                    "cell_type": c.cell_type,
                    "cell_language": c.cell_language,
                    "chars": len(c.source),
                }
                for c in nb.cells
            ],
        }
        print(json.dumps(summary, indent=2))
        return 0
    if args.cmd == "flatten":
        print(flatten_cells_to_script(args.path, args.language))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
