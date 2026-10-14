"""Unit tests for the dbutils/display LibCST recipes mined from the
``snowflake-notebook-migration`` transformation-rules.md (rules 13, 17, 35, 36).

Each recipe is exercised for a positive trigger (rewrites + records an edit), a
negative case (non-matching code untouched), and idempotency. No sqlite needed:
``RecipeResult.edits`` is populated in memory.

Run from the ``snowpark-connect/`` directory:

    pytest scripts/tests/test_dbutils_display_recipes.py
"""
from __future__ import annotations

from pathlib import Path

from recipes import _common  # provided on sys.path via pyproject pythonpath

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"


def _apply(name: str, source: str):
    return _common.load_recipe_module(str(_RECIPES_DIR / name)).apply(source, file="t.py")


def _code_only(source: str) -> str:
    return "\n".join(
        ln for ln in source.splitlines() if not ln.lstrip().startswith("#")
    )


def _assert_idempotent(name: str, source: str) -> str:
    first = _apply(name, source).source
    second = _apply(name, first).source
    assert second == first, f"{name} not idempotent:\n{first!r}\n!=\n{second!r}"
    return first


# --------------------------------------------------------------------------- #
# rule 13 — dbutils.secrets.get -> None + TODO
# --------------------------------------------------------------------------- #
SECRETS = "dbutils_secrets_get_stub_rewrite"


def test_secrets_get_stubbed_to_none():
    res = _apply(SECRETS, 'token = dbutils.secrets.get(scope="kv", key="pw")\n')
    code = _code_only(res.source)
    assert "token = None" in code
    assert "dbutils.secrets.get" not in code
    assert "SCOS-TODO" in res.source
    assert len(res.edits) == 1


def test_secrets_getargument_stubbed():
    res = _apply(SECRETS, 'v = dbutils.secrets.getArgument("k")\n')
    assert "v = None" in _code_only(res.source)


def test_secrets_bare_expression_stubbed():
    res = _apply(SECRETS, 'dbutils.secrets.get(scope="s", key="k")\n')
    assert "None" in _code_only(res.source)
    assert "dbutils.secrets.get" not in _code_only(res.source)


def test_secrets_list_untouched():
    # rule 38 (list/listScopes/getBytes) is NOT this recipe's job
    src = 'scopes = dbutils.secrets.listScopes()\n'
    res = _apply(SECRETS, src)
    assert res.source == src
    assert res.edits == []


def test_secrets_non_dbutils_untouched():
    src = 'x = myvault.secrets.get("k")\n'
    assert _apply(SECRETS, src).source == src


def test_secrets_idempotent():
    _assert_idempotent(SECRETS, 'token = dbutils.secrets.get(scope="kv", key="pw")\n')


# --------------------------------------------------------------------------- #
# rule 36 — dbutils.library.restartPython() removed
# --------------------------------------------------------------------------- #
RESTART = "dbutils_library_restartpython_strip_rewrite"


def test_restartpython_removed():
    src = "import numpy\ndbutils.library.restartPython()\nprint(1)\n"
    res = _apply(RESTART, src)
    assert "restartPython" not in res.source
    assert "import numpy" in res.source and "print(1)" in res.source
    assert len(res.edits) == 1


def test_restartpython_with_args_untouched():
    src = "dbutils.library.restartPython(timeout=5)\n"
    assert _apply(RESTART, src).source == src


def test_restartpython_other_method_untouched():
    src = 'dbutils.library.installPyPI("numpy")\n'
    res = _apply(RESTART, src)
    assert res.source == src and res.edits == []


def test_restartpython_idempotent():
    _assert_idempotent(RESTART, "dbutils.library.restartPython()\nx = 1\n")


# --------------------------------------------------------------------------- #
# rule 35 — dbutils.library.installPyPI(...) -> None + pip TODO (with pkg name)
# --------------------------------------------------------------------------- #
INSTALL = "dbutils_library_installpypi_stub_rewrite"


def test_installpypi_stubbed_with_package_name():
    res = _apply(INSTALL, 'dbutils.library.installPyPI("numpy")\n')
    assert "dbutils.library.installPyPI" not in _code_only(res.source)
    assert "None" in _code_only(res.source)
    assert "numpy" in res.source and "SCOS-TODO" in res.source
    assert len(res.edits) == 1


def test_installpypi_package_kwarg():
    res = _apply(INSTALL, 'dbutils.library.installPyPI(package="pandas", version="2.0")\n')
    assert "pandas" in res.source


def test_installpypi_non_literal_pkg_no_name_but_stubbed():
    res = _apply(INSTALL, "dbutils.library.installPyPI(pkg_var)\n")
    assert "installPyPI" not in _code_only(res.source)
    assert "SCOS-TODO" in res.source


def test_installpypi_idempotent():
    _assert_idempotent(INSTALL, 'dbutils.library.installPyPI("numpy")\n')


# --------------------------------------------------------------------------- #
# rule 17 — display(<pyplot figure>) -> <root>.show()
# --------------------------------------------------------------------------- #
MPL = "display_matplotlib_to_show_rewrite"


def test_display_plt_gcf_to_show():
    res = _apply(MPL, "display(plt.gcf())\n")
    assert _code_only(res.source).strip() == "plt.show()"
    assert len(res.edits) == 1


def test_display_pyplot_root():
    res = _apply(MPL, "display(pyplot.figure())\n")
    assert "pyplot.show()" in res.source


def test_display_matplotlib_pyplot_chain():
    res = _apply(MPL, "display(matplotlib.pyplot.gcf())\n")
    assert "matplotlib.pyplot.show()" in res.source


def test_display_bare_var_untouched():
    # unknown type -> not matched (display_to_show_rewrite owns the DataFrame case)
    src = "display(fig)\n"
    res = _apply(MPL, src)
    assert res.source == src and res.edits == []


def test_display_dataframe_untouched():
    src = "display(df.filter(col))\n"
    assert _apply(MPL, src).source == src


def test_display_method_call_untouched():
    src = "obj.display(plt.gcf())\n"
    assert _apply(MPL, src).source == src


def test_display_matplotlib_idempotent():
    _assert_idempotent(MPL, "display(plt.gcf())\n")
