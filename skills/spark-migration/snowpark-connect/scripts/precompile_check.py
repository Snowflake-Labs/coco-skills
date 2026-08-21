"""Phase 0.5 pre-flight: detect (and safely auto-fix) *pre-existing* Python
syntax errors in the migrated source before recipes and the LLM analyzer run.

Why this exists
---------------
Databricks-exported notebook cells occasionally ship with syntax errors that
never compiled in the source — the canonical case is an entire cell body that
is stray-indented at module level (``IndentationError: unexpected indent``).
LibCST recipes (Phase 0.5) call ``cst.parse_module`` and *silently skip*
un-parseable input, so such a cell survives untouched into Phase 2. There the
fixer's compile guard reverts the *whole file* on any non-compile — and it
cannot tell "my edit broke it" from "it was already broken", so it reverts on
every pass (parallel and solo), burning agent dispatches without ever fixing
anything.

This pre-flight closes that gap deterministically:

  1. It compiles every Python unit (whole ``.py`` file, or each python code
     cell of a notebook).
  2. For units that do not compile, it attempts a small set of **guarded,
     whitespace-only** auto-fixes (uniform dedent; module-scope logical-line
     dedent). A transform is accepted *only* if the result compiles, so it can
     never make a unit worse or change semantics beyond indentation.
  3. Every unit that started broken is recorded in
     ``migration_state.json["preexisting_syntax"]`` as
     ``{file, cell_id, error, auto_fixed}`` so downstream phases can treat a
     residual (unfixable) pre-existing error as a *source* problem rather than
     a fixer-caused regression (see ``scos_gates.py`` and
     ``agents/fixer.md``).

The module is stdlib + ``notebook_io`` only (no third-party deps), so it can be
invoked with a bare ``python3`` like ``notebook_io`` itself, or via ``uv run``.
It is idempotent: re-running on already-fixed source is a no-op.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from datetime import datetime, timezone
from typing import Optional


SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from notebook_io import (  # noqa: E402
    detect_format,
    parse_notebook,
    write_notebook,
)


# ---------------------------------------------------------------------------
# Compilation helpers
# ---------------------------------------------------------------------------

def _compile_error(source: str) -> Optional[str]:
    """Return a one-line error string if ``source`` is not valid Python, else
    ``None`` (it compiles)."""
    try:
        compile(source, "<unit>", "exec")
        return None
    except SyntaxError as e:
        loc = f" (line {e.lineno})" if e.lineno else ""
        return f"{type(e).__name__}: {e.msg}{loc}"
    except ValueError as e:  # e.g. null bytes
        return f"ValueError: {e}"


def _compiles(source: str) -> bool:
    return _compile_error(source) is None


# ---------------------------------------------------------------------------
# Guarded, whitespace-only auto-fixes
# ---------------------------------------------------------------------------

def _scan_line(line: str, depth: int, in_str: Optional[str]) -> tuple[int, Optional[str], bool]:
    """Advance the bracket-depth / string-state machine across one physical
    line. Returns ``(depth, in_str, ended_with_backslash)``.

    Only triple-quoted strings persist across a line boundary; an unterminated
    single/double-quoted string is reset at end-of-line (Python would treat it
    as an error anyway, and the re-compile acceptance gate is the real safety
    net for any mis-tracking here).
    """
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if in_str is not None:
            if c == "\\":            # escape (valid in both simple and triple)
                i += 2
                continue
            if line.startswith(in_str, i):
                i += len(in_str)
                in_str = None
                continue
            i += 1
            continue
        if c == "#":                 # comment runs to end of line
            break
        if c in "([{":
            depth += 1
            i += 1
            continue
        if c in ")]}":
            depth = max(0, depth - 1)
            i += 1
            continue
        if c in ("'", '"'):
            triple = line[i:i + 3]
            if triple in ('"""', "'''"):
                in_str = triple
                i += 3
                continue
            in_str = c
            i += 1
            continue
        i += 1
    ended_with_backslash = line.endswith("\\") and in_str is None
    # Non-triple strings do not span raw line boundaries.
    if in_str is not None and len(in_str) == 1:
        in_str = None
    return depth, in_str, ended_with_backslash


def _module_scope_starts(lines: list[str]) -> list[int]:
    """Return indices of physical lines that begin a logical statement at
    bracket-depth 0 and outside any string (i.e. genuine module-scope
    statements, not continuation lines inside ``(...)`` or a triple-quoted
    string)."""
    starts: list[int] = []
    depth = 0
    in_str: Optional[str] = None
    prev_cont = False
    for idx, line in enumerate(lines):
        if depth == 0 and in_str is None and not prev_cont:
            starts.append(idx)
        depth, in_str, ended_bs = _scan_line(line, depth, in_str)
        prev_cont = ended_bs and in_str is None
    return starts


def _leading_spaces(s: str) -> int:
    return len(s) - len(s.lstrip(" "))


def _autofix_module_dedent(source: str) -> Optional[str]:
    """Strip the common leading indent shared by all module-scope logical-line
    starts. Fixes the "entire cell body indented at module level" pattern while
    preserving the relative indentation of any nested blocks. Continuation
    lines (inside brackets / triple-quoted strings) are left untouched.

    Returns the transformed source, or ``None`` when the heuristic does not
    apply (no positive common indent).
    """
    lines = source.split("\n")
    starts = _module_scope_starts(lines)
    considered = [
        i for i in starts
        if lines[i].strip() and not lines[i].lstrip().startswith("#")
    ]
    if not considered:
        return None
    common = min(_leading_spaces(lines[i]) for i in considered)
    if common <= 0:
        return None
    start_set = set(starts)
    out = list(lines)
    for i in start_set:
        strip_n = min(common, _leading_spaces(lines[i]))
        if strip_n:
            out[i] = lines[i][strip_n:]
    return "\n".join(out)


