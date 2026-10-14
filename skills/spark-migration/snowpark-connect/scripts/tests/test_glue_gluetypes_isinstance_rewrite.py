"""Unit tests for ``glue_gluetypes_isinstance_rewrite`` (Glue G9).

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_glue_gluetypes_isinstance_rewrite.py
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from recipes import _common

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
_NAME = "glue_gluetypes_isinstance_rewrite"

_IMPORT = "import pyspark.sql.types as T"


def _apply(source: str):
    source = textwrap.dedent(source).lstrip("\n")
    return _common.load_recipe_module(str(_RECIPES_DIR / _NAME)).apply(
        source, file="t.py"
    )


def _code(source: str) -> str:
    """Strip comment lines so assertions target code, not SCOS markers."""
    return "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))


# ---------------------------------------------------------------------------
# Positive cases
# ---------------------------------------------------------------------------


def test_g9_canonical_if_rewrite():
    src = """
    if field.dataType in [BooleanType(), IntegerType(), LongType(), NullType()]:
        pass
    """
    res = _apply(src)
    code = _code(res.source)
    assert (
        "isinstance(field.dataType, (T.BooleanType, T.IntegerType, T.LongType, T.NullType))"
        in code
    )
    assert "BooleanType()" not in code
    assert " in [" not in code
    assert "[SPRKCNTPY3607-Fixed]" in res.source
    assert res.source.count(_IMPORT) == 1
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_assignment_position():
    src = "flag = t in [StringType(), DoubleType()]\n"
    res = _apply(src)
    code = _code(res.source)
    assert "flag = isinstance(t, (T.StringType, T.DoubleType))" in code
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_single_element_renders_trailing_comma():
    src = "flag = t in [IntegerType()]\n"
    res = _apply(src)
    code = _code(res.source)
    assert "isinstance(t, (T.IntegerType,))" in code
    compile(res.source, "t.py", "exec")


def test_not_in_produces_negated_isinstance():
    src = "flag = t not in [IntegerType()]\n"
    res = _apply(src)
    code = _code(res.source)
    assert "flag = not isinstance(t, (T.IntegerType,))" in code
    compile(res.source, "t.py", "exec")


def test_not_in_semantics_hold_under_and():
    """``not`` binds tighter than ``and``, so no extra parens are needed."""
    src = "flag = t not in [IntegerType()] and other\n"
    res = _apply(src)
    code = _code(res.source)
    assert "flag = not isinstance(t, (T.IntegerType,)) and other" in code
    compile(res.source, "t.py", "exec")

    # Semantic equivalence, evaluated for real.
    import pyspark.sql.types as T  # noqa: F401

    ns_before = {"t": T.StringType(), "other": True, "IntegerType": T.IntegerType}
    ns_after = dict(ns_before)
    exec(src, ns_before)  # noqa: S102
    exec(_code(res.source), ns_after)  # noqa: S102
    assert ns_before["flag"] == ns_after["flag"] is True


def test_not_in_inside_if():
    src = """
    if x not in [LongType(), ShortType()]:
        pass
    """
    res = _apply(src)
    code = _code(res.source)
    assert "if not isinstance(x, (T.LongType, T.ShortType)):" in code
    compile(res.source, "t.py", "exec")


def test_elif_and_while_positions():
    src = """
    if a:
        pass
    elif f.dataType in [BooleanType()]:
        pass
    while g in [DateType()]:
        pass
    """
    res = _apply(src)
    code = _code(res.source)
    assert "elif isinstance(f.dataType, (T.BooleanType,)):" in code
    assert "while isinstance(g, (T.DateType,)):" in code
    assert len(res.edits) == 2
    compile(res.source, "t.py", "exec")


def test_tuple_and_set_containers():
    for container in ("(IntegerType(), LongType())", "{IntegerType(), LongType()}"):
        src = f"flag = t in {container}\n"
        code = _code(_apply(src).source)
        assert "isinstance(t, (T.IntegerType, T.LongType))" in code


def test_dotted_type_names():
    src = "flag = t in [T2.BooleanType(), gluetypes.IntegerType()]\n"
    code = _code(_apply(src).source)
    assert "isinstance(t, (T.BooleanType, T.IntegerType))" in code


def test_multiline_container():
    src = """
    if field.dataType in [
        BooleanType(),
        IntegerType(),
    ]:
        pass
    """
    res = _apply(src)
    code = _code(res.source)
    assert "isinstance(field.dataType, (T.BooleanType, T.IntegerType))" in code
    compile(res.source, "t.py", "exec")


def test_nested_statement_gets_its_own_comment():
    src = """
    def f(field):
        if field.dataType in [BooleanType()]:
            flag = field.dataType in [IntegerType()]
        return flag
    """
    res = _apply(src)
    code = _code(res.source)
    assert "if isinstance(field.dataType, (T.BooleanType,)):" in code
    assert "flag = isinstance(field.dataType, (T.IntegerType,))" in code
    assert len(res.edits) == 2
    # one marker per rewritten line
    assert res.source.count("[SPRKCNTPY3607-Fixed]") == 2
    compile(res.source, "t.py", "exec")


# ---------------------------------------------------------------------------
# Import injection
# ---------------------------------------------------------------------------


def test_import_injected_exactly_once_for_multiple_rewrites():
    src = """
    a = x in [IntegerType()]
    b = y in [LongType()]
    c = z not in [StringType()]
    """
    res = _apply(src)
    assert res.source.count(_IMPORT) == 1
    assert len(res.edits) == 3
    compile(res.source, "t.py", "exec")


def test_import_not_duplicated_when_already_present():
    src = "import pyspark.sql.types as T\nflag = x in [IntegerType()]\n"
    res = _apply(src)
    assert res.source.count(_IMPORT) == 1
    assert "isinstance(x, (T.IntegerType,))" in _code(res.source)


def test_named_type_imports_still_get_T_prefix():
    """Documented single strategy: always ``T.``-prefixed + alias import."""
    src = "from pyspark.sql.types import BooleanType\nflag = x in [BooleanType()]\n"
    res = _apply(src)
    code = _code(res.source)
    assert "isinstance(x, (T.BooleanType,))" in code
    assert res.source.count(_IMPORT) == 1
    compile(res.source, "t.py", "exec")


def test_conflicting_T_binding_leaves_source_alone():
    for prelude in (
        "import numpy as T\n",
        "from foo import T\n",
        "T = 5\n",
        "def T():\n    pass\n",
        "class T:\n    pass\n",
        "def f(T):\n    pass\n",
    ):
        src = prelude + "flag = x in [IntegerType()]\n"
        res = _apply(src)
        assert res.source == src, prelude
        assert res.edits == [], prelude


# ---------------------------------------------------------------------------
# Negative cases
# ---------------------------------------------------------------------------


def test_plain_literal_membership_untouched():
    src = "x in [1, 2, 3]\n"
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


def test_non_type_suffixed_class_untouched():
    src = "flag = x in [SomeClass()]\n"
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


def test_mixed_elements_do_not_fire():
    for src in (
        "flag = x in [IntegerType(), 5]\n",
        "flag = x in [IntegerType(), SomeClass()]\n",
        "flag = x in [IntegerType(), *rest]\n",
    ):
        res = _apply(src)
        assert res.source == src, src
        assert res.edits == [], src


def test_type_call_with_arguments_does_not_fire():
    for src in (
        "flag = x in [DecimalType(10, 2)]\n",
        "flag = x in [ArrayType(StringType())]\n",
    ):
        res = _apply(src)
        assert res.source == src, src
        assert res.edits == [], src


def test_non_literal_container_untouched():
    src = "flag = x in allowed_types\n"
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


def test_empty_container_untouched():
    src = "flag = x in []\nt = IntegerType()\n"
    res = _apply(src)
    assert "isinstance" not in res.source


def test_chained_comparison_untouched():
    src = "flag = a in [IntegerType()] == b\n"
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


def test_equality_operator_untouched():
    src = "flag = x == [IntegerType()]\n"
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


def test_bare_type_instantiation_untouched():
    src = "t = IntegerType()\nschema = StructType([StructField('a', StringType())])\n"
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


def test_benign_pyspark_untouched():
    src = 'df = spark.read.table("t")\ndf.select("a").show()\n'
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent():
    for src in (
        "if field.dataType in [BooleanType(), IntegerType()]:\n    pass\n",
        "flag = t in [IntegerType()]\n",
        "flag = t not in [IntegerType()]\n",
        "import pyspark.sql.types as T\nflag = x in [LongType()]\n",
    ):
        once = _apply(src).source
        twice = _apply(once)
        assert once == twice.source, src
        assert twice.edits == [], src
        assert once.count(_IMPORT) == 1, src
        compile(once, "t.py", "exec")
