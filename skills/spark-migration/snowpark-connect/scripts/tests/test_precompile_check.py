"""Unit tests for ``precompile_check`` — the Phase 0.5 pre-flight that detects
and safely auto-fixes *pre-existing* Python syntax errors, and for the
``scos_gates`` baseline-awareness that consumes its ``preexisting_syntax``
record.

The regression these guard against: a Databricks-exported notebook cell that
is stray-indented at module level (``IndentationError: unexpected indent``)
never compiled in the source. Recipes skip un-parseable input, so it survived
into Phase 2 and trapped the fixer's compile guard in a whole-file revert loop.
The pre-flight now repairs the safe cases and records the rest so downstream
phases stop mis-attributing the failure to the fixer.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from precompile_check import (  # noqa: E402
    _autofix_module_dedent,
    _compile_error,
    run_precompile_check,
    try_autofix,
)
from scos_gates import _preexisting_unfixed_files  # noqa: E402


# ---------------------------------------------------------------------------
# _compile_error
# ---------------------------------------------------------------------------

def test_compile_error_none_for_valid_source() -> None:
    assert _compile_error("x = 1\ny = x + 2\n") is None


def test_compile_error_reports_indentation() -> None:
    err = _compile_error("    x = 1\n")
    assert err is not None
    assert "IndentationError" in err


# ---------------------------------------------------------------------------
# try_autofix — uniform over-indent (textwrap.dedent path)
# ---------------------------------------------------------------------------

def test_uniform_over_indent_is_autofixed() -> None:
    src = "    x = 1\n    y = x + 2\n"
    fixed = try_autofix(src)
    assert fixed is not None
    assert _compile_error(fixed) is None
    assert fixed.splitlines()[0] == "x = 1"


# ---------------------------------------------------------------------------
# _autofix_module_dedent — the real cell-23 mixed-indentation pattern
# ---------------------------------------------------------------------------

def test_module_scope_dedent_fixes_mixed_merge_block() -> None:
    """Reproduces the customer bug: module-level statements indented by 4, with
    continuation lines inside parens at varied indent and a column-0 closing
    paren (so plain dedent finds a common prefix of 0 and cannot help)."""
    src = "\n".join([
        "    src = foo.select('a').distinct()",
        "    ",
        "    (",
        "        obj.alias('tgt')",
        "    .merge(source=s.alias('src')",
        "           ,condition= \"\"\"a = b",
        "                AND c = d",
        "                \"\"\")",
        " .whenMatched()",
        " .execute()",
        ")",
    ])
    assert _compile_error(src) is not None            # starts broken
    assert textwrap.dedent(src) == src or _compile_error(textwrap.dedent(src)) is not None
    fixed = _autofix_module_dedent(src)
    assert fixed is not None
    assert _compile_error(fixed) is None
    # module-level statements are now at column 0
    assert fixed.splitlines()[0] == "src = foo.select('a').distinct()"


def test_try_autofix_uses_module_scope_for_mixed_case() -> None:
    src = "\n".join([
        "    a = 1",
        "    (",
        "        b",
        " .c()",
        ")",
    ])
    fixed = try_autofix(src)
    assert fixed is not None and _compile_error(fixed) is None


# ---------------------------------------------------------------------------
# Genuinely unfixable input is left byte-identical (never made worse)
# ---------------------------------------------------------------------------

def test_unfixable_source_returns_none() -> None:
    src = "def f(:\n    pass\n"          # real syntax error, no whitespace fix
    assert _compile_error(src) is not None
    assert try_autofix(src) is None


def test_valid_source_is_not_touched() -> None:
    src = "def f():\n    return 1\n"
    assert try_autofix(src) is None       # nothing to fix


# ---------------------------------------------------------------------------
# run_precompile_check driver — plain .py file
# ---------------------------------------------------------------------------

def _state(tmp_path: Path, manifest: list[str]) -> dict:
    return {"manifest": manifest, "migrated_dir": str(tmp_path)}


def test_run_autofixes_plain_py_and_records(tmp_path: Path) -> None:
    f = tmp_path / "job.py"
    f.write_text("    x = 1\n    y = x + 1\n", encoding="utf-8")
    state = _state(tmp_path, ["job.py"])

    summary = run_precompile_check(state)

    assert summary["preexisting_errors"] == 1
    assert summary["auto_fixed"] == 1
    assert summary["unresolved"] == 0
    # file on disk now compiles
    assert _compile_error(f.read_text(encoding="utf-8")) is None
    # state records the entry + informational phase
    entry = state["preexisting_syntax"][0]
    assert entry["file"] == "job.py" and entry["auto_fixed"] is True
    assert state["phases_completed"]["0_4_precompile"]["status"] == "passed"


def test_run_records_unfixable_without_modifying(tmp_path: Path) -> None:
    original = "def f(:\n    pass\n"
    f = tmp_path / "broken.py"
    f.write_text(original, encoding="utf-8")
    state = _state(tmp_path, ["broken.py"])

    summary = run_precompile_check(state)

    assert summary["unresolved"] == 1
    assert summary["auto_fixed"] == 0
    assert f.read_text(encoding="utf-8") == original       # byte-identical
    assert state["preexisting_syntax"][0]["auto_fixed"] is False


def test_run_is_idempotent(tmp_path: Path) -> None:
    f = tmp_path / "job.py"
    f.write_text("    x = 1\n", encoding="utf-8")
    state = _state(tmp_path, ["job.py"])

    run_precompile_check(state)
    second = run_precompile_check(state)          # already fixed on disk
    assert second["preexisting_errors"] == 0
    assert second["auto_fixed"] == 0


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    original = "    x = 1\n"
    f = tmp_path / "job.py"
    f.write_text(original, encoding="utf-8")
    state = _state(tmp_path, ["job.py"])

    summary = run_precompile_check(state, dry_run=True)
    assert summary["preexisting_errors"] == 1
    assert f.read_text(encoding="utf-8") == original       # untouched on dry-run


# ---------------------------------------------------------------------------
# run_precompile_check driver — Databricks exported-text notebook
# ---------------------------------------------------------------------------

_EXPORTED_NB = (
    "# Databricks notebook source\n"
    "\n"
    "# COMMAND ----------\n"
    "\n"
    "x = 1\n"
    "y = x + 2\n"
    "\n"
    "# COMMAND ----------\n"
    "\n"
    "    a = 1\n"          # <- entire cell stray-indented at module level
    "    b = a + 1\n"
)


def test_run_autofixes_notebook_cell(tmp_path: Path) -> None:
    nb = tmp_path / "notebook_job.py"
    nb.write_text(_EXPORTED_NB, encoding="utf-8")
    state = _state(tmp_path, ["notebook_job.py"])

    summary = run_precompile_check(state)

    assert summary["auto_fixed"] == 1
    assert summary["unresolved"] == 0
    entry = state["preexisting_syntax"][0]
    assert entry["file"] == "notebook_job.py"
    assert entry["cell_id"] is not None and entry["auto_fixed"] is True
    # the fixed notebook's stray cell now begins at column 0
    text = nb.read_text(encoding="utf-8")
    assert "\na = 1\n" in text and "\n    a = 1\n" not in text


# ---------------------------------------------------------------------------
# scos_gates baseline-awareness helper
# ---------------------------------------------------------------------------

def test_preexisting_unfixed_files_only_returns_unfixed() -> None:
    state = {"preexisting_syntax": [
        {"file": "dir/fixed.py", "cell_id": 1, "error": "x", "auto_fixed": True},
        {"file": "dir/broken.py", "cell_id": 3, "error": "y", "auto_fixed": False},
    ]}
    assert _preexisting_unfixed_files(state) == {"broken.py"}


def test_preexisting_unfixed_files_empty_when_absent() -> None:
    assert _preexisting_unfixed_files({}) == set()
