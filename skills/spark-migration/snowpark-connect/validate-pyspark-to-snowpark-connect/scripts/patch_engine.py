#!/usr/bin/env python3
"""patch_engine.py — the patch-blueprint substrate for SCOS validation.

Non-Spark I/O is never shimmed or mocked. The validation skill rewrites the
*code under test* with a small, auditable set of search/replace patches: non-Spark
I/O is turned into native Spark reads/writes (via env-var indirection so the same
patched code runs on local PySpark, a
Databricks cluster, or SCOS), secrets/widgets become inline literals, and dead
external calls (logging/telemetry) are deleted outright.

The single source of truth is ``Validation/shared/patch_blueprint.json``. Each
entry is keyed on a single ``relative_file`` and the engine derives both physical
paths from it — ``Validation/source/<relative_file>`` (the Phase A PySpark copy)
and ``Output/<relative_file>`` (the Phase B SCOS copy)::

    {
      "patches": [
        {
          "id": "ingress_users",
          "relative_file": "src/x.py",
          "note": "swap s3 read for native spark read via SCOS_INPUT_* env",
          "search": "<exact text present in BOTH copies>",
          "replace": "df = spark.read.parquet(os.environ['SCOS_INPUT_USERS'])"
        }
      ]
    }

A top-level ``search``/``replace`` is applied to **both** sides. When the source
and migrated copies have drifted (the search text differs), give per-side
overrides; **the presence of a ``source``/``migrated`` sub-block also selects
which sides to patch** (none present ⇒ both)::

    # differ between sides:
    {"id": "x", "relative_file": "src/x.py",
     "source":   {"search": "<text in PySpark copy>",  "replace": "..."},
     "migrated": {"search": "<text in SCOS copy>",     "replace": "..."}}

    # one side only (e.g. a SCOS-only fix); empty block inherits top-level search/replace:
    {"id": "x", "relative_file": "src/x.py", "search": "...", "replace": "...",
     "migrated": {}}

Regex mode (``"regex": true``)::

    {"id": "strip_exit", "relative_file": "**/*.py", "regex": true,
     "replace_all": true,
     "search": "dbutils\\\\.notebook\\\\.exit\\\\([^\\\\n]*\\\\)",
     "replace": "sys.exit(0)"}

When ``regex`` is true, ``search`` is compiled as a Python regex (default flags —
no DOTALL/MULTILINE; opt in via inline ``(?s)``/``(?m)``). ``replace`` supports
backreferences (``\\1``, ``\\g<name>``). The same uniqueness and compile gates
apply.

Glob ``relative_file`` (contains ``*``, ``?``, or ``[``)::

    {"id": "strip_all_exits", "relative_file": "src/**/*.py", "regex": true,
     "replace_all": true, "search": "dbutils\\\\.notebook\\\\.exit\\\\(.*\\\\)",
     "replace": "sys.exit(0)"}

A glob pattern expands to every matching file under each side's prefix directory.
Files with zero matches are silently skipped, but at least one file across all
sides must match the search (otherwise the entry fails). Glob entries use
top-level ``search``/``replace`` only — per-side ``source``/``migrated`` blocks
are not supported (return an error).

Rules enforced by :func:`add_patches` (the gatekeeper):

1. **Uniqueness** — with ``replace_all=false`` (the default) each emitted side's
   ``search`` must match *exactly once* in its file. 0 matches → the search has
   drifted or is wrong; >1 → ambiguous, the author must widen the context. Set
   ``replace_all=true`` to intentionally rewrite every occurrence (e.g. deleting
   every logging call).
2. **Compiles** — after applying in memory, ``.py`` files must still
   ``ast.parse`` cleanly. This is what makes removals (``replace: ""``) safe.
   For ``.ipynb`` notebooks, matching is done per code cell (a single ``search``
   cannot span a cell boundary) with uniqueness enforced across the whole
   notebook; the patched notebook is re-serialized to JSON and translated to
   Python (via ``notebook_source``) before ``ast.parse``.

Patches are applied as an **atomic batch**: entries are validated in order
against an in-memory working copy (so two entries editing the same file stack
correctly), and only if *every* entry passes are the files written, the entries
appended to the blueprint, and **both** the ``Output/`` and ``Validation/source/``
sides committed by ``validate.py`` in one ``[TEST-PATCH]`` commit (so a later
``git revert`` of that commit cleanly undoes both sides). If any entry fails,
nothing is written.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# notebook_source lives in the sibling harness/ dir — used to compile-check
# patched .ipynb notebooks (translate to Python, then ast.parse).
_harness_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harness")
if _harness_dir not in sys.path:
    sys.path.insert(0, _harness_dir)
try:
    import notebook_source as _notebook_source
except ImportError:
    _notebook_source = None  # type: ignore[assignment]

BLUEPRINT_REL = os.path.join("Validation", "shared", "patch_blueprint.json")
SIDES = ("source", "migrated")
# Each side's path prefix under conv_root; the entry's relative_file is appended.
SIDE_PREFIX = {"source": os.path.join("Validation", "source"), "migrated": "Output"}

_GLOB_CHARS = set("*?[")


def _is_glob(rel: str) -> bool:
    return bool(_GLOB_CHARS & set(rel))


# ---------------------------------------------------------------------------
# Blueprint IO
# ---------------------------------------------------------------------------


def blueprint_path(conv_root: Path) -> Path:
    return Path(conv_root) / BLUEPRINT_REL


def load_blueprint(conv_root: Path) -> Dict[str, Any]:
    path = blueprint_path(conv_root)
    if not path.is_file():
        return {"patches": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"patch_blueprint.json is not valid JSON: {exc}") from exc
    data.setdefault("patches", [])
    return data


def save_blueprint(conv_root: Path, blueprint: Dict[str, Any]) -> None:
    path = blueprint_path(conv_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(blueprint, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Smoke testing
# ---------------------------------------------------------------------------


@dataclass
class SideResult:
    side: str
    file: str
    ok: bool
    error: Optional[str]
    match_count: int
    patched_text: Optional[str]
    patch_id: Optional[str] = None


def _match_lines(text: str, search: str) -> List[int]:
    """1-based line numbers where ``search`` begins. For diagnostics only."""
    lines: List[int] = []
    start = 0
    while True:
        idx = text.find(search, start)
        if idx < 0:
            break
        lines.append(text.count("\n", 0, idx) + 1)
        start = idx + max(len(search), 1)
    return lines


def _check_scala_syntax(patched: str) -> Optional[str]:
    """Check .scala syntax after patching, using ``scalac -Ystop-after:parser``
    (pure syntax, no classpath needed) when scalac is on PATH. When scalac is
    unavailable there is no pre-commit syntax gate — the authoritative check is
    the harness build (sbt/Maven/Gradle compile). Returns an error string if the
    patch broke syntax, None if OK (or if scalac is unavailable).
    """
    import os
    import shutil
    import subprocess
    import tempfile

    scalac = shutil.which("scalac")
    if not scalac:
        return None
    with tempfile.NamedTemporaryFile(suffix=".scala", mode="w",
                                     delete=False, encoding="utf-8") as f:
        f.write(patched)
        fname = f.name
    try:
        result = subprocess.run(
            [scalac, "-Ystop-after:parser", fname],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "syntax error").strip()
            return err.split("\n")[0]
        return None
    except (subprocess.TimeoutExpired, OSError):
        return None  # scalac unavailable / timed out — rely on the harness build
    finally:
        try:
            os.unlink(fname)
        except OSError:
            pass


def _check_java_syntax(patched: str) -> Optional[str]:
    """Check .java syntax after patching, using ``javac`` parse-only when
    javac is on PATH. Falls back to a comment/string-aware brace-balance
    check when javac is unavailable. Returns an error string if the patch
    broke syntax, None if OK.
    """
    import os
    import shutil
    import subprocess
    import tempfile

    javac = shutil.which("javac")
    if javac:
        # Extract public class name (if any) to name the temp file correctly;
        # javac requires the file name to match the public class.
        import re as _re
        pub_match = _re.search(r'\bpublic\s+(?:abstract\s+|final\s+)*class\s+(\w+)', patched)
        suffix = f"{pub_match.group(1)}.java" if pub_match else "Tmp.java"
        tmp_dir = tempfile.mkdtemp()
        fname = os.path.join(tmp_dir, suffix)
        with open(fname, "w", encoding="utf-8") as f:
            f.write(patched)
        try:
            result = subprocess.run(
                [javac, "-J-Duser.language=en", "-J-Duser.country=US",
                 "-proc:none", "-implicit:none", "-Xmaxerrs", "1", fname],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                err = (result.stderr or result.stdout or "syntax error").strip()
                # Filter out non-syntax errors that javac reports due to
                # missing classpath (e.g. "cannot find symbol", "package does not exist").
                # We only care about true syntax errors here.
                first_line = err.split("\n")[0]
                syntax_indicators = [
                    "';' expected", "illegal start", "unclosed",
                    "reached end of file while parsing", "expected",
                    "not a statement", "orphaned",
                ]
                if any(ind in first_line.lower() for ind in syntax_indicators):
                    return first_line
                # Not a syntax error (likely classpath/resolution) — pass
                return None
            return None
        except (subprocess.TimeoutExpired, OSError):
            pass  # fall through to brace-balance
        finally:
            try:
                os.unlink(fname)
                os.rmdir(tmp_dir)
            except OSError:
                pass

    # Fallback: comment/string-aware brace-balance check
    depth = 0
    i = 0
    n = len(patched)
    while i < n:
        c = patched[i]
        if c == '/' and i + 1 < n:
            nxt = patched[i + 1]
            if nxt == '/':
                # line comment — skip to end of line
                i = patched.find('\n', i)
                if i == -1:
                    break
                i += 1
                continue
            elif nxt == '*':
                # block comment — skip to */
                end = patched.find('*/', i + 2)
                i = end + 2 if end != -1 else n
                continue
        elif c == '"':
            # string literal (handles simple escape sequences)
            i += 1
            while i < n and patched[i] != '"':
                if patched[i] == '\\':
                    i += 1
                i += 1
            i += 1
            continue
        elif c == '\'':
            # char literal
            i += 1
            while i < n and patched[i] != '\'':
                if patched[i] == '\\':
                    i += 1
                i += 1
            i += 1
            continue
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth < 0:
                return "unbalanced braces: unexpected '}'"
        i += 1
    if depth != 0:
        return f"unbalanced braces: {depth} unclosed '{{'"
    return None


def smoke_test_side(
    conv_root: Path,
    side_spec: Dict[str, Any],
    *,
    replace_all: bool,
    side: str,
    current_text: Optional[str] = None,
) -> SideResult:
    """Validate one side of a patch entry without writing anything.

    Checks file existence, match uniqueness (unless ``replace_all``), and that
    the patched ``.py`` text still parses. When ``current_text`` is supplied it
    is validated against that in-memory text instead of re-reading disk — this
    lets a batch stack multiple patches that touch the same file.

    When ``side_spec["regex"]`` is true, ``search`` is treated as a Python regex
    (default flags — no DOTALL/MULTILINE; authors opt in via inline ``(?s)``/
    ``(?m)``). ``replace`` supports backreferences (``\\1``, ``\\g<name>``).
    """
    rel = side_spec.get("file", "")
    search = side_spec.get("search", "")
    replace = side_spec.get("replace", "")
    use_regex = bool(side_spec.get("regex", False))

    def fail(msg: str, count: int = 0) -> SideResult:
        return SideResult(side, rel, False, msg, count, None)

    if not rel:
        return fail("missing 'file'")
    if not search:
        return fail("missing 'search' (empty search is not allowed)")

    # Notebook migration: a migrated-side patch keyed on `<name>.py` targets the
    # actual migrated file `<name>.py.ipynb`. Resolve this UP FRONT (based on disk
    # existence, independent of whether current_text is supplied) so the .ipynb
    # dispatch below and the working-copy key in add_patches stay consistent for
    # stacked patches on the same notebook.
    if side == "migrated" and rel.endswith(".py") and not rel.endswith(".py.ipynb"):
        if (Path(conv_root) / (rel + ".ipynb")).is_file():
            rel = rel + ".ipynb"

    if current_text is not None:
        text = current_text
    else:
        abs_path = Path(conv_root) / rel
        if not abs_path.is_file():
            return fail(f"file not found: {rel}")
        try:
            text = abs_path.read_text(encoding="utf-8")
        except OSError as exc:
            return fail(f"cannot read {rel}: {exc}")

    # .ipynb: match/replace per code cell, compile-check via notebook translation.
    if rel.endswith(".ipynb"):
        return _smoke_test_ipynb(text, rel, search, replace, replace_all, side, use_regex)

    if use_regex:
        try:
            pattern = re.compile(search)
        except re.error as exc:
            return fail(f"invalid regex in search: {exc}")
        matches = list(pattern.finditer(text))
        count = len(matches)
        if count == 0:
            return fail(
                f"search not found in {rel} (it may have drifted, already be applied, "
                "or be mis-copied — re-read the file and copy the exact text)",
                0,
            )
        if not replace_all and count > 1:
            return fail(
                f"ambiguous: search matched {count} times in {rel}; "
                "widen the search with surrounding context to make it "
                "unique, or set replace_all=true to rewrite every occurrence",
                count,
            )
        patched = pattern.sub(replace, text, count=0 if replace_all else 1)
    else:
        count = text.count(search)
        if count == 0:
            return fail(
                f"search not found in {rel} (it may have drifted, already be applied, "
                "or be mis-copied — re-read the file and copy the exact text)",
                0,
            )
        if not replace_all and count > 1:
            locs = _match_lines(text, search)
            return fail(
                f"ambiguous: search matched {count} times in {rel} at lines "
                f"{locs}; widen the search with surrounding context to make it "
                "unique, or set replace_all=true to rewrite every occurrence",
                count,
            )
        patched = text.replace(search, replace) if replace_all else text.replace(search, replace, 1)

    if rel.endswith(".py"):
        try:
            ast.parse(patched, filename=rel)
        except SyntaxError as exc:
            return fail(
                f"patched {rel} no longer parses: {exc.msg} (line {exc.lineno}). "
                "If this is a removal, the surrounding block may need a 'pass'.",
                count,
            )
    elif rel.endswith(".scala") or rel.endswith(".sc"):
        # Use scalac -Ystop-after:parser when available (pure syntax, no classpath
        # needed). When scalac is absent there is no pre-commit syntax gate — the
        # harness build remains the authoritative syntax/type-check gate.
        err = _check_scala_syntax(patched)
        if err:
            return fail(f"patched {rel} has syntax errors: {err}", count)
    elif rel.endswith(".java"):
        # Use javac when available; falls back to brace-balance checker.
        # The harness build (mvn/gradle compile) remains the authoritative gate.
        err = _check_java_syntax(patched)
        if err:
            return fail(f"patched {rel} has syntax errors: {err}", count)

    return SideResult(side, rel, True, None, count, patched)


def _smoke_test_ipynb(
    text: str,
    rel: str,
    search: str,
    replace: str,
    replace_all: bool,
    side: str,
    use_regex: bool = False,
) -> SideResult:
    """Validate a patch against a ``.ipynb`` notebook.

    Matching is done over each code cell's source independently (a single
    ``search`` cannot span a cell boundary), but uniqueness is enforced across the
    *whole* notebook, mirroring the ``.py`` path: with ``replace_all=false`` the
    search must match exactly once across all cells; with ``replace_all=true`` every
    occurrence in every cell is rewritten. ``use_regex`` switches matching from
    literal substring to Python regex (per cell). The patched notebook is
    re-serialized to JSON and, when ``notebook_source`` is importable, translated
    to Python and ``ast.parse``d as the compile gate.
    """

    def fail(msg: str, count: int = 0) -> SideResult:
        return SideResult(side, rel, False, msg, count, None)

    try:
        nb = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        return fail(f"cannot parse {rel} as JSON: {exc}")

    pattern = None
    if use_regex:
        try:
            pattern = re.compile(search)
        except re.error as exc:
            return fail(f"invalid regex in search: {exc}")

    def count_in(src: str) -> int:
        return len(pattern.findall(src)) if pattern is not None else src.count(search)

    cells = nb.get("cells", [])
    total_count = 0
    matching_cells: List[int] = []  # indices of code cells containing the search
    for i, cell in enumerate(cells):
        if cell.get("cell_type") != "code":
            continue
        src = cell.get("source", "")
        if isinstance(src, list):
            src = "".join(src)
        cell_count = count_in(src)
        if cell_count > 0:
            total_count += cell_count
            matching_cells.append(i)

    if total_count == 0:
        return fail(
            f"search not found in any code cell of {rel} (it may have drifted, "
            "already be applied, or be mis-copied — re-read the file and copy the exact text)",
            0,
        )
    if not replace_all and total_count > 1:
        return fail(
            f"ambiguous: search matched {total_count} times across cells "
            f"{matching_cells} in {rel}; widen the search with surrounding "
            "context to make it unique across the notebook, or set "
            "replace_all=true to rewrite every occurrence in every cell",
            total_count,
        )

    remaining = None if replace_all else 1  # non-replace_all: at most one edit total
    patched_cells: List[int] = []
    for i in matching_cells:
        cell = cells[i]
        src = cell.get("source", "")
        if isinstance(src, list):
            src = "".join(src)
        if pattern is not None:
            patched_src = pattern.sub(replace, src, count=0 if replace_all else (remaining or 0))
        elif replace_all:
            patched_src = src.replace(search, replace)
        else:
            patched_src = src.replace(search, replace, remaining or 0)
        # Re-serialize source as list of lines (notebook convention).
        cell["source"] = patched_src.splitlines(keepends=True)
        patched_cells.append(i)
        if not replace_all:
            break  # uniqueness was enforced above; the single match lives in one cell

    patched_text = json.dumps(nb, indent=1, ensure_ascii=False) + "\n"

    # Compile gate: translate ONLY the patched cell(s) to Python and ast.parse.
    # Scoping to the edited cells (not the whole notebook) keeps an unrelated
    # cell that the translator mishandles from rejecting a valid patch elsewhere.
    if _notebook_source is not None:
        for i in patched_cells:
            try:
                py_source = _notebook_source.notebook_dict_to_python({"cells": [cells[i]]})
                ast.parse(py_source, filename=rel)
            except SyntaxError as exc:
                return fail(
                    f"patched cell {i} of {rel} no longer translates to valid "
                    f"Python: {exc.msg} (line {exc.lineno}). "
                    "Check that the replacement text is syntactically valid.",
                    total_count,
                )

    return SideResult(side, rel, True, None, total_count, patched_text)


# ---------------------------------------------------------------------------
# Apply (batch)
# ---------------------------------------------------------------------------


def _expand_entry(entry: Dict[str, Any]) -> Tuple[List[Tuple[str, Dict[str, Any]]], Optional[str]]:
    """Expand one blueprint entry into ``[(side, {file, search, replace, regex}), ...]``.

    Schema (see module docstring):
      * ``relative_file`` (required) → ``Validation/source/<rel>`` (source side)
        and ``Output/<rel>`` (migrated side).
      * top-level ``search``/``replace`` are the defaults for every emitted side.
      * a ``source``/``migrated`` sub-block **selects** that side (none present ⇒
        both) and may override ``search``/``replace`` (file is always derived).
      * ``"regex": true`` on the entry → specs carry ``regex: True`` so
        ``smoke_test_side`` uses regex matching.
      * If ``relative_file`` is a glob pattern (contains ``*``, ``?``, ``[``),
        it expands against the side's prefix dir and produces one spec per
        matched file. Glob entries must use top-level search/replace (no per-side
        blocks).

    ``replace`` distinguishes an absent key from ``""`` (empty ⇒ deletion).
    Returns ``(specs, error)``; ``error`` is non-None when the entry is malformed.
    """
    rel = entry.get("relative_file")
    if not rel or not isinstance(rel, str):
        return [], "entry missing 'relative_file'"
    rel = rel.replace("\\", "/").lstrip("/")
    if not rel:
        return [], "'relative_file' is empty"

    top_search = entry.get("search")
    top_replace = entry.get("replace", "")
    use_regex = bool(entry.get("regex", False))

    present = [s for s in SIDES if isinstance(entry.get(s), dict)]
    selected = present if present else list(SIDES)

    # Glob expansion
    if _is_glob(rel):
        if present:
            return [], "glob relative_file does not support per-side source/migrated blocks"
        if top_search is None or top_search == "":
            return [], "glob entry has no 'search' — provide a top-level 'search'"
        # _expand_entry doesn't have conv_root; produce a sentinel for later expansion
        specs: List[Tuple[str, Dict[str, Any]]] = []
        for side in selected:
            specs.append((side, {
                "file": None,  # sentinel — resolved by _expand_glob_specs
                "search": top_search,
                "replace": top_replace,
                "regex": use_regex,
                "_glob_pattern": rel,
                "_side_prefix": SIDE_PREFIX[side],
            }))
        return specs, None

    # Non-glob (single file)
    specs = []
    for side in selected:
        block = entry.get(side) or {}
        search = block["search"] if "search" in block else top_search
        replace = block["replace"] if "replace" in block else top_replace
        if search is None or search == "":
            return [], (f"side '{side}' has no 'search' — provide a top-level "
                        f"'search' or one inside the '{side}' block")
        file_rel = os.path.join(SIDE_PREFIX[side], *rel.split("/"))
        specs.append((side, {"file": file_rel, "search": search, "replace": replace,
                             "regex": use_regex}))
    return specs, None


def _expand_glob_specs(
    conv_root: Path, specs: List[Tuple[str, Dict[str, Any]]]
) -> Tuple[List[Tuple[str, Dict[str, Any]]], Optional[str]]:
    """Resolve glob sentinels in specs to concrete file specs.

    Returns ``(resolved_specs, error)``. Error is non-None only when zero files
    match the glob across ALL sides.
    """
    resolved: List[Tuple[str, Dict[str, Any]]] = []
    pattern = None
    for side, spec in specs:
        if spec.get("_glob_pattern") is None:
            resolved.append((side, spec))
            continue
        pattern = spec["_glob_pattern"]
        prefix = spec["_side_prefix"]
        prefix_path = Path(conv_root) / prefix
        if not prefix_path.is_dir():
            continue
        for matched in sorted(prefix_path.glob(pattern)):
            if matched.is_file():
                file_rel = str(matched.relative_to(conv_root)).replace(os.sep, "/")
                resolved.append((side, {
                    "file": file_rel,
                    "search": spec["search"],
                    "replace": spec["replace"],
                    "regex": spec["regex"],
                }))
    if not resolved and pattern is not None:
        return [], f"search not found in any file matching '{pattern}'"
    return resolved, None


def _entry_signature(entry: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
    """A content fingerprint for an entry: its expanded (side, file, search,
    replace) tuples plus ``replace_all`` and ``regex``, independent of
    ``id``/``note``. Two entries with the same fingerprint are the *same patch*
    and the second is a redundant re-add. Returns None for a malformed entry (let
    the normal flow surface that error rather than silently deduping it). Also
    returns None for glob entries (globs expand at apply time and cannot be
    fingerprinted without conv_root)."""
    specs, err = _expand_entry(entry)
    if err:
        return None
    # Glob entries have sentinel file=None — cannot fingerprint without expansion
    if any(spec.get("file") is None for _, spec in specs):
        return None
    ra = bool(entry.get("replace_all", False))
    rx = bool(entry.get("regex", False))
    return (ra, rx, tuple(sorted(
        (side, spec["file"], spec["search"], spec["replace"]) for side, spec in specs
    )))


def _fold_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize an entry to the compact form: if it carries **both** a
    ``source`` and a ``migrated`` sub-block whose ``search`` AND ``replace`` are
    identical, fold them into a single top-level ``search``/``replace`` and drop
    the sub-blocks. Per-side blocks that genuinely differ (drifted text) or a
    lone single-side block (intentional side selection) are left untouched.
    Returns a shallow copy when it folds, else the entry unchanged."""
    s, m = entry.get("source"), entry.get("migrated")
    if not (isinstance(s, dict) and isinstance(m, dict)):
        return entry
    # Only fold pure search/replace blocks (nothing side-specific beyond them).
    if (set(s) - {"search", "replace"}) or (set(m) - {"search", "replace"}):
        return entry
    if s.get("search") != m.get("search") or s.get("replace") != m.get("replace"):
        return entry
    folded = {k: v for k, v in entry.items() if k not in ("source", "migrated")}
    if "search" in s:
        folded["search"] = s["search"]
    if "replace" in s:
        folded["replace"] = s["replace"]
    return folded


def add_patches(
    conv_root: Path, entries: List[Dict[str, Any]]
) -> Tuple[bool, List[SideResult], List[str], List[str]]:
    """Smoke-test a batch of patch entries and apply them atomically.

    Entries are processed in order against an in-memory working copy of each
    touched file, so two entries that edit the same file stack correctly. If
    **any** side of **any** entry fails its checks, nothing is written and the
    blueprint is untouched.

    **Auto-dedup:** an entry whose content fingerprint (expanded file/search/
    replace per side + ``replace_all`` + ``regex``, ignoring ``id``/``note``)
    already exists in the blueprint, or repeats earlier in the same batch, is a
    redundant re-add — it is **skipped** (not re-applied, not re-appended). This
    keeps the blueprint free of look-alike duplicates when an author or a runner
    re-submits a patch that is already in place, and avoids the spurious "search
    not found" that re-applying an already-applied patch would otherwise raise.
    Glob entries are never deduped (their expansion depends on the filesystem).

    Returns ``(ok, results, written_files, deduped_ids)`` where ``written_files``
    are the conv-root-relative paths modified on disk and ``deduped_ids`` are the
    ids of entries skipped as duplicates.
    """
    if not entries:
        return False, [SideResult("(batch)", "", False, "no patch entries supplied", 0, None)], [], []

    blueprint = load_blueprint(conv_root)
    existing_sigs = set()
    for p in blueprint.get("patches", []):
        sig = _entry_signature(p)
        if sig is not None:
            existing_sigs.add(sig)

    working: Dict[str, str] = {}      # rel path -> current in-memory text
    results: List[SideResult] = []
    applied: List[Dict[str, Any]] = []
    deduped_ids: List[str] = []
    batch_sigs: set = set()

    for entry in entries:
        pid = entry.get("id")
        if not pid:
            results.append(SideResult("(entry)", "", False, "entry missing 'id'", 0, None))
            return False, results, [], []

        specs, err = _expand_entry(entry)
        if err:
            results.append(SideResult("(entry)", "", False, err, 0, None, patch_id=pid))
            return False, results, [], []

        sig = _entry_signature(entry)
        if sig is not None and (sig in existing_sigs or sig in batch_sigs):
            results.append(SideResult("(deduped)", specs[0][1].get("file", "") if specs else "",
                                      True, None, 0, None, patch_id=pid))
            deduped_ids.append(pid)
            continue
        if sig is not None:
            batch_sigs.add(sig)

        # Resolve glob sentinels to concrete file specs
        is_glob_entry = any(spec.get("_glob_pattern") for _, spec in specs)
        if is_glob_entry:
            specs, err = _expand_glob_specs(conv_root, specs)
            if err:
                results.append(SideResult("(entry)", "", False, err, 0, None, patch_id=pid))
                return False, results, [], []

        replace_all = bool(entry.get("replace_all", False))

        if is_glob_entry:
            # Glob semantics: skip files with 0 matches; require >=1 file matches
            any_matched = False
            for side, spec in specs:
                rel = spec["file"]
                # Read back either the original rel or a previously resolved
                # .py.ipynb fallback so stacked patches to the same migrated
                # notebook build on each other (mirrors the non-glob path below).
                r = smoke_test_side(
                    conv_root, spec, replace_all=replace_all, side=side,
                    current_text=working.get(rel) or working.get(rel + ".ipynb"),
                )
                r.patch_id = pid
                if r.ok:
                    any_matched = True
                    results.append(r)
                    working[r.file if r.file else rel] = r.patched_text or ""
                elif r.match_count == 0 and "search not found" in (r.error or ""):
                    # Skip files with 0 matches silently
                    continue
                else:
                    # Non-zero-match failures (ambiguous, parse error) are real errors
                    results.append(r)
                    return False, results, [], []
            if not any_matched:
                glob_pat = entry.get("relative_file", "?")
                results.append(SideResult("(entry)", "", False,
                                          f"search not found in any file matching '{glob_pat}'",
                                          0, None, patch_id=pid))
                return False, results, [], []
        else:
            for side, spec in specs:
                rel = spec["file"]
                # Look up in-memory content using either the original rel or a previously
                # resolved fallback path (e.g. rel.py → rel.py.ipynb).
                cached_text = working.get(rel) or working.get(rel + ".ipynb")
                r = smoke_test_side(
                    conv_root, spec, replace_all=replace_all, side=side,
                    current_text=cached_text,
                )
                r.patch_id = pid
                results.append(r)
                if not r.ok:
                    return False, results, [], []
                # Use r.file in case smoke_test_side resolved a .py.ipynb fallback.
                working[r.file if r.file else rel] = r.patched_text or ""
        applied.append(entry)

    # All entries valid — commit the applied (non-deduped) ones to disk + blueprint.
    written: List[str] = []
    for rel, text in working.items():
        (Path(conv_root) / rel).write_text(text, encoding="utf-8")
        written.append(rel)

    new_ids = {e.get("id") for e in applied}
    # Persist in the compact form: fold identical source/migrated sub-blocks into
    # a top-level search/replace (also normalizes any pre-existing entries that
    # were authored with redundant per-side blocks).
    folded_new = [_fold_entry(e) for e in applied]
    blueprint["patches"] = [
        _fold_entry(p) for p in blueprint.get("patches", []) if p.get("id") not in new_ids
    ]
    blueprint["patches"].extend(folded_new)
    save_blueprint(conv_root, blueprint)

    return True, results, written, deduped_ids


# ---------------------------------------------------------------------------
# Known-patches library
# ---------------------------------------------------------------------------
# Each entry in KNOWN_PATCHES is a dict with:
#   id          — stable identifier matching the pattern family
#   description — one-line explanation
#   detect(source_text, relative_file) -> list[dict]   — returns one dict per
#                 match; empty list means no match in this file
#   build_patch(match_info, relative_file) -> dict      — returns a patch entry
#                 in the standard blueprint schema (id, relative_file, search,
#                 replace, plus optional regex/replace_all/note)
#
# suggest_known_patches() drives the full pipeline: run every detector,
# call the corresponding builder for each match, and return the patch list.
# ---------------------------------------------------------------------------


def _normalize_env_name(s: str) -> str:
    """Upper-snake env-var suffix: non-alphanum → '_', trailing '_' stripped."""
    result = re.sub(r"[^A-Za-z0-9]+", "_", s).upper().rstrip("_")
    return result or "TABLE"


# --- remove_os_system -------------------------------------------------------

def _detect_os_system(source_text: str, relative_file: str) -> List[Dict[str, Any]]:
    """Fire when at least one standalone os.system(...) call exists."""
    if re.search(r"(?m)^[ \t]*os\.system\(", source_text):
        return [{}]
    return []


def _build_os_system_patch(match_info: Dict[str, Any], relative_file: str) -> Dict[str, Any]:
    return {
        "id": "remove_os_system",
        "relative_file": relative_file,
        "regex": True,
        "replace_all": True,
        "note": "os.system() → pass — not available in Spark/SCOS",
        # (?m) enables ^ / $ to match per-line; capture leading whitespace so
        # the replacement preserves indentation.
        "search": r"(?m)^(\s*)os\.system\([^\n]*\)\s*(#.*)?$",
        "replace": r"\1pass  # SCOS: removed os.system",
    }


# --- remove_top_level_sys_path_mutation --------------------------------------

def _detect_sys_path_mutation(source_text: str, relative_file: str) -> List[Dict[str, Any]]:
    """Fire when at least one module-scope (indent=0) sys.path.insert/append(...) exists."""
    if re.search(r"(?m)^sys\.path\.(?:insert|append)\(", source_text):
        return [{}]
    return []


def _build_sys_path_mutation_patch(match_info: Dict[str, Any], relative_file: str) -> Dict[str, Any]:
    return {
        "id": "remove_top_level_sys_path_mutation",
        "relative_file": relative_file,
        "regex": True,
        "replace_all": True,
        "note": "module-scope sys.path.insert()/append() → comment — dead plumbing in managed Spark runtime",
        "search": r"(?m)^sys\.path\.(?:insert|append)\([^\n]*\)\s*(#.*)?$",
        "replace": "# SCOS: removed sys.path mutation",
    }


# --- saveastable_env_indirection ---------------------------------------------

def _detect_saveastable_env(source_text: str, relative_file: str) -> List[Dict[str, Any]]:
    """Return one entry per unique string-literal arg to .saveAsTable().

    Deliberately conservative: matches only ``.saveAsTable("literal")`` with a
    single bare string argument. Real-world calls that carry trailing kwargs
    (``mode=``, ``format=``, ``path=``) or dynamic names (``"schema." + var``,
    f-strings) are left for the LLM to rewrite, since env indirection there must
    preserve the extra arguments — not something a blanket search/replace can do
    safely.
    """
    pat = re.compile(r'\.saveAsTable\(\s*(["\'])([^"\']+)\1\s*\)')
    seen: set = set()
    results: List[Dict[str, Any]] = []
    for m in pat.finditer(source_text):
        literal = m.group(2)
        # Already wrapped with os.environ — impossible when the arg is a bare
        # string literal, but guard defensively against adjacent context.
        if "os.environ" in m.group(0):
            continue
        if literal not in seen:
            seen.add(literal)
            results.append({"literal": literal, "full_match": m.group(0)})
    return results


def _build_saveastable_env_patch(match_info: Dict[str, Any], relative_file: str) -> Dict[str, Any]:
    literal = match_info["literal"]
    env_suffix = _normalize_env_name(literal)
    env_var = f"SCOS_OUTPUT_{env_suffix}"
    search = match_info["full_match"]
    replace = f'.saveAsTable(os.environ.get("{env_var}", "{literal}"))'
    return {
        "id": f"saveastable_env_{env_suffix.lower()}",
        "relative_file": relative_file,
        "replace_all": True,
        "note": f".saveAsTable('{literal}') → env-var indirection via {env_var}",
        "search": search,
        "replace": replace,
    }


# --- widget_get_env_indirection ----------------------------------------------

def _detect_widget_get_env(source_text: str, relative_file: str) -> List[Dict[str, Any]]:
    """Return one entry per unique string-literal key passed to dbutils.widgets.get()."""
    pat = re.compile(r'dbutils\.widgets\.get\(\s*(["\'])([^"\']+)\1\s*\)')
    seen: set = set()
    results: List[Dict[str, Any]] = []
    for m in pat.finditer(source_text):
        key = m.group(2)
        if key not in seen:
            seen.add(key)
            results.append({"key": key, "full_match": m.group(0)})
    return results


def _build_widget_get_env_patch(match_info: Dict[str, Any], relative_file: str) -> Dict[str, Any]:
    key = match_info["key"]
    env_var = key.upper()
    search = match_info["full_match"]
    replace = f'os.environ["{env_var}"]'
    return {
        "id": f"widget_get_{env_var.lower()}",
        "relative_file": relative_file,
        "replace_all": True,
        "note": f"dbutils.widgets.get('{key}') → os.environ['{env_var}']",
        "search": search,
        "replace": replace,
    }


# --- remove_dbutils_notebook_exit --------------------------------------------

def _detect_dbutils_notebook_exit(source_text: str, relative_file: str) -> List[Dict[str, Any]]:
    """Fire when at least one dbutils.notebook.exit(...) call exists."""
    if re.search(r"dbutils\.notebook\.exit\(", source_text):
        return [{}]
    return []


def _build_dbutils_notebook_exit_patch(match_info: Dict[str, Any], relative_file: str) -> Dict[str, Any]:
    return {
        "id": "remove_dbutils_notebook_exit",
        "relative_file": relative_file,
        "regex": True,
        "replace_all": True,
        "note": "dbutils.notebook.exit(...) → sys.exit(0) — Databricks notebook control API",
        "search": r"dbutils\.notebook\.exit\([^\n]*\)",
        "replace": "sys.exit(0)",
    }


# --- remove_drop_table_sql --------------------------------------------------

def _detect_drop_table_sql(source_text: str, relative_file: str) -> List[Dict[str, Any]]:
    """Fire when at least one spark.sql('DROP TABLE ...' / "DROP TABLE ...") call exists."""
    if re.search(
        r"""(?im)^[ \t]*spark\.sql\(\s*(?:"drop\s+table|'drop\s+table)""",
        source_text,
    ):
        return [{}]
    return []


def _build_drop_table_sql_patch(match_info: Dict[str, Any], relative_file: str) -> Dict[str, Any]:
    return {
        "id": "remove_drop_table_sql",
        "relative_file": relative_file,
        "regex": True,
        "replace_all": True,
        "note": "spark.sql('DROP TABLE ...') → pass — destructive DDL must not run against shared test schema",
        # (?mi): case-insensitive + per-line ^ / $; capture leading whitespace for indentation
        "search": r"""(?mi)^(\s*)spark\.sql\(\s*(?:"drop\s+table[^"]*"|'drop\s+table[^']*')\s*\)\s*(#.*)?$""",
        "replace": r"\1pass  # SCOS: removed DROP TABLE (destructive)",
    }


# --- widget_declaration_env_default ------------------------------------------
# The declaration counterpart to widget_get_env_indirection. Where widget_get
# rewrites a *read* (``dbutils.widgets.get("k")`` → ``os.environ["K"]``), this
# rewrites the *declaration* (``dbutils.widgets.text("k", "default", ...)``) to
# ``os.environ.setdefault("K", "default")``. Because the env-var name is
# ``name.upper()`` — the exact key widget_get uses — the two compose: the
# declaration seeds the default, the read consumes it, and neither depends on
# the Databricks widget runtime.

# Databricks form: dbutils.widgets.text|dropdown|combobox|multiselect("name", "default", ...)
_DBUTILS_WIDGET_DECL = re.compile(
    r'dbutils\.widgets\.(?:text|dropdown|combobox|multiselect)\('
    r'\s*(["\'])(?P<name>[^"\']+)\1'          # positional name (string literal)
    r'\s*,\s*(["\'])(?P<default>[^"\']*)\3'    # positional default (string literal)
    r'[^)]*\)'                                  # trailing args (choices/label) up to close paren
)


def _detect_widget_declaration(source_text: str, relative_file: str) -> List[Dict[str, Any]]:
    """Return one entry per unique dbutils.widgets declaration with literal name+default.

    Covers ``dbutils.widgets.text/dropdown/combobox/multiselect("name", "default", ...)``.
    Declarations whose name or default is not a bare string literal are skipped
    (left to the LLM), matching the conservative posture of the other detectors.
    (ipywidgets is handled separately — see ``_detect_ipywidgets``.)
    """
    seen: set = set()
    results: List[Dict[str, Any]] = []
    for m in _DBUTILS_WIDGET_DECL.finditer(source_text):
        name = m.group("name")
        if name not in seen:
            seen.add(name)
            results.append(
                {"name": name, "default": m.group("default"), "full_match": m.group(0)}
            )
    return results


def _build_widget_declaration_patch(match_info: Dict[str, Any], relative_file: str) -> Dict[str, Any]:
    name = match_info["name"]
    default = match_info["default"]
    env_var = name.upper()
    return {
        "id": f"widget_decl_{env_var.lower()}",
        "relative_file": relative_file,
        "replace_all": True,
        "note": (
            f"widget declaration '{name}' → os.environ.setdefault('{env_var}', "
            f"'{default}') — seeds the default the widget_get rewrite reads"
        ),
        "search": match_info["full_match"],
        "replace": f'os.environ.setdefault("{env_var}", "{default}")',
    }


# --- ipywidgets_env_indirection ----------------------------------------------
# ipywidgets differ from dbutils.widgets in two ways that matter here:
#   1. The value is read via attribute access on the widget object
#      (``w.value``), not a standalone ``.get("name")`` call.
#   2. The library is not guaranteed to be installed in the SCOS / local PySpark
#      harness venv.
# So the read side cannot compose with widget_get the way the dbutils
# declaration does. Instead this detector rewrites BOTH sites so no ipywidgets
# object is needed at runtime:
#   * the constructor call  →  os.environ.get("KEY", "<default>")  (a plain str)
#   * every ``<var>.value`` read of that widget  →  ``<var>``      (already a str)
# For the inline form ``widgets.Text(value=…, description=…).value`` there is no
# intermediate variable, so the whole ``…).value`` expression collapses to the
# env lookup in one shot.
#
# The bare ``import ipywidgets`` line is intentionally left in place: removing it
# deterministically is unsafe when non-literal widget calls (which this detector
# skips) still reference the module. If the package is absent from the venv, that
# is handled during venv seeding / by the LLM for any residual references.
#
# Only string-literal ``value=`` args fire (the dominant parameter case). Numeric
# / boolean widgets (IntText, Checkbox, sliders) and dynamic values are skipped.

_IPY_CTORS = (
    "Text|Textarea|Password|Dropdown|Combobox|Select|SelectMultiple"
    "|RadioButtons|ToggleButtons"
)
_IPYWIDGETS_DESC = re.compile(r'description\s*=\s*(["\'])([^"\']+)\1')
_IPYWIDGETS_VALUE = re.compile(r'value\s*=\s*(["\'])([^"\']*)\1')
# Assigned form: VAR = widgets.Ctor(...)  — NOT immediately followed by .value
# (that inline case is handled by _IPY_INLINE below). Args stop at the first ')'.
_IPY_ASSIGN = re.compile(
    r'(?P<var>[A-Za-z_]\w*)\s*=\s*'
    r'(?P<call>(?:ipywidgets|widgets)\.(?:' + _IPY_CTORS + r')\((?P<args>[^\n)]*)\))'
    r'(?!\s*\.value)'
)
# Inline form: widgets.Ctor(...).value  (with or without an assignment target).
_IPY_INLINE = re.compile(
    r'(?P<full>(?:ipywidgets|widgets)\.(?:' + _IPY_CTORS + r')\((?P<args>[^\n)]*)\)\.value)'
)


def _ipy_value_write_re(var: str) -> str:
    """Regex matching a write to ``<var>.value`` (plain or augmented assignment).

    Excludes ``==`` comparisons via the ``(?!=)`` tail so only real writes match.
    """
    return (
        r"\b" + re.escape(var)
        + r"\.value\s*(?:[+\-*/%@&|^]|//|\*\*|<<|>>)?=(?!=)"
    )


def _detect_ipywidgets(source_text: str, relative_file: str) -> List[Dict[str, Any]]:
    """Return decl/read/inline patch matches for ipywidgets parameter widgets.

    See the module comment above the regexes for the rewrite model. Each returned
    dict carries a ``kind`` (``decl``/``read``/``inline``) that ``_build_ipywidgets_patch``
    dispatches on.
    """
    results: List[Dict[str, Any]] = []
    seen: set = set()
    for m in _IPY_ASSIGN.finditer(source_text):
        args = m.group("args")
        # A nested call after value= (e.g. description=fn()) makes the [^\n)]*
        # capture stop at the inner ')', truncating the constructor. Skip it —
        # a truncated search would build a patch that fails ast.parse and reject
        # the whole atomic batch, dropping every other patch for the file.
        if "(" in args:
            continue
        val_m = _IPYWIDGETS_VALUE.search(args)
        if not val_m:
            continue
        var = m.group("var")
        # Our collapse-to-str model assumes the widget value is read-only. If
        # <var>.value is ever written back (`w.value = ...`, `w.value += ...`),
        # skip the whole widget so the read patch can't turn a write into a
        # broken rebind; leave it for the LLM.
        if re.search(_ipy_value_write_re(var), source_text):
            continue
        desc_m = _IPYWIDGETS_DESC.search(args)
        key = _normalize_env_name(desc_m.group(2)) if desc_m else var.upper()
        default = val_m.group(2)
        decl_id = f"ipywidget_decl_{key.lower()}"
        if decl_id not in seen:
            seen.add(decl_id)
            results.append(
                {"kind": "decl", "key": key, "default": default, "call": m.group("call")}
            )
        # Only emit the read-collapse patch when <var>.value is actually read —
        # a zero-match entry would fail the whole atomic patch batch (non-glob
        # entries treat 0 matches as a hard error).
        read_id = f"ipywidget_read_{var}"
        if read_id not in seen and re.search(r"\b" + re.escape(var) + r"\.value\b", source_text):
            seen.add(read_id)
            results.append({"kind": "read", "var": var})
    for m in _IPY_INLINE.finditer(source_text):
        args = m.group("args")
        if "(" in args:
            continue
        val_m = _IPYWIDGETS_VALUE.search(args)
        if not val_m:
            continue
        desc_m = _IPYWIDGETS_DESC.search(args)
        default = val_m.group(2)
        # No intermediate var here, so fall back to the default value (not a
        # shared constant) for the key, so two description-less inline widgets
        # get distinct ids/env vars instead of colliding on one.
        key = _normalize_env_name(desc_m.group(2)) if desc_m else _normalize_env_name(default)
        inline_id = f"ipywidget_inline_{key.lower()}"
        if inline_id not in seen:
            seen.add(inline_id)
            results.append(
                {"kind": "inline", "key": key, "default": default, "full": m.group("full")}
            )
    return results


def _build_ipywidgets_patch(match_info: Dict[str, Any], relative_file: str) -> Dict[str, Any]:
    kind = match_info["kind"]
    if kind == "read":
        var = match_info["var"]
        return {
            "id": f"ipywidget_read_{var}",
            "relative_file": relative_file,
            "regex": True,
            "replace_all": True,
            "note": f"ipywidgets {var}.value → {var} — value already resolved from env at the declaration",
            "search": r"\b" + re.escape(var) + r"\.value\b",
            "replace": var,
        }
    key = match_info["key"]
    default = match_info["default"]
    replace = f'os.environ.get("{key}", "{default}")'
    if kind == "inline":
        return {
            "id": f"ipywidget_inline_{key.lower()}",
            "relative_file": relative_file,
            "replace_all": True,
            "note": f"inline ipywidgets .value → os.environ.get('{key}', '{default}')",
            "search": match_info["full"],
            "replace": replace,
        }
    # decl
    return {
        "id": f"ipywidget_decl_{key.lower()}",
        "relative_file": relative_file,
        "replace_all": True,
        "note": f"ipywidgets constructor → os.environ.get('{key}', '{default}') (no live widget object)",
        "search": match_info["call"],
        "replace": replace,
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

KNOWN_PATCHES: List[Dict[str, Any]] = [
    {
        "id": "remove_os_system",
        "description": (
            "Replace standalone os.system(...) lines with pass — "
            "os.system() is unavailable in Spark/SCOS"
        ),
        "detect": _detect_os_system,
        "build_patch": _build_os_system_patch,
    },
    {
        "id": "remove_top_level_sys_path_mutation",
        "description": (
            "Remove module-scope (indent=0) sys.path.insert(...)/append(...) calls — "
            "dead plumbing in a managed Spark/SCOS runtime"
        ),
        "detect": _detect_sys_path_mutation,
        "build_patch": _build_sys_path_mutation_patch,
    },
    {
        "id": "saveastable_env_indirection",
        "description": (
            "Redirect .saveAsTable('<literal>') through "
            "os.environ.get('SCOS_OUTPUT_<NAME>', '<literal>') for harness capture"
        ),
        "detect": _detect_saveastable_env,
        "build_patch": _build_saveastable_env_patch,
    },
    {
        "id": "widget_get_env_indirection",
        "description": (
            "Replace dbutils.widgets.get('<key>') with os.environ['<KEY>'] — "
            "Databricks widget API unavailable in local PySpark / SCOS"
        ),
        "detect": _detect_widget_get_env,
        "build_patch": _build_widget_get_env_patch,
    },
    {
        "id": "widget_declaration_env_default",
        "description": (
            "Rewrite dbutils.widgets.text/dropdown/combobox/multiselect('<name>', "
            "'<default>', ...) to os.environ.setdefault('<NAME>', '<default>'), "
            "seeding the default that the widget_get rewrite reads"
        ),
        "detect": _detect_widget_declaration,
        "build_patch": _build_widget_declaration_patch,
    },
    {
        "id": "ipywidgets_env_indirection",
        "description": (
            "Rewrite ipywidgets parameter widgets (Text/Dropdown/... with a "
            "string-literal value=) so no live widget object survives: the "
            "constructor becomes os.environ.get('<KEY>', '<default>') and every "
            "<var>.value read collapses to <var>"
        ),
        "detect": _detect_ipywidgets,
        "build_patch": _build_ipywidgets_patch,
    },
    {
        "id": "remove_dbutils_notebook_exit",
        "description": (
            "Replace dbutils.notebook.exit(...) with sys.exit(0) — "
            "Databricks notebook control API absent in Spark/SCOS"
        ),
        "detect": _detect_dbutils_notebook_exit,
        "build_patch": _build_dbutils_notebook_exit_patch,
    },
    {
        "id": "remove_drop_table_sql",
        "description": (
            "Replace spark.sql('DROP TABLE [IF EXISTS] ...') with pass — "
            "destructive DDL must not run against the shared Snowflake test schema"
        ),
        "detect": _detect_drop_table_sql,
        "build_patch": _build_drop_table_sql_patch,
    },
]


def suggest_known_patches(
    source_text: str,
    relative_file: str,
) -> List[Dict[str, Any]]:
    """Run every KNOWN_PATCHES detector against *source_text* and return patch entries.

    Each returned dict is a valid patch entry (same schema ``patch-add`` expects).
    Within a single file, entries are de-duplicated by ``id`` so the same
    high-confidence pattern doesn't produce two entries for the same file.
    """
    patches: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for kp in KNOWN_PATCHES:
        try:
            matches = kp["detect"](source_text, relative_file)
        except Exception:  # noqa: BLE001 — detector bugs must not abort the sweep
            continue
        for m in matches:
            try:
                entry = kp["build_patch"](m, relative_file)
            except Exception:  # noqa: BLE001
                continue
            pid = entry.get("id", "")
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                patches.append(entry)
    return patches


# ---------------------------------------------------------------------------
# Investigation scan — sites the patch-author must LOOK INTO (no auto-fix).
#
# Unlike KNOWN_PATCHES (which carry a confident search/replace), these detectors
# only flag *where* a non-Spark I/O or namespace concern lives. The patch-author
# works the resulting worklist one category at a time — authoring + applying a
# patch per pattern — instead of reading whole files to rediscover them. The list
# is intentionally NOT exhaustive; the agent still sweeps for anything missed.
#
# Each pattern is (category, compiled_regex, hint). A line matching the regex
# becomes one investigation site. Patterns already handled by KNOWN_PATCHES
# (widgets, notebook.exit, os.system, sys.path, bare-literal saveAsTable,
# DROP TABLE) are deliberately excluded so the worklist stays residual-only.
# ---------------------------------------------------------------------------

_INVESTIGATION_PATTERNS: List[Tuple[str, "re.Pattern[str]", str]] = [
    (
        "cloud_read_write",
        re.compile(r"""(?:s3a?://|dbfs:/|gs://|abfss?://|wasbs?://)"""),
        "Cloud path. Redirect the read to os.environ['SCOS_INPUT_<ID>'] "
        "(or the write to SCOS_SINK_<ID>). See the Patch recipes tables in patch-author.md.",
    ),
    (
        "cloud_sdk",
        re.compile(r"""\bboto3\b|\.get_object\(|\.put_object\(|secretsmanager"""),
        "boto3 / cloud SDK. Rewrite reads to native Spark via SCOS_INPUT_<ID>, "
        "secrets to an inline literal, telemetry writes to deletion.",
    ),
    (
        "connector_read",
        re.compile(r"""\.format\(\s*["'](?:snowflake|jdbc|redshift|bigquery|mongo)["']"""),
        "Connector read — PER-SIDE patch: source→spark.table(...), "
        "migrated→rebind sfDatabase/sfSchema (or spark.table if no driver).",
    ),
    (
        "namespace_read",
        re.compile(
            r"""(?i)^(?=.*(?:spark\.table|spark\.sql|\bfrom\b|\bjoin\b))"""
            r""".*["'][A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_]"""
        ),
        "Possible hardcoded DB.SCHEMA.TABLE in a table/SQL read. If it is a prod "
        "qualifier, rebind the prefix to SCOS_DATABASE_NAME / SCOS_OUTPUT_SCHEMA. "
        "NOTE: this is the noisiest detector — it also matches dotted literals in "
        "log/f-strings that are not table names; expect false positives and skip those.",
    ),
    (
        "sf_namespace_option",
        re.compile(r"""\.option\(\s*["'](?:sfDatabase|sfSchema)["']\s*,\s*["'][^"']+["']"""),
        "Hardcoded sfDatabase/sfSchema literal. Rebind to SCOS_DATABASE_NAME / "
        "SCOS_OUTPUT_SCHEMA (migrated side). NOTE: this alone does NOT fix a "
        "source-side connector read — see connector_read.",
    ),
    (
        "file_open",
        re.compile(r"""(?<![\w.])open\(|smart_open|fsspec"""),
        "File open — if it reads a config/aux blob the workload needs, redirect to "
        "os.environ['SCOS_TEST_AUX_<NAME>'] and declare the table (relational:false).",
    ),
    (
        "external_dep",
        re.compile(r"""\brequests\.|\bpyodbc\.|dbutils\.fs\.|dbutils\.secrets\."""),
        "External dependency. Rewrite to native Spark, an inline literal, or delete "
        "if it carries no data into the computation.",
    ),
    (
        "display",
        re.compile(r"""(?<![\w.])display\(|displayHTML\("""),
        "Databricks viewer (undefined in PySpark/SCOS → NameError). If display_only "
        "sinks were synthesized, rewrite display(EXPR) to a SCOS_SINK_DISPLAY_<N> "
        "write; else delete.",
    ),
]


def scan_investigation_sites(
    source_text: str,
    relative_file: str,
) -> List[Dict[str, Any]]:
    """Return a worklist of non-Spark-I/O sites the patch-author should investigate.

    One entry per unique (category, stripped-line) within *source_text*, carrying
    the first line number, an occurrence count, and a hint. These are candidates,
    not confident patches — no ``search``/``replace`` is emitted.
    """
    sites: List[Dict[str, Any]] = []
    index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    lines = source_text.splitlines()
    for lineno, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for category, pattern, hint in _INVESTIGATION_PATTERNS:
            if not pattern.search(raw):
                continue
            key = (category, stripped)
            existing = index.get(key)
            if existing is None:
                entry = {
                    "category": category,
                    "relative_file": relative_file,
                    "line": lineno,
                    "text": stripped[:200],
                    "occurrences": 1,
                    "hint": hint,
                }
                index[key] = entry
                sites.append(entry)
            else:
                existing["occurrences"] += 1
    return sites


# Migration-tool I/O annotations. The migrate skill's ``spark_io_detect`` recipe
# prepends a ``# SCOS: [<CODE>-<STATUS>] spark_io_detect: <msg>`` comment above
# each Spark read/write it classifies (file I/O → SPRKCNTPY3200, streaming →
# SPRKCNTPY2000, JDBC, …). Those markers land in the MIGRATED (`Output/`) copy
# and pinpoint exactly the I/O that needs a stage/table or a harness patch — a
# higher-signal worklist than our own regex sweep. Surface them too.
_SCOS_IO_ANNOTATION = re.compile(r"#\s*SCOS:\s*\[[^\]]*\]\s*spark_io_detect\b")


def scan_scos_annotations(
    source_text: str,
    relative_file: str,
) -> List[Dict[str, Any]]:
    """Flag `# SCOS: [...] spark_io_detect: ...` markers the migrate skill emitted.

    Run this over the **migrated** (`Output/`) copy, where the annotations live.
    One entry per annotated line, category ``scos_io_annotation``.
    """
    sites: List[Dict[str, Any]] = []
    for lineno, raw in enumerate(source_text.splitlines(), start=1):
        if _SCOS_IO_ANNOTATION.search(raw):
            sites.append({
                "category": "scos_io_annotation",
                "relative_file": relative_file,
                "line": lineno,
                "text": raw.strip()[:200],
                "occurrences": 1,
                "hint": "Migration tool (spark_io_detect) flagged this I/O. It needs a "
                        "Snowflake stage/table or a harness patch (SCOS_INPUT/SINK "
                        "redirect) — investigate the annotated read/write.",
            })
    return sites
