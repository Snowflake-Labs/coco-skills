"""Unit tests for the ``dbutils_widgets_to_env_rewrite`` LibCST recipe.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_dbutils_widgets_recipe.py
"""
from __future__ import annotations

from pathlib import Path

from recipes import _common

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
_NAME = "dbutils_widgets_to_env_rewrite"


def _apply(source: str):
    mod = _common.load_recipe_module(str(_RECIPES_DIR / _NAME))
    return mod.apply(source, file="t.py")


def _code(source: str) -> str:
    return "\n".join(
        l for l in source.splitlines() if not l.lstrip().startswith("#")
    )


def test_get_to_subscript():
    res = _apply('x = dbutils.widgets.get("yr")\n')
    code = _code(res.source)
    assert 'os.environ["yr"]' in code
    assert "dbutils.widgets" not in code
    assert "import os" in code  # injected
    assert len(res.edits) == 1


def test_text_to_setdefault_preserves_default():
    res = _apply('dbutils.widgets.text("yr", "2024")\n')
    code = _code(res.source)
    assert 'os.environ.setdefault("yr", "2024")' in code


def test_get_inside_int_wrapper():
    res = _apply('import os\nn = int(dbutils.widgets.get("yr"))\n')
    code = _code(res.source)
    assert 'int(os.environ["yr"])' in code
    # os already imported -> not duplicated
    assert code.count("import os") == 1


def test_getargument_with_default():
    res = _apply('v = dbutils.widgets.getArgument("yr", "0")\n')
    assert 'os.environ.get("yr", "0")' in _code(res.source)


def test_remove_to_pop():
    res = _apply('dbutils.widgets.remove("yr")\n')
    assert 'os.environ.pop("yr", None)' in _code(res.source)


def test_dropdown_setdefault():
    res = _apply('dbutils.widgets.dropdown("m", "a", ["a", "b"])\n')
    assert 'os.environ.setdefault("m", "a")' in _code(res.source)


def test_negative_removeall_untouched():
    res = _apply("dbutils.widgets.removeAll()\n")
    assert "dbutils.widgets.removeAll()" in res.source
    assert len(res.edits) == 0


def test_negative_non_dbutils_receiver():
    res = _apply('x = form.widgets.get("yr")\n')
    assert 'form.widgets.get("yr")' in res.source
    assert len(res.edits) == 0


def test_negative_fs_untouched():
    res = _apply('dbutils.fs.ls("/mnt/x")\n')
    assert "dbutils.fs.ls" in res.source
    assert len(res.edits) == 0


def test_no_import_os_when_no_rewrite():
    res = _apply("y = 1\n")
    assert "import os" not in res.source


def test_idempotent():
    first = _apply('n = int(dbutils.widgets.get("yr"))\n').source
    second = _apply(first).source
    assert first == second
