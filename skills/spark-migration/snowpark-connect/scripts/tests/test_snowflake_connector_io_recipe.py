"""Unit tests for ``snowflake_connector_io_to_snowflake_session_rewrite``.

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_snowflake_connector_io_recipe.py
"""
from __future__ import annotations

from pathlib import Path

from recipes import _common

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
_NAME = "snowflake_connector_io_to_snowflake_session_rewrite"


def _apply(source: str):
    return _common.load_recipe_module(str(_RECIPES_DIR / _NAME)).apply(source, file="t.py")


def _code(source: str) -> str:
    return "\n".join(l for l in source.splitlines() if not l.lstrip().startswith("#"))


def test_read_query_to_snowflake_session():
    src = 'df = spark.read.format("snowflake").option("query", Q).load()\n'
    res = _apply(src)
    code = _code(res.source)
    assert "SnowflakeSession(spark).sql(Q)" in code
    assert 'format("snowflake")' not in code
    assert "spark.sql(" not in code  # NOT bare spark.sql
    assert _SF_IMPORT_LINE in res.source
    assert len(res.edits) == 1


def test_read_dbtable_literal_to_select_star():
    src = 'df = spark.read.format("snowflake").option("dbtable", "DB.SC.T").load()\n'
    code = _code(_apply(src).source)
    assert 'SnowflakeSession(spark).sql("SELECT * FROM DB.SC.T")' in code


def test_renamed_session_receiver_preserved():
    src = 'df = spark_session.read.format("snowflake").option("query", q).load()\n'
    code = _code(_apply(src).source)
    assert "SnowflakeSession(spark_session).sql(q)" in code
    # self.spark receiver
    src2 = 'df = self.spark.read.format("snowflake").option("query", q).load()\n'
    assert "SnowflakeSession(self.spark).sql(q)" in _code(_apply(src2).source)


def test_net_snowflake_spark_format_alias():
    src = 'df = spark.read.format("net.snowflake.spark.snowflake").option("query", q).load()\n'
    assert "SnowflakeSession(spark).sql(q)" in _code(_apply(src).source)


def test_write_dbtable_with_mode_to_saveastable():
    src = 'df.write.format("snowflake").option("dbtable", T).mode("overwrite").save()\n'
    code = _code(_apply(src).source)
    assert 'df.write.mode("overwrite").saveAsTable(T)' in code
    assert 'format("snowflake")' not in code


def test_write_no_mode_to_saveastable():
    src = 'out.write.format("snowflake").option("dbtable", "DB.SC.T").save()\n'
    code = _code(_apply(src).source)
    assert 'out.write.saveAsTable("DB.SC.T")' in code


def test_options_dict_is_todo_not_rewritten():
    src = 'df = spark.read.format("snowflake").options(**cfg).load()\n'
    res = _apply(src)
    code = _code(res.source)
    # left intact
    assert 'format("snowflake")' in code and ".load()" in code
    assert "SnowflakeSession" not in code
    assert "SCOS: TODO" in res.source


def test_read_without_query_or_dbtable_is_todo():
    src = 'df = spark.read.format("snowflake").option("sfWarehouse", "WH").load()\n'
    res = _apply(src)
    assert "SCOS: TODO" in res.source
    assert 'format("snowflake")' in _code(res.source)


def test_non_snowflake_format_untouched():
    for fmt in ("parquet", "delta", "csv", "json"):
        src = f'df = spark.read.format("{fmt}").load(p)\n'
        res = _apply(src)
        assert res.source == src
        assert res.edits == []


def test_saveastable_without_snowflake_format_untouched():
    src = 'df.write.saveAsTable("t")\n'
    res = _apply(src)
    assert res.source == src and res.edits == []


def test_idempotent():
    src = 'df = spark.read.format("snowflake").option("query", Q).load()\n'
    once = _apply(src).source
    twice = _apply(once).source
    assert once == twice


def test_import_not_duplicated_when_present():
    src = (
        "from snowflake.snowpark_connect.snowflake_session import SnowflakeSession\n"
        'df = spark.read.format("snowflake").option("query", Q).load()\n'
    )
    out = _apply(src).source
    assert out.count("import SnowflakeSession") == 1


def test_explicit_context_options_emit_use_calls():
    src = (
        'df = (spark.read.format("snowflake")\n'
        '      .option("sfDatabase", "BRAND_PLK")\n'
        '      .option("sfSchema", "STORES")\n'
        '      .option("sfWarehouse", "ANALYSIS_PLK")\n'
        '      .option("query", "SELECT * FROM t")\n'
        "      .load())\n"
    )
    code = _code(_apply(src).source)
    # context set before the query, in role->warehouse->database->schema order
    assert 'SnowflakeSession(spark).use_warehouse("ANALYSIS_PLK")' in code
    assert 'SnowflakeSession(spark).use_database("BRAND_PLK")' in code
    assert 'SnowflakeSession(spark).use_schema("STORES")' in code
    assert 'SnowflakeSession(spark).sql("SELECT * FROM t")' in code
    # use_database precedes use_schema; all precede the .sql() read
    assert code.index("use_database") < code.index("use_schema") < code.index(".sql(")


def test_options_splat_only_rewrites_query_without_use_calls():
    # sfDatabase/sfSchema live in a splatted dict -> not statically visible,
    # so the query is still converted but no use_* calls are emitted.
    src = 'df = spark.read.format("snowflake").options(**cfg).option("query", Q).load()\n'
    code = _code(_apply(src).source)
    assert "SnowflakeSession(spark).sql(Q)" in code
    assert "use_database" not in code and "use_schema" not in code


_SF_IMPORT_LINE = "from snowflake.snowpark_connect.snowflake_session import SnowflakeSession"
