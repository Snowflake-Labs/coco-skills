"""Scala-branch tests for the canonical patch_engine.py.

PySpark's patch_engine uses ast.parse to gate .py patches; for .scala/.sc files
it uses ``scalac -Ystop-after:parser`` when scalac is on PATH (no pre-gate
otherwise — the harness build is authoritative). These tests exercise that
Scala branch + the language-agnostic blueprint engine on .scala files.
Relocated from the (deleted) Scala validator's own copy now that patch_engine.py
is canonical here.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import patch_engine as pe  # noqa: E402

_HAS_SCALAC = shutil.which("scalac") is not None


# --- helpers to lay down both sides ---------------------------------------

def _setup(tmp_path, rel, source_text, output_text=None):
    src = tmp_path / "Validation" / "source" / rel
    out = tmp_path / "Output" / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(source_text)
    out.write_text(output_text if output_text is not None else source_text)
    return src, out


# --- add_patches: both sides, uniqueness, dedup, scala gate ----------------

def test_add_patches_both_sides(tmp_path):
    rel = "src/App.scala"
    body = 'object App { val x = System.getenv("FOO") }'
    _setup(tmp_path, rel, body)
    entry = {"id": "env_foo", "relative_file": rel,
             "search": 'System.getenv("FOO")', "replace": 'System.getProperty("FOO")'}
    ok, results, written, deduped = pe.add_patches(tmp_path, [entry])
    assert ok and not deduped and len(written) == 2
    assert 'System.getProperty("FOO")' in (tmp_path / "Validation/source" / rel).read_text()
    assert 'System.getProperty("FOO")' in (tmp_path / "Output" / rel).read_text()
    bp = json.loads((tmp_path / "Validation/shared/patch_blueprint.json").read_text())
    assert bp["patches"][0]["id"] == "env_foo"


def test_add_patches_dedup_on_resubmit(tmp_path):
    rel = "src/App.scala"
    _setup(tmp_path, rel, 'object App { val x = System.getenv("FOO") }')
    entry = {"id": "env_foo", "relative_file": rel,
             "search": 'System.getenv("FOO")', "replace": 'System.getProperty("FOO")'}
    pe.add_patches(tmp_path, [entry])
    ok, results, written, deduped = pe.add_patches(tmp_path, [entry])  # re-submit
    assert ok and deduped == ["env_foo"] and written == []


def test_add_patches_per_side_override_on_drift(tmp_path):
    rel = "src/App.scala"
    _setup(tmp_path, rel,
           source_text='val p = "/data/in.csv"',
           output_text='val p = sys.env("IN")')
    entry = {"id": "in_path", "relative_file": rel,
             "source": {"search": '"/data/in.csv"', "replace": 'System.getProperty("SCOS_IN")'},
             "migrated": {"search": 'sys.env("IN")', "replace": 'System.getProperty("SCOS_IN")'}}
    ok, _, written, _ = pe.add_patches(tmp_path, [entry])
    assert ok and len(written) == 2
    assert 'System.getProperty("SCOS_IN")' in (tmp_path / "Validation/source" / rel).read_text()
    assert 'System.getProperty("SCOS_IN")' in (tmp_path / "Output" / rel).read_text()


def test_add_patches_ambiguous_rejected_atomic(tmp_path):
    rel = "src/App.scala"
    _setup(tmp_path, rel, "val a = f(x); val b = f(x)")  # two matches
    entry = {"id": "amb", "relative_file": rel, "search": "f(x)", "replace": "g(x)"}
    ok, results, written, _ = pe.add_patches(tmp_path, [entry])
    assert not ok and written == []
    assert any("ambiguous" in (r.error or "") for r in results)
    assert "g(x)" not in (tmp_path / "Output" / rel).read_text()


@pytest.mark.skipif(not _HAS_SCALAC, reason="scalac not on PATH; .scala syntax "
                    "gate is a no-op without it (harness build is authoritative)")
def test_add_patches_scala_gate_rejects_unbalancing_removal(tmp_path):
    rel = "src/App.scala"
    _setup(tmp_path, rel, "object App {\n  def run() = { compute() }\n}\n")
    entry = {"id": "bad", "relative_file": rel, "search": "{ compute() }", "replace": "{ compute()"}
    ok, results, written, _ = pe.add_patches(tmp_path, [entry])
    # scalac -Ystop-after:parser rejects the broken syntax before commit
    assert not ok and written == []
    assert any("syntax" in (r.error or "") for r in results)


def test_add_patches_scala_gate_allows_balanced_removal(tmp_path):
    rel = "src/App.scala"
    _setup(tmp_path, rel, "object App {\n  logger.info(\"x\")\n  val y = 1\n}\n")
    entry = {"id": "drop_log", "relative_file": rel,
             "search": "  logger.info(\"x\")\n", "replace": ""}
    ok, _, written, _ = pe.add_patches(tmp_path, [entry])
    assert ok and len(written) == 2
    assert "logger.info" not in (tmp_path / "Output" / rel).read_text()
