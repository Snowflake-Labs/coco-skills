"""Unit tests for ``glue_connector_writeback_todo_annotate``.

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_glue_connector_writeback_todo_annotate.py
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from recipes import _common

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
_NAME = "glue_connector_writeback_todo_annotate"
_SIBLING = "snowflake_connector_io_to_snowflake_session_rewrite"


def _apply(source: str):
    source = textwrap.dedent(source).lstrip("\n")
    return _common.load_recipe_module(str(_RECIPES_DIR / _NAME)).apply(
        source, file="t.py"
    )


def _code(source: str) -> str:
    """Strip comment lines so assertions target code, not SCOS markers."""
    return "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))


_FULL_CHAIN = """
df.write.format("net.snowflake.spark.snowflake").options(**sfOpts) \\
  .option("dbtable", tmp) \\
  .option("preactions", "CREATE TABLE IF NOT EXISTS a; CREATE TRANSIENT TABLE b;") \\
  .option("postactions", "MERGE INTO a USING b ON a.k = b.k; DROP TABLE b;") \\
  .mode("append").save()
"""


# ---------------------------------------------------------------------------
# Positive cases
# ---------------------------------------------------------------------------
def test_full_connector_chain_is_annotated_and_code_unchanged():
    src = textwrap.dedent(_FULL_CHAIN).lstrip("\n")
    res = _apply(src)
    # annotate-only: the code itself must be byte-identical
    assert _code(res.source) == _code(src)
    assert src in res.source
    assert "SCOS: TODO - [SPRKCNTPY3608-IO]" in res.source
    assert _NAME + ":" in res.source
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_bare_preactions_without_visible_format_still_triggers():
    """The format usually arrives via .options(**sfOpts), so the recipe must not
    require a statically visible .format("snowflake")."""
    src = 'df.write.options(**sfOpts).option("preactions", PRE).mode("append").save()\n'
    res = _apply(src)
    assert "SPRKCNTPY3608-IO" in res.source
    assert _code(res.source) == _code(src)
    assert len(res.edits) == 1


def test_postactions_alone_triggers():
    src = 'df.write.format("snowflake").option("postactions", POST).save()\n'
    res = _apply(src)
    assert "SPRKCNTPY3608-IO" in res.source
    assert len(res.edits) == 1


def test_option_key_matched_case_insensitively():
    src = 'df.write.option("PreActions", PRE).save()\n'
    res = _apply(src)
    assert "SPRKCNTPY3608-IO" in res.source


def test_todo_carries_the_replacement_shape_and_hard_won_rules():
    src = 'df.write.format("snowflake").option("postactions", POST).save()\n'
    out = _apply(src).source
    # replacement shape
    assert "saveAsTable" in out
    assert "CREATE TABLE IF NOT EXISTS" in out and "WHERE 1=0" in out
    assert "MERGE INTO" in out
    assert "WHEN MATCHED THEN UPDATE SET *" in out
    assert "WHEN NOT MATCHED THEN INSERT *" in out
    assert "DROP TABLE IF EXISTS" in out
    # ordering + guard rules
    assert "UPSERTS BEFORE DELETES" in out
    assert "spark.catalog.tableExists" in out
    # dialect trap
    assert "SPARK SQL ONLY" in out
    assert "backticks" in out
    assert "DELETE ... USING" in out


def test_annotation_is_multiline_block():
    src = 'df.write.option("preactions", PRE).save()\n'
    out = _apply(src).source
    comment_lines = [l for l in out.splitlines() if l.lstrip().startswith("#")]
    assert len(comment_lines) >= 5
    # exactly one line carries the driver's `recipe_id:` anchor
    assert sum(1 for l in comment_lines if _NAME + ":" in l) == 1


def test_annotation_inside_a_function_body():
    src = """
    def write_cdc(df):
        df.write.option("preactions", PRE).save()
    """
    res = _apply(src)
    assert "SPRKCNTPY3608-IO" in res.source
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


# ---------------------------------------------------------------------------
# Negative cases — byte-identical, no edits
# ---------------------------------------------------------------------------
def test_connector_write_without_hooks_untouched():
    """Owned by snowflake_connector_io_to_snowflake_session_rewrite."""
    src = 'df.write.format("snowflake").option("dbtable", T).mode("overwrite").save()\n'
    res = _apply(src)
    assert res.source == src and res.edits == []


def test_other_option_keys_untouched():
    for key in ("dbtable", "query", "mergeSchema", "sfWarehouse", "header"):
        src = f'df.write.option("{key}", V).save()\n'
        res = _apply(src)
        assert res.source == src, key
        assert res.edits == [], key


def test_plain_pyspark_untouched():
    for src in (
        'df = spark.read.table("t")\n',
        'df.select("a").show()\n',
        'df.write.mode("append").saveAsTable("d.t")\n',
    ):
        res = _apply(src)
        assert res.source == src and res.edits == []


def test_preactions_as_non_option_key_untouched():
    """The word appearing as a dict key or a variable is not an option key."""
    for src in (
        'cfg = {"preactions": "CREATE TABLE x;"}\n',
        "preactions = build_preactions(t)\n",
        'df.write.option(KEY_PREACTIONS, "postactions").save()\n',
    ):
        res = _apply(src)
        assert res.source == src, src
        assert res.edits == [], src


def test_keyword_option_call_is_not_matched():
    src = 'df.write.option(key="preactions", value=PRE).save()\n'
    res = _apply(src)
    assert res.source == src and res.edits == []


# ---------------------------------------------------------------------------
# Non-overlap with the sibling connector recipe
# ---------------------------------------------------------------------------
def test_skips_statement_already_marked_by_sibling_recipe():
    src = (
        f"# SCOS: TODO - [SPRKCNTPY5400-IO] {_SIBLING}: connector I/O with "
        f"non-literal configuration\n"
        'df.write.format("snowflake").options(**sfOpts).option("preactions", PRE).save()\n'
    )
    res = _apply(src)
    assert res.source == src and res.edits == []


def test_does_not_modify_code_so_sibling_rewrite_still_possible():
    """This recipe runs first alphabetically; it must leave the chain fully
    intact so the sibling recipe can still see (and convert) it."""
    src = 'df.write.format("snowflake").option("dbtable", T).option("preactions", PRE).save()\n'
    res = _apply(src)
    assert _code(res.source) == _code(src)
    assert 'format("snowflake")' in res.source
    assert '.option("dbtable", T)' in res.source


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
def test_idempotent():
    src = textwrap.dedent(_FULL_CHAIN).lstrip("\n")
    once = _apply(src).source
    second = _apply(once)
    assert once == second.source
    assert second.edits == []


def test_idempotent_single_line():
    src = 'df.write.option("preactions", PRE).save()\n'
    once = _apply(src).source
    second = _apply(once)
    assert once == second.source
    assert second.edits == []


def test_output_always_compiles():
    for src in (
        textwrap.dedent(_FULL_CHAIN).lstrip("\n"),
        'df.write.option("preactions", PRE).save()\n',
        'df.write.format("snowflake").option("postactions", POST).save()\n',
    ):
        compile(_apply(src).source, "t.py", "exec")
