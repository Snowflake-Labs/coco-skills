"""Unit tests for ``glue_transforms_to_dataframe_rewrite`` (Glue G3/G6/G7/G10).

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_glue_transforms_to_dataframe_rewrite.py
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from recipes import _common

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
_NAME = "glue_transforms_to_dataframe_rewrite"

_GLUE_HEADER = "from awsglue.transforms import *\n"


def _apply(source: str):
    source = textwrap.dedent(source).lstrip("\n")
    return _common.load_recipe_module(str(_RECIPES_DIR / _NAME)).apply(
        source, file="t.py"
    )


def _code(source: str) -> str:
    """Strip comment lines so assertions target code, not SCOS markers."""
    return "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))


# ---------------------------------------------------------------------------
# G3 -- ResolveChoice
# ---------------------------------------------------------------------------


def test_resolvechoice_match_catalog_is_noop():
    src = 'Resolved = ResolveChoice.apply(frame=DyF, choice="match_catalog")\n'
    res = _apply(src)
    code = _code(res.source)
    assert "Resolved = DyF" in code
    assert "ResolveChoice" not in code
    assert "[SPRKCNTPY3605-Fixed]" in res.source
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_resolvechoice_cast_choice_is_annotate_only():
    src = 'Resolved = ResolveChoice.apply(frame=DyF, choice="cast:long")\n'
    res = _apply(src)
    # code untouched
    assert _code(res.source).strip() == src.strip()
    assert "SCOS: TODO" in res.source
    assert "withColumn" in res.source
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_resolvechoice_make_cols_and_project_are_annotate_only():
    for choice in ("make_cols", "project:long"):
        src = f'R = ResolveChoice.apply(frame=DyF, choice="{choice}")\n'
        res = _apply(src)
        assert _code(res.source).strip() == src.strip()
        assert "SCOS: TODO" in res.source


def test_resolvechoice_non_literal_choice_is_todo():
    src = "R = ResolveChoice.apply(frame=DyF, choice=c)\n"
    res = _apply(src)
    assert _code(res.source).strip() == src.strip()
    assert "SCOS: TODO" in res.source


# ---------------------------------------------------------------------------
# G10 -- DropFields / SelectFields / RenameField
# ---------------------------------------------------------------------------


def test_dropfields_to_drop():
    src = 'out = DropFields.apply(frame=DyF, paths=["a", "b"])\n'
    res = _apply(src)
    code = _code(res.source)
    assert 'out = DyF.drop("a", "b")' in code
    assert "DropFields" not in code
    assert "[SPRKCNTPY3605-Fixed]" in res.source
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_selectfields_to_select():
    src = 'out = SelectFields.apply(frame=DyF, paths=["a", "b"])\n'
    code = _code(_apply(src).source)
    assert 'out = DyF.select("a", "b")' in code
    assert "SelectFields" not in code


def test_renamefield_to_withcolumnrenamed():
    src = 'out = RenameField.apply(frame=DyF, old_name="a", new_name="b")\n'
    res = _apply(src)
    code = _code(res.source)
    assert 'out = DyF.withColumnRenamed("a", "b")' in code
    assert "RenameField" not in code
    assert len(res.edits) == 1


def test_positional_frame_and_paths():
    src = 'out = DropFields.apply(DyF, ["a", "b"])\n'
    code = _code(_apply(src).source)
    assert 'out = DyF.drop("a", "b")' in code


def test_positional_renamefield():
    src = 'out = RenameField.apply(DyF, "a", "b")\n'
    code = _code(_apply(src).source)
    assert 'out = DyF.withColumnRenamed("a", "b")' in code


def test_dotted_receiver_is_handled():
    src = 'out = awsglue.transforms.DropFields.apply(frame=DyF, paths=["a"])\n'
    code = _code(_apply(src).source)
    assert 'out = DyF.drop("a")' in code
    assert "awsglue.transforms.DropFields" not in code


def test_single_path_list():
    src = 'out = SelectFields.apply(frame=DyF, paths=["only"])\n'
    code = _code(_apply(src).source)
    assert 'out = DyF.select("only")' in code


def test_frame_may_be_an_expression():
    src = 'out = DropFields.apply(frame=make_frame(x), paths=["a"])\n'
    code = _code(_apply(src).source)
    assert 'out = make_frame(x).drop("a")' in code


def test_multiline_call_is_rewritten():
    src = """
    out = DropFields.apply(
        frame=DyF,
        paths=["a", "b"],
    )
    """
    res = _apply(src)
    code = _code(res.source)
    assert 'out = DyF.drop("a", "b")' in code
    compile(res.source, "t.py", "exec")


def test_non_literal_paths_is_todo_not_rewritten():
    src = "out = DropFields.apply(frame=DyF, paths=cols)\n"
    res = _apply(src)
    assert _code(res.source).strip() == src.strip()
    assert "SCOS: TODO" in res.source
    assert len(res.edits) == 1


def test_missing_frame_is_todo():
    src = 'out = DropFields.apply(paths=["a"])\n'
    res = _apply(src)
    assert _code(res.source).strip() == src.strip()
    assert "SCOS: TODO" in res.source


def test_kwargs_splat_is_todo():
    src = "out = DropFields.apply(**cfg)\n"
    res = _apply(src)
    assert _code(res.source).strip() == src.strip()
    assert "SCOS: TODO" in res.source


# ---------------------------------------------------------------------------
# G10 -- annotate-only transforms
# ---------------------------------------------------------------------------


def test_relationalize_is_annotate_only():
    src = 'out = Relationalize.apply(frame=DyF, name="root")\n'
    res = _apply(src)
    assert _code(res.source).strip() == src.strip()
    assert "SCOS: TODO" in res.source
    assert "explode" in res.source
    assert len(res.edits) == 1


def test_unbox_is_annotate_only():
    src = 'out = Unbox.apply(frame=DyF, path="s", format="json")\n'
    res = _apply(src)
    assert _code(res.source).strip() == src.strip()
    assert "SCOS: TODO" in res.source
    assert "explode" in res.source


def test_join_is_annotate_only_with_suggestion():
    src = _GLUE_HEADER + "out = Join.apply(frame1=A, frame2=B, keys1=['k'], keys2=['k'])\n"
    res = _apply(src)
    assert _code(res.source).strip() == src.strip()
    assert "SCOS: TODO" in res.source
    assert 'a.join(b, on=..., how="inner")' in res.source
    assert len(res.edits) == 1


def test_map_is_annotate_only_with_coalesce_idiom():
    src = _GLUE_HEADER + "out = Map.apply(frame=DyF, f=fn)\n"
    res = _apply(src)
    assert _code(res.source).strip() == src.strip()
    assert "SCOS: TODO" in res.source
    assert 'F.coalesce(F.col("before")[c], F.col("after")[c])' in res.source


def test_map_and_join_untouched_without_glue_evidence():
    for src in (
        "out = Map.apply(frame=f, f=fn)\n",
        "out = Join.apply(frame1=a, frame2=b, keys1=['k'], keys2=['k'])\n",
    ):
        res = _apply(src)
        assert res.source == src
        assert res.edits == []


# ---------------------------------------------------------------------------
# G6 -- DynamicFrame lifecycle
# ---------------------------------------------------------------------------


def test_fromdf_returns_first_positional():
    src = 'out = DynamicFrame.fromDF(df, gc, "ctx")\n'
    res = _apply(src)
    code = _code(res.source)
    assert "out = df" in code
    assert "fromDF" not in code
    assert "[SPRKCNTPY3605-Fixed]" in res.source
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_fromdf_two_arg_form():
    src = "return DynamicFrame.fromDF(df, gc)\n"
    code = _code(_apply("def f():\n    " + src).source)
    assert "return df" in code


def test_todf_zero_arg_with_glue_evidence_is_dropped():
    src = _GLUE_HEADER + "df = dyf.toDF()\n"
    res = _apply(src)
    code = _code(res.source)
    assert "df = dyf" in code
    assert "toDF" not in code
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_todf_roundtrip_collapses():
    src = _GLUE_HEADER + 'out = DynamicFrame.fromDF(df, gc, "c").toDF()\n'
    code = _code(_apply(src).source)
    assert "out = df" in code
    assert "toDF" not in code and "fromDF" not in code


# ---------------------------------------------------------------------------
# HARD REGRESSION GUARDS for .toDF()
# ---------------------------------------------------------------------------


def test_g2_recipe_output_todf_star_cols_is_byte_identical():
    """Recipe G2's own output must never be touched.

    ``df.toDF(*[c.lower() for c in df.columns])`` is the lowercase-normalization
    emitted by the Glue catalog-read recipe. Rewriting it away would silently
    undo the case normalization.
    """
    src = "df = df.toDF(*[c.lower() for c in df.columns])\n"
    res = _apply(src)
    assert res.source == src
    assert res.edits == []
    # ... and also inside a file that DOES have Glue evidence.
    src2 = _GLUE_HEADER + "df = df.toDF(*[c.lower() for c in df.columns])\n"
    res2 = _apply(src2)
    assert res2.source == src2
    assert res2.edits == []


def test_todf_with_string_args_is_untouched():
    for src in (
        'df2 = df.toDF("a", "b")\n',
        _GLUE_HEADER + 'df2 = df.toDF("a", "b")\n',
        _GLUE_HEADER + "df2 = df.toDF(*cols)\n",
    ):
        res = _apply(src)
        assert res.source == src
        assert res.edits == []


def test_todf_zero_arg_without_glue_evidence_is_untouched():
    src = "df = dyf.toDF()\n"
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


# ---------------------------------------------------------------------------
# G6 -- schema() method -> schema property
# ---------------------------------------------------------------------------


def test_schema_call_in_list_comprehension_becomes_property():
    """The canonical crash case: mid-expression, inside a comprehension.

    Leaving the parens raises TypeError: 'StructType' object is not callable.
    """
    src = (
        _GLUE_HEADER
        + "keep = [f.name for f in dyf.schema().fields if f.name in TARGET]\n"
    )
    res = _apply(src)
    code = _code(res.source)
    assert "dyf.schema.fields" in code
    assert "dyf.schema()" not in code
    assert "[SPRKCNTPY3605-Fixed]" in res.source
    assert "property" in res.source.lower()
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_schema_call_as_whole_statement_becomes_property():
    src = _GLUE_HEADER + "s = dyf.schema()\n"
    res = _apply(src)
    code = _code(res.source)
    assert "s = dyf.schema" in code
    assert "dyf.schema()" not in code
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_schema_with_dotted_receiver_becomes_property():
    src = _GLUE_HEADER + "n = len(self.dyf.schema().fields)\n"
    code = _code(_apply(src).source)
    assert "self.dyf.schema.fields" in code
    assert "schema()" not in code


def test_schema_with_args_is_untouched():
    """A callable .schema(...) taking arguments is a validation-library builder."""
    for src in (
        _GLUE_HEADER + "s = model.schema(many=True)\n",
        _GLUE_HEADER + 's = reader.schema("a INT")\n',
        _GLUE_HEADER + "s = obj.schema(*args)\n",
    ):
        res = _apply(src)
        assert res.source == src, src
        assert res.edits == [], src


def test_schema_without_glue_evidence_is_untouched():
    """pandera / marshmallow / graphene expose a legitimately callable .schema()."""
    src = "s = model.schema()\nfields = model.schema().fields\n"
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


def test_schema_property_access_is_not_retouched():
    src = _GLUE_HEADER + "keep = [f.name for f in df.schema.fields]\n"
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


def test_schema_and_todf_in_same_statement_emit_distinct_comments():
    src = _GLUE_HEADER + "n = len(dyf.toDF().schema().fields)\n"
    res = _apply(src)
    code = _code(res.source)
    assert "dyf.schema.fields" in code
    assert "toDF" not in code and "schema()" not in code
    fixed_lines = [
        l for l in res.source.splitlines() if "[SPRKCNTPY3605-Fixed]" in l
    ]
    assert len(fixed_lines) == 2, fixed_lines
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_schema_rewrite_is_idempotent():
    for src in (
        _GLUE_HEADER + "keep = [f.name for f in dyf.schema().fields]\n",
        _GLUE_HEADER + "s = dyf.schema()\n",
    ):
        once = _apply(src).source
        twice = _apply(once)
        assert once == twice.source, src
        assert twice.edits == [], src
        compile(once, "t.py", "exec")


# ---------------------------------------------------------------------------
# G7 -- Custom Transform collection ceremony
# ---------------------------------------------------------------------------


def test_dynamicframecollection_is_annotate_only():
    src = _GLUE_HEADER + 'return DynamicFrameCollection({"CustomTransform": d}, gc)\n'
    res = _apply(src)
    assert "DynamicFrameCollection" in _code(res.source)
    assert "SCOS: TODO" in res.source
    assert "df-in/df-out" in res.source


def test_selectfromcollection_is_annotate_only():
    src = "out = SelectFromCollection.apply(dfc=coll, key='k')\n"
    res = _apply(src)
    assert _code(res.source).strip() == src.strip()
    assert "SCOS: TODO" in res.source


# ---------------------------------------------------------------------------
# Negative cases
# ---------------------------------------------------------------------------


def test_benign_pyspark_untouched():
    src = 'df = spark.read.table("t")\ndf.select("a").show()\n'
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


def test_unknown_apply_receiver_untouched():
    src = _GLUE_HEADER + 'out = MyRule.apply(frame=f, paths=["a"])\n'
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


def test_pandas_apply_untouched():
    src = _GLUE_HEADER + "out = pandas_df.apply(fn)\n"
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


def test_no_gate_token_is_fast_noop():
    src = "x = 1\ny = compute(x)\n"
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


# ---------------------------------------------------------------------------
# Idempotency / compile
# ---------------------------------------------------------------------------


def test_idempotent_rewrites():
    for src in (
        'Resolved = ResolveChoice.apply(frame=DyF, choice="match_catalog")\n',
        'out = DropFields.apply(frame=DyF, paths=["a", "b"])\n',
        'out = SelectFields.apply(frame=DyF, paths=["a"])\n',
        'out = RenameField.apply(frame=DyF, old_name="a", new_name="b")\n',
        'out = DynamicFrame.fromDF(df, gc, "ctx")\n',
        _GLUE_HEADER + "df = dyf.toDF()\n",
    ):
        once = _apply(src).source
        twice = _apply(once)
        assert once == twice.source, src
        assert twice.edits == [], src
        compile(once, "t.py", "exec")


def test_idempotent_annotations():
    for src in (
        'R = ResolveChoice.apply(frame=DyF, choice="cast:long")\n',
        'out = Relationalize.apply(frame=DyF, name="root")\n',
        "out = DropFields.apply(frame=DyF, paths=cols)\n",
        _GLUE_HEADER + "out = Map.apply(frame=DyF, f=fn)\n",
        _GLUE_HEADER + "out = Join.apply(frame1=A, frame2=B, keys1=['k'], keys2=['k'])\n",
    ):
        once = _apply(src).source
        twice = _apply(once)
        assert once == twice.source, src
        assert twice.edits == [], src
        compile(once, "t.py", "exec")


def test_full_glue_pipeline_compiles():
    src = """
    from awsglue.transforms import *
    from awsglue.dynamicframe import DynamicFrame

    df = dyf.toDF()
    Resolved = ResolveChoice.apply(frame=df, choice="match_catalog")
    dropped = DropFields.apply(frame=Resolved, paths=["tmp", "junk"])
    picked = SelectFields.apply(frame=dropped, paths=["id", "name"])
    renamed = RenameField.apply(frame=picked, old_name="id", new_name="pk")
    out = DynamicFrame.fromDF(renamed, glueContext, "out")
    """
    res = _apply(src)
    code = _code(res.source)
    compile(res.source, "t.py", "exec")
    assert "df = dyf" in code
    assert "Resolved = df" in code
    assert 'dropped = Resolved.drop("tmp", "junk")' in code
    assert 'picked = dropped.select("id", "name")' in code
    assert 'renamed = picked.withColumnRenamed("id", "pk")' in code
    assert "out = renamed" in code
    assert len(res.edits) == 6
