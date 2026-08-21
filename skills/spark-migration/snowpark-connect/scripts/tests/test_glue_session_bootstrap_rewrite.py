"""Unit tests for ``glue_session_bootstrap_rewrite``.

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_glue_session_bootstrap_rewrite.py
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from recipes import _common

_RECIPES_DIR = Path(__file__).resolve().parents[1] / "recipes"
_NAME = "glue_session_bootstrap_rewrite"

_SNOWPARK_IMPORT = "from snowflake import snowpark_connect"
_SESSION_CALL = "snowpark_connect.init_spark_session()"


def _apply(source: str):
    source = textwrap.dedent(source).lstrip("\n")
    return _common.load_recipe_module(str(_RECIPES_DIR / _NAME)).apply(
        source, file="t.py"
    )


def _code(source: str) -> str:
    """Strip comment lines so assertions target code, not SCOS markers."""
    return "\n".join(
        l for l in source.splitlines() if not l.lstrip().startswith("#")
    )


def _dedent(source: str) -> str:
    return textwrap.dedent(source).lstrip("\n")


# ---------------------------------------------------------------------------
# Transform 1: GlueContext(...) -> snowpark_connect.init_spark_session()
# ---------------------------------------------------------------------------


def test_gluecontext_assign_rewritten_with_import():
    src = """
    from awsglue.context import GlueContext
    glueContext = GlueContext(sc)
    """
    res = _apply(src)
    code = _code(res.source)
    assert f"glueContext = {_SESSION_CALL}" in code
    assert "GlueContext(sc)" not in code
    assert res.source.count(_SNOWPARK_IMPORT) == 1
    assert "SPRKCNTPY3600-Fixed" in res.source
    assert _NAME + ":" in res.source
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_gluecontext_in_return_rewritten():
    src = """
    from awsglue.context import GlueContext

    def build():
        return GlueContext(SparkContext.getOrCreate())
    """
    res = _apply(src)
    code = _code(res.source)
    assert f"return {_SESSION_CALL}" in code
    assert "GlueContext(" not in code
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_gluecontext_bare_expression_rewritten():
    src = """
    from awsglue.context import GlueContext
    GlueContext(sc)
    """
    res = _apply(src)
    code = _code(res.source)
    assert _SESSION_CALL in code
    assert "GlueContext(sc)" not in code
    assert len(res.edits) == 1
    compile(res.source, "t.py", "exec")


def test_dotted_gluecontext_path_rewritten():
    src = """
    import awsglue.context
    glueContext = awsglue.context.GlueContext(sc)
    """
    code = _code(_apply(src).source)
    assert f"glueContext = {_SESSION_CALL}" in code
    assert "awsglue.context.GlueContext(sc)" not in code


def test_import_not_duplicated_when_already_present():
    src = """
    from awsglue.context import GlueContext
    from snowflake import snowpark_connect
    glueContext = GlueContext(sc)
    """
    out = _apply(src).source
    assert out.count(_SNOWPARK_IMPORT) == 1


# ---------------------------------------------------------------------------
# Transform 2: <var>.spark_session -> <var>
# ---------------------------------------------------------------------------


def test_spark_session_hop_dropped():
    src = """
    from awsglue.context import GlueContext
    glueContext = GlueContext(sc)
    spark = glueContext.spark_session
    """
    res = _apply(src)
    code = _code(res.source)
    assert "spark = glueContext" in code
    assert ".spark_session" not in code
    assert "SPRKCNTPY3600-Fixed" in res.source
    compile(res.source, "t.py", "exec")


def test_spark_session_hop_dropped_mid_chain():
    src = """
    from awsglue.context import GlueContext
    df = glueContext.spark_session.read.table("t")
    """
    code = _code(_apply(src).source)
    assert 'df = glueContext.read.table("t")' in code
    assert ".spark_session" not in code


def test_spark_session_not_touched_without_glue_evidence():
    # A non-Glue codebase with its own ``.spark_session`` property must be
    # left byte-for-byte alone.
    src = _dedent(
        """
        class Runner:
            @property
            def spark_session(self):
                return self._s

        spark = Runner().spark_session
        """
    )
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


# ---------------------------------------------------------------------------
# Transform 3: dead SparkContext binding -> commented out
# ---------------------------------------------------------------------------


def test_sparkcontext_only_feeding_gluecontext_is_commented_out():
    src = """
    from awsglue.context import GlueContext
    sc = SparkContext.getOrCreate()
    glueContext = GlueContext(sc)
    """
    res = _apply(src)
    code = _code(res.source)
    assert "sc = SparkContext.getOrCreate()" not in code
    assert "# sc = SparkContext.getOrCreate()" in res.source
    assert "pass" in code
    assert "SPRKCNTPY3600-Fixed" in res.source
    compile(res.source, "t.py", "exec")


def test_sparkcontext_direct_construction_commented_out():
    src = """
    from awsglue.context import GlueContext
    sc = SparkContext(conf=conf)
    glueContext = GlueContext(sc)
    """
    code = _code(_apply(src).source)
    assert "sc = SparkContext(conf=conf)" not in code


def test_sparkcontext_keyword_arg_to_gluecontext_commented_out():
    src = """
    from awsglue.context import GlueContext
    sc = SparkContext.getOrCreate()
    glueContext = GlueContext(sparkContext=sc)
    """
    code = _code(_apply(src).source)
    assert "sc = SparkContext.getOrCreate()" not in code


def test_sparkcontext_with_other_uses_is_left_alone():
    # ``sc`` still does real work -> another recipe owns it.
    src = """
    from awsglue.context import GlueContext
    sc = SparkContext.getOrCreate()
    glueContext = GlueContext(sc)
    rdd = sc.parallelize([1, 2, 3])
    """
    code = _code(_apply(src).source)
    assert "sc = SparkContext.getOrCreate()" in code
    assert "rdd = sc.parallelize([1, 2, 3])" in code


def test_sparkcontext_never_passed_to_gluecontext_is_left_alone():
    src = """
    from awsglue.job import Job
    sc = SparkContext.getOrCreate()
    rdd = sc.parallelize([1])
    """
    code = _code(_apply(src).source)
    assert "sc = SparkContext.getOrCreate()" in code


def test_sparkcontext_reassigned_is_left_alone():
    src = """
    from awsglue.context import GlueContext
    sc = SparkContext.getOrCreate()
    glueContext = GlueContext(sc)
    sc = other()
    """
    code = _code(_apply(src).source)
    assert "sc = SparkContext.getOrCreate()" in code


# ---------------------------------------------------------------------------
# Transform 4: Job lifecycle -> commented out with the G8 bookmark warning
# ---------------------------------------------------------------------------


def test_job_lifecycle_commented_out_with_bookmark_warning():
    src = """
    from awsglue.job import Job
    job = Job(glueContext)
    job.init(args["JOB_NAME"], args)
    job.commit()
    """
    res = _apply(src)
    code = _code(res.source)
    assert "Job(glueContext)" not in code
    assert "job.init(" not in code
    assert "job.commit()" not in code
    assert "# job = Job(glueContext)" in res.source
    assert "# job.commit()" in res.source
    assert "SPRKCNTPY3606-Error" in res.source
    # The G8 pointer is the whole reason we do not delete these silently.
    assert "G8" in res.source
    assert "BOOKMARK" in res.source
    assert len(res.edits) == 3
    compile(res.source, "t.py", "exec")


def test_init_commit_on_untraceable_var_left_alone():
    # ``tracker`` is not assigned from Job(...) -> not ours to remove.
    src = """
    from awsglue.context import GlueContext
    tracker = Tracker()
    tracker.init(a)
    tracker.commit()
    """
    code = _code(_apply(src).source)
    assert "tracker.init(a)" in code
    assert "tracker.commit()" in code


def test_attribute_receiver_commit_left_alone():
    src = """
    from awsglue.job import Job
    self.job.commit()
    """
    res = _apply(src)
    assert res.source == _dedent(src)
    assert res.edits == []


def test_commented_out_sole_function_body_still_compiles():
    src = """
    from awsglue.job import Job
    job = Job(ctx)

    def finish():
        job.commit()
    """
    res = _apply(src)
    assert "pass" in _code(res.source)
    compile(res.source, "t.py", "exec")


# ---------------------------------------------------------------------------
# End-to-end G1 bootstrap
# ---------------------------------------------------------------------------


def test_full_g1_bootstrap():
    src = """
    import sys
    from awsglue.context import GlueContext
    from awsglue.job import Job
    from pyspark.context import SparkContext

    sc = SparkContext.getOrCreate()
    glueContext = GlueContext(sc)
    spark = glueContext.spark_session
    job = Job(glueContext)
    job.init(args["JOB_NAME"], args)
    df = spark.read.table("t")
    job.commit()
    """
    res = _apply(src)
    code = _code(res.source)
    assert f"glueContext = {_SESSION_CALL}" in code
    assert "spark = glueContext" in code
    assert ".spark_session" not in code
    assert "SparkContext.getOrCreate()" not in code
    assert "Job(" not in code
    assert "job.init(" not in code and "job.commit()" not in code
    # the real pipeline line survives untouched
    assert 'df = spark.read.table("t")' in code
    assert res.source.count(_SNOWPARK_IMPORT) == 1
    compile(res.source, "t.py", "exec")


# ---------------------------------------------------------------------------
# Negative cases + idempotency
# ---------------------------------------------------------------------------


def test_benign_pyspark_untouched():
    src = 'df = spark.read.table("t")\ndf.select("a").show()\n'
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


def test_already_migrated_source_untouched():
    src = _dedent(
        f"""
        {_SNOWPARK_IMPORT}
        spark = {_SESSION_CALL}
        df = spark.read.table("t")
        """
    )
    res = _apply(src)
    assert res.source == src
    assert res.edits == []


def test_idempotent_full_bootstrap():
    src = """
    import sys
    from awsglue.context import GlueContext
    from awsglue.job import Job
    from pyspark.context import SparkContext

    sc = SparkContext.getOrCreate()
    glueContext = GlueContext(sc)
    spark = glueContext.spark_session
    job = Job(glueContext)
    job.init(args["JOB_NAME"], args)
    job.commit()
    """
    once = _apply(src).source
    second = _apply(once)
    assert once == second.source
    assert second.edits == []
    compile(once, "t.py", "exec")