def try_autofix(source: str) -> Optional[str]:
    """Attempt whitespace-only, semantics-preserving repairs. Each candidate is
    accepted only if it compiles. Returns the fixed source, or ``None`` if no
    guarded transform makes the unit compile."""
    candidates = (
        textwrap.dedent(source),
        _autofix_module_dedent(source),
    )
    for cand in candidates:
        if cand is not None and cand != source and _compiles(cand):
            return cand
    return None


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _is_notebook_entry(abs_path: str) -> bool:
    try:
        return detect_format(abs_path).get("format") != "not_notebook"
    except Exception:  # noqa: BLE001
        return False


def _resolve(rel_or_abs: str, migrated_dir: str) -> str:
    if os.path.isabs(rel_or_abs):
        return rel_or_abs
    return os.path.join(migrated_dir, rel_or_abs)


def run_precompile_check(
    state: dict,
    *,
    dry_run: bool = False,
) -> dict:
    """Compile every manifest Python unit, auto-fix what is safely fixable, and
    record pre-existing syntax errors into ``state["preexisting_syntax"]``.

    Mutates ``state`` in place (adds ``preexisting_syntax`` and the
    ``phases_completed["0_4_precompile"]`` record). Does NOT persist state to
    disk — the caller owns that. Returns a summary dict.
    """
    manifest: list[str] = state.get("manifest", [])
    migrated_dir: str = state.get("migrated_dir", "")

    entries: list[dict] = []
    units_checked = 0
    units_fixed = 0

    for entry in manifest:
        abs_path = _resolve(entry, migrated_dir)
        if not os.path.exists(abs_path):
            continue
        is_nb = _is_notebook_entry(abs_path)
        if not is_nb and not entry.endswith(".py"):
            continue

        if is_nb:
            try:
                nb = parse_notebook(abs_path)
            except Exception:  # noqa: BLE001 — unreadable notebook; leave for later gates
                continue
            dirty = False
            for cell in nb.cells:
                if cell.cell_type != "code" or cell.cell_language != "python":
                    continue
                units_checked += 1
                err = _compile_error(cell.source)
                if err is None:
                    continue
                fixed = try_autofix(cell.source)
                if fixed is not None and not dry_run:
                    cell.source = fixed
                    dirty = True
                    units_fixed += 1
                entries.append({"file": entry, "cell_id": cell.index,
                                "error": err, "auto_fixed": fixed is not None})
            if dirty and not dry_run:
                write_notebook(abs_path, nb)
        else:
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    src = f.read()
            except OSError:
                continue
            units_checked += 1
            err = _compile_error(src)
            if err is None:
                continue
            fixed = try_autofix(src)
            if fixed is not None and not dry_run:
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write(fixed)
                units_fixed += 1
            entries.append({"file": entry, "cell_id": None,
                            "error": err, "auto_fixed": fixed is not None})

    state["preexisting_syntax"] = entries
    unresolved = [e for e in entries if not e["auto_fixed"]]
    phases = state.setdefault("phases_completed", {})
    phases["0_4_precompile"] = {
        "status": "passed",
        "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "units_checked": units_checked,
        "preexisting_errors": len(entries),
        "auto_fixed": units_fixed,
        "unresolved": len(unresolved),
    }
    return {
        "units_checked": units_checked,
        "preexisting_errors": len(entries),
        "auto_fixed": units_fixed,
        "unresolved": len(unresolved),
        "entries": entries,
    }


def _print_summary(summary: dict, *, dry_run: bool) -> None:
    print("=" * 60)
    print("PHASE 0.5 PRE-FLIGHT: PRE-EXISTING SYNTAX CHECK")
    print("=" * 60)
    print(f"  Units checked        : {summary['units_checked']}")
    print(f"  Pre-existing errors  : {summary['preexisting_errors']}")
    print(f"  Auto-fixed           : {summary['auto_fixed']}"
          f"{' (dry-run: fixable)' if dry_run else ''}")
    print(f"  Unresolved (source)  : {summary['unresolved']}")
    for e in summary["entries"]:
        loc = f"{e['file']}" + (f" cell {e['cell_id']}" if e["cell_id"] is not None else "")
        status = "auto-fixed" if e["auto_fixed"] else "UNRESOLVED"
        print(f"    [{status}] {loc}: {e['error']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 0.5 pre-flight: detect and safely auto-fix pre-existing "
            "Python syntax errors before recipes/analyzer run."
        )
    )
    parser.add_argument("--state", required=True, help="Path to migration_state.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report without modifying files or state.")
    args = parser.parse_args()

    state_path = os.path.abspath(args.state)
    if not os.path.exists(state_path):
        print(f"ERROR: migration_state.json not found: {state_path}", file=sys.stderr)
        return 1

    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)
    if not state.get("manifest"):
        print("ERROR: manifest is empty in migration_state.json", file=sys.stderr)
        return 1
    if not state.get("migrated_dir"):
        print("ERROR: migrated_dir not set in migration_state.json", file=sys.stderr)
        return 1

    summary = run_precompile_check(state, dry_run=args.dry_run)
    if not args.dry_run:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    _print_summary(summary, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
